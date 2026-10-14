import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.pool import NullPool

from agent_supra.server import db
from agent_supra.server.alerts import Alerter
from agent_supra.server.app import create_app
from agent_supra.server.settings import Settings


def test_vercel_refuses_ephemeral_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_SUPRA_DB_URL", raising=False)
    with pytest.raises(ValueError, match="persistent PostgreSQL"):
        Settings.from_env()


@pytest.mark.parametrize("environment, expected", [
    ("production", "https://agent-supra.vercel.app"),
    ("preview", "https://agent-supra-preview.vercel.app"),
])
def test_vercel_uses_correct_https_url(monkeypatch, environment, expected):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("VERCEL_ENV", environment)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "agent-supra.vercel.app")
    monkeypatch.setenv("VERCEL_URL", "agent-supra-preview.vercel.app")
    monkeypatch.delenv("BASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.base_url == expected
    assert settings.secure_cookies and settings.serverless


def test_serverless_avoids_per_instance_connection_pools():
    engine = db.make_engine("postgresql://user:pass@host/db", serverless=True)
    assert isinstance(engine.pool, NullPool)
    engine.dispose()


@pytest.mark.parametrize("secret", [None, "test-secret"])
def test_prune_rejects_unauthorized_requests(supervisor, secret):
    task = supervisor.create_task("must survive")
    run = supervisor.create_run(task)
    app = create_app(Settings(serverless=True, cron_secret=secret), supervisor=supervisor)
    with TestClient(app) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert client.get("/api/cron/prune", headers=headers).status_code == 401
    assert supervisor.timeline(run)["run"]["id"] == run


def test_cron_prunes_expired_runs_only(supervisor):
    old = supervisor.create_run(supervisor.create_task("old"))
    new = supervisor.create_run(supervisor.create_task("new"))
    with supervisor.engine.begin() as conn:
        conn.execute(update(db.runs).where(db.runs.c.id == old).values(created_at=time.time() - 40 * 86400))
    app = create_app(Settings(serverless=True, cron_secret="test-secret"), supervisor=supervisor)
    with TestClient(app) as client:
        response = client.get("/api/cron/prune", headers={"Authorization": "Bearer test-secret"})
        assert response.json() == {"ok": True, "removed": 1}
        assert response.headers["cache-control"] == "no-store"
    assert [r["id"] for r in supervisor.runs()] == [new]


def test_cron_respects_disabled_retention(supervisor):
    run = supervisor.create_run(supervisor.create_task("keep"))
    app = create_app(Settings(serverless=True, cron_secret="test-secret", retention_days=0), supervisor=supervisor)
    with TestClient(app) as client:
        assert client.get("/api/cron/prune", headers={"Authorization": "Bearer test-secret"}).json()["removed"] == 0
    assert supervisor.timeline(run)["run"]["id"] == run


def test_serverless_alert_delivered_before_return(supervisor):
    from agent_supra.server.accounts import Accounts
    sent = []
    alerter = Alerter(Accounts(supervisor.engine), Settings(serverless=True),
                      sender=lambda ws, item: sent.append(item))
    item = {"actor": "supervisor", "action": "pause", "workspace_id": supervisor.default_workspace}
    alerter(item)
    assert sent == [item]
    alerter.flush()


def test_alert_delivery_attempts_email_after_slack_failure(monkeypatch):
    delivered = []

    def fail_slack(_webhook, _message):
        raise OSError("webhook failed")

    monkeypatch.setattr("agent_supra.server.alerts.send_slack", fail_slack)
    monkeypatch.setattr("agent_supra.server.alerts.send_email",
                        lambda _settings, _to, _subject, _body: delivered.append("email"))
    alerter = Alerter(None, Settings(serverless=True, smtp_host="smtp.example.com"))
    item = {"action": "pause", "message": "test", "rule": "no_progress", "run_id": "run_test"}
    ws = {"slack_webhook": "https://hooks.slack.com/services/T/B/x", "alert_email": "owner@example.com"}
    with pytest.raises(RuntimeError, match="Alert delivery failed for Slack"):
        alerter._deliver(ws, item)
    assert delivered == ["email"]


def test_serverless_lifespan_starts_no_retention_thread(supervisor, monkeypatch):
    def unexpected_thread(*args, **kwargs):
        raise AssertionError("Serverless must use cron, not a perpetual thread")
    monkeypatch.setattr("agent_supra.server.app.threading.Thread", unexpected_thread)
    app = create_app(Settings(serverless=True), supervisor=supervisor)
    # Exercise lifespan directly: TestClient itself needs its own thread.
    import asyncio
    async def exercise():
        async with app.router.lifespan_context(app):
            pass
    asyncio.run(exercise())
