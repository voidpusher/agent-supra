"""Shared wording for events and rules, used by the dashboard and the offline report."""

from __future__ import annotations

import html

RULE_LABELS = {
    "no_progress": "No progress",
    "exact_repeat": "Exact repeat",
    "cyclic_loop": "Going in circles",
    "context_runaway": "Context running away",
    "budget": "Budget reached",
    "call_cap": "Call cap reached",
    "manual": "Manual",
}

RULE_HELP = [
    ("no_progress", "The same tests keep failing while the agent keeps changing the code."),
    ("exact_repeat", "The same failure again with no code change, or a tool failing the same way."),
    ("cyclic_loop", "The agent flips between two versions of the code with the same failures."),
    ("context_runaway", "Input tokens rise call after call with no test progress, or pass your ceiling."),
    ("budget", "Estimated task spend reaches the task's budget. Applies in observation mode too."),
    ("call_cap", "The task would exceed its maximum number of model calls."),
]


def rule_label(rule: str) -> str:
    return RULE_LABELS.get(rule, rule.replace("_", " ").capitalize())


def _rev(d: dict) -> str:
    rev = d.get("rev")
    return f' <span class="muted">at <code>{html.escape(str(rev))[:10]}</code></span>' if rev else ""


def describe_event(e: dict) -> str:
    """One line of safe HTML describing an event."""
    d, t = e["data"], e["type"]
    if t == "model.call":
        model = html.escape(str(d.get("model") or "model"))
        return (f'Model call <code>{model}</code> <span class="muted">{int(d.get("input_tokens", 0)):,} in, '
                f'{int(d.get("output_tokens", 0)):,} out</span>')
    if t == "tool.finished":
        name = html.escape(str(d.get("tool")))
        if d.get("ok"):
            return f'Tool <code>{name}</code> <span class="ok">ok</span>{_rev(d)}'
        kind = "temporary error" if d.get("error_class") == "transient" else "failed"
        return f'Tool <code>{name}</code> <span class="bad">{kind}</span>{_rev(d)}'
    if t == "tests.reported":
        failing, suite = d.get("failing") or [], d.get("suite") or []
        if d.get("error_class") == "transient":
            return f'Tests could not run (temporary error){_rev(d)}'
        if not failing:
            return f'Tests: <span class="ok">all {len(suite)} passed</span>{_rev(d)}'
        names = ", ".join(html.escape(f.split("::")[-1]) for f in failing[:3])
        more = f" and {len(failing) - 3} more" if len(failing) > 3 else ""
        return (f'Tests: <span class="bad">{len(failing)} of {len(suite)} failing</span> '
                f'<span class="muted">({names}{more})</span>{_rev(d)}')
    if t == "run.finished":
        return f"Run finished: <strong>{html.escape(str(d.get('outcome')))}</strong>"
    if t == "run.started":
        return "Run started" + (" as a sub-agent" if d.get("parent_run_id") else "")
    if t == "progress.updated":
        return f"Progress note: {html.escape(str(d.get('note', ''))[:300])}"
    return html.escape(t)
