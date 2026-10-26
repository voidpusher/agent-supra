"""Opt-in production assessment. Never loads a Supra key from the project's .env.

Set AGENT_SUPRA_API_KEY and AGENT_SUPRA_URL explicitly, or put the dedicated test
key in ignored .local/live-reconciliation.env. Run with --run-live. The Gemini
key can come from the existing ignored .env. Production runs and test alerts are
intentional; no global outage or workspace-default change is performed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from agent_supra import Client, SupervisedRun, SupervisorPaused
from agent_supra.detector import DEFAULT_POLICY


def production_client(key, url):
    if url not in {'https://agentsupra.in', 'https://agent-supra.vercel.app'}:
        raise ValueError('Expected an explicit, approved production URL without paths or query parameters')
    if not key or not key.startswith('as_live_'):
        raise ValueError('A dedicated production API key is required')
    import httpx
    session = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
    def transport(method, path, body, params):
        try:
            response = session.request(method, url + path, json=body, params=params,
                headers={'Authorization': 'Bearer ' + key})
        except httpx.HTTPError:
            raise OSError('Production transport unavailable') from None
        if 300 <= response.status_code < 400:
            return 400, {'error': 'Redirect refused'}
        return response.status_code, response.json()
    return Client(api_key=key, base_url=url, fail_mode='closed', retries=0, transport=transport), session


def is_blocked(fn):
    try:
        fn()
    except SupervisorPaused:
        return True
    return False


def limit_checks(backend):
    results = []
    for name, budget, cap, cost, rule in [('budget', .000001, None, 1, 'budget'),
                                         ('call-cap', None, 1, 0, 'call_cap')]:
        task = backend.create_task('INTERNAL LIVE TEST / synthetic ' + name,
            budget_usd=budget, max_calls=cap, policy={'observation_mode': True})
        run = SupervisedRun(backend, task)
        # Deliberate synthetic accounting fixture, not an actual provider request.
        run.record_model_call('internal-synthetic-limit-fixture', 0, 0, cost_micros=cost)
        blocked = is_blocked(lambda: run.check(before_model_call=True))
        timeline = backend.timeline(run.run_id)
        assert blocked and timeline['run']['state'] == 'paused'
        assert rule in {i['rule'] for i in timeline['interventions']}
        results.append({'check': name, 'run_id': run.run_id, 'passed': True,
                        'synthetic_accounting': True, 'actual_provider_calls': 0})
    return results


class FaultGate:
    def __init__(self, transport):
        self.transport = transport
        self.offline = False
        self.lose_ack = False
    def __call__(self, method, path, body, params):
        if self.offline:
            raise OSError('Injected client-only outage')
        result = self.transport(method, path, body, params)
        if self.lose_ack and method == 'POST' and path.endswith('/events'):
            self.lose_ack = False
            raise OSError('Injected lost acknowledgement after server acceptance')
        return result


class PacedProvider:
    """Space successful/failed dispatches across scenarios, without hidden retries."""
    def __init__(self, provider, interval=5.0):
        self.provider, self.interval = provider, interval
        self.last_dispatch = None
        self.models = self
    def generate_content(self, **kwargs):
        if self.last_dispatch is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_dispatch)))
        self.last_dispatch = time.monotonic()
        return self.provider.models.generate_content(**kwargs)


def recovery_checks(backend):
    gate = FaultGate(backend._transport)
    def client(mode):
        value = Client(api_key=backend.api_key, base_url=backend.base_url, fail_mode=mode,
                       retries=0, transport=gate)
        value.cooldown = 0
        return value
    opened = client('open')
    task = opened.create_task('INTERNAL LIVE TEST / client outage recovery', policy={'observation_mode': True})
    run = SupervisedRun(opened, task)
    gate.offline = True
    decision = run.report_tests(['connection-probe'], [], rev='offline-1')
    assert decision.get('degraded') and decision['allowed']
    gate.offline = False
    assert opened.flush()
    gate.lose_ack = True
    decision = run.report_tests(['connection-probe'], [], rev='lost-ack-2')
    assert decision.get('degraded')
    assert opened.flush() and opened.flush()
    events = backend.timeline(run.run_id)['events']
    assert sum(e['type'] == 'tests.reported' for e in events) == 2
    assert len({e['event_id'] for e in events}) == len(events)
    run.finish('success')
    assert backend.decide(run.run_id)['state'] == 'completed'
    closed = client('closed')
    task2 = closed.create_task('INTERNAL LIVE TEST / fail-closed outage', policy={'observation_mode': True})
    run2 = SupervisedRun(closed, task2)
    executed = []
    gate.offline = True
    blocked = is_blocked(lambda: run2.tool('must_not_execute', lambda: executed.append(True)))
    gate.offline = False
    assert blocked and not executed
    run2.finish('unknown')
    assert closed.flush()
    return {'passed': True, 'run_ids': [run.run_id, run2.run_id], 'client_only_fault_injection': True,
            'buffered_events_recovered': True, 'lost_ack_retry_not_duplicated': True,
            'fail_closed_prevents_tool': True, 'production_shutdown': False}


def control_checks(backend, external_action=None, ready=None, wait_seconds=180):
    task = backend.create_task('INTERNAL LIVE TEST / dashboard controls', policy={'observation_mode': True})
    run = SupervisedRun(backend, task)
    executed = []
    backend.pause(run.run_id, 'Internal control-boundary test')
    assert is_blocked(lambda: run.tool('blocked_before_continue', lambda: executed.append('unexpected')))
    for action, state in [('continue', 'running'), ('stop', 'stopped')]:
        if ready:
            ready({'run_id': run.run_id, 'awaiting_dashboard_action': action})
        print('DASHBOARD_ACTION', action, backend.base_url + '/app/runs/' + run.run_id, flush=True)
        if external_action:
            external_action(action, run.run_id)
        deadline = time.monotonic() + wait_seconds
        while backend.decide(run.run_id)['state'] != state:
            if time.monotonic() > deadline:
                backend.stop(run.run_id, 'Dashboard test timed out; stopping only this test run')
                raise TimeoutError('Dashboard action was not observed')
            time.sleep(2)
        if action == 'continue':
            run.tool('continued_agent_tool', lambda: executed.append('continued'))
    assert executed == ['continued']
    assert is_blocked(lambda: run.tool('blocked_after_stop', lambda: executed.append('unexpected')))
    assert is_blocked(lambda: run.check(before_model_call=True))
    interventions = backend.timeline(run.run_id)['interventions']
    assert {'pause', 'continue', 'stop'} <= {i['action'] for i in interventions}
    return {'passed': True, 'run_id': run.run_id, 'continued_tool_executed_once': True,
            'stopped_tool_and_model_boundary_blocked': True,
            'actions_via_dashboard': external_action is None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.add_argument('--dashboard-controls', action='store_true')
    parser.add_argument('--model', default='gemini-3.5-flash-lite')
    parser.add_argument('--scenario', choices=['all', 'healthy', 'stuck', 'observe'], default='all')
    parser.add_argument('--skip-agent', action='store_true', help='only control, limit and recovery checks; no Gemini calls')
    parser.add_argument('--agent-only', action='store_true', help='run only selected agent scenarios; no limit/control fixture alerts')
    args = parser.parse_args(argv)
    if args.skip_agent and args.agent_only:
        parser.error('--skip-agent and --agent-only cannot be combined')
    from dotenv import dotenv_values
    dedicated = dotenv_values('.local/live-reconciliation.env')
    key = os.environ.get('AGENT_SUPRA_API_KEY') or dedicated.get('AGENT_SUPRA_API_KEY')
    url = os.environ.get('AGENT_SUPRA_URL') or dedicated.get('AGENT_SUPRA_URL')
    backend, session = production_client(key, url)
    # Authenticate before creating tasks or initializing the provider.
    backend.whoami()
    scenarios = [] if args.skip_agent else (['healthy', 'stuck', 'observe'] if args.scenario == 'all' else [args.scenario])
    folder = Path('.local/live-reconciliation') / uuid4().hex
    folder.mkdir(parents=True)
    report = {'kind': 'Internal production assessment', 'url': url, 'model': args.model,
              'results': [], 'errors': [], 'passed': False, 'synthetic_invoices_only': True,
              'agent_scenarios_required': scenarios,
              'limit_recovery_control_checks_required': not args.agent_only,
              'alert_receipt': 'not independently verified'}
    def checkpoint(control=None):
        if control:
            report['control_status'] = control
        (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    checkpoint()
    print('Live evidence:', (folder / 'report.json').resolve(), flush=True)
    try:
        from examples.reconciliation_agent import reconcile
        from scripts.reconciliation_test import expected
        if scenarios:
            from google import genai
            gemini_key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
            if not gemini_key:
                raise ValueError('GEMINI_API_KEY is missing')
            with genai.Client(api_key=gemini_key, http_options={'timeout': 30_000, 'retry_options': {'attempts': 1}}) as provider:
                paced = PacedProvider(provider)
                for scenario in scenarios:
                    result = reconcile(backend, folder / scenario, paced, scenario=scenario, model=args.model,
                        policy={'threshold': 4, 'rules': DEFAULT_POLICY['rules']})
                    result['passed'] = expected(result)
                    report['results'].append(result)
                    checkpoint()
                    print(scenario, result['state'], result['model_calls'], 'calls', result['passed'], flush=True)
        if not args.agent_only:
            report['limits'] = limit_checks(backend)
            checkpoint()
            report['recovery'] = recovery_checks(backend)
            checkpoint()
            if args.dashboard_controls:
                report['controls'] = control_checks(backend, ready=checkpoint)
            else:
                report['controls'] = control_checks(backend, external_action=lambda action, run_id:
                    backend.continue_run(run_id, 'Internal API control test') if action == 'continue'
                    else backend.stop(run_id, 'Internal API control test'))
            report['control_status'] = {'run_id': report['controls']['run_id'], 'completed': True}
        report['passed'] = (len(report['results']) == len(scenarios) and all(r['passed'] for r in report['results'])
                            and (args.agent_only or (report['recovery']['passed'] and report['controls']['passed'])))
    except Exception as exc:
        report['errors'].append({'error': type(exc).__name__,
            'code': getattr(exc, 'code', None) if type(getattr(exc, 'code', None)) is int else None})
        print('Assessment error:', type(exc).__name__, flush=True)
    finally:
        checkpoint()
        session.close()
    print('Assessment passed:', report['passed'], flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
