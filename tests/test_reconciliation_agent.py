import json

import pytest

from agent_supra import Client
from examples.reconciliation_agent import INVOICES, Ledger, parse_action, reconcile, total
from scripts.reconciliation_test import FixturePlanner, expected


@pytest.mark.parametrize('raw', [
    '{}', '[]', '{"tool":"shell","invoice":"INV-A","amount_cents":1}',
    '{"tool":"set_amount","invoice":"../secret","amount_cents":1}',
    '{"tool":"set_amount","invoice":"INV-A","amount_cents":true}',
    '{"tool":"set_amount","invoice":"INV-A","amount_cents":1.2}',
    '{"tool":"set_amount","invoice":"INV-A","amount_cents":-1}',
    '{"tool":"set_amount","invoice":"INV-A","amount_cents":1000001}',
    '{"tool":"set_amount","invoice":"INV-A","amount_cents":1,"amount_cents":2}',
    'x' * 2049,
])
def test_invalid_model_actions_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_action(raw)


def test_reconciliation_checks_real_ledger_and_preserves_requirements(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    assert len(ledger.validate()[1]) == 3
    assert [total(i) for i in INVOICES.values()] == [2985, 4400, 5000]
    for invoice, amount in ledger.expected.items():
        ledger.apply({'tool': 'set_amount', 'invoice': invoice, 'amount_cents': amount})
    assert not ledger.validate()[1]
    ledger.path.write_text(json.dumps({'INV-A': True, 'INV-B': 4400, 'INV-C': 5000}))
    with pytest.raises(ValueError):
        ledger.validate()
    ledger.requirements.write_text('{}')
    with pytest.raises(RuntimeError, match='requirements changed'):
        ledger.validate()


@pytest.mark.parametrize('scenario', ['healthy', 'stuck', 'observe'])
def test_our_agent_reaches_expected_supervisor_state(account, tmp_path, scenario):
    server, key = account
    def transport(method, path, body, params):
        response = server.request(method, path, json=body, params=params,
                                  headers={'Authorization': 'Bearer ' + key})
        return response.status_code, response.json()
    backend = Client(api_key=key, transport=transport, fail_mode='closed', retries=0)
    result = reconcile(backend, tmp_path / scenario, FixturePlanner(scenario),
                       scenario=scenario, model='self-test-scripted')
    assert expected(result), result
    assert result['provider_dispatches'] == result['model_calls']
    if scenario == 'healthy':
        assert result['model_calls'] == 6
        assert sum(a['validation_ran'] for a in result['attempts']) == 3
        assert sum(a['tool'] == 'calculate_total' for a in result['attempts']) == 3


def test_conflicting_requirements_cannot_be_fixed_by_an_edit(tmp_path):
    ledger = Ledger(tmp_path / 'conflict', conflicting=True)
    for value in (5000, 5001, 4999):
        ledger.apply({'tool': 'set_amount', 'invoice': 'INV-C', 'amount_cents': value})
        assert ledger.validate()[1]


def test_invalid_model_output_cannot_hide_a_stuck_run(account, tmp_path):
    server, key = account
    def transport(method, path, body, params):
        response = server.request(method, path, json=body, params=params,
                                  headers={'Authorization': 'Bearer ' + key})
        return response.status_code, response.json()
    provider = FixturePlanner('stuck')
    original = provider.generate_content
    def invalid(**kwargs):
        reply = original(**kwargs)
        reply.text = '{"tool":"shell"}'
        return reply
    provider.generate_content = invalid
    result = reconcile(Client(api_key=key, transport=transport, fail_mode='closed'),
                       tmp_path / 'invalid', provider, scenario='stuck', model='self-test-scripted')
    assert expected(result)
    assert all(a['rejected'] for a in result['attempts'])
