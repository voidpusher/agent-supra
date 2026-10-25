"""Run a constrained repair agent through a real, isolated Agent Supra HTTP server.

    python -m scripts.self_test_agent             # repeatable scripted fault tests
    python -m scripts.self_test_agent --gemini    # also run real Gemini repair tasks

Requires the project's dev dependencies. Gemini uses GEMINI_API_KEY from the
environment or the ignored .env. No production credentials are needed. Reports
and temporary toy repositories stay under the ignored .local/self-test directory.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import logging
import os
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from agent_supra import Client, SupervisedRun, SupervisorPaused
from agent_supra.demo import BUG, HEALTHY_PATCHES, SOURCE, STUCK_PATCHES, TESTS, edit_file, rev, run_tests
from agent_supra.server.accounts import Accounts
from agent_supra.server.app import create_app
from agent_supra.server.beta import Beta
from agent_supra.server.core import Supervisor
from agent_supra.server.settings import Settings

IMPOSSIBLE = '\n\ndef test_conflicting_requirement():\n    assert apply_discount(100, 10) == 91\n'
PROMPT = '''Fix this Python function by returning one arithmetic expression.
def apply_discount(price, pct):
    return {expression}
Tests (must not be edited):
{tests}
Failing test IDs: {failing}
Earlier proposals: {history}
Return ONLY an expression using price, pct, numeric constants, +, -, *, /,
and parentheses. No calls, attributes, imports, explanations or code fences.
Even if the requirements conflict, propose your best expression.'''


def validate_expression(proposal: str) -> str:
    """Accept only bounded arithmetic; model output cannot introduce Python code."""
    expression = proposal.strip().removeprefix('return ').strip()
    if not expression or len(expression) > 200:
        raise ValueError('proposal must be a short arithmetic expression')
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError as exc:
        raise ValueError('proposal is not an expression') from exc
    nodes = list(ast.walk(tree))
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult,
               ast.Div, ast.UAdd, ast.USub, ast.Name, ast.Load, ast.Constant)
    if len(nodes) > 40 or any(not isinstance(node, allowed) for node in nodes):
        raise ValueError('proposal contains unsupported operations')
    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in {'price', 'pct'}:
            raise ValueError('proposal contains an unknown variable')
        if isinstance(node, ast.Constant) and (type(node.value) not in (int, float)
                                               or not abs(node.value) <= 1_000_000):
            raise ValueError('proposal contains an unsupported constant')
    return expression


class ScriptedModel:
    """Deliberate faulty proposals and synthetic usage, never billed model calls."""
    def __init__(self, patches):
        self.patches = patches
        self.calls = 0
        self.models = self
        self.chats = SimpleNamespace()

    def generate_content(self, **_):
        proposal = self.patches[min(self.calls, len(self.patches) - 1)]
        self.calls += 1
        return SimpleNamespace(text=proposal, usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=20, thoughts_token_count=0))


@contextmanager
def local_service(folder: Path):
    """Exercise the real HTTP client, authentication, API, detector and database."""
    sup = Supervisor(f'sqlite:///{(folder / "supervisor.db").as_posix()}')
    accounts = Accounts(sup.engine)
    _, ws = accounts.signup('agent@self-test.invalid', secrets.token_urlsafe(24), 'Internal self-test')
    key_id, api_key = accounts.create_key(ws, 'Temporary internal self-test')
    accounts.update_workspace(ws, prices={'self-test-scripted': [1, 1]})
    Beta(sup.engine, 'operator@self-test.invalid').classify(ws, 'internal')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    settings = Settings(database_url=f'sqlite:///{(folder / "supervisor.db").as_posix()}',
                        base_url=f'http://127.0.0.1:{port}', contact_email='operator@self-test.invalid',
                        events_per_minute=100_000, retention_days=0)
    alerts = []
    app = create_app(settings, supervisor=sup, alert_sender=lambda ws, item: alerts.append(item))
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('google_genai.models').setLevel(logging.WARNING)
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('isolated supervisor did not start')
            time.sleep(.05)
        client = Client(api_key=api_key, base_url=settings.base_url, fail_mode='closed', retries=0)
        yield client, sup, ws, alerts
    finally:
        accounts.revoke_key(ws, key_id)
        server.should_exit = True
        worker.join(timeout=15)
        listener.close()
        if worker.is_alive():
            raise RuntimeError('isolated supervisor did not stop')
        sup.engine.dispose()


def repair_agent(backend, folder: Path, *, name: str, provider, model: str,
                 impossible=False, observe=False, budget=.05, max_steps=8) -> dict:
    repo = folder / name
    repo.mkdir()
    (repo / 'shop.py').write_text(SOURCE.format(expr=BUG))
    (repo / 'test_shop.py').write_text(TESTS + (IMPOSSIBLE if impossible else ''))
    task = backend.create_task(f'INTERNAL SELF-TEST / {name}', budget_usd=budget,
                               max_calls=max_steps, policy={'observation_mode': observe})
    attempts, history, expression, blocked = [], [], BUG, False
    with SupervisedRun(backend, task) as run:
        wrapped = run.wrap_gemini(provider)
        result = run.tool('run_tests', run_tests, repo, rev=rev(repo))
        run.report_tests(result.suite, result.failing, rev=rev(repo))
        try:
            for step in range(1, max_steps + 1):
                run.check()
                reply = wrapped.models.generate_content(model=model, contents=PROMPT.format(
                    expression=expression, tests=(repo / 'test_shop.py').read_text(),
                    failing=result.failing, history=history[-6:]),
                    config={'temperature': 0, 'max_output_tokens': 512})
                rejected = False
                try:
                    proposal = run.tool('validate_proposal', validate_expression,
                                        reply.text or '', rev=rev(repo))
                except ValueError:
                    history.append('Rejected: unsupported expression')
                    rejected = True
                else:
                    expression = proposal
                    history.append(expression)
                    run.tool('edit_file', edit_file, repo, expression, rev=rev(repo))
                # A rejected proposal is still an attempt without progress. Keep
                # reporting the real test result rather than hiding that attempt.
                result = run.tool('run_tests', run_tests, repo, rev=rev(repo))
                decision = run.report_tests(result.suite, result.failing, rev=rev(repo))
                attempts.append({'attempt': step, 'failing': len(result.failing),
                                 'expression': expression, 'rejected': rejected})
                if not decision.get('allowed', False):
                    raise SupervisorPaused(decision)
                if not result.failing:
                    run.finish('success')
                    break
            else:
                run.finish('failed')
        except SupervisorPaused:
            # Prove the wrapper refuses the very next call before reaching the model.
            before = getattr(provider, 'calls', None)
            try:
                wrapped.models.generate_content(model=model, contents='This call must be blocked.')
            except SupervisorPaused:
                blocked = before is None or provider.calls == before
    timeline = backend.timeline(run.run_id)
    findings = [i for i in timeline['interventions'] if i['actor'] == 'supervisor']
    calls = [e for e in timeline['events'] if e['type'] == 'model.call']
    result = {'scenario': name, 'source': 'scripted fault fixture' if isinstance(provider, ScriptedModel)
              else 'real Gemini', 'model': model, 'run_id': run.run_id, 'state': timeline['run']['state'],
              'reason': timeline['run']['reason'], 'model_calls': len(calls),
              'estimated_usd': timeline['run']['spend_micros'] / 1_000_000,
              'blocked_after_pause': blocked, 'attempts': attempts,
              'rules': [i['rule'] for i in findings], 'event_count': len(timeline['events'])}
    # No model.call can be received after the first pause intervention.
    pauses = [i for i in findings if i['action'] == 'pause']
    result['calls_after_pause'] = sum(e['ts'] > min(i['created_at'] for i in pauses) for e in calls) if pauses else 0
    return result


def expected(result: dict) -> bool:
    name = result['scenario']
    if name.endswith('healthy'):
        return result['state'] == 'completed' and not result['rules'] and bool(result['attempts']) and result['model_calls'] > 0
    if name == 'scripted-observe':
        return result['state'] == 'failed' and bool(result['rules']) and not result['blocked_after_pause'] and result['model_calls'] == 6
    correct_reason = 'budget' in result['rules'] if name == 'scripted-budget' else any(
        rule in result['rules'] for rule in ('no_progress', 'exact_repeat', 'cyclic_loop'))
    return result['state'] == 'paused' and correct_reason and result['blocked_after_pause'] and result['calls_after_pause'] == 0


def write_reports(folder: Path, results: list[dict], errors: list[dict], checks: dict) -> None:
    report = {'internal_tests_only': True, 'results': results, 'errors': errors, 'checks': checks,
              'passed': not errors and all(r['passed'] for r in results) and all(checks.values())}
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in (
        r['scenario'], r['source'], r['state'] + (' (step limit)' if r['scenario'] == 'scripted-observe' else ''),
        r['model_calls'], ', '.join(r['rules']) or 'none',
        r['calls_after_pause'], 'PASS' if r['passed'] else 'FAIL')) + '</tr>' for r in results)
    (folder / 'report.html').write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Agent Supra internal agent test</title>
<style>body{{background:#101011;color:#eeeae5;font:15px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:24px}}h1{{font-size:34px}}th,td{{padding:14px;text-align:left;border-bottom:1px solid #353030}}table{{border-collapse:collapse;width:100%;font-size:13px}}.table{{overflow:auto}}code{{color:#ff8b7b}}p{{color:#aaa5a2}}</style>
<h1>Agent Supra · Internal agent test</h1><p>Real HTTP supervision, real file edits and pytest results.
Scripted fixtures use synthetic token usage. Gemini rows use real provider calls.
These runs do not represent outside users. No credentials appear in this report.
The observation scenario intentionally reaches its step limit; PASS means the expected behavior occurred.</p>
<h2>{'PASS' if report['passed'] else 'FAIL'}</h2><div class="table"><table><thead><tr><th>Scenario</th><th>Source</th><th>State</th><th>Calls</th><th>Rules</th><th>Calls after pause</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Checks</h2><pre>{html.escape(json.dumps(checks, indent=2))}</pre>
<h2>Errors</h2><pre>{html.escape(json.dumps(errors, indent=2))}</pre>
<p>Details, attempts and run IDs: <code>report.json</code>. Database: <code>supervisor.db</code>.
The temporary HTTP server has stopped and its API key has been revoked.</p></html>''', encoding='utf-8')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gemini', action='store_true', help='also run real Gemini tasks; provider charges may apply')
    parser.add_argument('--model', default=os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite'))
    parser.add_argument('--output', type=Path, default=Path('.local/self-test'))
    args = parser.parse_args(argv)
    folder = args.output.resolve() / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
    folder.mkdir(parents=True)
    results, errors, checks = [], [], {}
    with local_service(folder) as (backend, sup, ws, alerts):
        scenarios = [dict(name='scripted-healthy', provider=ScriptedModel(HEALTHY_PATCHES)),
                     dict(name='scripted-stuck', provider=ScriptedModel(STUCK_PATCHES)),
                     dict(name='scripted-observe', provider=ScriptedModel(STUCK_PATCHES), observe=True, max_steps=6),
                     dict(name='scripted-budget', provider=ScriptedModel(STUCK_PATCHES), budget=.00005)]
        gemini = None
        if args.gemini:
            from dotenv import dotenv_values
            from google import genai
            api_key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
            if not api_key:
                errors.append({'scenario': 'Gemini setup', 'error': 'GEMINI_API_KEY is missing'})
            else:
                gemini = genai.Client(api_key=api_key, http_options={'timeout': 30_000})
                scenarios.extend([dict(name='gemini-healthy', provider=gemini, model=args.model, max_steps=6),
                                  dict(name='gemini-stuck', provider=gemini, model=args.model,
                                       impossible=True, max_steps=6)])
        try:
            for scenario in scenarios:
                scenario.setdefault('model', 'self-test-scripted')
                try:
                    result = repair_agent(backend, folder, **scenario)
                    result['passed'] = expected(result)
                    results.append(result)
                    print(f"{result['scenario']}: {result['state']} | {result['model_calls']} calls | "
                          f"{','.join(result['rules']) or 'no flags'} | {'PASS' if result['passed'] else 'FAIL'}", flush=True)
                except Exception as exc:
                    # Provider errors can include request details; never print their raw message.
                    errors.append({'scenario': scenario['name'], 'error': type(exc).__name__})
                    print(f"{scenario['name']}: ERROR ({type(exc).__name__})", flush=True)
            sup.on_intervention.flush()
            metrics = Beta(sup.engine, 'operator@self-test.invalid').dashboard()
            checks = {'all_runs_internal': metrics['measured_runs'] == 0 and metrics['outside_users'] == 0,
                      'alerts_recorded_locally': bool(alerts),
                      'no_buffered_events': backend.flush(),
                      'no_model_calls_after_pause': all(r['calls_after_pause'] == 0 for r in results)}
        finally:
            if gemini:
                gemini.close()
    write_reports(folder, results, errors, checks)
    print(f'Report: {folder / "report.html"}')
    return 0 if not errors and all(r['passed'] for r in results) and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
