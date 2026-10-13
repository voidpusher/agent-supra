import time

from tests.conftest import csrf_from, make_key, signup

STUCK = ["t::b", "t::c"]
SUITE = ["t::a", "t::b", "t::c"]


def api(client, key):
    return {"Authorization": f"Bearer {key}"}


def stuck_run(client, key, policy=None, attempts=4):
    h = api(client, key)
    task = client.post("/v1/tasks", json={"objective": "fix checkout", "budget_usd": 5,
                                          "policy": policy or {"observation_mode": False}}, headers=h).json()["task_id"]
    run = client.post(f"/v1/tasks/{task}/runs", json={}, headers=h).json()["run_id"]
    events = []
    for i in range(attempts):
        events.append({"event_id": f"c{i}", "seq": 2 * i + 1, "type": "model.call",
                       "data": {"model": "gemini-2.5-flash", "input_tokens": 10_000, "output_tokens": 500}})
        events.append({"event_id": f"t{i}", "seq": 2 * i + 2, "type": "tests.reported",
                       "data": {"suite": SUITE, "failing": STUCK, "rev": f"r{i}"}})
    decision = client.post(f"/v1/runs/{run}/events", json={"events": events}, headers=h).json()
    return task, run, decision


# ---------------------------------------------------------------- public pages
def test_public_pages_render(server):
    for path in ("/", "/privacy", "/terms", "/docs/quickstart", "/login", "/signup"):
        r = server.get(path)
        assert r.status_code == 200, path
        assert "Content-Security-Policy" in r.headers
        assert r.headers["X-Frame-Options"] == "DENY"
    assert "Create a free account" in server.get("/").text


def test_healthz(server):
    assert server.get("/healthz").json()["ok"] is True


def test_dashboard_requires_login(server):
    for path in ("/app", "/app/keys", "/app/settings", "/app/runs/run_x"):
        r = server.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


# ---------------------------------------------------------------- accounts
def test_signup_login_logout(server):
    signup(server)
    assert server.get("/app").status_code == 200
    page = server.get("/app")
    server.post("/logout", data={"csrf": csrf_from(page.text)})
    assert server.get("/app", follow_redirects=False).status_code == 303
    login = server.get("/login")
    bad = server.post("/login", data={"email": "sanchit@example.com", "password": "wrong password!",
                                      "csrf": csrf_from(login.text)})
    assert bad.status_code == 401 and "match an account" in bad.text
    ok = server.post("/login", data={"email": "SANCHIT@example.com ", "password": "correct horse battery",
                                     "csrf": csrf_from(login.text)}, follow_redirects=False)
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_signup_validation(server):
    page = server.get("/signup")
    r = server.post("/signup", data={"email": "a@b.co", "password": "short", "csrf": csrf_from(page.text)})
    assert r.status_code == 400 and "at least 10" in r.text
    signup(server)
    server.cookies.clear()
    page = server.get("/signup")
    r = server.post("/signup", data={"email": "sanchit@example.com", "password": "another long pass",
                                     "csrf": csrf_from(page.text)})
    assert r.status_code == 400 and "already exists" in r.text


def test_login_form_needs_csrf_token(server):
    r = server.post("/login", data={"email": "x@y.co", "password": "whatever123"})
    assert r.status_code == 403


def test_dashboard_forms_need_csrf(account):
    server, key = account
    _, run, _ = stuck_run(server, key)
    r = server.post(f"/app/runs/{run}/stop", data={"csrf": "forged", "reason": "x"})
    assert r.status_code == 403
    assert server.get(f"/v1/runs/{run}/decision", headers=api(server, key)).json()["state"] == "paused"


def test_login_rate_limited(supervisor):
    from fastapi.testclient import TestClient

    from agent_supra.server.app import create_app
    from agent_supra.server.settings import Settings
    client = TestClient(create_app(Settings(database_url="sqlite://", auth_attempts_per_minute=3),
                                   supervisor=supervisor))
    page = client.get("/login")
    codes = [client.post("/login", data={"email": "a@b.co", "password": "nope nope nope",
                                         "csrf": csrf_from(page.text)}).status_code for _ in range(5)]
    assert codes[-1] == 429


# ---------------------------------------------------------------- API keys
def test_api_requires_a_valid_key(server):
    assert server.post("/v1/tasks", json={"objective": "x"}).status_code == 401
    assert server.post("/v1/tasks", json={"objective": "x"},
                       headers={"Authorization": "Bearer as_live_nope_nope"}).status_code == 401


def test_key_shown_once_and_revocable(account):
    server, key = account
    page = server.get("/app/keys")
    assert key not in page.text and key[:16] in page.text  # only the prefix is listed
    assert server.get("/v1/whoami", headers=api(server, key)).status_code == 200
    key_id = server.ctx.accounts.list_keys(server.ctx.accounts.authenticate_key(key).workspace_id)[0]["id"]
    server.post(f"/app/keys/{key_id}/revoke", data={"csrf": csrf_from(page.text)})
    assert server.get("/v1/whoami", headers=api(server, key)).status_code == 401


def test_keys_are_stored_hashed(account):
    server, key = account
    from sqlalchemy import select

    from agent_supra.server import db
    with server.ctx.supervisor.engine.connect() as conn:
        stored = [r.key_hash for r in conn.execute(select(db.api_keys)).all()]
    assert key not in stored and len(stored[0]) == 64


# ---------------------------------------------------------------- agent flow over the API
def test_stuck_run_pauses_and_alerts(account):
    server, key = account
    _, run, decision = stuck_run(server, key)
    assert decision["state"] == "paused" and decision["allowed"] is False
    server.ctx.alerter.flush()
    [(ws, item)] = server.alerts
    assert item["rule"] == "no_progress" and item["run_id"] == run
    page = server.get(f"/app/runs/{run}")
    assert "Paused: No progress" in page.text and page.text.count('class="hit"') == 4
    runs_page = server.get("/app")
    assert "1 run waiting for you" in runs_page.text


def test_reviewer_actions_from_dashboard(account):
    server, key = account
    _, run, _ = stuck_run(server, key)
    page = server.get(f"/app/runs/{run}")
    csrf = csrf_from(page.text)
    r = server.post(f"/app/runs/{run}/continue", data={"csrf": csrf, "reason": ""})
    assert r.status_code == 400
    r = server.post(f"/app/runs/{run}/continue", data={"csrf": csrf, "reason": "one more try"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert server.get(f"/v1/runs/{run}/decision", headers=api(server, key)).json()["allowed"]
    assert "Continued by sanchit@example.com" in server.get(f"/app/runs/{run}").text
    server.post(f"/app/runs/{run}/stop", data={"csrf": csrf, "reason": "done"})
    assert server.get(f"/v1/runs/{run}/decision", headers=api(server, key)).json()["state"] == "stopped"


def test_workspaces_are_isolated(server):
    signup(server, "a@example.com")
    key_a = make_key(server)
    _, run_a, _ = stuck_run(server, key_a)
    server.cookies.clear()
    signup(server, "b@example.com")
    key_b = make_key(server)
    h = api(server, key_b)
    assert server.get(f"/v1/runs/{run_a}/decision", headers=h).status_code == 404
    assert server.get(f"/v1/runs/{run_a}/timeline", headers=h).status_code == 404
    assert server.post(f"/v1/runs/{run_a}/stop", json={"reason": "x"}, headers=h).status_code == 404
    assert server.post(f"/v1/runs/{run_a}/events", headers=h, json={"events": [
        {"event_id": "x", "seq": 99, "type": "progress.updated", "data": {}}]}).status_code == 404
    assert server.get(f"/app/runs/{run_a}").status_code == 404     # B's session can't see A's run
    assert run_a not in server.get("/app").text


def test_workspace_settings_apply_to_new_tasks(account):
    server, key = account
    page = server.get("/app/settings")
    r = server.post("/app/settings", data={
        "csrf": csrf_from(page.text), "name": "Acme agents", "threshold": "3",
        "rule_no_progress": "pause", "rule_exact_repeat": "pause", "rule_cyclic_loop": "observe",
        "rule_context_runaway": "observe", "rule_budget": "pause", "rule_call_cap": "pause",
        "prices": "my-model 2 8\n", "slack_webhook": "", "alert_email": ""}, follow_redirects=False)
    assert r.status_code == 303
    # observation_mode unchecked -> pausing on; threshold 3 -> paused after 3 attempts
    _, _, decision = stuck_run(server, key, policy={}, attempts=3)
    assert decision["state"] == "paused"
    h = api(server, key)
    task = server.post("/v1/tasks", json={"objective": "priced"}, headers=h).json()["task_id"]
    run = server.post(f"/v1/tasks/{task}/runs", json={}, headers=h).json()["run_id"]
    server.post(f"/v1/runs/{run}/events", headers=h, json={"events": [{"event_id": "m", "seq": 1,
                "type": "model.call", "data": {"model": "my-model", "input_tokens": 1_000_000, "output_tokens": 0}}]})
    assert server.get(f"/v1/runs/{run}/timeline", headers=h).json()["run_spend_micros"] == 2_000_000


def test_settings_reject_bad_input(account):
    server, _ = account
    page = server.get("/app/settings")
    base = {"csrf": csrf_from(page.text), "name": "x", "threshold": "4"}
    assert server.post("/app/settings", data={**base, "slack_webhook": "http://169.254.169.254/"}).status_code == 400
    assert server.post("/app/settings", data={**base, "prices": "bad line"}).status_code == 400
    assert server.post("/app/settings", data={**base, "threshold": "1"}).status_code == 400


def test_test_alert(account):
    server, _ = account
    page = server.get("/app/settings")
    r = server.post("/app/settings/test-alert", data={"csrf": csrf_from(page.text)}, follow_redirects=False)
    assert "alert-none" in r.headers["location"]
    server.post("/app/settings", data={"csrf": csrf_from(page.text), "name": "w", "threshold": "4",
                                       "slack_webhook": "https://hooks.slack.com/services/T/B/x"})
    server.post("/app/settings/test-alert", data={"csrf": csrf_from(page.text)})
    server.ctx.alerter.flush()
    assert len(server.alerts) == 1


def test_flags_alert_only_when_enabled(account):
    server, key = account
    stuck_run(server, key, policy={"observation_mode": True})
    server.ctx.alerter.flush()
    assert server.alerts == []


def test_change_password_logs_out_everywhere(account):
    server, _ = account
    page = server.get("/app/settings")
    r = server.post("/app/settings/password", data={"csrf": csrf_from(page.text), "current": "wrong one!!",
                                                    "new": "brand new password"})
    assert r.status_code == 400
    server.post("/app/settings/password", data={"csrf": csrf_from(page.text), "current": "correct horse battery",
                                                "new": "brand new password"})
    assert server.get("/app", follow_redirects=False).status_code == 303
    login = server.get("/login")
    ok = server.post("/login", data={"email": "sanchit@example.com", "password": "brand new password",
                                     "csrf": csrf_from(login.text)}, follow_redirects=False)
    assert ok.status_code == 303


def test_delete_account_removes_everything(account):
    server, key = account
    _, run, _ = stuck_run(server, key)
    page = server.get("/app/settings")
    ws_name = server.ctx.accounts.workspace(server.ctx.accounts.authenticate_key(key).workspace_id)["name"]
    assert server.post("/app/settings/delete", data={"csrf": csrf_from(page.text), "confirm": "nope"}).status_code == 400
    server.post("/app/settings/delete", data={"csrf": csrf_from(page.text), "confirm": ws_name})
    assert server.get("/v1/whoami", headers=api(server, key)).status_code == 401
    from sqlalchemy import func, select

    from agent_supra.server import db
    with server.ctx.supervisor.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.events)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(db.users)).scalar_one() == 0


# ---------------------------------------------------------------- abuse protection
def test_body_size_and_event_validation(account):
    server, key = account
    h = api(server, key)
    r = server.post("/v1/tasks", content=b"x" * 2_000_000, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 413
    task = server.post("/v1/tasks", json={"objective": "x"}, headers=h).json()["task_id"]
    run = server.post(f"/v1/tasks/{task}/runs", json={}, headers=h).json()["run_id"]
    bad = server.post(f"/v1/runs/{run}/events", headers=h, json={"events": [
        {"event_id": "e", "seq": 1, "type": "evil.type", "data": {}}]})
    assert bad.status_code == 400 and "unknown event type" in bad.json()["error"]
    huge = server.post(f"/v1/runs/{run}/events", headers=h, json={"events": [
        {"event_id": "e", "seq": 1, "type": "tests.reported", "data": {"suite": ["t"] * 6000, "failing": []}}]})
    assert huge.status_code == 400
    assert server.post("/v1/tasks", json={"objective": ""}, headers=h).status_code == 422


def test_event_rate_limit(supervisor):
    from fastapi.testclient import TestClient

    from agent_supra.server.app import create_app
    from agent_supra.server.settings import Settings
    client = TestClient(create_app(Settings(database_url="sqlite://", events_per_minute=8,
                                            auth_attempts_per_minute=100), supervisor=supervisor))
    signup(client)
    key = make_key(client)
    codes = [client.get("/v1/whoami", headers=api(client, key)).status_code for _ in range(3)]
    assert codes == [200, 200, 200]   # whoami is not rate limited
    task = client.post("/v1/tasks", json={"objective": "x"}, headers=api(client, key))
    statuses = [client.post("/v1/tasks", json={"objective": "x"}, headers=api(client, key)).status_code
                for _ in range(5)]
    assert task.status_code == 200 and 429 in statuses


def test_prune_removes_old_runs(supervisor):
    task = supervisor.create_task("old")
    run = supervisor.create_run(task)
    supervisor.ingest(run, {"event_id": "e", "seq": 1, "type": "progress.updated", "data": {}})
    assert supervisor.prune(older_than_days=30) == 0
    from sqlalchemy import update

    from agent_supra.server import db
    with supervisor.engine.begin() as conn:
        conn.execute(update(db.runs).values(created_at=time.time() - 40 * 86400))
        conn.execute(update(db.tasks).values(created_at=time.time() - 40 * 86400))
    assert supervisor.prune(older_than_days=30) == 1
    assert supervisor.runs() == []
