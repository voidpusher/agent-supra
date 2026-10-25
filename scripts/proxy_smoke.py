"""Exercise loopback proxy HTTP + Supra API; --gemini adds two real provider calls."""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import threading
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn

from agent_supra.claude_hook import http_client
from agent_supra.pricing import Price, lookup
from agent_supra.proxy import Config, Ledger, create_app
from scripts.self_test_agent import local_service


@contextmanager
def proxy_server(app):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('local proxy failed to start')
            time.sleep(.03)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        worker.join(timeout=15)
        listener.close()
        if worker.is_alive():
            raise RuntimeError('local proxy did not stop cleanly')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gemini', action='store_true')
    parser.add_argument('--sdk-path', type=Path, help='optional isolated OpenAI SDK install for a real SDK-client check')
    args = parser.parse_args()
    output = (Path('.local/proxy-smoke') / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    output.mkdir(parents=True)
    provider_key = 'synthetic-provider-key'
    if args.gemini:
        import os
        from dotenv import dotenv_values
        provider_key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
        if not provider_key:
            raise SystemExit('Place GEMINI_API_KEY in the ignored .env or environment; never in chat.')
    calls = []
    def mock(request):
        calls.append(1)
        usage = {'prompt_tokens': 20, 'completion_tokens': 10}
        if json.loads(request.content).get('stream'):
            common = {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'proxy-fixture'}
            chunks = [dict(common, choices=[{'index': 0, 'delta': {'content': 'OK'}, 'finish_reason': 'stop'}]), dict(common, choices=[], usage=usage)]
            wire = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
            return httpx.Response(200, content=wire.encode(), headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json={'id': 'chatcmpl-fixture', 'object': 'chat.completion', 'created': 1, 'model': 'proxy-fixture',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}], 'usage': usage})
    model = 'gemini-3.5-flash-lite' if args.gemini else 'proxy-fixture'
    config = Config(model, lookup(model) if args.gemini else Price(1, 2), provider='gemini' if args.gemini else 'openai',
                    budget_usd=.02, max_calls=2, max_output_tokens=64, timeout=30)
    with local_service(output) as (backend, supervisor, workspace, alerts):
        ledger = Ledger(output / 'private-journal', 'smoke', config)
        try:
            token = secrets.token_urlsafe(32)
            app = create_app(config, ledger, http_client(backend.api_key, backend.base_url), provider_key, token,
                             transport=None if args.gemini else httpx.MockTransport(mock))
            with proxy_server(app) as base, httpx.Client(base_url=base, timeout=45, trust_env=False,
                                                       headers={'Authorization': 'Bearer ' + token}) as client:
                body = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}]}
                sdk = None
                if args.sdk_path:
                    code = '''import sys,json
sys.path.insert(0,sys.argv[1])
import openai
p=json.load(sys.stdin)
c=openai.OpenAI(base_url=p['base']+'/v1',api_key=p['token'],max_retries=0,timeout=45)
def call(key,stream=False):
 options={'stream_options':{'include_usage':True}} if stream else {}
 return c.chat.completions.create(model=p['model'],messages=[{'role':'user','content':'Reply with OK.'}],stream=stream,extra_headers={'Idempotency-Key':key},**options)
r=call('ordinary')
assert r.usage.prompt_tokens >= 0
try:
 call('ordinary')
 duplicate=200
except openai.APIStatusError as e:
 duplicate=e.status_code
stream=call('streamed',True)
usage_seen=False
try:
 for chunk in stream:
  usage_seen |= chunk.usage is not None
finally:
 stream.close()
try:
 call('over-cap')
 blocked=200
except openai.APIStatusError as e:
 blocked=e.status_code
c.close()
print(json.dumps(dict(version=openai.__version__,ordinary=200,duplicate=duplicate,stream=200,blocked=blocked,usage_seen=usage_seen)))
'''
                    proc = subprocess.run([sys.executable, '-c', code, str(args.sdk_path.resolve())],
                        input=json.dumps(dict(base=base, token=token, model=model)), capture_output=True, text=True, timeout=60)
                    if proc.returncode:
                        raise RuntimeError('SDK-client check failed; raw output withheld')
                    sdk = json.loads(proc.stdout)
                    ordinary, duplicate, streamed, blocked = [sdk[k] for k in ('ordinary', 'duplicate', 'stream', 'blocked')]
                else:
                    ordinary = client.post('/v1/chat/completions', json=body, headers={'Idempotency-Key': 'ordinary'}).status_code
                    duplicate = client.post('/v1/chat/completions', json=body, headers={'Idempotency-Key': 'ordinary'}).status_code
                    streamed = client.post('/v1/chat/completions', json=dict(body, stream=True), headers={'Idempotency-Key': 'streamed'}).status_code
                    blocked = client.post('/v1/chat/completions', json=body, headers={'Idempotency-Key': 'over-cap'}).status_code
                timeline = backend.timeline(ledger.get('run'))
                run = timeline['run']
                usage_events = [e for e in timeline['events'] if e['type'] == 'model.call']
                passed = (ordinary == 200 and duplicate == 409 and streamed == 200
                    and blocked == 403 and run['model_calls'] == 2 and run['unpriced_calls'] == 0
                    and run['spend_micros'] > 0 and run['state'] == 'paused' and len(usage_events) == 2
                    and all(not e['data']['usage_missing'] for e in usage_events))
                result = {'passed': passed and (sdk is None or sdk['usage_seen']), 'ordinary_http': ordinary, 'duplicate_http': duplicate,
                    'stream_http': streamed, 'over_cap_http': blocked, 'run_state': run['state'], 'sdk_client': sdk,
                    'model_calls': run['model_calls'], 'estimated_spend_micros': run['spend_micros'],
                    'usage_missing': run['unpriced_calls'], 'pending_events': ledger.pending()}
        finally:
            ledger.close()
    report = {'kind': 'Real Gemini via local proxy' if args.gemini else 'Scripted provider via local proxy',
              'production_supra_used': False, 'temporary_supra_key_revoked': True, 'results': result}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    print('Sanitized report:', output / 'report.json')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
