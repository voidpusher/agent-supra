"""The HTTP SDK client against the real app (through TestClient), including outages."""

import pytest

from agent_supervisor import Client, SupervisedRun, SupervisorAPIError, SupervisorPaused
from agent_supervisor.demo import HEALTHY_PATCHES, STUCK_PATCHES, run_agent


class Transport:
    """Routes the SDK's requests into the test app; can simulate an outage."""

    def __init__(self, test_client, key):
        self.http, self.key = test_client, key
        self.down = False
        self.calls = 0

    def __call__(self, method, path, body, params):
        self.calls += 1
        if self.down:
            raise OSError("connection refused")
        r = self.http.request(method, path, json=body, params=params,
                              headers={"Authorization": f"Bearer {self.key}"})
        return r.status_code, r.json()


def make_client(account, **kwargs):
    server, key = account
    transport = Transport(server, key)
    client = Client(api_key=key, base_url="http://testserver", transport=transport, retries=0, **kwargs)
    return server, client, transport


def test_demo_agent_over_http(account):
    server, client, _ = make_client(account)
    stuck = run_agent(client, client.create_task("stuck", policy={"observation_mode": False}),
                      STUCK_PATCHES, log=lambda *_: None)
    healthy = run_agent(client, client.create_task("healthy", policy={"observation_mode": False}),
                        HEALTHY_PATCHES, log=lambda *_: None)
    assert client.timeline(stuck)["run"]["state"] == "paused"
    assert client.timeline(healthy)["run"]["state"] == "completed"
    model_calls = [e for e in client.timeline(stuck)["events"] if e["type"] == "model.call"]
    assert len(model_calls) == 4


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("AGENT_SUPERVISOR_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        Client()


def test_bad_key_raises_immediately(account):
    server, _ = account
    client = Client(api_key="as_live_wrong_key", transport=Transport(server, "as_live_wrong_key"), retries=0)
    with pytest.raises(SupervisorAPIError) as info:
        client.create_task("x")
    assert info.value.status == 401


def test_fail_open_buffers_and_catches_up(account):
    server, client, transport = make_client(account)
    client.cooldown = 0
    transport.down = True
    task = client.create_task("written while offline", policy={"observation_mode": False})
    run = SupervisedRun(client, task)
    run.record_model_call("gemini-2.5-flash", 1000, 100)
    run.check()                                   # fail-open: allowed to continue
    assert task.startswith("pending_task_") and run.run_id.startswith("pending_run_")
    for i in range(4):
        run.report_tests(["t::a", "t::b"], ["t::b"], rev=f"r{i}")
    transport.down = False
    with pytest.raises(SupervisorPaused):         # back online: buffered loop is judged
        run.check()
    timeline = client.timeline(run.run_id)
    assert timeline["run"]["state"] == "paused"
    assert len(timeline["events"]) == 6           # run.started + call + 4 test reports


def test_fail_closed_pauses_when_unreachable(account):
    _, client, transport = make_client(account, fail_mode="closed")
    task = client.create_task("x")
    run = SupervisedRun(client, task)
    transport.down = True
    with pytest.raises(SupervisorPaused) as info:
        run.check()
    assert info.value.state == "unreachable"


def test_cooldown_skips_network_during_outage(account):
    _, client, transport = make_client(account)
    run = SupervisedRun(client, client.create_task("x"))
    transport.down = True
    run.check()
    before = transport.calls
    for _ in range(5):
        run.check()
        run.progress("still going")
    assert transport.calls == before              # no waiting on timeouts each step


def test_buffer_is_bounded(account):
    _, client, transport = make_client(account, max_buffer=10)
    run = SupervisedRun(client, client.create_task("x"))
    transport.down = True
    for _ in range(25):
        run.progress("offline")
    assert client.dropped_events == 15
