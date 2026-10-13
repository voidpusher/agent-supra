import pytest

from agent_supra.detector import action_for, evaluate, merge_policy
from tests.helpers import EventLog

FAILING = ["t::b", "t::c"]


def rules(findings):
    return sorted(f.rule for f in findings)


def test_different_edits_same_failing_tests_is_no_progress():
    log = EventLog()
    for i in range(4):
        log.tests(FAILING, rev=f"r{i}")
    [f] = evaluate(log.events)
    assert f.rule == "no_progress"
    assert f.details["code_versions"] == 4
    assert len(f.evidence) == 4


def test_identical_repeat_without_code_change_is_exact_repeat():
    log = EventLog()
    for _ in range(4):
        log.tests(FAILING, rev="same")
    assert rules(evaluate(log.events)) == ["exact_repeat"]


def test_alternating_two_versions_is_cyclic():
    log = EventLog()
    for i in range(4):
        log.tests(FAILING, rev="A" if i % 2 == 0 else "B")
    assert rules(evaluate(log.events)) == ["cyclic_loop"]


def test_below_threshold_is_quiet():
    log = EventLog()
    for i in range(3):
        log.tests(FAILING, rev=f"r{i}")
    assert evaluate(log.events) == []


def test_two_failures_then_success_is_not_flagged():
    log = EventLog()
    log.tests(FAILING, "r1")
    log.tests(FAILING, "r2")
    log.tests([], "r3")
    assert evaluate(log.events) == []


def test_long_productive_run_is_not_flagged():
    log = EventLog()
    failing = ["t::a", "t::b", "t::c", "t::d"]
    for i in range(8):
        log.call(10_000 + 5_000 * i)
        log.tests(failing[: max(0, 4 - i // 2)], rev=f"r{i}")
    assert evaluate(log.events) == []


def test_deleting_failing_tests_is_not_progress():
    log = EventLog()
    full = ("t::a", "t::b", "t::c", "t::d")
    log.tests(FAILING, "r1", suite=full)
    log.tests(FAILING, "r2", suite=full)
    log.tests(["t::c"], "r3", suite=("t::a", "t::c", "t::d"))  # t::b deleted
    log.tests([], "r4", suite=("t::a", "t::d"))                 # t::c deleted too
    [f] = evaluate(log.events)
    assert f.rule == "no_progress"
    assert set(f.details["failing"]) == set(FAILING)


def test_restored_test_is_judged_on_its_own_result():
    log = EventLog()
    full = ("t::a", "t::b")
    log.tests(["t::b"], "r1", suite=full)
    log.tests([], "r2", suite=("t::a",))     # deleted while failing: still counted
    log.tests([], "r3", suite=full)          # back, and passing: genuinely fixed
    assert evaluate(log.events) == []


def test_transient_errors_neither_count_nor_reset():
    log = EventLog()
    log.tests(FAILING, "r1")
    log.tests(FAILING, "r2")
    log.tests([], "r2b", transient=True)     # flaky runner: ignored
    log.tests(FAILING, "r3")
    assert evaluate(log.events) == []
    log.tests(FAILING, "r4")
    assert rules(evaluate(log.events)) == ["no_progress"]


def test_changing_failure_resets_suspicion():
    log = EventLog()
    for i in range(3):
        log.tests(FAILING, f"r{i}")
    log.tests(["t::c"], "r9")                # one test fixed
    for i in range(2):
        log.tests(["t::c"], f"s{i}")
    assert evaluate(log.events) == []


def test_tool_exact_repeat_and_reset_on_success():
    log = EventLog()
    for _ in range(4):
        log.tool(ok=False)
    assert rules(evaluate(log.events)) == ["exact_repeat"]

    log = EventLog()
    for _ in range(3):
        log.tool(ok=False)
    log.tool(ok=True)
    log.tool(ok=False)
    assert evaluate(log.events) == []


def test_tool_transient_errors_do_not_count():
    log = EventLog()
    for _ in range(6):
        log.tool(ok=False, error_class="transient")
    assert evaluate(log.events) == []


def test_tool_changed_arguments_is_not_exact_repeat():
    log = EventLog()
    for i in range(5):
        log.tool(ok=False, args_fp=f"a{i}")
    assert evaluate(log.events) == []


def test_context_runaway_without_progress():
    log = EventLog()
    for i in range(5):
        log.call(10_000 + 6_000 * i)
    [f] = evaluate(log.events)
    assert f.rule == "context_runaway"


def test_context_growth_with_progress_is_fine():
    log = EventLog()
    failing = ["t::a", "t::b", "t::c", "t::d"]
    for i in range(5):
        log.call(10_000 + 6_000 * i)
        log.tests(failing[: 4 - i], f"r{i}")
    assert "context_runaway" not in rules(evaluate(log.events))


def test_context_ceiling():
    log = EventLog()
    log.call(250_000)
    [f] = evaluate(log.events, {"context_token_ceiling": 200_000})
    assert f.rule == "context_runaway"


def test_since_seq_ignores_older_events():
    log = EventLog()
    for i in range(4):
        log.tests(FAILING, f"r{i}")
    assert evaluate(log.events, since_seq=log.events[-1]["seq"]) == []


def test_missing_telemetry_lowers_confidence_and_never_pauses():
    log = EventLog()
    for i in range(5):
        log.tests(FAILING, f"r{i}")
    del log.events[2]                        # a lost event; 4 remain
    [f] = evaluate(log.events)
    assert f.confidence == "low"
    assert action_for(f, {"observation_mode": False}) == "flag"


def test_observation_mode_flags_and_opt_in_pauses():
    log = EventLog()
    for i in range(4):
        log.tests(FAILING, f"r{i}")
    [f] = evaluate(log.events)
    assert action_for(f, {}) == "flag"                       # default: observation mode
    assert action_for(f, {"observation_mode": False}) == "pause"
    assert action_for(f, {"observation_mode": False, "rules": {"no_progress": "observe"}}) == "flag"


def test_rule_can_be_switched_off():
    log = EventLog()
    for i in range(4):
        log.tests(FAILING, f"r{i}")
    assert evaluate(log.events, {"rules": {"no_progress": "off"}}) == []


def test_threshold_is_configurable_and_validated():
    log = EventLog()
    for i in range(2):
        log.tests(FAILING, f"r{i}")
    assert rules(evaluate(log.events, {"threshold": 2})) == ["no_progress"]
    with pytest.raises(ValueError):
        merge_policy({"threshold": 1})


def test_trading_one_failure_for_another_is_not_progress():
    log = EventLog()
    log.tests(["t::a", "t::b"], "r0")
    for i, failing in enumerate([["t::a"], ["t::b"], ["t::a"]]):
        log.tests(failing, f"r{i + 1}")
    # r1 is a new low (1 failing, attempt 1 of the streak); r2-r3 only swap which test fails
    assert evaluate(log.events) == []
    log.tests(["t::b"], "r4")
    [f] = evaluate(log.events)
    assert f.rule == "no_progress" and "trading one failure" in f.message


def test_getting_worse_is_not_progress():
    log = EventLog()
    log.tests(["t::a"], "r0")
    for i in range(3):
        log.tests(["t::a", "t::b", "t::c"], f"r{i + 1}")
    assert rules(evaluate(log.events)) == ["no_progress"]
