"""A real Gemini agent that fixes a bug, supervised. Use it to test the product.

    pip install agent-supra google-genai pytest
    export GEMINI_API_KEY=...                  # from https://aistudio.google.com/apikey
    export AGENT_SUPRA_API_KEY=as_live_...  # from your dashboard
    export AGENT_SUPRA_URL=https://your-deployment

    python examples/gemini_agent.py              # solvable bug: should finish, no pause
    python examples/gemini_agent.py --impossible # contradictory tests: should get paused

``--impossible`` adds a test that can never pass alongside the others, so Gemini
keeps trying different fixes while the same test fails: the loop the supervisor
exists to catch. Turn observation mode off (Settings) or pass --pause so the
run is actually paused rather than only flagged.
"""

from __future__ import annotations

import argparse
import os
import sys

from google import genai

from agent_supra import Client, SupervisedRun, SupervisorPaused
from agent_supra.demo import BUG, edit_file, make_repo, rev, run_tests

IMPOSSIBLE_TEST = '''

def test_rounding_rule():
    # Contradicts test_ten_percent: no implementation can pass both.
    assert apply_discount(100, 10) == 91
'''

PROMPT = """You are fixing a bug in a Python function.

The function is:

    def apply_discount(price: float, pct: float) -> float:
        \"\"\"Return the price after a percentage discount (pct is 0-100).\"\"\"
        return {expr}

These tests fail: {failing}

The test file:
{tests}

Previous attempts that did not work: {history}

Reply with ONLY the new Python expression after `return`, nothing else. No code fences."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--impossible", action="store_true", help="add a test that can never pass")
    parser.add_argument("--pause", action="store_true", help="pause on loops even if the workspace is in observation mode")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--budget", type=float, default=0.50, help="task budget in USD")
    parser.add_argument("--fail-mode", choices=("open", "closed"), default="open",
                        help="whether the agent continues during a supervisor outage")
    args = parser.parse_args()

    repo = make_repo()
    if args.impossible:
        with open(repo / "test_shop.py", "a") as f:
            f.write(IMPOSSIBLE_TEST)

    sup = Client(fail_mode=args.fail_mode)
    policy = {"observation_mode": False} if args.pause else None
    label = "impossible" if args.impossible else "solvable"
    task = sup.create_task(f"Gemini fixes apply_discount ({label})", budget_usd=args.budget, policy=policy)
    print(f"Task created. Watch it at {sup.base_url}/app\n")

    expr, history = BUG, []
    with SupervisedRun(sup, task) as run:
        gemini = run.wrap_gemini(genai.Client())
        result = run.tool("run_tests", run_tests, repo, rev=rev(repo))
        run.report_tests(result.suite, result.failing, rev=rev(repo))
        try:
            for step in range(1, args.max_steps + 1):
                run.check()
                reply = gemini.models.generate_content(
                    model=args.model,
                    contents=PROMPT.format(expr=expr, failing=", ".join(result.failing),
                                           tests=(repo / "test_shop.py").read_text(),
                                           history="; ".join(history) or "none"))
                expr = (reply.text or "").strip().strip("`").removeprefix("return ").strip()
                history.append(expr)
                run.tool("edit_file", edit_file, repo, expr, rev=rev(repo))
                result = run.tool("run_tests", run_tests, repo, rev=rev(repo))
                decision = run.report_tests(result.suite, result.failing, rev=rev(repo))
                status = "all tests pass" if not result.failing else f"{len(result.failing)} failing"
                print(f"attempt {step}: return {expr}  ->  {status}")
                if not result.failing:
                    run.finish("success")
                    print("\nFixed. The supervisor left this run alone.")
                    return 0
                if not decision.get("allowed", True):
                    raise SupervisorPaused({**decision, "run_id": run.run_id})
            run.finish("failed")
            print("\nGave up after max steps.")
        except SupervisorPaused as paused:
            print(f"\nPaused by the supervisor: {paused.reason}")
            print(f"Review it at {sup.base_url}/app/runs/{run.run_id}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
