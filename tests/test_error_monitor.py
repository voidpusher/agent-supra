"""The operator inbox captures handled 500s without storing request secrets."""

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from agent_supra.server import db
from agent_supra.server.app import create_app
from agent_supra.server.core import Supervisor
from agent_supra.server.settings import Settings
from tests.conftest import csrf_from, signup


def test_server_error_inbox_groups_and_redacts(monkeypatch):
    settings = Settings(database_url="sqlite://", base_url="http://testserver",
                        contact_email="operator@example.com", smtp_host="smtp.example")
    app = create_app(settings, supervisor=Supervisor("sqlite://"))

    @app.get("/explode/{token}")
    def explode(token: str):
        raise RuntimeError(f"private value: {token}")

    delivered = []
    monkeypatch.setattr("agent_supra.server.errors.send_email",
                        lambda *args: delivered.append(("email", args)))
    monkeypatch.setattr("agent_supra.server.errors.send_slack",
                        lambda *args: delivered.append(("slack", args)))
    operator = TestClient(app, raise_server_exceptions=False)
    # The operator address is reserved from public signup and provisioned by CLI.
    app.state.ctx.accounts.signup("operator@example.com", "correct horse battery")
    login = operator.get("/login")
    assert operator.post("/login", data={"email": "operator@example.com",
                                         "password": "correct horse battery",
                                         "csrf": csrf_from(login.text)},
                         follow_redirects=False).status_code == 303
    session = app.state.ctx.accounts.session(operator.cookies.get("as_session"))
    app.state.ctx.accounts.update_workspace(
        session.workspace_id, slack_webhook="https://hooks.slack.com/services/T/B/x")

    for token in ("secret-one", "secret-two"):
        response = operator.get(f"/explode/{token}")
        assert response.status_code == 500
        assert response.json() == {"error": "internal error"}

    inbox = operator.get("/app/errors")
    assert inbox.status_code == 200
    assert "RuntimeError" in inbox.text
    assert "/explode/{token}" in inbox.text
    assert ">2</td>" in inbox.text
    assert "secret-one" not in inbox.text and "secret-two" not in inbox.text
    assert len(delivered) == 2  # first occurrence only, through both channels
    assert all("secret-" not in str(args) for _, args in delivered)

    other = TestClient(app, raise_server_exceptions=False)
    signup(other, "other@example.com")
    assert other.get("/app/errors").status_code == 403
    assert 'href="/app/errors"' not in other.get("/app").text


def test_operator_email_cannot_be_claimed_by_public_signup():
    app = create_app(Settings(database_url="sqlite://", contact_email="operator@example.com"),
                     supervisor=Supervisor("sqlite://"))
    client = TestClient(app)
    page = client.get("/signup")
    response = client.post("/signup", data={"email": "OPERATOR@example.com",
                                            "password": "correct horse battery",
                                            "csrf": csrf_from(page.text)})
    assert response.status_code == 403


def test_error_monitor_failure_does_not_mask_original_error(monkeypatch):
    app = create_app(Settings(database_url="sqlite://"), supervisor=Supervisor("sqlite://"))

    @app.get("/explode")
    def explode():
        raise ValueError("private failure")

    def unavailable(*args):
        raise OSError("database unavailable")

    monkeypatch.setattr(app.state.ctx.monitor, "record", unavailable)
    response = TestClient(app, raise_server_exceptions=False).get("/explode")
    assert response.status_code == 500
    assert response.json() == {"error": "internal error"}


def test_failed_error_alert_retries_on_a_later_occurrence(monkeypatch):
    app = create_app(Settings(database_url="sqlite://"), supervisor=Supervisor("sqlite://"))
    monitor = app.state.ctx.monitor
    attempts = []
    def notify(item):
        attempts.append(item)
        return len(attempts) > 1
    monkeypatch.setattr(monitor, "_notify", notify)

    def failure():
        raise RuntimeError("private detail")

    for _ in range(2):
        try:
            failure()
        except RuntimeError as exc:
            monitor.record(exc, "/test")
        if len(attempts) == 1:
            with monitor.engine.begin() as conn:
                conn.execute(update(db.errors).values(
                    last_alert_attempt=db.errors.c.last_alert_attempt - 301))

    assert len(attempts) == 2
    [item] = monitor.list_recent()
    assert item["occurrences"] == 2
    assert item["notified_at"] is not None


def test_existing_database_gains_error_table():
    supervisor = Supervisor("sqlite://")
    with supervisor.engine.begin() as conn:
        db.errors.drop(conn)

    assert db.migrate(supervisor.engine) == 1
    with supervisor.engine.connect() as conn:
        assert conn.execute(select(db.schema_info.c.version)).scalar_one() == 1
        assert conn.execute(select(db.errors.c.fingerprint)).all() == []
