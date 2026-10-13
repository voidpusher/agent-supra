from types import SimpleNamespace

import pytest

from agent_supra import SupervisedRun, SupervisorPaused
from agent_supra.server import SupervisorError
from agent_supra.pricing import register_price

FAILING = ["t::b", "t::c"]
SUITE = ["t::a", "t::b", "t::c"]
PAUSE = {"observation_mode": False}


def stuck(run, n, start=0):
    for i in range(start, start + n):
        run.record_model_call("demo-model", 10_000, 500)
        run.report_tests(SUITE, FAILING, rev=f"r{i}")


def test_loop_pauses_run_and_sdk_refuses_next_step(supervisor):
    sup = supervisor
    task = sup.create_task("fix", policy=PAUSE)
    run = SupervisedRun(sup, task)
    stuck(run, 4)
    assert sup.decide(run.run_id)["state"] == "paused"
    calls = []
    with pytest.raises(SupervisorPaused) as info:
        run.tool("edit", lambda: calls.append(1))
    assert calls == []                       # the tool never started
    assert info.value.retryable is False
    [i] = sup.interventions(run.run_id)
    assert i["rule"] == "no_progress" and i["action"] == "pause"
    assert i["spend_micros"] > 0


def test_observation_mode_flags_without_pausing(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix"))   # default policy = observation
    stuck(run, 6)
    assert sup.decide(run.run_id)["allowed"] is True
    flags = sup.interventions(run.run_id)
    assert [f["action"] for f in flags] == ["flag"]     # recorded once, not every event


def test_continue_needs_reason_and_restarts_detection(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix", policy=PAUSE))
    stuck(run, 4)
    with pytest.raises(SupervisorError):
        sup.continue_run(run.run_id, "sanchit", "  ")
    assert sup.continue_run(run.run_id, "sanchit", "trying a new approach")["allowed"]
    stuck(run, 3, start=10)                  # 3 more failures: below threshold again
    assert sup.decide(run.run_id)["allowed"]
    stuck(run, 1, start=20)                  # 4th since continuation: paused again
    assert not sup.decide(run.run_id)["allowed"]
    actions = [(i["actor"], i["action"]) for i in sup.interventions(run.run_id)]
    assert actions == [("supervisor", "pause"), ("sanchit", "continue"), ("supervisor", "pause")]


def test_stop_is_permanent(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix"))
    sup.stop(run.run_id, "sanchit", "not worth it")
    with pytest.raises(SupervisorError):
        sup.continue_run(run.run_id, "sanchit", "changed my mind")
    with pytest.raises(SupervisorPaused):
        run.check()


def test_duplicate_event_delivery_is_ignored(supervisor):
    sup = supervisor
    task = sup.create_task("fix")
    run_id = sup.create_run(task)
    event = {"event_id": "e1", "seq": 1, "type": "model.call",
             "data": {"model": "demo-model", "input_tokens": 1_000_000, "output_tokens": 0}}
    sup.ingest(run_id, event)
    sup.ingest(run_id, dict(event))
    assert sup.task_spend(task)["estimated_micros"] == 3_000_000
    assert sup.task_spend(task)["calls"] == 1


def test_budget_is_shared_by_child_runs(supervisor):
    sup = supervisor
    task = sup.create_task("fix", budget_usd=1.0)
    parent = SupervisedRun(sup, task)
    child = parent.child()
    parent.record_model_call("demo-model", 200_000, 0)   # $0.60
    child.record_model_call("demo-model", 200_000, 0)    # $0.60 -> $1.20 total
    assert not sup.decide(child.run_id)["allowed"]
    assert sup.interventions(child.run_id)[0]["rule"] == "budget"
    assert sup.decide(parent.run_id)["allowed"]           # not yet judged: blocked on its next model call
    with pytest.raises(SupervisorPaused):
        parent.check(before_model_call=True)


def test_continue_over_budget_requires_more_budget(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix", budget_usd=0.5))
    run.record_model_call("demo-model", 200_000, 0)
    assert sup.decide(run.run_id)["state"] == "paused"
    with pytest.raises(SupervisorError):
        sup.continue_run(run.run_id, "sanchit", "keep going")
    assert sup.continue_run(run.run_id, "sanchit", "keep going", add_budget_usd=1.0)["allowed"]


def test_budget_is_enforced_even_in_observation_mode(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix", budget_usd=0.1))   # observation policy
    run.record_model_call("demo-model", 100_000, 0)
    assert not sup.decide(run.run_id)["allowed"]


def test_call_cap_blocks_the_next_model_call(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix", max_calls=2))
    run.record_model_call("demo-model", 10, 1)
    run.record_model_call("demo-model", 10, 1)
    run.check()                                          # tools are still fine
    with pytest.raises(SupervisorPaused):
        run.check(before_model_call=True)
    assert sup.interventions(run.run_id)[0]["rule"] == "call_cap"


class FakeAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100,
                                cache_read_input_tokens=500, cache_creation_input_tokens=None)
        return SimpleNamespace(model=kwargs["model"], usage=usage, content=[])


def test_wrapped_client_records_usage_and_is_blocked_when_paused(supervisor):
    register_price("my-model", 3.0, 15.0)
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix"))
    fake = FakeAnthropic()
    client = run.wrap_anthropic(fake)
    client.messages.create(model="my-model", max_tokens=10, messages=[])
    [call] = [e for e in sup.events(run.run_id) if e["type"] == "model.call"]
    assert call["data"]["input_tokens"] == 1500          # cache reads counted (conservative)
    assert call["data"]["cost_micros"] == 1500 * 3 + 100 * 15
    sup.pause(run.run_id, "sanchit", "looking at it")
    with pytest.raises(SupervisorPaused):
        client.messages.create(model="my-model", max_tokens=10, messages=[])
    assert fake.calls == 1                               # the provider was never called


def test_unpriced_model_is_tracked_not_hidden(supervisor):
    sup = supervisor
    task = sup.create_task("fix")
    run = SupervisedRun(sup, task)
    run.record_model_call("mystery-model", 1000, 100)
    assert sup.task_spend(task)["unpriced_calls"] == 1


def test_context_manager_leaves_paused_runs_for_review(supervisor):
    sup = supervisor
    task = sup.create_task("fix", policy=PAUSE)
    with SupervisedRun(sup, task) as run:
        stuck(run, 4)
    assert sup.decide(run.run_id)["state"] == "paused"   # not silently "completed"

    with SupervisedRun(sup, task) as ok_run:
        ok_run.report_tests(SUITE, [], rev="x")
        ok_run.finish("success")
    assert sup.runs(task)[-1]["state"] == "completed"


def test_child_run_cannot_attach_to_another_task(supervisor):
    sup = supervisor
    a = sup.create_run(sup.create_task("a"))
    with pytest.raises(SupervisorError):
        sup.create_run(sup.create_task("b"), parent_run_id=a)


def test_tool_errors_are_fingerprinted_not_stored(supervisor):
    sup = supervisor
    run = SupervisedRun(sup, sup.create_task("fix"))

    def boom():
        raise ValueError("secret token sk-123 at /home/me/app.py line 42")

    with pytest.raises(ValueError):
        run.tool("deploy", boom)
    [e] = [e for e in sup.events(run.run_id) if e["type"] == "tool.finished"]
    assert "secret" not in str(e["data"])
    assert e["data"]["error_class"] == "permanent"
