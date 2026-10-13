import os
import re

import pytest
from fastapi.testclient import TestClient

from agent_supervisor.server.app import create_app
from agent_supervisor.server.core import Supervisor
from agent_supervisor.server.settings import Settings


def database_url() -> str:
    # Set TEST_DATABASE_URL=postgresql+psycopg://... to run the suite on Postgres.
    return os.environ.get("TEST_DATABASE_URL") or "sqlite://"


@pytest.fixture
def supervisor():
    sup = Supervisor(database_url())
    yield sup
    if sup.engine.dialect.name == "postgresql":
        from agent_supervisor.server import db
        db.metadata.drop_all(sup.engine)


class Sent(list):
    def __call__(self, ws, item):
        self.append((ws, item))


@pytest.fixture
def server(supervisor):
    sent = Sent()
    settings = Settings(database_url=database_url(), base_url="http://testserver",
                        auth_attempts_per_minute=1000, events_per_minute=100_000)
    app = create_app(settings, supervisor=supervisor, alert_sender=sent)
    client = TestClient(app)
    client.alerts = sent
    client.ctx = app.state.ctx
    return client


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def signup(client, email="sanchit@example.com", password="correct horse battery"):
    page = client.get("/signup")
    r = client.post("/signup", data={"email": email, "password": password, "csrf": csrf_from(page.text)},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def make_key(client) -> str:
    page = client.get("/app/keys")
    r = client.post("/app/keys", data={"name": "test agent", "csrf": csrf_from(page.text)})
    return re.search(r'id="new-key">([^<]+)<', r.text).group(1)


@pytest.fixture
def account(server):
    signup(server)
    key = make_key(server)
    return server, key
