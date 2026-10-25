import time

from sqlalchemy import func, select, update

from agent_supra.server import db
from agent_supra.server.beta import Beta
from tests.conftest import csrf_from, make_key, signup
from tests.test_app import api, stuck_run


def beta(server):
    return Beta(server.ctx.supervisor.engine, server.ctx.settings.contact_email)


def operator_login(server):
    uid, ws = server.ctx.accounts.signup(server.ctx.settings.contact_email, 'operator test password')
    server.cookies.set('as_session', server.ctx.accounts.create_session(uid))
    return ws


def test_beta_is_operator_only_and_cohorts_need_csrf(account):
    server, key = account
    assert server.get('/app/beta').status_code == 403
    assert server.post('/app/beta/cohort', data={'kind': 'external'}).status_code == 403
    operator_ws = operator_login(server)
    page = server.get('/app/beta')
    assert page.status_code == 200
    assert 'Gathering evidence' in page.text
    assert 'Not measured' in page.text
    assert server.post('/app/beta/cohort', data={'workspace_id': operator_ws, 'kind': 'external'}).status_code == 403
    assert server.post('/app/beta/cohort', data={'workspace_id': operator_ws, 'kind': 'external',
        'csrf': csrf_from(page.text)}).status_code == 400
    server.cookies.clear()
    assert server.get('/app/beta', follow_redirects=False).headers['location'] == '/login'


def test_activation_receipts_ignore_client_timestamps_and_retries(account):
    server, key = account
    h = api(server, key)
    task = server.post('/v1/tasks', headers=h, json={'objective': 'beta receipt test'}).json()['task_id']
    run = server.post(f'/v1/tasks/{task}/runs', headers=h, json={}).json()['run_id']
    ws = server.ctx.accounts.authenticate_key(key).workspace_id
    beta(server).classify(ws, 'external')
    assert beta(server).dashboard()['measured_runs'] == 0  # Empty runs do not activate.
    payload = {'events': [{'event_id': 'm1', 'seq': 1, 'type': 'model.call', 'ts': 1,
        'data': {'model': 'test', 'input_tokens': 1, 'output_tokens': 1}}]}
    assert server.post(f'/v1/runs/{run}/events', headers=h, json=payload).status_code == 200
    with server.ctx.supervisor.engine.connect() as conn:
        first = conn.execute(select(db.run_activity)).one()
    assert abs(first.first_received_at - time.time()) < 10
    assert server.post(f'/v1/runs/{run}/events', headers=h, json=payload).status_code == 200
    with server.ctx.supervisor.engine.connect() as conn:
        again = conn.execute(select(db.run_activity)).one()
    assert first == again
    measured = beta(server).dashboard()
    assert measured['weekly_active_teams'] == 1 and measured['weekly_runs'] == 1
    assert measured['funnel'] == {'signups': 1, 'keys': 1, 'activated': 1, 'flagged': 0}
    assert measured['activation_sample'] == 1
    assert measured['activation_minutes'] >= 0
    assert measured['outside_users'] == 1
    # Rolling activity follows server receipts, and historical signup latency stays unknown.
    with server.ctx.supervisor.engine.begin() as conn:
        conn.execute(update(db.run_activity).values(last_received_at=time.time() - 8*86400))
        conn.execute(update(db.workspaces).where(db.workspaces.c.id == ws).values(created_at=1))
    measured = beta(server).dashboard()
    assert measured['weekly_active_teams'] == 0 and measured['weekly_runs'] == 0
    assert measured['measured_runs'] == 1 and measured['activation_minutes'] is None


def test_internal_and_unreviewed_workspaces_never_count(account):
    server, key = account
    _, run, _ = stuck_run(server, key)
    ws = server.ctx.accounts.authenticate_key(key).workspace_id
    assert beta(server).dashboard()['measured_runs'] == 0
    operator_ws = operator_login(server)
    # Even a manually altered cohort cannot count the operator as an outside user.
    with server.ctx.supervisor.engine.begin() as conn:
        conn.execute(db.beta_cohorts.insert().values(workspace_id=operator_ws, kind='external', updated_at=time.time()))
    b = beta(server)
    b.classify(ws, 'external')
    assert b.dashboard()['funnel']['signups'] == 1
    assert b.dashboard()['measured_runs'] == 1
    b.classify(ws, 'internal')
    assert b.dashboard()['measured_runs'] == 0
    b.classify(ws, 'unreviewed')
    assert b.dashboard()['measured_runs'] == 0
    page = server.get('/app/beta')
    response = server.post('/app/beta/cohort', data={'csrf': csrf_from(page.text),
        'workspace_id': ws, 'kind': 'external'})
    assert response.status_code == 200 and 'Beta classification saved.' in response.text


def test_feedback_revisions_and_metrics(account):
    server, key = account
    _, run, _ = stuck_run(server, key, policy={'observation_mode': True})
    ws = server.ctx.accounts.authenticate_key(key).workspace_id
    b = beta(server)
    b.classify(ws, 'external')
    finding = server.ctx.supervisor.interventions(run, workspace_id=ws)[0]['id']
    page = server.get(f'/app/runs/{run}')
    assert 'Was this flag useful?' in page.text
    form = {'csrf': csrf_from(page.text), 'intervention_id': finding, 'verdict': 'useful'}
    assert server.post(f'/app/runs/{run}/feedback', data={**form, 'csrf': 'bad'}).status_code == 403
    assert server.post(f'/app/runs/{run}/feedback', data=form).status_code == 200
    assert server.post(f'/app/runs/{run}/feedback', data=form).status_code == 200
    metrics = b.dashboard()
    assert metrics['reviewed'] == 1 and metrics['useful_percent'] == 100
    assert metrics['counts']['useful'] == 1
    assert not metrics['gate_met']
    assert server.post(f'/app/runs/{run}/feedback', data={**form, 'verdict': 'false_alarm'}).status_code == 200
    assert b.dashboard()['useful_percent'] == 0
    assert b.run_feedback(ws, run)[finding] == 'false_alarm'
    assert server.post(f'/app/runs/{run}/feedback', data={**form, 'verdict': 'nonsense'}).status_code == 400
    missed = {'csrf': form['csrf'], 'verdict': 'missed'}
    assert server.post(f'/app/runs/{run}/feedback', data=missed).status_code == 200
    assert server.post(f'/app/runs/{run}/feedback', data=missed).status_code == 200
    assert b.dashboard()['counts']['missed'] == 1
    assert b.dashboard()['reviewed'] == 1  # Miss reports do not dilute flag accuracy.
    # A caller cannot apply a flag verdict without identifying the flag.
    assert server.post(f'/app/runs/{run}/feedback', data={'csrf': form['csrf'], 'verdict': 'useful'}).status_code == 400


def test_feedback_tenant_isolation_and_wrong_flag(account):
    server, key = account
    ws = server.ctx.accounts.authenticate_key(key).workspace_id
    _, run, _ = stuck_run(server, key)
    _, other_run, _ = stuck_run(server, key)
    finding = server.ctx.supervisor.interventions(run, workspace_id=ws)[0]['id']
    form = {'csrf': csrf_from(server.get('/app/keys').text), 'intervention_id': finding, 'verdict': 'useful'}
    assert server.post(f'/app/runs/{other_run}/feedback', data=form).status_code == 404
    server.cookies.clear()
    signup(server, email='different@example.com')
    form['csrf'] = csrf_from(server.get('/app/keys').text)
    assert server.post(f'/app/runs/{run}/feedback', data=form).status_code == 404
    assert server.get(f'/app/runs/{run}').status_code == 404
    assert server.get('/app/beta').status_code == 403
    with server.ctx.supervisor.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.flag_feedback)).scalar_one() == 0


def test_feedback_and_receipts_follow_retention_and_deletion(account):
    server, key = account
    ws = server.ctx.accounts.authenticate_key(key).workspace_id
    _, run, _ = stuck_run(server, key)
    b = beta(server)
    b.classify(ws, 'external')
    uid = server.ctx.accounts.session(server.cookies.get('as_session')).user_id
    finding = server.ctx.supervisor.interventions(run, workspace_id=ws)[0]['id']
    b.feedback(ws, uid, run, finding, 'useful')
    with server.ctx.supervisor.engine.begin() as conn:
        conn.execute(update(db.runs).where(db.runs.c.id == run).values(created_at=time.time() - 40*86400))
    server.ctx.supervisor.prune(30)
    assert b.dashboard()['measured_runs'] == 0
    assert b.dashboard()['reviewed'] == 0
    server.ctx.accounts.delete_workspace(ws, uid)
    with server.ctx.supervisor.engine.connect() as conn:
        for table in (db.run_activity, db.flag_feedback, db.beta_cohorts):
            assert conn.execute(select(func.count()).select_from(table)).scalar_one() == 0


def test_phase_zero_gate_requires_real_activation_and_useful_review(server):
    b = beta(server)
    for i in range(5):
        server.cookies.clear()
        signup(server, email=f'outside{i}@example.com')
        key = make_key(server)
        ws = server.ctx.accounts.authenticate_key(key).workspace_id
        b.classify(ws, 'external')
        for j in range(10):
            _, run, _ = stuck_run(server, key, policy={'observation_mode': True})
        if i == 0:
            uid = server.ctx.accounts.session(server.cookies.get('as_session')).user_id
            finding = server.ctx.supervisor.interventions(run, workspace_id=ws)[0]['id']
            b.feedback(ws, uid, run, finding, 'too_early')
    metrics = b.dashboard()
    assert metrics['outside_users'] == 5 and metrics['measured_runs'] == 50
    assert metrics['weekly_active_teams'] == 5 and not metrics['gate_met']
    b.feedback(server.ctx.accounts.session(server.cookies.get('as_session')).workspace_id,
               server.ctx.accounts.session(server.cookies.get('as_session')).user_id,
               run, server.ctx.supervisor.interventions(run, workspace_id=ws)[0]['id'], 'useful')
    assert b.dashboard()['gate_met']
