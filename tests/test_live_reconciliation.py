import pytest

from agent_supra import Client, SupervisorAPIError
from scripts.live_reconciliation_test import PacedProvider, control_checks, limit_checks, production_client, recovery_checks


@pytest.fixture
def backend(account):
    server, key = account
    def transport(method, path, body, params):
        response = server.request(method, path, json=body, params=params,
                                  headers={'Authorization': 'Bearer ' + key})
        return response.status_code, response.json()
    return Client(api_key=key, transport=transport, fail_mode='closed', retries=0)


@pytest.mark.parametrize('url', ['http://agentsupra.in', 'https://agentsupra.in.evil.invalid',
    'https://agentsupra.in/?key=private', 'https://user:pass@agentsupra.in', 'http://localhost:8000', None])
def test_live_harness_rejects_unintended_targets_before_network(url):
    with pytest.raises(ValueError):
        production_client('as_live_fixture_not_a_real_key', url)


def test_live_limit_regression_enforces_even_in_observation(backend):
    results = limit_checks(backend)
    assert len(results) == 2 and all(r['passed'] for r in results)


def test_live_recovery_regression_deduplicates_lost_ack_and_blocks_closed(backend):
    assert recovery_checks(backend)['passed']


def test_live_control_regression_preserves_running_agent_boundary(backend):
    def action(name, run_id):
        if name == 'continue':
            backend.continue_run(run_id, 'Independent control regression')
        else:
            backend.stop(run_id, 'Independent control regression')
    result = control_checks(backend, external_action=action)
    assert result['passed'] and not result['actions_via_dashboard']


def test_production_transport_never_forwards_key_to_redirect(monkeypatch):
    import httpx
    original = httpx.Client
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={'Location': 'https://untrusted.invalid/collect'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs:
        original(transport=httpx.MockTransport(handler), **kwargs))
    backend, session = production_client('as_live_fixture_not_a_real_key', 'https://agentsupra.in')
    try:
        with pytest.raises(SupervisorAPIError) as error:
            backend.whoami()
        assert error.value.status == 400
        assert len(requests) == 1 and requests[0].url.host == 'agentsupra.in'
    finally:
        session.close()


def test_provider_pacing_and_errors_do_not_add_hidden_retries(monkeypatch):
    from types import SimpleNamespace
    import scripts.live_reconciliation_test as module
    now = [0.0]
    sleeps, calls = [], []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise RuntimeError('Fixture provider error')
        return 'fixture reply'
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(module.time, 'sleep', sleep)
    provider = PacedProvider(SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert provider.models.generate_content() == 'fixture reply'
    assert provider.models.generate_content() == 'fixture reply'
    with pytest.raises(RuntimeError):
        provider.models.generate_content()
    assert sleeps == [5.0, 5.0] and len(calls) == 3
