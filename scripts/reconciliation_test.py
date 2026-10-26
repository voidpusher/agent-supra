"""Test our invoice agent against a real isolated Supra API.

python -m scripts.reconciliation_test           # deterministic tool-loop tests
python -m scripts.reconciliation_test --gemini  # actual Gemini, up to 28 calls
Provider charges apply to --gemini; all ledger data is synthetic.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from examples.reconciliation_agent import INVOICES, reconcile, total
from scripts.self_test_agent import local_service


class FixturePlanner:
    """Deterministic control, never represented as a model inference."""
    def __init__(self, scenario):
        self.scenario = scenario
        self.index = 0
        self.models = self
    def generate_content(self, **kwargs):
        if self.scenario == 'healthy':
            invoice = list(INVOICES)[min(self.index // 2, 2)]
            tool = 'calculate_total' if self.index % 2 == 0 else 'set_amount'
            cents = 0 if tool == 'calculate_total' else total(INVOICES[invoice])
        else:
            invoice, cents, tool = 'INV-C', 5001 if self.index % 2 == 0 else 5000, 'set_amount'
        self.index += 1
        return SimpleNamespace(text=json.dumps({'tool': tool, 'invoice': invoice, 'amount_cents': cents}),
            usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=20))


def expected(result):
    common = (result['requirements_unchanged'] and result['provider_calls_after_pause'] == 0
              and result['provider_dispatches'] == result['model_calls'])
    if result['scenario'] == 'healthy':
        return common and result['state'] == 'completed' and result['remaining_failures'] == 0 and not result['rules']
    loop_rules = {'no_progress', 'exact_repeat', 'cyclic_loop'} & set(result['rules'])
    if result['scenario'] == 'stuck':
        return common and result['state'] == 'paused' and result['blocked_after_pause'] and bool(loop_rules)
    return common and result['state'] == 'failed' and result['model_calls'] == 8 and bool(loop_rules)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gemini', action='store_true')
    parser.add_argument('--model', default='gemini-3.5-flash-lite')
    parser.add_argument('--scenario', choices=['all', 'healthy', 'stuck', 'observe'], default='all')
    parser.add_argument('--provider-timeout', type=int, choices=[30,60], default=60)
    args = parser.parse_args(argv)
    folder = Path('.local/reconciliation-test') / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
    folder.mkdir(parents=True)
    provider = None
    paced = None
    if args.gemini:
        from dotenv import dotenv_values
        from google import genai
        key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
        if not key:
            raise SystemExit('GEMINI_API_KEY is missing; keep it in the ignored .env or environment.')
        provider = genai.Client(api_key=key, http_options={
            'timeout': args.provider_timeout*1000, 'retry_options': {'attempts': 1}})
        from scripts.live_reconciliation_test import PacedProvider
        paced = PacedProvider(provider, interval=7)
    scenarios = ('healthy', 'stuck', 'observe') if args.scenario == 'all' else (args.scenario,)
    report = {'kind': 'Internal business-agent test; no outside users', 'scenarios': list(scenarios),
              'provider': 'real Gemini' if args.gemini else 'scripted control (synthetic usage)',
              'model': args.model if args.gemini else 'self-test-scripted',
              'synthetic_invoices_only': True, 'results': [], 'errors': []}
    report['provider_timeout_seconds'] = args.provider_timeout
    report.update(passed=False, temporary_local_key_revoked=False)
    def checkpoint():
        (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    checkpoint()
    try:
        with local_service(folder.resolve()) as (backend, supervisor, workspace, alerts):
            for scenario in scenarios:
                try:
                    result = reconcile(backend, folder / scenario, paced or FixturePlanner(scenario),
                                       scenario=scenario, model=report['model'])
                    result['passed'] = expected(result)
                    timeline = json.dumps(backend.timeline(result['run_id']))
                    result['provider_content_absent_from_timeline'] = all(
                        field not in timeline for field in ('invoice_facts', 'previous_attempts', 'response_json_schema'))
                    result['passed'] = result['passed'] and result['provider_content_absent_from_timeline']
                    report['results'].append(result)
                    checkpoint()
                    print(f"{scenario}: {result['state']}, {result['model_calls']} provider calls, "
                          f"rules={','.join(result['rules']) or 'none'}, "
                          f"{'PASS' if result['passed'] else 'FAIL'}", flush=True)
                except Exception as exc:
                    # Provider exceptions may contain request details. Save only their type.
                    code = getattr(exc, 'code', None)
                    safe_code = code if type(code) is int else None
                    report['errors'].append({'scenario': scenario, 'error': type(exc).__name__, 'code': safe_code})
                    checkpoint()
                    print(f'{scenario}: ERROR ({type(exc).__name__}, code={safe_code})', flush=True)
            supervisor.on_intervention.flush()
            from agent_supra.server.beta import Beta
            metrics = Beta(supervisor.engine, 'operator@self-test.invalid').dashboard()
            report['excluded_from_external_metrics'] = metrics['measured_runs'] == 0 and metrics['outside_users'] == 0
            report['alerts_captured_locally'] = len(alerts)
            report['metadata_flushed'] = backend.flush()
    finally:
        if provider:
            provider.close()
    report['temporary_local_key_revoked'] = True
    report['passed'] = (len(report['results']) == len(scenarios) and not report['errors']
                        and all(r['passed'] for r in report['results'])
                        and report['excluded_from_external_metrics'] and report['metadata_flushed'])
    (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(value))}</td>' for value in (
        r['scenario'], r['state'], r['model_calls'], r['remaining_failures'], ', '.join(r['rules']) or 'none',
        r['provider_calls_after_pause'], 'PASS' if r['passed'] else 'FAIL')) + '</tr>' for r in report['results'])
    (folder / 'report.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Agent Supra invoice agent test</title><style>body{font:16px/1.6 system-ui;max-width:1000px;'
        'margin:48px auto;padding:24px;color:#202124}table{border-collapse:collapse;width:100%}'
        'td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}.table{overflow:auto}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        f'<h1>Invoice reconciliation agent: {"PASS" if report["passed"] else "FAIL"}</h1>'
        f'<p>{html.escape(report["provider"])}. Real HTTP supervision, local ledger edits and immutable validation checks.</p>'
        '<p>The conflicting scenarios deliberately inject persistent behavior. Observation ends at the local '
        'eight-attempt ceiling; that expected failure is not a Supra pause. This is internal validation, not outside adoption.</p>'
        '<div class="table"><table><tr><th>Scenario</th><th>State</th><th>Calls</th><th>Failing</th><th>Rules</th>'
        f'<th>Calls after pause</th><th>Check</th></tr>{rows}</table></div>'
        f'<details><summary>Evidence</summary><pre>{html.escape(json.dumps(report, indent=2))}</pre></details></html>',
        encoding='utf-8')
    print('Evidence:', (folder / 'report.html').resolve())
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
