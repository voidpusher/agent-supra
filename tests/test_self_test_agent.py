import pytest

from agent_supra import Client
from scripts.self_test_agent import ScriptedModel, expected, repair_agent, validate_expression


@pytest.mark.parametrize('expression', [
    'price * (100 - pct) / 100', '-price + 100.5', 'return price - pct',
])
def test_repair_agent_accepts_only_its_arithmetic_language(expression):
    assert validate_expression(expression) == expression.removeprefix('return ')


@pytest.mark.parametrize('expression', [
    '__import__("os").getcwd()', 'price.__class__', 'price if pct else 0',
    '[price][0]', '10 ** 1000', 'True', '"price"', 'unknown + 1',
    '1e999', '1000001', 'price; print(1)', 'price' * 100,
])
def test_repair_agent_rejects_code_injection_and_unbounded_expressions(expression):
    with pytest.raises(ValueError):
        validate_expression(expression)


def test_rejected_proposals_still_report_tests_and_can_be_paused(account, tmp_path):
    server, key = account

    def transport(method, path, body, params):
        response = server.request(method, path, json=body, params=params,
                                  headers={'Authorization': f'Bearer {key}'})
        return response.status_code, response.json()

    backend = Client(api_key=key, transport=transport, fail_mode='closed')
    model = ScriptedModel(['__import__("os").getcwd()'])
    result = repair_agent(backend, tmp_path, name='rejected-stuck', provider=model,
                          model='demo-model', max_steps=6)
    assert result['state'] == 'paused'
    assert result['blocked_after_pause'] and result['calls_after_pause'] == 0
    assert result['model_calls'] == model.calls
    assert result['attempts'] and all(a['rejected'] for a in result['attempts'])
    assert expected(result)
