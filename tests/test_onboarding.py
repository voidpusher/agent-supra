import pytest

import agent_supra
from agent_supra import Client, SupervisorAPIError, SupervisorUnavailable
from scripts.onboarding_smoke import first_run_source


@pytest.mark.parametrize('failure', [None, 'auth', 'offline', 'finish', 'verification'])
def test_rendered_first_run_requires_server_confirmation(account, monkeypatch, capsys, failure):
    server, key = account
    source = first_run_source(server.get('/docs/quickstart').text)

    def transport(method, path, body, params):
        if failure == 'offline':
            raise OSError('offline')
        if failure == 'auth':
            return 401, {'error': 'Invalid key'}
        if failure == 'finish' and body and any(e['type'] == 'run.finished' for e in body.get('events', [])):
            return 503, {'error': 'unavailable'}
        if failure == 'verification' and path.endswith('/decision'):
            return 503, {'error': 'unavailable'}
        response = server.request(method, path, json=body, params=params,
                                  headers={'Authorization': 'Bearer ' + key})
        return response.status_code, response.json()

    def client(**kwargs):
        assert kwargs['fail_mode'] == 'closed'
        return Client(api_key=key, base_url='http://testserver', transport=transport,
                      retries=0, **kwargs)

    monkeypatch.setattr(agent_supra, 'Client', client)
    if failure:
        expected = {'auth': SupervisorAPIError, 'offline': SupervisorUnavailable}.get(failure, RuntimeError)
        with pytest.raises(expected):
            exec(compile(source, 'first_run.py', 'exec'), {})
        assert 'Verified completed run:' not in capsys.readouterr().out
    else:
        namespace = {}
        exec(compile(source, 'first_run.py', 'exec'), namespace)
        output = capsys.readouterr().out
        assert 'Verified completed run: run_' in output
        assert key not in output
        run = namespace['run']
        backend = namespace['sup']
        assert backend.decide(run.run_id)['state'] == 'completed'
        assert any(e['type'] == 'tests.reported' for e in backend.timeline(run.run_id)['events'])


def test_missing_first_run_example_fails_clearly():
    with pytest.raises(ValueError, match='missing'):
        first_run_source('<html><pre>unrelated sample</pre></html>')
