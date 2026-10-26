"""24 known-outcome HTTP trials, optionally followed by six real Gemini trials.

python -m scripts.reconciliation_reliability --gemini
Uses only an isolated local Supra service, synthetic invoices, and the existing
ignored Gemini key. Reports distinguish controlled fixtures from real inference.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agent_supra import Client
from agent_supra.server.beta import Beta
from examples.reconciliation_agent import make_case, reconcile, total
from scripts.live_reconciliation_test import PacedProvider
from scripts.self_test_agent import local_service

FAMILIES = ('rounding', 'duplicates', 'missing_amount', 'partial_progress',
            'stuck', 'observe', 'invalid_output', 'provider_error')
HEALTHY = {'rounding', 'duplicates', 'missing_amount', 'partial_progress'}
LOOPS = {'stuck', 'observe', 'invalid_output'}
LOOP_RULES = {'no_progress', 'exact_repeat', 'cyclic_loop'}
REAL_CASES = [('rounding',0),('rounding',2),('duplicates',1),('missing_amount',2),('stuck',0),('observe',1)]


class InjectedProviderFailure(RuntimeError):
    pass


class AttemptedProvider:
    def __init__(self, provider):
        self.provider, self.calls, self.models = provider, 0, self
    def generate_content(self, **kwargs):
        self.calls += 1
        return self.provider.models.generate_content(**kwargs)


def fixture_case(family, seed):
    return make_case(family if family in HEALTHY else 'standard', seed)


class ControlledPlanner:
    def __init__(self, family, case):
        self.family, self.case, self.index = family, case, 0
        self.models = self
        self.expected = {key: total(value) for key, value in case['invoices'].items()}
        self.plans = []
        def action(tool, invoice, cents=0):
            return {'tool': tool, 'invoice': invoice, 'amount_cents': cents}
        if family == 'partial_progress':
            a, b = case['initial']['INV-A'], case['initial']['INV-B']
            self.plans = [action('set_amount', 'INV-A', a), action('set_amount', 'INV-A', a+1),
                action('set_amount', 'INV-A', self.expected['INV-A']), action('calculate_total', 'INV-B'),
                action('set_amount', 'INV-B', b+1), action('set_amount', 'INV-B', b+2),
                action('set_amount', 'INV-B', self.expected['INV-B']), action('calculate_total', 'INV-C'),
                action('set_amount', 'INV-C', self.expected['INV-C'])]
        elif family in HEALTHY:
            if family == 'duplicates':
                self.plans.append(action('remove_duplicate', 'INV-A'))
            for invoice, cents in self.expected.items():
                self.plans.extend([action('calculate_total', invoice), action('set_amount', invoice, cents)])
    def generate_content(self, **kwargs):
        self.index += 1
        if self.family == 'provider_error':
            raise InjectedProviderFailure('Deliberately injected provider failure')
        if self.family == 'invalid_output':
            raw = 'not JSON' if self.case['seed'] == 0 else json.dumps(
                {'tool': ['invalid'] if self.case['seed'] == 1 else 'set_amount',
                 'invoice': 'INV-A', 'amount_cents': True})
        elif self.family in {'stuck', 'observe'}:
            raw = json.dumps({'tool': 'set_amount', 'invoice': 'INV-C',
                              'amount_cents': self.expected['INV-C'] + self.index % 2})
        else:
            raw = json.dumps(self.plans[min(self.index-1, len(self.plans)-1)])
        return SimpleNamespace(text=raw, usage_metadata=SimpleNamespace(prompt_token_count=100,
                                                                        candidates_token_count=20))


def latency(values):
    if not values:
        return {'samples': 0, 'p50_ms': None, 'p95_ms': None}
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': round(statistics.median(values), 2),
            'p95_ms': round(ordered[math.ceil(.95 * len(values))-1], 2)}


def trial(backend, folder, family, seed, provider, source, model):
    samples, run_ids, timelines = [], [], []
    def transport(method, path, body, params):
        start = time.perf_counter()
        try:
            response = backend._transport(method, path, body, params)
            if method == 'POST' and path.endswith('/runs') and response[0] < 300:
                run_ids.append(response[1]['run_id'])
            if method == 'GET' and path.endswith('/timeline') and response[0] < 300:
                timelines.append(response[1])
            return response
        finally:
            samples.append((time.perf_counter()-start)*1000)
    measured = Client(api_key=backend.api_key, base_url=backend.base_url, transport=transport,
                      fail_mode='closed', retries=0)
    start = time.perf_counter()
    row = {'family': family, 'seed': seed, 'source': source, 'passed': False,
           'expected': 'completed' if family in HEALTHY else 'flag-only' if family == 'observe'
                       else 'provider-error' if family == 'provider_error' else 'paused'}
    attempted = AttemptedProvider(provider)
    try:
        result = reconcile(measured, folder, attempted, scenario='healthy' if family in HEALTHY
                           else 'observe' if family == 'observe' else 'stuck', model=model,
                           case=fixture_case(family, seed))
        row.update(result)
        loop = bool(LOOP_RULES & set(result['rules']))
        common = (result['requirements_unchanged'] and result['provider_calls_after_pause'] == 0
                  and result['provider_dispatches'] == result['model_calls'])
        if family in HEALTHY:
            row['passed'] = common and result['state'] == 'completed' and not result['remaining_failures'] and not result['rules']
        elif family == 'observe':
            row['passed'] = common and loop and result['state'] == 'failed' and result['model_calls'] == 8
        else:
            row['passed'] = common and loop and result['state'] == 'paused' and result['blocked_after_pause']
    except Exception as exc:
        row['error'] = type(exc).__name__
        row['provider_code'] = getattr(exc, 'code', None) if type(getattr(exc, 'code', None)) is int else None
        if run_ids:
            snapshot = backend.timeline(run_ids[-1])
            timelines.append(snapshot)
            actual = snapshot['run']
            row.update(run_id=run_ids[-1], state=actual['state'], model_calls=actual['model_calls'],
                       spend_micros=actual['spend_micros'], unpriced_calls=actual['unpriced_calls'])
        row['passed'] = (source == 'controlled' and family == 'provider_error'
                         and isinstance(exc, InjectedProviderFailure) and row.get('state') == 'failed')
    row['provider_dispatches'] = attempted.calls
    if timelines:
        usage = [e['data'] for e in timelines[-1]['events'] if e['type']=='model.call']
        row['input_tokens'] = sum(u['input_tokens'] for u in usage)
        row['output_tokens'] = sum(u['output_tokens'] for u in usage)
    row['unrecorded_provider_attempts'] = max(0, attempted.calls-row.get('model_calls',0))
    row['metadata_flushed'] = measured.flush()
    row['passed'] = row['passed'] and row['metadata_flushed']
    row['elapsed_ms'] = round((time.perf_counter()-start)*1000, 2)
    row['api_latency'] = latency(samples)
    return row


def summarize(rows, source):
    subset = [r for r in rows if r['source'] == source]
    completed = [r for r in subset if r['family'] in HEALTHY]
    loops = [r for r in subset if r['family'] in LOOPS]
    return {'trials': len(subset), 'passed': sum(r['passed'] for r in subset),
            'healthy_trials': len(completed), 'loop_trials': len(loops),
            'healthy_trials_completed': sum(r.get('state')=='completed' for r in completed),
            'healthy_trials_with_errors': sum('error' in r for r in completed),
            'usage_basis': 'synthetic tokens; no paid provider requests' if source == 'controlled' else 'provider-reported usage; estimates, not invoices',
            'false_pauses': sum(r.get('state') == 'paused' and bool(LOOP_RULES & set(r.get('rules',[]))) for r in completed),
            'limit_pauses': sum(bool({'budget','call_cap'} & set(r.get('rules',[]))) for r in subset),
            'missed_loop_flags': sum(not (LOOP_RULES & set(r.get('rules', []))) for r in loops if 'error' not in r),
            'unassessed_loop_trials_due_to_error': sum('error' in r for r in loops),
            'unexpected_errors': sum('error' in r and not r['passed'] for r in subset),
            'injected_provider_errors': sum(r['family'] == 'provider_error' and 'error' in r for r in subset),
            'calls_after_pause': sum(r.get('provider_calls_after_pause', 0) for r in subset),
            'unrecorded_provider_attempts': sum(r.get('unrecorded_provider_attempts',0) for r in subset),
            'input_tokens_reported': sum(r.get('input_tokens',0) for r in subset),
            'output_tokens_reported': sum(r.get('output_tokens',0) for r in subset),
            'trial_wall_time': latency([r['elapsed_ms'] for r in subset]),
            'estimated_spend_micros': sum(r.get('spend_micros', 0) for r in subset)}


def write_report(folder, report):
    (folder/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
        r['source'], r['family'], r['seed'], r.get('state', 'error'), r.get('model_calls', '—'),
        ','.join(r.get('rules', [])) or 'none', r.get('error', ''), r['passed']))+'</tr>' for r in report['trials'])
    (folder/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width"><title>Invoice-agent reliability</title>'
        '<style>body{font:15px/1.6 system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#222}'
        'table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #ddd;padding:10px}'
        '.table{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        '<h1>Our invoice agent: reliability assessment</h1><p><strong>Status: '+html.escape(report.get('status','incomplete'))+'</strong></p><p>Internal synthetic invoices; real local HTTP supervision. '
        'Controlled planners and real Gemini results are separate. Known-outcome controls do not measure real-world accuracy.</p>'
        '<p>Observation ends at the agent step ceiling; the injected provider-error cases pass only when the run fails honestly. '
        'Wall time includes configured pacing. These samples are not production latency measurements.</p>'
        '<h2>Summary</h2><pre>'+html.escape(json.dumps(report['summary'],indent=2))+'</pre>'
        '<div class="table"><table><tr><th>Source</th><th>Case</th><th>Seed</th><th>State</th><th>Calls</th>'
        '<th>Rules</th><th>Error</th><th>Passed</th></tr>'+rows+'</table></div></html>',encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gemini', action='store_true')
    parser.add_argument('--model', default='gemini-3.5-flash-lite')
    parser.add_argument('--gemini-only', action='store_true', help='skip the controlled matrix for an explicit provider repeat')
    parser.add_argument('--gemini-case', action='append', choices=[f'{f}:{s}' for f,s in REAL_CASES],
                        help='repeat selected original provider cases; may be repeated')
    parser.add_argument('--provider-timeout', type=int, choices=[30,60], default=60,
                        help='request deadline in seconds; retries remain disabled')
    args = parser.parse_args(argv)
    if (args.gemini_only or args.gemini_case) and not args.gemini:
        parser.error('Provider-only options require --gemini')
    real_cases = REAL_CASES
    if args.gemini_case:
        if len(set(args.gemini_case)) != len(args.gemini_case):
            parser.error('Select each provider case at most once')
        real_cases = [(item.split(':')[0],int(item.split(':')[1])) for item in args.gemini_case]
    folder = Path('.local/reconciliation-reliability')/uuid4().hex
    folder.mkdir(parents=True)
    report = {'kind': 'Internal reliability benchmark', 'trials': [], 'summary': {}, 'passed': False,
              'gemini_model': args.model, 'gemini_dispatch_spacing_seconds': 7,
              'outside_adoption': False, 'temporary_local_key_revoked': False, 'status': 'running'}
    report['provider_timeout_seconds'] = args.provider_timeout
    report['requested_controlled_trials'] = 0 if args.gemini_only else 24
    report['requested_real_trials'] = len(real_cases) if args.gemini else 0
    def checkpoint():
        report['summary'] = {s:summarize(report['trials'],s) for s in ('controlled','real-gemini')}
        write_report(folder,report)
    checkpoint()
    print('Evidence:', (folder/'report.html').resolve(), flush=True)
    with local_service(folder.resolve()) as (backend, supervisor, workspace, alerts):
        for family in (() if args.gemini_only else FAMILIES):
            for seed in range(3):
                case = fixture_case(family, seed)
                row = trial(backend, folder/f'{family}-{seed}', family, seed,
                            ControlledPlanner(family, case), 'controlled', 'self-test-scripted')
                report['trials'].append(row)
                checkpoint()
                print('controlled',family,seed,'PASS' if row['passed'] else 'FAIL',flush=True)
        if args.gemini:
            from dotenv import dotenv_values
            from google import genai
            key = os.environ.get('GEMINI_API_KEY') or dotenv_values('.env').get('GEMINI_API_KEY')
            if not key:
                raise ValueError('GEMINI_API_KEY is missing')
            with genai.Client(api_key=key,http_options={'timeout':args.provider_timeout*1000,'retry_options':{'attempts':1}}) as raw:
                provider = PacedProvider(raw,interval=7)
                for family,seed in real_cases:
                    row = trial(backend,folder/f'gemini-{family}-{seed}',family,seed,provider,'real-gemini',args.model)
                    report['trials'].append(row)
                    checkpoint()
                    print('real-gemini',family,seed,'PASS' if row['passed'] else 'FAIL',flush=True)
                    if row.get('provider_code') == 429:
                        report['provider_quota_blocked_remaining_trials'] = True
                        break  # no retry burst or invented results after quota rejection
        metrics = Beta(supervisor.engine,'operator@self-test.invalid').dashboard()
        report['excluded_from_external_metrics'] = metrics['measured_runs'] == 0 and metrics['outside_users'] == 0
        report['metadata_flushed'] = backend.flush()
    report['temporary_local_key_revoked'] = True
    required = report['requested_controlled_trials'] + report['requested_real_trials']
    report['unattempted_real_trials'] = report['requested_real_trials'] - sum(r['source']=='real-gemini' for r in report['trials'])
    report['passed'] = (len(report['trials']) == required and all(r['passed'] for r in report['trials'])
                        and report['excluded_from_external_metrics'] and report['metadata_flushed'])
    report['status'] = 'passed' if report['passed'] else 'failed'
    checkpoint()
    print(json.dumps(report['summary'],indent=2),flush=True)
    print('Overall passed:',report['passed'],flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
