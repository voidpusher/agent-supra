import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_supra.claude_hook import HookClient
from agent_supra.pricing import Price
from agent_supra.proxy import Config, Ledger, SSEUsage, create_app, prepare, usage_from
from agent_supra.watch import replay

TOKEN = 'local-fixture-token-not-a-real-key'
BODY = {'model': 'proxy-fixture', 'messages': [{'role': 'user', 'content': 'private-prompt-do-not-persist'}]}
REPLY = {'model': 'proxy-fixture', 'choices': [{'message': {'role': 'assistant', 'content': 'private-completion-do-not-persist'}}],
         'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}


class Bytes(httpx.AsyncByteStream):
    def __init__(self, parts, broken=False):
        self.parts, self.broken, self.closed = parts, broken, False
    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.broken:
            raise httpx.ReadError('private upstream details')
    async def aclose(self):
        self.closed = True


def sse(data):
    return ('data: ' + json.dumps(data) + '\r\n\r\n').encode()


@pytest.fixture
def proxy_factory(account, tmp_path):
    server, key = account
    opened = []
    def make(handler=None, **options):
        requests = []
        def transport(method, path, body, params):
            result = server.request(method, path, json=body, params=params, headers={'Authorization': f'Bearer {key}'})
            return result.status_code, result.json()
        backend = HookClient(api_key=key, transport=transport, retries=0)
        config = Config('proxy-fixture', Price(1, 2), upstream_base='http://127.0.0.1:9000/v1', **options)
        ledger = Ledger(tmp_path / f'ledger-{len(opened)}', 'scope', config)
        async def upstream(request):
            requests.append(request)
            assert request.headers['authorization'] == 'Bearer upstream-fixture-key'
            assert TOKEN not in str(request.headers)
            if handler:
                result = handler(request)
                return await result if hasattr(result, '__await__') else result
            return httpx.Response(200, json=REPLY)
        app = create_app(config, ledger, backend, 'upstream-fixture-key', TOKEN, transport=httpx.MockTransport(upstream))
        client = TestClient(app, base_url='http://127.0.0.1', headers={'Authorization': 'Bearer ' + TOKEN})
        client.__enter__()
        opened.append((client, ledger))
        return client, ledger, backend, requests
    yield make
    for client, ledger in reversed(opened):
        client.__exit__(None, None, None)
        ledger.close()


def test_nonstream_usage_cost_and_privacy(proxy_factory):
    client, ledger, backend, upstream = proxy_factory()
    response = client.post('/v1/chat/completions', json=BODY)
    assert response.status_code == 200 and response.json() == REPLY
    timeline = backend.timeline(ledger.get('run'))
    assert timeline['run']['model_calls'] == 1 and timeline['run']['spend_micros'] == 40
    public = json.dumps(timeline)
    private_state = '\n'.join(ledger.conn.iterdump())
    for secret in ('private-prompt-do-not-persist', 'private-completion-do-not-persist', 'upstream-fixture-key', TOKEN):
        assert secret not in public + private_state
    forwarded = json.loads(upstream[0].content)
    assert forwarded['max_completion_tokens'] == 1024 and forwarded['store'] is False


@pytest.mark.parametrize('limit', ['calls', 'budget'])
def test_new_requests_blocked_at_limit_before_upstream(proxy_factory, limit):
    options = {'max_calls': 1} if limit == 'calls' else {'budget_usd': .00004}
    client, ledger, backend, upstream = proxy_factory(**options)
    assert client.post('/v1/chat/completions', json=BODY).status_code == 200
    assert client.post('/v1/chat/completions', json=BODY).status_code in (403, 429)
    assert len(upstream) == 1 and backend.decide(ledger.get('run'))['state'] == 'paused'


def test_concurrent_requests_cannot_race_past_cap(proxy_factory):
    async def delayed(request):
        await asyncio.sleep(.1)
        return httpx.Response(200, json=REPLY)
    client, ledger, backend, upstream = proxy_factory(delayed, max_calls=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post('/v1/chat/completions', json=BODY).status_code, range(2)))
    assert sorted(results) == [200, 403] and len(upstream) == 1


def test_idempotency_never_reexecutes_or_caches_private_content(proxy_factory):
    client, ledger, backend, upstream = proxy_factory()
    headers = {'Idempotency-Key': 'one-logical-call'}
    assert client.post('/v1/chat/completions', json=BODY, headers=headers).status_code == 200
    assert client.post('/v1/chat/completions', json=BODY, headers=headers).status_code == 409
    assert client.post('/v1/chat/completions', json=dict(BODY, temperature=.5), headers=headers).status_code == 409
    assert len(upstream) == 1


def test_same_prompt_without_request_id_is_a_distinct_call(proxy_factory):
    client, ledger, backend, upstream = proxy_factory()
    for _ in range(2):
        assert client.post('/v1/chat/completions', json=BODY).status_code == 200
    assert len(upstream) == 2 and ledger.get('calls') == 2


def test_stream_fragmentation_and_duplicate_usage_are_counted_once(proxy_factory):
    wire = sse({'choices': [{'delta': {'content': 'hello ☃'}}]}) + sse({'choices': [], 'usage': REPLY['usage']}) * 2 + b'data: [DONE]\n\n'
    parts = [wire[i:i+7] for i in range(0, len(wire), 7)]
    stream = Bytes(parts)
    client, ledger, backend, upstream = proxy_factory(lambda req: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    response = client.post('/v1/chat/completions', json=dict(BODY, stream=True, stream_options={'include_usage': False}))
    expected = sse({'choices': [{'delta': {'content': 'hello ☃'}}]}) + b'data: [DONE]\n\n'
    assert response.status_code == 200 and response.content == expected and stream.closed
    assert json.loads(upstream[0].content)['stream_options']['include_usage'] is True
    assert backend.timeline(ledger.get('run'))['run']['spend_micros'] == 40
    assert ledger.get('calls') == 1


@pytest.mark.parametrize('kind', ['missing_usage', 'broken_stream', 'no_done', 'upstream_error', 'redirect', 'invalid_json'])
def test_uncertain_usage_blocks_every_future_dispatch(proxy_factory, kind):
    def handler(req):
        if kind == 'upstream_error':
            raise httpx.ConnectError('private upstream error')
        if kind == 'redirect':
            return httpx.Response(302, headers={'location': 'https://unrelated.example'})
        if kind == 'invalid_json':
            return httpx.Response(200, content=b'not json')
        if kind in {'broken_stream', 'no_done'}:
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes([sse({'choices': []})], broken=kind == 'broken_stream'))
        return httpx.Response(200, json={'choices': []})
    client, ledger, backend, upstream = proxy_factory(handler)
    streaming = kind in {'broken_stream', 'no_done'}
    client.post('/v1/chat/completions', json=dict(BODY, stream=streaming))
    assert client.post('/v1/chat/completions', json=BODY).status_code == 503
    assert len(upstream) == 1 and ledger.get('uncertain')
    run = backend.timeline(ledger.get('run'))['run']
    assert run['state'] == 'paused' and run['unpriced_calls'] == 1


def test_pending_telemetry_prevents_next_dispatch_then_recovers(proxy_factory):
    client, ledger, backend, upstream = proxy_factory(max_calls=1)
    actual = backend._transport
    fail = [True]
    def outage(method, path, body, params):
        if fail[0] and path.endswith('/events') and body['events'][0]['type'] == 'model.call':
            return 503, {'error': 'temporary outage'}
        return actual(method, path, body, params)
    backend._transport = outage
    assert client.post('/v1/chat/completions', json=BODY).status_code == 200
    assert ledger.pending() == 1
    assert client.post('/v1/chat/completions', json=BODY).status_code == 503
    fail[0] = False
    assert client.post('/v1/chat/completions', json=BODY).status_code == 403
    assert len(upstream) == 1 and ledger.pending() == 0
    assert backend.timeline(ledger.get('run'))['run']['model_calls'] == 1


def test_supervision_outage_never_calls_provider(proxy_factory):
    client, ledger, backend, upstream = proxy_factory()
    backend._transport = lambda *a: (503, {})
    assert client.post('/v1/chat/completions', json=BODY).status_code == 503
    assert len(upstream) == 0


@pytest.mark.parametrize('headers,status', [({'Authorization': 'Bearer wrong'}, 401), ({'Origin': 'https://evil.example'}, 403), ({'Host': 'evil.example'}, 403)])
def test_local_auth_and_browser_boundaries(proxy_factory, headers, status):
    client, ledger, backend, upstream = proxy_factory()
    assert client.post('/v1/chat/completions', json=BODY, headers=headers).status_code == status
    assert not upstream


@pytest.mark.parametrize('change', [{'model': 'other'}, {'max_completion_tokens': 99999}, {'n': 2},
    {'web_search_options': {}}, {'store': True}, {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'secret'}}]}]}])
def test_unsupported_or_unbounded_requests_never_reach_provider(proxy_factory, change):
    client, ledger, backend, upstream = proxy_factory()
    assert client.post('/v1/chat/completions', json=dict(BODY, **change)).status_code == 400
    assert not upstream


def test_incremental_parser_has_bounded_buffers_and_strict_usage():
    parser = SSEUsage()
    with pytest.raises(ValueError):
        parser.feed(b'x' * 1_000_001)
    assert usage_from({'usage': {'prompt_tokens': True, 'completion_tokens': 1}}) is None
    assert usage_from({'usage': {'prompt_tokens': -1, 'completion_tokens': 1}}) is None


def test_missing_usage_remains_unpriced_even_for_known_model(supervisor):
    task = supervisor.create_task('missing usage')
    run = supervisor.create_run(task)
    supervisor.ingest(run, {'event_id': 'missing', 'seq': 1, 'type': 'model.call',
        'data': {'model': 'demo-model', 'input_tokens': 0, 'output_tokens': 0, 'usage_missing': True}})
    timeline = supervisor.timeline(run)
    assert timeline['events'][0]['data']['cost_micros'] is None
    assert timeline['run']['unpriced_calls'] == 1


def test_client_disconnect_records_unknown_usage_and_releases_lock(proxy_factory):
    wire = Bytes([sse({'choices': [{'delta': {'content': 'partial'}}]})])
    client, ledger, backend, upstream = proxy_factory(lambda req: httpx.Response(200,
        headers={'content-type': 'text/event-stream'}, stream=wire))
    async def cancelled_client():
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(dict(BODY, stream=True)).encode(), 'more_body': False}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise asyncio.CancelledError()
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
            'method': 'POST', 'scheme': 'http', 'path': '/v1/chat/completions', 'raw_path': b'/v1/chat/completions',
            'query_string': b'', 'root_path': '', 'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 12345),
            'headers': [(b'host', b'127.0.0.1'), (b'authorization', ('Bearer ' + TOKEN).encode()), (b'content-type', b'application/json')]}
        with pytest.raises(asyncio.CancelledError):
            await client.app(scope, receive, send)
    asyncio.run(cancelled_client())
    assert wire.closed and ledger.get('uncertain') and ledger.get('calls') == 1
    assert client.post('/v1/chat/completions', json=BODY).status_code == 503
    assert len(upstream) == 1


def test_explicit_prices_apply_to_case_sensitive_model_names(tmp_path):
    config = Config('Vendor/Model-X', Price(1, 2))
    ledger = Ledger(tmp_path / 'ledger', 'scope', config)
    ledger.dispatch('id', 'digest')
    ledger.record('id', (20, 10))
    assert ledger.get('spend') == 40
    ledger.close()


def test_dashboard_stop_prevents_next_dispatch(proxy_factory):
    client, ledger, backend, upstream = proxy_factory()
    assert client.post('/v1/chat/completions', json=BODY).status_code == 200
    backend.stop(ledger.get('run'), 'operator stop')
    assert client.post('/v1/chat/completions', json=BODY).status_code == 403
    assert len(upstream) == 1


def test_crashed_dispatch_replays_as_unknown_once_without_provider_call(proxy_factory, tmp_path):
    client, active, backend, upstream = proxy_factory()
    config = Config('proxy-fixture', Price(1, 2))
    path = tmp_path / 'crashed'
    crashed = Ledger(path, 'scope', config)
    crashed.sync(backend)
    run = crashed.get('run')
    crashed.dispatch('interrupted-dispatch', 'digest')
    crashed.close()
    for _ in range(2):
        recovered = Ledger(path, 'scope', config, replay=True)
        assert replay(recovered, backend) == 0
        recovered.close()
    result = backend.timeline(run)['run']
    assert result['model_calls'] == 1 and result['unpriced_calls'] == 1 and result['state'] == 'paused'
    assert not upstream


def test_client_requesting_stream_usage_receives_the_usage_chunk(proxy_factory):
    wire = sse({'choices': [], 'usage': REPLY['usage']}) + b'data: [DONE]\n\n'
    client, ledger, backend, upstream = proxy_factory(lambda req: httpx.Response(200,
        headers={'content-type': 'text/event-stream'}, content=wire))
    result = client.post('/v1/chat/completions', json=dict(BODY, stream=True, stream_options={'include_usage': True}))
    assert result.content == wire
