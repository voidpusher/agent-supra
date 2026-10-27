"""Test an isolated MiroFish LLM client through Supra; never starts its simulation.

Clone MiroFish under ignored .local/mirofish/source and install OpenAI into
.local/mirofish/dependencies. --gemini adds exactly one bounded paid request.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx

from agent_supra.claude_hook import http_client
from agent_supra.pricing import Price, lookup
from agent_supra.proxy import Config, Ledger, create_app
from scripts.proxy_smoke import proxy_server
from scripts.self_test_agent import local_service


# Load the actual utility modules, without starting the Flask app/package facade
# or importing the unrelated Zep/OASIS simulation stack. No MiroFish code is patched.
BOOTSTRAP = '''import sys,json,os,types,logging
from pathlib import Path
p=json.load(sys.stdin)
os.environ['PYTHON_DOTENV_DISABLED']='1'
logging.disable(logging.CRITICAL)
sys.path.insert(0,p['dependencies'])
for name,relative in [('app','backend/app'),('app.utils','backend/app/utils')]:
 m=types.ModuleType(name);m.__path__=[str(Path(p['source'])/relative)];sys.modules[name]=m
'''
CLIENT = BOOTSTRAP + '''
from app.utils.llm_client import LLMClient
c=LLMClient(api_key=p['token'],base_url=p['base']+'/v1',model=p['model'])
c.client=c.client.with_options(max_retries=0,timeout=75)
result={}
try:
 value=c.chat_json(messages=[{'role':'user','content':'Synthetic compatibility test. Return only the JSON object {"ok":true}.'}],temperature=0,max_tokens=64,max_attempts=p['attempts'])
 result={'valid_json':value=={'ok':True},'error':None}
except Exception as e:
 result={'valid_json':False,'error':type(e).__name__,'http_status':getattr(e,'status_code',None)}
finally:c.client.close()
print(json.dumps(result))
'''
UPSTREAM_TESTS = BOOTSTRAP + '''
import pytest
raise SystemExit(pytest.main([str(Path(p['source'])/'backend/tests/test_openai_chat_compat.py'),str(Path(p['source'])/'backend/tests/test_llm_json_responses.py'),'-q','-p','no:cacheprovider','--junitxml='+p['report']]))
'''


def child(code, data):
    # Only the proxy-local token enters the MiroFish process. Provider keys remain
    # in the parent proxy; inherited API keys are removed from this child environment.
    system_names = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP',
                    'USERPROFILE','HOME','APPDATA','LOCALAPPDATA','LANG','LC_ALL'}
    env = {k:v for k,v in os.environ.items() if k.upper() in system_names}
    env['PYTHON_DOTENV_DISABLED'] = '1'
    env['PYTHONPATH'] = ''
    return subprocess.run([sys.executable,'-c',code],input=json.dumps(data),env=env,
                          capture_output=True,text=True,timeout=100)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('.local/mirofish/source'))
    parser.add_argument('--dependencies',type=Path,default=Path('.local/mirofish/dependencies'))
    parser.add_argument('--gemini',action='store_true')
    args = parser.parse_args(argv)
    source, dependencies = args.source.resolve(), args.dependencies.resolve()
    commit = subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    for relative in ['backend/app/utils/llm_client.py','backend/app/utils/openai_chat_compat.py']:
        if not (source/relative).is_file():
            raise ValueError('MiroFish source is missing the tested client modules')
    output = Path('.local/mirofish/results')/uuid4().hex
    output.mkdir(parents=True)
    output = output.resolve()
    common = dict(source=str(source),dependencies=str(dependencies))
    tests = child(UPSTREAM_TESTS,dict(common,report=str(output/'upstream-tests.xml')))
    import xml.etree.ElementTree as ET
    cases = list(ET.parse(output/'upstream-tests.xml').getroot().iter('testcase')) if (output/'upstream-tests.xml').exists() else []
    upstream = {'passed':tests.returncode==0 and bool(cases),'tests':len(cases),
                'failures':sum(c.find('failure') is not None or c.find('error') is not None for c in cases)}
    report = {'kind':'Internal third-party client compatibility; not outside adoption',
              'mirofish_commit':commit,'full_simulation_tested':False,'zep_cloud_used':False,
              'production_supra_used':False,'upstream_unit_tests':upstream,'results':[],
              'temporary_supra_key_revoked':False,'passed':False}
    provider_key = 'synthetic-provider-key'
    if args.gemini:
        from dotenv import dotenv_values
        provider_key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
        if not provider_key:
            raise ValueError('GEMINI_API_KEY is missing; never put it in chat')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    with local_service(output) as (backend,supervisor,workspace,alerts):
        for name in ['json_success','json_retry','retry_blocked_at_cap'] + (['real_gemini'] if args.gemini else []):
            real = name=='real_gemini'
            model = 'gemini-3.5-flash-lite' if real else 'mirofish-fixture'
            config = Config(model,lookup(model) if real else Price(1,2),provider='gemini' if real else 'openai',
                            budget_usd=.02,max_calls=1 if name=='retry_blocked_at_cap' or real else 4,
                            max_output_tokens=128,timeout=60)
            dispatches = []
            def mock(request):
                dispatches.append(1)
                truncated = name in {'json_retry','retry_blocked_at_cap'} and len(dispatches)==1
                return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':model,
                    'choices':[{'index':0,'finish_reason':'length' if truncated else 'stop',
                                'message':{'role':'assistant','content':'{"ok":' if truncated else '{"ok":true}'}}],
                    'usage':{'prompt_tokens':20,'completion_tokens':10}})
            ledger = Ledger(output/name,'mirofish-'+name,config)
            try:
                token = secrets.token_urlsafe(32)
                app = create_app(config,ledger,http_client(backend.api_key,backend.base_url),provider_key if real else 'synthetic-provider-key',token,
                                 transport=None if real else httpx.MockTransport(mock))
                with proxy_server(app) as base:
                    proc = child(CLIENT,dict(common,token=token,base=base,model=model,
                                            attempts=2 if name in {'json_retry','retry_blocked_at_cap'} else 1))
                    result = json.loads(proc.stdout) if proc.returncode==0 else {'error':'ClientProcessFailure'}
                    timeline = backend.timeline(ledger.get('run'))
                    actual = timeline['run']
                    expected_calls = 2 if name=='json_retry' else 1
                    passed = (actual['model_calls']==expected_calls and actual['unpriced_calls']==0 and not ledger.pending()
                              and (result.get('http_status')==403 and actual['state']=='paused' and len(dispatches)==1
                                   if name=='retry_blocked_at_cap' else result.get('valid_json') is True))
                    report['results'].append(dict(scenario=name,passed=bool(passed),client=result,
                        state=actual['state'],run_id=actual['id'],recorded_model_calls=actual['model_calls'],
                        controlled_upstream_dispatches=None if real else len(dispatches),
                        estimated_spend_micros=actual['spend_micros'],usage_missing=actual['unpriced_calls']))
                    print(name,'PASS' if passed else 'FAIL',flush=True)
            finally:
                ledger.close()
    report['temporary_supra_key_revoked'] = True
    report['passed'] = upstream['passed'] and all(r['passed'] for r in report['results'])
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    (output/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>MiroFish compatibility</title><style>body{font:16px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>MiroFish client through Agent Supra</h1><p>Client compatibility and controlled retry/cap checks only. Full graph, Zep, OASIS simulation, UI, report-agent tool loops and loop detection are not verified by this test. Synthetic inputs; internal activity, not outside adoption.</p><pre>'+html.escape(json.dumps(report,indent=2))+'</pre></html>',encoding='utf-8')
    print(json.dumps(report,indent=2))
    print('Evidence:',output/'report.html')
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
