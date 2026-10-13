"""Deterministic loop detection.

``evaluate()`` is a pure function: it takes a run's events and a policy and
returns findings. No LLM judges anything here; every finding names the rule,
the events that support it, and a plain-English message.

Event shapes (``data`` field):

* ``model.call``      model, input_tokens, output_tokens, cost_micros
* ``tool.finished``   tool, args_fp, ok, error_fp, error_class, rev
* ``tests.reported``  suite (all test IDs), failing (failing IDs), rev, error_class?
* ``run.started`` / ``run.finished`` / ``progress.updated``

``error_class`` is ``"transient"`` for network / rate-limit style failures.
Transient events never count toward a loop and never reset one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HEURISTIC_RULES = ("no_progress", "exact_repeat", "cyclic_loop", "context_runaway")
ENFORCED_RULES = ("budget", "call_cap")

DEFAULT_POLICY: dict[str, Any] = {
    # Observation mode: heuristic rules only flag, never pause.
    # Explicit limits (budget, call cap) are still enforced.
    "observation_mode": True,
    "threshold": 4,               # attempts with no progress before a flag
    "context_window": 5,          # model calls inspected by context_runaway
    "context_growth": 2.0,        # last/first input-token ratio that counts as runaway
    "context_token_ceiling": None,  # optional hard ceiling on input tokens per call
    "rules": {
        "no_progress": "pause",
        "exact_repeat": "pause",
        "cyclic_loop": "observe",
        "context_runaway": "observe",
        "budget": "pause",
        "call_cap": "pause",
    },
}


def merge_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_POLICY, **(policy or {})}
    merged["rules"] = {**DEFAULT_POLICY["rules"], **((policy or {}).get("rules") or {})}
    if int(merged["threshold"]) < 2:
        raise ValueError("threshold must be at least 2")
    return merged


@dataclass
class Finding:
    rule: str
    message: str
    evidence: list[str]            # event IDs
    confidence: str = "high"       # "low" when telemetry is missing
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        # One finding per rule per streak: keyed by the streak's first event,
        # or an explicit anchor (context_runaway uses the last progress point).
        if "anchor" in self.details:
            return f"{self.rule}:@{self.details['anchor']}"
        return f"{self.rule}:{self.evidence[0] if self.evidence else ''}"


def _seq(e: dict) -> int:
    return int(e["seq"])


def has_gaps(events: list[dict]) -> bool:
    """True when per-run sequence numbers are missing (lost or late telemetry)."""
    seqs = sorted({_seq(e) for e in events})
    return bool(seqs) and (seqs[0] != 1 or seqs[-1] != len(seqs))


def _short(ids: set[str], limit: int = 3) -> str:
    ordered = sorted(ids)
    shown = ", ".join(ordered[:limit])
    return shown + (f" +{len(ordered) - limit} more" if len(ordered) > limit else "")


def _test_analysis(events: list[dict], threshold: int) -> tuple[list[Finding], list[int]]:
    """Primary rule. Returns findings and the seqs where real progress happened.

    Progress = the effective failing set changed. Tests that vanish from the
    suite while failing are carried forward as still failing, so deleting a
    test is never counted as fixing it.
    """
    findings: list[Finding] = []
    progress_seqs: list[int] = []
    streak: list[dict] = []          # attempts since the failing count last hit a new low
    sets: list[frozenset[str]] = []  # effective failing set of each attempt in the streak
    best: int | None = None
    prev_eff: frozenset[str] | None = None
    carried: set[str] = set()

    for e in events:
        if e["type"] != "tests.reported":
            continue
        d = e["data"]
        if d.get("error_class") == "transient":
            continue
        suite = set(d.get("suite") or [])
        failing = set(d.get("failing") or [])
        if prev_eff is not None:
            carried |= set(prev_eff) - suite      # failing tests that disappeared
        carried -= suite                          # tests that came back are judged on their own
        eff = frozenset(failing | carried)

        # Progress = fewer failing tests than ever before. Swapping one failure for
        # another, or getting worse, is not progress.
        if best is None or len(eff) < best:
            if best is not None:
                progress_seqs.append(_seq(e))
            best = len(eff)
            streak, sets = ([e], [eff]) if eff else ([], [])
        elif eff:
            streak.append(e)
            sets.append(eff)
        prev_eff = eff

    if len(streak) >= threshold and prev_eff:
        revs = [s["data"].get("rev") for s in streak]
        distinct = list(dict.fromkeys(revs))
        ids = [s["event_id"] for s in streak]
        failing_txt = _short(set(prev_eff))
        if len(set(sets)) > 1:
            rule = "no_progress"
            msg = (f"{len(streak)} attempts in a row without getting below {best} failing test(s); "
                   f"the agent keeps trading one failure for another (now failing: {failing_txt}).")
        elif len(distinct) == 1:
            rule = "exact_repeat"
            msg = (f"The same {len(prev_eff)} failing test(s) ({failing_txt}) on {len(streak)} "
                   f"consecutive attempts with no code change.")
        elif (len(distinct) == 2 and len(revs) >= 4
              and all(revs[i] == revs[i % 2] for i in range(len(revs)))):
            rule = "cyclic_loop"
            msg = (f"The agent is alternating between two versions of the code; the same "
                   f"{len(prev_eff)} test(s) ({failing_txt}) fail on {len(streak)} attempts.")
        else:
            rule = "no_progress"
            msg = (f"The same {len(prev_eff)} failing test(s) ({failing_txt}) on {len(streak)} "
                   f"consecutive attempts, across {len(distinct)} different code versions.")
        findings.append(Finding(rule, msg, ids, details={
            "failing": sorted(prev_eff), "attempts": len(streak), "code_versions": len(distinct)}))
    return findings, progress_seqs


def _tool_repeat(events: list[dict], threshold: int) -> list[Finding]:
    """Exact repeat on tool calls: same tool, args, error and code state."""
    streaks: dict[str, list[dict]] = {}
    for e in events:
        if e["type"] != "tool.finished":
            continue
        d = e["data"]
        if d.get("error_class") == "transient":
            continue
        tool = d.get("tool", "?")
        if d.get("ok"):
            streaks[tool] = []
            continue
        key = (d.get("args_fp"), d.get("error_fp"), d.get("rev"))
        cur = streaks.get(tool) or []
        if cur and (cur[-1]["data"].get("args_fp"), cur[-1]["data"].get("error_fp"),
                    cur[-1]["data"].get("rev")) == key:
            cur.append(e)
        else:
            cur = [e]
        streaks[tool] = cur

    findings = []
    for tool, streak in streaks.items():
        if len(streak) >= threshold:
            findings.append(Finding(
                "exact_repeat",
                f"Tool '{tool}' failed {len(streak)} times in a row with the same arguments, "
                f"the same error and no code change.",
                [s["event_id"] for s in streak],
                details={"tool": tool, "attempts": len(streak)}))
    return findings


def _context(events: list[dict], policy: dict, progress_seqs: list[int]) -> list[Finding]:
    calls = [e for e in events if e["type"] == "model.call"]
    if not calls:
        return []
    ceiling = policy.get("context_token_ceiling")
    last = calls[-1]
    if ceiling and int(last["data"].get("input_tokens", 0)) > int(ceiling):
        return [Finding("context_runaway",
                        f"Input of {last['data']['input_tokens']:,} tokens exceeded the "
                        f"configured ceiling of {int(ceiling):,}.",
                        [last["event_id"]], details={"ceiling": ceiling, "anchor": "ceiling"})]

    window = int(policy["context_window"])
    if len(calls) < window:
        return []
    recent = calls[-window:]
    sizes = [int(c["data"].get("input_tokens", 0)) for c in recent]
    rising = all(b > a for a, b in zip(sizes, sizes[1:]))
    grew = sizes[0] > 0 and sizes[-1] >= sizes[0] * float(policy["context_growth"])
    first_seq, last_seq = _seq(recent[0]), _seq(recent[-1])
    progressed = any(first_seq < s <= last_seq for s in progress_seqs)
    if rising and grew and not progressed:
        anchor = max([s for s in progress_seqs if s <= first_seq], default=0)
        return [Finding("context_runaway",
                        f"Input grew from {sizes[0]:,} to {sizes[-1]:,} tokens over {window} calls "
                        f"with no test progress in between.",
                        [c["event_id"] for c in recent], details={"sizes": sizes, "anchor": anchor})]
    return []


def evaluate(events: list[dict], policy: dict | None = None, since_seq: int = 0) -> list[Finding]:
    """Evaluate one run's events. Only events after ``since_seq`` are judged
    (a reviewer's continuation resets the baseline)."""
    policy = merge_policy(policy)
    ordered = sorted(events, key=_seq)
    confidence = "low" if has_gaps(ordered) else "high"
    window = [e for e in ordered if _seq(e) > since_seq]
    threshold = int(policy["threshold"])

    findings, progress = _test_analysis(window, threshold)
    if not any(f.rule == "exact_repeat" for f in findings):
        findings += _tool_repeat(window, threshold)
    findings += _context(window, policy, progress)

    for f in findings:
        f.confidence = confidence
        if f.rule in policy["rules"] and policy["rules"][f.rule] == "off":
            f.details["disabled"] = True
    return [f for f in findings if not f.details.get("disabled")]


def action_for(finding: Finding, policy: dict | None) -> str:
    """"pause" or "flag" for a finding under a policy."""
    policy = merge_policy(policy)
    mode = policy["rules"].get(finding.rule, "observe")
    if finding.rule in ENFORCED_RULES:
        return "pause" if mode == "pause" else "flag"
    if policy["observation_mode"] or finding.confidence != "high":
        return "flag"
    return "pause" if mode == "pause" else "flag"
