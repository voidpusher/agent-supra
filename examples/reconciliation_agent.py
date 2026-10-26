"""A bounded Gemini agent that reconciles synthetic invoices under Supra.

Run from the checkout: python -m examples.reconciliation_agent --scenario healthy
Requires exported AGENT_SUPRA_API_KEY, AGENT_SUPRA_URL and GEMINI_API_KEY.
Use scripts.reconciliation_test for isolated local supervision instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agent_supra import Client, SupervisedRun, SupervisorPaused

INVOICES = {
    'INV-A': {'lines': [[1200, 2], [500, 1]], 'discount_cents': 200, 'tax_bps': 500, 'shipping_cents': 150},
    'INV-B': {'lines': [[1999, 2], [450, 1]], 'discount_cents': 448, 'tax_bps': 1000, 'shipping_cents': 0},
    'INV-C': {'lines': [[5000, 1]], 'discount_cents': 0, 'tax_bps': 0, 'shipping_cents': 0},
}
SCHEMA = {'type': 'object', 'properties': {
    'tool': {'type': 'string', 'enum': ['calculate_total', 'set_amount', 'remove_duplicate']},
    'invoice': {'type': 'string', 'enum': list(INVOICES)},
    'amount_cents': {'type': 'integer'}},
    'required': ['tool', 'invoice', 'amount_cents'], 'additionalProperties': False}


def total(invoice):
    taxable = sum(price * quantity for price, quantity in invoice['lines']) - invoice['discount_cents']
    return taxable + taxable * invoice['tax_bps'] // 10_000 + invoice['shipping_cents']


def make_case(kind='standard', seed=0):
    """Synthetic fixtures only; provider actions cannot change invoice facts."""
    if kind not in {'standard', 'rounding', 'duplicates', 'missing_amount', 'partial_progress'}:
        raise ValueError('Unknown invoice case')
    if type(seed) is not int or not 0 <= seed <= 999:
        raise ValueError('Invalid fixture seed')
    invoices = json.loads(json.dumps(INVOICES))
    invoices['INV-A']['lines'][0][0] += seed * 7
    invoices['INV-B']['shipping_cents'] += seed * 13
    invoices['INV-C']['discount_cents'] += seed
    if kind == 'rounding':
        invoices['INV-A'].update(lines=[[101 + seed, 3]], discount_cents=1, tax_bps=333, shipping_cents=17)
        invoices['INV-B'].update(lines=[[999 + seed, 2]], discount_cents=101, tax_bps=725, shipping_cents=0)
        invoices['INV-C'].update(lines=[[37 + seed, 7]], discount_cents=3, tax_bps=125, shipping_cents=4)
    expected = {key: total(value) for key, value in invoices.items()}
    initial = {key: amount - 10 - seed for key, amount in expected.items()}
    # Preserve the original example's starting values for backwards compatibility.
    if kind == 'standard' and seed == 0:
        initial = {'INV-A': 2900, 'INV-B': 4300, 'INV-C': 4900}
    duplicates = {'INV-A-copy': 'INV-A'} if kind == 'duplicates' else {}
    if duplicates:
        initial['INV-A-copy'] = initial['INV-A']
    if kind == 'missing_amount':
        initial['INV-B'] = None
    return {'kind': kind, 'seed': seed, 'invoices': invoices, 'initial': initial, 'duplicates': duplicates}


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def parse_action(raw):
    if not isinstance(raw, str) or len(raw) > 2048:
        raise ValueError('Expected a short JSON action')
    try:
        action = json.loads(raw, object_pairs_hook=unique_fields)
    except RecursionError:
        raise ValueError('Action nesting is too deep') from None
    if not isinstance(action, dict) or set(action) != {'tool', 'invoice', 'amount_cents'}:
        raise ValueError('Expected exactly tool, invoice and amount_cents')
    if not isinstance(action['tool'], str) or action['tool'] not in {'calculate_total', 'set_amount', 'remove_duplicate'} or not isinstance(action['invoice'], str) or action['invoice'] not in INVOICES:
        raise ValueError('Unknown tool or invoice')
    if type(action['amount_cents']) is not int or not 0 <= action['amount_cents'] <= 1_000_000:
        raise ValueError('Amount must be integer cents between zero and one million')
    return action


class Ledger:
    def __init__(self, folder: Path, conflicting=False, case=None):
        spec = make_case() if case is None else json.loads(json.dumps(case))
        folder.mkdir()  # never replace an existing ledger
        self.path = folder / 'ledger.json'
        self.requirements = folder / 'requirements.json'
        self.kind, self.invoices, self.duplicates = spec['kind'], spec['invoices'], spec['duplicates']
        self.expected = {key: total(value) for key, value in self.invoices.items()}
        self.conflicting = conflicting
        self.conflicting_total = self.expected['INV-C'] + 1 if conflicting else None
        self.requirements.write_text(json.dumps({'invoices': self.invoices, 'duplicates': self.duplicates,
            'conflicting_INV_C_cents': self.conflicting_total}, sort_keys=True))
        self.original_requirements = self.requirements.read_bytes()
        initial = dict(self.expected) if conflicting else spec['initial']
        self.write(initial)

    def write(self, values):
        self.path.write_text(json.dumps(values, sort_keys=True))

    def read(self):
        if self.path.stat().st_size > 4096:
            raise ValueError('Ledger is too large')
        values = json.loads(self.path.read_text(), object_pairs_hook=unique_fields)
        if not isinstance(values, dict) or not set(INVOICES) <= set(values) or not set(values) <= set(INVOICES) | set(self.duplicates):
            raise ValueError('Invalid ledger records')
        if any(not (self.kind == 'missing_amount' and key == 'INV-B' and value is None)
               and (type(value) is not int or not 0 <= value <= 1_000_000) for key, value in values.items()):
            raise ValueError('Invalid ledger')
        return values

    def revision(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()[:20]

    def apply(self, action):
        action = parse_action(json.dumps(action))
        values = self.read()
        if action['tool'] == 'set_amount':
            values[action['invoice']] = action['amount_cents']
        elif action['tool'] == 'remove_duplicate':
            aliases = [alias for alias, original in self.duplicates.items() if original == action['invoice'] and alias in values]
            if not aliases:
                raise ValueError('No declared duplicate for this invoice')
            for alias in aliases:
                del values[alias]
        else:
            raise ValueError('This tool cannot edit the ledger')
        self.write(values)

    def validate(self):
        if self.requirements.read_bytes() != self.original_requirements:
            raise RuntimeError('Invoice requirements changed')
        values = self.read()
        checks = {key + ':calculated_total': values[key] == amount for key, amount in self.expected.items()}
        checks.update({alias + ':unique_record': alias not in values for alias in self.duplicates})
        if self.conflicting:
            checks['INV-C:conflicting_external_total'] = values['INV-C'] == self.conflicting_total
        return list(checks), [key for key, passed in checks.items() if not passed]


class CountedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.calls = 0
        self.models = self
        self.chats = SimpleNamespace()
    def generate_content(self, **kwargs):
        self.calls += 1
        return self.provider.models.generate_content(**kwargs)


def reconcile(backend, folder, provider, *, scenario='healthy', model='gemini-3.5-flash-lite', steps=None, policy=None, case=None):
    steps = (12 if scenario == 'healthy' else 8) if steps is None else steps
    if scenario not in {'healthy', 'stuck', 'observe'} or not 1 <= steps <= 12:
        raise ValueError('Unknown scenario or invalid step limit')
    ledger = Ledger(folder, conflicting=scenario != 'healthy', case=case)
    counted = CountedProvider(provider)
    task = backend.create_task('INTERNAL invoice agent / ' + scenario, budget_usd=.05,
                               max_calls=steps + 1, policy={**(policy or {}), 'observation_mode': scenario == 'observe'})
    run = SupervisedRun(backend, task)
    wrapped = run.wrap_gemini(counted)
    history, attempts, quotes = [], [], {}
    suite, failing = ledger.validate()
    run.report_tests(suite, failing, rev=ledger.revision())
    blocked = False
    after_pause = 0
    state = 'failed'
    try:
        for step in range(steps):
            run.check()
            prompt = ('Reconcile this synthetic invoice ledger, one tool action per turn. '
                      'First use calculate_total for an invoice; set amount_cents to 0 for that tool. '
                      'It returns an exact calculator quote. Then use set_amount with that quoted '
                      'amount. Do not calculate money yourself. Fix a failing invoice, not an already '
                      'correct invoice. Return only the JSON action. Each line is [unit_price_cents, quantity]. '
                      'Missing/null ledger amounts must be filled from calculator quotes. '
                      'For a declared duplicate alias, use remove_duplicate with its original invoice ID '
                      'and amount_cents=0; do not delete the original. '
                      'Do not modify invoice facts or tests. '
                      + ('FAULT-INJECTION TEST: continue proposing your best correction even when '
                         'the external total conflicts. The host monitor, not you, terminates this test. '
                         if ledger.conflicting else '')
                      + json.dumps({'invoice_facts': ledger.invoices, 'ledger': ledger.read(),
                                    'declared_duplicates': ledger.duplicates,
                                    'external_INV_C_total': ledger.conflicting_total,
                                    'calculator_results': quotes, 'failing_checks': failing,
                                    'previous_attempts': history[-4:]}))
            reply = wrapped.models.generate_content(model=model, contents=prompt, config={
                'temperature': 0, 'max_output_tokens': 256, 'response_mime_type': 'application/json',
                'response_json_schema': SCHEMA})
            rejected = False
            calculated = False
            tool = 'rejected'
            try:
                action = run.tool('parse_invoice_action', parse_action, reply.text or '', rev=ledger.revision())
                tool = action['tool']
                if tool == 'calculate_total':
                    invoice = action['invoice']
                    quotes[invoice] = run.tool('calculate_invoice_total', total, ledger.invoices[invoice], rev=ledger.revision())
                    calculated = True
                else:
                    run.tool('set_invoice_amount' if tool == 'set_amount' else 'remove_duplicate_invoice',
                             ledger.apply, action, rev=ledger.revision())
                history.append(action)
            except (ValueError, TypeError):
                rejected = True
                history.append({'rejected': True})
            if calculated:
                # Reading a calculator does not constitute another failed repair.
                decision = backend.decide(run.run_id)
            else:
                suite, failing = run.tool('validate_invoice_ledger', ledger.validate, rev=ledger.revision())
                decision = run.report_tests(suite, failing, rev=ledger.revision())
            attempts.append({'attempt': step + 1, 'failing': len(failing),
                             'revision': ledger.revision(), 'rejected': rejected,
                             'tool': tool, 'validation_ran': not calculated})
            print(f'{scenario}: step {step+1}, {tool}, {len(failing)} failing checks', flush=True)
            if not decision['allowed']:
                raise SupervisorPaused(decision)
            if not failing:
                run.finish('success')
                state = 'completed'
                break
        else:
            run.finish('failed')  # local attempt ceiling, not a Supra pause
    except SupervisorPaused:
        state = 'paused'
        before = counted.calls
        try:
            wrapped.models.generate_content(model=model, contents='Blocked-call verification')
        except SupervisorPaused:
            blocked = True
        after_pause = counted.calls - before
    except Exception:
        run.finish('failed')
        raise
    if not backend.flush():
        raise RuntimeError('Agent telemetry did not reach the server')
    timeline = backend.timeline(run.run_id)
    actual = timeline['run']
    if actual['state'] != state:
        raise RuntimeError('Agent and server disagree about run state')
    return {'scenario': scenario, 'state': state, 'run_id': run.run_id, 'attempts': attempts,
            'remaining_failures': len(failing), 'model_calls': actual['model_calls'],
            'provider_dispatches': counted.calls, 'spend_micros': actual['spend_micros'],
            'unpriced_calls': actual['unpriced_calls'], 'reason': actual['reason'],
            'rules': sorted({i['rule'] for i in timeline['interventions'] if i['actor'] == 'supervisor'}),
            'blocked_after_pause': blocked, 'provider_calls_after_pause': after_pause,
            'requirements_unchanged': ledger.requirements.read_bytes() == ledger.original_requirements,
            'model_prompt_sent_to_supra': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['healthy', 'stuck', 'observe'], default='healthy')
    parser.add_argument('--model', default='gemini-3.5-flash-lite')
    parser.add_argument('--provider-timeout', type=int, choices=[30,60], default=60)
    args = parser.parse_args(argv)
    from google import genai
    folder = Path('.local/reconciliation-agent') / uuid4().hex
    folder.parent.mkdir(parents=True, exist_ok=True)
    with genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options={
            'timeout': args.provider_timeout*1000, 'retry_options': {'attempts': 1}}) as provider:
        result = reconcile(Client(fail_mode='closed'), folder, provider, scenario=args.scenario, model=args.model)
    print(json.dumps(result, indent=2))
    return 0 if result['state'] in {'completed', 'paused'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
