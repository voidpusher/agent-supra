from agent_supervisor import pricing
from agent_supervisor.demo import HEALTHY_PATCHES, STUCK_PATCHES, main, run_agent
from agent_supervisor.report import render_report


def test_prefix_and_versioned_names():
    base = pricing.estimate_cost_micros("gemini-2.5-flash", 1_000_000, 0)
    assert base == 300_000
    assert pricing.estimate_cost_micros("gemini-2.5-flash-001", 1_000_000, 0) == base
    assert pricing.estimate_cost_micros("models/gemini-2.5-flash", 1_000_000, 0) == base
    assert pricing.estimate_cost_micros("gemini-2.5-flash-lite", 1_000_000, 0) == 100_000
    assert pricing.estimate_cost_micros("totally-unknown", 10, 10) is None


def test_long_context_tier():
    short = pricing.estimate_cost_micros("gemini-2.5-pro", 200_000, 1000)
    long = pricing.estimate_cost_micros("gemini-2.5-pro", 200_001, 1000)
    assert short == 250_000 + 10_000
    assert long == 500_003 + 15_000


def test_overrides_win(monkeypatch):
    overrides = pricing.parse_prices({"gemini-2.5-flash": [1, 1]})
    assert pricing.estimate_cost_micros("gemini-2.5-flash", 1_000_000, 0, overrides) == 1_000_000
    monkeypatch.setenv("AGENT_SUPERVISOR_PRICES", '{"house-model": [2, 4]}')
    assert pricing.estimate_cost_micros("house-model", 1_000_000, 1_000_000) == 6_000_000


def test_demo_pauses_stuck_run_and_leaves_healthy_run_alone(supervisor):
    policy = {"observation_mode": False}
    stuck = run_agent(supervisor, supervisor.create_task("stuck", policy=policy), STUCK_PATCHES, log=lambda *_: None)
    healthy = run_agent(supervisor, supervisor.create_task("healthy", policy=policy), HEALTHY_PATCHES,
                        log=lambda *_: None)
    assert supervisor.decide(stuck)["state"] == "paused"
    assert [i["rule"] for i in supervisor.interventions(stuck)] == ["no_progress"]
    assert supervisor.decide(healthy)["state"] == "completed"
    assert supervisor.interventions(healthy) == []
    html = render_report(supervisor)
    assert html.count("class=ev") == 4


def test_demo_cli(tmp_path, capsys):
    report = tmp_path / "report.html"
    assert main(["--report", str(report)]) == 0
    assert "PAUSED by supervisor" in capsys.readouterr().out
    assert "No progress" in report.read_text()
