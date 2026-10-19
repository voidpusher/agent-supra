"""Email recovery proves inbox ownership before changing a password."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_supra.server import db
from agent_supra.server.app import create_app
from agent_supra.server.core import Supervisor
from agent_supra.server.settings import Settings
from tests.conftest import csrf_from


def test_password_recovery_is_generic_single_use_and_ends_sessions(monkeypatch):
    app = create_app(Settings(database_url="sqlite://", base_url="http://testserver",
                              smtp_host="smtp.example", contact_email="operator@example.com"),
                     supervisor=Supervisor("sqlite://"))
    accounts = app.state.ctx.accounts
    user_id, _ = accounts.signup("operator@example.com", "old secret password")
    active = accounts.create_session(user_id)
    guest = TestClient(app)
    sent = []
    monkeypatch.setattr("agent_supra.server.web.send_email",
                        lambda *args: sent.append(args))

    page = guest.get("/forgot-password")
    assert page.status_code == 200
    assert guest.post("/forgot-password", data={"email": "operator@example.com"}).status_code == 403
    csrf = csrf_from(page.text)
    unknown = guest.post("/forgot-password", data={"email": "missing@example.com", "csrf": csrf})
    assert "If the account exists" in unknown.text
    response = guest.post("/forgot-password", data={"email": "operator@example.com", "csrf": csrf})
    assert "If the account exists" in response.text
    assert len(sent) == 1
    token = re.search(r'token=([A-Za-z0-9_-]+)', sent[0][3]).group(1)
    with accounts.engine.connect() as conn:
        [row] = conn.execute(select(db.password_resets)).all()
    assert row.token_hash != token
    assert accounts.session(active) is not None

    # Repeated requests do not flood the inbox or invalidate the first link.
    csrf = csrf_from(response.text) if 'name="csrf"' in response.text else csrf
    guest.post("/forgot-password", data={"email": "operator@example.com", "csrf": csrf})
    assert len(sent) == 1
    reset = guest.get(f"/reset-password?token={token}")
    assert reset.status_code == 200
    assert reset.headers["referrer-policy"] == "no-referrer"
    assert reset.headers["cache-control"] == "no-store"
    success = guest.post("/reset-password", data={"token": token,
                                                   "password": "a new secret password",
                                                   "csrf": csrf_from(reset.text)})
    assert success.status_code == 200
    assert "Password updated" in success.text
    assert accounts.authenticate("operator@example.com", "a new secret password") == user_id
    assert accounts.authenticate("operator@example.com", "old secret password") is None
    assert accounts.session(active) is None
    assert "invalid or expired" in guest.post("/reset-password", data={
        "token": token, "password": "another secret password",
        "csrf": csrf_from(success.text) if 'name="csrf"' in success.text else csrf}).text


def test_expired_reset_token_is_rejected():
    app = create_app(Settings(database_url="sqlite://"), supervisor=Supervisor("sqlite://"))
    accounts = app.state.ctx.accounts
    accounts.signup("member@example.com", "old secret password")
    token = accounts.issue_password_reset("member@example.com")
    with accounts.engine.begin() as conn:
        conn.execute(db.password_resets.update().values(expires_at=0))
    assert accounts.reset_password(token, "a new secret password") is False
    assert accounts.authenticate("member@example.com", "old secret password") is not None
