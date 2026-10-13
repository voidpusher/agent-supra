"""Two-run demo: one agent gets stuck and is paused, one struggles but succeeds untouched.

    python -m agent_supervisor.demo                 # pausing enabled for loop rules
    python -m agent_supervisor.demo --observe       # observation mode: flag only
    python -m agent_supervisor.demo --http http://localhost:8000   # needs AGENT_SUPERVISOR_API_KEY

Each run works on a real tiny repository in a temp folder and runs real
pytest. The "model" is scripted (FakeModel) so the demo is free and repeatable;
swap in a real client via ``run.wrap_anthropic(anthropic.Anthropic())``.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .pricing import usd
from .report import render_report
from .sdk import SupervisedRun, SupervisorPaused

SOURCE = '''def apply_discount(price: float, pct: float) -> float:
    """Return the price after a percentage discount (pct is 0-100)."""
    return {expr}
'''

TESTS = '''from shop import apply_discount


def test_no_discount():
    assert apply_discount(100, 0) == 100


def test_ten_percent():
    assert apply_discount(100, 10) == 90


def test_full_discount():
    assert apply_discount(100, 100) == 0


def test_returns_number():
    assert isinstance(apply_discount(50, 20), (int, float))
'''

BUG = "price * pct"

# Every attempt edits the code, but the same two tests keep failing.
STUCK_PATCHES = [
    "price * (1 - pct)",
    "price * (1 + pct / 100)",
    "price + pct * 0",
    "price / (1 + pct)",
    "round(price * (1 - pct), 2)",
    "price - pct * 0.1",
    "price * (1 - pct) + 0",
    "abs(price * (1 - pct))",
]

# Fails twice, changes approach, passes.
HEALTHY_PATCHES = [
    "price * (1 - pct)",
    "price * (1 + pct / 100)",
    "price * (100 - pct) / 100",
]


# ---------------------------------------------------------------- the sandbox repo
def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="supervised-repo-"))
    (repo / "shop.py").write_text(SOURCE.format(expr=BUG))
    (repo / "test_shop.py").write_text(TESTS)
    return repo


def rev(repo: Path) -> str:
    return hashlib.sha1((repo / "shop.py").read_bytes()).hexdigest()[:10]


def edit_file(repo: Path, expr: str) -> None:
    (repo / "shop.py").write_text(SOURCE.format(expr=expr))


@dataclass
class SuiteResult:
    suite: list[str]
    failing: list[str]


def run_tests(repo: Path) -> SuiteResult:
    report = repo / ".junit.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    f"--junitxml={report}"], cwd=repo, capture_output=True, text=True, timeout=120)
    suite, failing = [], []
    for case in ET.parse(report).getroot().iter("testcase"):
        test_id = f"{case.get('classname')}::{case.get('name')}"
        suite.append(test_id)
        if case.find("failure") is not None or case.find("error") is not None:
            failing.append(test_id)
    return SuiteResult(suite, failing)


# ---------------------------------------------------------------- a scripted "model"
class FakeModel:
    """Looks like ``anthropic.Anthropic()`` to the SDK. Context grows every turn,
    the way a real agent transcript does."""

    def __init__(self, patches: list[str]):
        self._patches = list(patches)
        self._turn = 0
        self.messages = self

    def create(self, model: str, messages: list, max_tokens: int = 1024, **_):
        patch = self._patches[min(self._turn, len(self._patches) - 1)]
        usage = SimpleNamespace(input_tokens=12_000 + 9_000 * self._turn, output_tokens=800)
        self._turn += 1
        return SimpleNamespace(model=model, usage=usage,
                               content=[SimpleNamespace(type="text", text=patch)])


# ---------------------------------------------------------------- the agent loop
def run_agent(backend, task_id: str, patches: list[str], max_steps: int = 8, log=print) -> str:
    repo = make_repo()
    with SupervisedRun(backend, task_id) as run:
        client = run.wrap_anthropic(FakeModel(patches))
        result = run.tool("run_tests", run_tests, repo, rev=rev(repo))
        run.report_tests(result.suite, result.failing, rev=rev(repo))
        log(f"  start: {len(result.failing)} failing test(s)")
        try:
            for step in range(1, max_steps + 1):
                run.check()
                reply = client.messages.create(model="demo-model", max_tokens=1024,
                                               messages=[{"role": "user", "content": "Fix the failing tests."}])
                patch = reply.content[0].text
                run.tool("edit_file", edit_file, repo, patch, rev=rev(repo))
                result = run.tool("run_tests", run_tests, repo, rev=rev(repo))
                decision = run.report_tests(result.suite, result.failing, rev=rev(repo))
                status = "all tests pass" if not result.failing else f"{len(result.failing)} failing"
                log(f"  attempt {step}: return {patch:<28} -> {status}")
                if not result.failing:
                    run.finish("success")
                    return run.run_id
                if not decision["allowed"]:
                    raise SupervisorPaused(decision)
            run.finish("failed")
        except SupervisorPaused as paused:
            log(f"  PAUSED by supervisor: {paused.reason}")
    return run.run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--observe", action="store_true", help="observation mode: flag, never pause")
    parser.add_argument("--db", default=":memory:", help="SQLite file (default: in memory)")
    parser.add_argument("--http", help="send events to a running supervisor API instead")
    parser.add_argument("--report", default="supervisor_report.html", help="where to write the HTML report")
    args = parser.parse_args(argv)
    try:
        import pytest  # noqa: F401  (the sample repo's tests run under pytest)
    except ImportError:
        print("The demo runs real tests and needs pytest: pip install pytest")
        return 1

    policy = {"observation_mode": args.observe}
    if args.http:
        from .client import Client
        backend, sup = Client(base_url=args.http), None
    else:
        from .server import Supervisor
        backend = sup = Supervisor("sqlite://" if args.db == ":memory:" else f"sqlite:///{args.db}")

    mode = "observation mode (flag only)" if args.observe else "pausing enabled"
    print(f"Agent Supervisor demo · {mode}\n")
    print("Run A: an agent that keeps editing code while the same tests fail")
    task_a = backend.create_task("Run A: stuck agent fixing apply_discount", budget_usd=5.0, policy=policy)
    run_a = run_agent(backend, task_a, STUCK_PATCHES)
    print("\nRun B: an agent that fails twice, changes approach, and succeeds")
    task_b = backend.create_task("Run B: healthy agent fixing apply_discount", budget_usd=5.0, policy=policy)
    run_b = run_agent(backend, task_b, HEALTHY_PATCHES)

    if sup is not None:
        print()
        for run_id in (run_a, run_b):
            tl = sup.timeline(run_id)
            flags = [i for i in tl["interventions"] if i["actor"] == "supervisor"]
            print(f"{tl['task']['objective'].split(':')[0]}: {tl['run']['state']}, "
                  f"estimated spend {usd(tl['run_spend_micros'])}, "
                  f"{len(flags)} supervisor intervention(s)"
                  + "".join(f"\n   - {f['action']} [{f['rule']}] {f['message']}" for f in flags))
        Path(args.report).write_text(render_report(sup, [run_a, run_b]))
        print(f"\nTimeline report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
