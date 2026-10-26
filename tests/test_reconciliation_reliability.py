from decimal import Decimal, ROUND_FLOOR

import pytest

from agent_supra import Client
from examples.reconciliation_agent import Ledger, make_case, parse_action, total
from scripts.reconciliation_reliability import ControlledPlanner, FAMILIES, InjectedProviderFailure, fixture_case, summarize, trial


@pytest.fixture
def backend(account):
    server, key = account
    def transport(method, path, body, params):
        r = server.request(method, path, json=body, params=params,
                           headers={'Authorization':'Bearer '+key})
        return r.status_code, r.json()
    return Client(api_key=key, transport=transport, fail_mode='closed', retries=0)


@pytest.mark.parametrize('seed', range(3))
def test_rounding_matches_independent_decimal_reference(seed):
    for invoice in make_case('rounding',seed)['invoices'].values():
        subtotal = sum(Decimal(price)*quantity for price,quantity in invoice['lines'])
        taxable = subtotal-invoice['discount_cents']
        tax = (taxable*Decimal(invoice['tax_bps'])/Decimal(10_000)).quantize(Decimal('1'),rounding=ROUND_FLOOR)
        assert total(invoice) == int(taxable+tax+invoice['shipping_cents'])


def test_only_declared_duplicate_is_removed_and_original_is_preserved(tmp_path):
    ledger = Ledger(tmp_path/'duplicate',case=make_case('duplicates',1))
    original = ledger.read()['INV-A']
    assert 'INV-A-copy:unique_record' in ledger.validate()[1]
    with pytest.raises(ValueError,match='No declared duplicate'):
        ledger.apply({'tool':'remove_duplicate','invoice':'INV-B','amount_cents':0})
    ledger.apply({'tool':'remove_duplicate','invoice':'INV-A','amount_cents':0})
    assert ledger.read()['INV-A'] == original and 'INV-A-copy' not in ledger.read()
    assert 'INV-A:calculated_total' in ledger.validate()[1]
    assert 'INV-A-copy:unique_record' not in ledger.validate()[1]


def test_missing_amount_is_repaired_without_dropping_its_check(tmp_path):
    ledger = Ledger(tmp_path/'missing',case=make_case('missing_amount',2))
    suite,failing = ledger.validate()
    assert 'INV-B:calculated_total' in failing
    ledger.apply({'tool':'set_amount','invoice':'INV-B','amount_cents':ledger.expected['INV-B']})
    after,remaining = ledger.validate()
    assert after == suite and 'INV-B:calculated_total' not in remaining


def test_duplicate_json_fields_are_not_silently_discarded(tmp_path):
    ledger = Ledger(tmp_path/'ambiguous')
    ledger.path.write_text('{"INV-A":1,"INV-A":2,"INV-B":4400,"INV-C":5000}')
    with pytest.raises(ValueError,match='Duplicate JSON field'):
        ledger.read()


@pytest.mark.parametrize('raw', ['{"tool":[],"invoice":"INV-A","amount_cents":1}', '['*1000+'0'+']'*1000])
def test_malformed_proposals_are_normalized_to_rejected_actions(raw):
    with pytest.raises(ValueError):
        parse_action(raw)


@pytest.mark.parametrize('family',FAMILIES)
@pytest.mark.parametrize('seed',range(3))
def test_reliability_known_outcome_matrix(backend,tmp_path,family,seed):
    case = fixture_case(family,seed)
    result = trial(backend,tmp_path/'case',family,seed,ControlledPlanner(family,case),
                   'controlled','self-test-scripted')
    assert result['passed'],result
    assert result['api_latency']['samples'] > 0
    if family == 'partial_progress':
        assert result['state'] == 'completed' and not result['rules']
        assert sum(a['validation_ran'] for a in result['attempts']) == 7
    if family == 'provider_error':
        assert result['state'] == 'failed' and result['error'] == 'InjectedProviderFailure'


def test_summary_excludes_provider_errors_from_claimed_loop_assessments():
    rows=[{'source':'real-gemini','family':'stuck','passed':False,'error':'ClientError','elapsed_ms':1}]
    summary=summarize(rows,'real-gemini')
    assert summary['unassessed_loop_trials_due_to_error'] == 1
    assert summary['unexpected_errors'] == 1 and summary['missed_loop_flags'] == 0


def test_provider_failure_retains_prior_usage_and_marks_unreturned_attempt(backend,tmp_path):
    case=fixture_case('rounding',0)
    planner=ControlledPlanner('rounding',case)
    original=planner.generate_content
    def generate(**kwargs):
        if planner.index:
            raise InjectedProviderFailure('Fixture failure after one success')
        return original(**kwargs)
    planner.generate_content=generate
    result=trial(backend,tmp_path/'case','rounding',0,planner,'controlled','demo-model')
    assert not result['passed'] and result['state']=='failed'
    assert result['model_calls']==1 and result['provider_dispatches']==2
    assert result['spend_micros']>0 and result['unrecorded_provider_attempts']==1
    assert result['input_tokens']==100 and result['output_tokens']==20
    assert result['metadata_flushed']


def test_limit_pauses_are_not_counted_as_loop_false_positives():
    summary=summarize([{'source':'controlled','family':'rounding','state':'paused',
                        'rules':['budget'],'passed':False,'elapsed_ms':1}], 'controlled')
    assert summary['false_pauses']==0 and summary['limit_pauses']==1
