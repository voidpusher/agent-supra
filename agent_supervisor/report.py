"""Self-contained HTML timeline: every run, its steps, spend, and why it was flagged."""

from __future__ import annotations

import html
import time

from .present import describe_event as _describe, rule_label
from .pricing import usd

STATE_LABEL = {"running": "Running", "paused": "Paused", "completed": "Completed",
               "failed": "Failed", "stopped": "Stopped"}

CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a64;--line:#e4e2da;--accent:#c2410c;
--accent-bg:#fff1e8;--good:#15803d;--good-bg:#eaf6ee;--warn:#a16207;--warn-bg:#fdf6e3;--hl:#fff7ed}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe6;--muted:#a3a29b;
--line:#33322e;--accent:#fb923c;--accent-bg:#2c1d12;--good:#4ade80;--good-bg:#132619;--warn:#facc15;
--warn-bg:#2a2410;--hl:#26201a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:18px;margin:0}
.sub{color:var(--muted);margin:0 0 28px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;
border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:0 0 20px;
overflow-x:auto}
.head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.paused,.stopped{background:var(--accent-bg);color:var(--accent)}.completed{background:var(--good-bg);color:var(--good)}
.failed{background:var(--accent-bg);color:var(--accent)}.running{background:var(--warn-bg);color:var(--warn)}
.meta{color:var(--muted);font-size:13px}
.flag{border-left:3px solid var(--accent);background:var(--accent-bg);padding:12px 14px;border-radius:6px;margin:0 0 14px}
.flag.observe{border-color:var(--warn);background:var(--warn-bg)}
.flag b{display:block;margin-bottom:2px}
.ok{color:var(--good)}.bad{color:var(--accent)}
tr.ev td{background:var(--hl)}code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.quiet,.muted{color:var(--muted)}
"""


def _run_card(sup, run: dict) -> str:
    tl = sup.timeline(run["id"])
    evidence = {eid for i in tl["interventions"] for eid in i["evidence"]}
    rows, running = [], 0
    for e in tl["events"]:
        cost = e["data"].get("cost_micros") if e["type"] == "model.call" else None
        running += int(cost or 0)
        cls = " class=ev" if e["event_id"] in evidence else ""
        rows.append(f"<tr{cls}><td class=num>{e['seq']}</td><td>{_describe(e)}</td>"
                    f"<td class=num>{usd(cost) if e['type'] == 'model.call' else ''}</td>"
                    f"<td class=num>{usd(running)}</td></tr>")
    flags = []
    for i in tl["interventions"]:
        if i["actor"] == "supervisor":
            kind = "Paused" if i["action"] == "pause" else "Flagged (observation only)"
            cls = "flag" if i["action"] == "pause" else "flag observe"
            spent = f" Estimated spend at that point: {usd(i['spend_micros'])}." if i["spend_micros"] is not None else ""
            low = " Low confidence: some telemetry was missing." if i["confidence"] != "high" else ""
            flags.append(f"<div class='{cls}'><b>{kind} · {html.escape(rule_label(i['rule']))}</b>"
                         f"{html.escape(i['message'])}{spent}{low} "
                         f"<span class=quiet>Supporting steps are highlighted below.</span></div>")
        else:
            flags.append(f"<div class='flag observe'><b>{html.escape(i['action'].title())} by "
                         f"{html.escape(i['actor'])}</b>{html.escape(i['message'] or '')}</div>")
    state = run["state"]
    title = html.escape(tl["task"]["objective"] or run["id"])
    return f"""<section class=card id="{run['id']}">
<div class=head><h2>{title}</h2><span class="pill {state}">{STATE_LABEL.get(state, state)}</span></div>
<p class=meta>Run <code>{run['id']}</code>, attempt {run['attempt']}, {len(tl['events'])} events, estimated spend {usd(tl['run_spend_micros'])}</p>
{''.join(flags)}
<table><thead><tr><th class=num>#</th><th>Step</th><th class=num>Est. cost</th><th class=num>Running total</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></section>"""


def render_report(sup, run_ids: list[str] | None = None) -> str:
    runs = list(reversed(sup.runs()))
    if run_ids is not None:
        runs = [r for r in runs if r["id"] in run_ids]
    summary = "".join(
        f"<tr><td><a href='#{r['id']}'>{html.escape(sup.timeline(r['id'])['task']['objective'] or r['id'])}</a></td>"
        f"<td><span class='pill {r['state']}'>{STATE_LABEL.get(r['state'], r['state'])}</span></td>"
        f"<td class=num>{usd(sup.run_spend(r['id']))}</td>"
        f"<td>{html.escape(rule_label((r['reason'] or '').split(':')[0]) if r['reason'] else '—')}</td></tr>" for r in runs)
    cards = "".join(_run_card(sup, r) for r in runs)
    generated = time.strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Agent Supervisor report</title>
<style>{CSS}</style></head><body><main>
<h1>Agent Supervisor report</h1>
<p class=sub>{len(runs)} run(s), generated {generated}. Costs are estimates from token usage.</p>
<section class=card><table><thead><tr><th>Task</th><th>State</th><th class=num>Est. spend</th><th>Reason</th></tr></thead>
<tbody>{summary}</tbody></table></section>
{cards}
</main></body></html>"""
