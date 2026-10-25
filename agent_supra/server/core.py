"""Supervisor core: tasks, runs, events, decisions and interventions, scoped to a workspace.

Every public method takes the caller's ``workspace_id``; a run or task from
another workspace is reported as not found, never revealed.

Run states: running -> paused -> running (continue) | stopped
            running -> completed | failed  (when the run reports its outcome)
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from typing import Any, Callable

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection, Engine

from .. import pricing
from ..detector import ENFORCED_RULES, Finding, action_for, evaluate, merge_policy
from . import db

TERMINAL = {"completed", "failed", "stopped"}
OUTCOMES = {"success": "completed", "failed": "failed", "unknown": "completed"}
EVENT_TYPES = {"run.started", "run.finished", "model.call", "tool.finished", "tests.reported",
               "progress.updated"}
MAX_EVENT_BYTES = 32_000
MAX_TEST_IDS = 5_000
LOCAL_WORKSPACE = "ws_local"


class SupervisorError(Exception):
    """A bad request against the supervisor."""

    status = 400


class NotFound(SupervisorError):
    status = 404


class Conflict(SupervisorError):
    status = 409


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Supervisor:
    """The service core. ``Supervisor()`` alone gives an in-memory SQLite instance
    with a local workspace, which is what the demo and in-process use need."""

    def __init__(self, database_url: str | Engine = "sqlite://", workspace_id: str | None = None):
        self.engine = database_url if isinstance(database_url, Engine) else db.make_engine(database_url)
        db.migrate(self.engine)
        self._sqlite_lock = threading.RLock() if self.engine.dialect.name == "sqlite" else None
        self.on_intervention: Callable[[dict], None] | None = None
        self.default_workspace = workspace_id or self._ensure_local_workspace()

    # ------------------------------------------------------------------ plumbing
    @contextlib.contextmanager
    def _tx(self):
        lock = self._sqlite_lock or contextlib.nullcontext()
        with lock, self.engine.begin() as conn:
            yield conn

    @contextlib.contextmanager
    def _read(self):
        lock = self._sqlite_lock or contextlib.nullcontext()
        with lock, self.engine.connect() as conn:
            yield conn

    def _lock_run(self, conn: Connection, run_id: str, ws: str):
        """Lock in a fixed order (task, then run) so concurrent requests can't deadlock."""
        run = self._run(conn, run_id, ws)
        task = self._task(conn, run.task_id, ws, lock=True)
        return self._run(conn, run_id, ws, lock=True), task

    def _ensure_local_workspace(self) -> str:
        with self._tx() as conn:
            if not conn.execute(select(db.workspaces.c.id).where(db.workspaces.c.id == LOCAL_WORKSPACE)).first():
                conn.execute(db.workspaces.insert().values(
                    id=LOCAL_WORKSPACE, name="Local", default_policy="{}", prices="{}",
                    alert_on_flags=False, created_at=time.time()))
        return LOCAL_WORKSPACE

    def _ws(self, workspace_id: str | None) -> str:
        return workspace_id or self.default_workspace

    def _workspace(self, conn: Connection, ws: str):
        row = conn.execute(select(db.workspaces).where(db.workspaces.c.id == ws)).first()
        if row is None:
            raise NotFound("unknown workspace")
        return row

    def _run(self, conn: Connection, run_id: str, ws: str, lock: bool = False):
        q = select(db.runs).where(and_(db.runs.c.id == run_id, db.runs.c.workspace_id == ws))
        if lock:
            q = q.with_for_update()
        row = conn.execute(q).first()
        if row is None:
            raise NotFound(f"unknown run {run_id}")
        return row

    def _task(self, conn: Connection, task_id: str, ws: str, lock: bool = False):
        q = select(db.tasks).where(and_(db.tasks.c.id == task_id, db.tasks.c.workspace_id == ws))
        if lock:
            q = q.with_for_update()
        row = conn.execute(q).first()
        if row is None:
            raise NotFound(f"unknown task {task_id}")
        return row

    def _policy(self, conn: Connection, task) -> dict:
        ws = self._workspace(conn, task.workspace_id)
        base = json.loads(ws.default_policy or "{}")
        own = json.loads(task.policy or "{}")
        rules = {**(base.get("rules") or {}), **(own.get("rules") or {})}
        return merge_policy({**base, **own, "rules": rules})

    # ------------------------------------------------------------------ tasks & runs
    def create_task(self, objective: str, budget_usd: float | None = None, max_calls: int | None = None,
                    policy: dict | None = None, workspace_id: str | None = None) -> str:
        ws = self._ws(workspace_id)
        if not objective or len(objective) > 500:
            raise SupervisorError("objective must be 1-500 characters")
        if budget_usd is not None and budget_usd < 0:
            raise SupervisorError("budget must be positive")
        if max_calls is not None and max_calls < 1:
            raise SupervisorError("max_calls must be at least 1")
        try:
            merge_policy(policy)
        except (ValueError, TypeError) as exc:
            raise SupervisorError(f"invalid policy: {exc}")
        task_id = new_id("task")
        with self._tx() as conn:
            self._workspace(conn, ws)
            conn.execute(db.tasks.insert().values(
                id=task_id, workspace_id=ws, objective=objective,
                budget_micros=pricing.to_micros(budget_usd) if budget_usd is not None else None,
                max_calls=max_calls, policy=json.dumps(policy or {}), created_at=time.time()))
        return task_id

    def create_run(self, task_id: str, parent_run_id: str | None = None,
                   workspace_id: str | None = None) -> str:
        ws = self._ws(workspace_id)
        run_id = new_id("run")
        with self._tx() as conn:
            self._task(conn, task_id, ws, lock=True)
            if parent_run_id and self._run(conn, parent_run_id, ws).task_id != task_id:
                raise SupervisorError("a child run must belong to its parent's task")
            attempt = conn.execute(select(func.count()).select_from(db.runs)
                                   .where(db.runs.c.task_id == task_id)).scalar_one() + 1
            now = time.time()
            conn.execute(db.runs.insert().values(
                id=run_id, workspace_id=ws, task_id=task_id, parent_run_id=parent_run_id,
                attempt=attempt, state="running", baseline_seq=0, spend_micros=0, model_calls=0,
                unpriced_calls=0, created_at=now, updated_at=now))
        return run_id

    # ------------------------------------------------------------------ events
    def _validate_event(self, event: dict) -> dict:
        for key in ("event_id", "seq", "type"):
            if key not in event:
                raise SupervisorError(f"event missing '{key}'")
        if event["type"] not in EVENT_TYPES:
            raise SupervisorError(f"unknown event type '{event['type']}'")
        event_id = str(event["event_id"])
        if not 1 <= len(event_id) <= 64:
            raise SupervisorError("event_id must be 1-64 characters")
        try:
            seq = int(event["seq"])
        except (TypeError, ValueError):
            raise SupervisorError("seq must be an integer")
        if seq < 1:
            raise SupervisorError("seq must be at least 1")
        data = dict(event.get("data") or {})
        for key in ("suite", "failing"):
            if key in data:
                if not isinstance(data[key], list) or len(data[key]) > MAX_TEST_IDS:
                    raise SupervisorError(f"'{key}' must be a list of at most {MAX_TEST_IDS} test IDs")
                data[key] = [str(x)[:300] for x in data[key]]
        if event["type"] == "model.call":
            for key in ("input_tokens", "output_tokens"):
                try:
                    data[key] = max(0, int(data.get(key) or 0))
                except (TypeError, ValueError):
                    raise SupervisorError(f"'{key}' must be an integer")
            cost = data.get("cost_micros")
            if cost is not None:
                if isinstance(cost, bool) or not (
                    isinstance(cost, int) or
                    (isinstance(cost, str) and cost.isascii() and cost.isdecimal() and len(cost) <= 19)
                ):
                    raise SupervisorError("'cost_micros' must be a non-negative integer")
                cost = int(cost)
                if cost < 0 or cost > 2**63 - 1:
                    raise SupervisorError("'cost_micros' must be a non-negative 64-bit integer")
                data["cost_micros"] = cost
        encoded = json.dumps(data)
        if len(encoded) > MAX_EVENT_BYTES:
            raise SupervisorError(f"event data is larger than {MAX_EVENT_BYTES} bytes")
        ts = event.get("ts")
        return {"event_id": event_id, "seq": seq, "type": event["type"],
                "ts": float(ts) if isinstance(ts, (int, float)) else time.time(), "data": data}

    def ingest(self, run_id: str, event: dict, workspace_id: str | None = None) -> dict:
        """Store one event (idempotent on event_id), re-judge the run, return its decision."""
        return self.ingest_many(run_id, [event], workspace_id)

    def ingest_many(self, run_id: str, batch: list[dict], workspace_id: str | None = None) -> dict:
        ws = self._ws(workspace_id)
        if not batch:
            raise SupervisorError("no events")
        if len(batch) > 500:
            raise SupervisorError("at most 500 events per request")
        clean = [self._validate_event(e) for e in batch]
        new_interventions: list[dict] = []
        with self._tx() as conn:
            run, task = self._lock_run(conn, run_id, ws)
            overrides = pricing.parse_prices(self._workspace(conn, ws).prices)
            inserted_any = False
            for e in sorted(clean, key=lambda x: x["seq"]):
                data = e["data"]
                if e["type"] == "model.call":
                    data.setdefault("cost_micros", None)
                    if data.get("cost_micros") is None and not data.get("usage_missing"):
                        data["cost_micros"] = pricing.estimate_cost_micros(
                            data.get("model", ""), data["input_tokens"], data["output_tokens"], overrides)
                    data["price_version"] = pricing.PRICE_TABLE_VERSION
                if not db.insert_ignore(conn, self.engine, db.events, run_id=run_id, event_id=e["event_id"],
                                        seq=e["seq"], type=e["type"], ts=e["ts"], data=json.dumps(data)):
                    continue  # duplicate delivery
                inserted_any = True
                values: dict[str, Any] = {"updated_at": time.time()}
                if e["type"] == "model.call":
                    values["model_calls"] = db.runs.c.model_calls + 1
                    if data["cost_micros"] is None:
                        values["unpriced_calls"] = db.runs.c.unpriced_calls + 1
                    else:
                        values["spend_micros"] = db.runs.c.spend_micros + int(data["cost_micros"])
                conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(**values))
                if e["type"] == "run.finished":
                    current = self._run(conn, run_id, ws)
                    if current.state == "running":
                        outcome = str(data.get("outcome", "unknown"))
                        conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                            state=OUTCOMES.get(outcome, "failed"), outcome=outcome[:20]))
            if inserted_any:
                received = time.time()
                if not db.insert_ignore(conn, self.engine, db.run_activity, run_id=run_id,
                                        first_received_at=received, last_received_at=received):
                    conn.execute(update(db.run_activity).where(db.run_activity.c.run_id == run_id)
                                 .values(last_received_at=received))
            if inserted_any and self._run(conn, run_id, ws).state == "running":
                new_interventions = self._judge(conn, run_id, task)
            decision = self._decision(conn, run_id, ws)
        self._notify(new_interventions)
        return decision

    def _notify(self, items: list[dict]) -> None:
        if self.on_intervention:
            for item in items:
                try:
                    self.on_intervention(item)
                except Exception:  # alerts must never break ingestion
                    pass

    # ------------------------------------------------------------------ spend & judging
    def _task_totals(self, conn: Connection, task_id: str) -> dict:
        row = conn.execute(select(func.coalesce(func.sum(db.runs.c.spend_micros), 0),
                                  func.coalesce(func.sum(db.runs.c.model_calls), 0),
                                  func.coalesce(func.sum(db.runs.c.unpriced_calls), 0))
                           .where(db.runs.c.task_id == task_id)).first()
        return {"estimated_micros": int(row[0]), "calls": int(row[1]), "unpriced_calls": int(row[2])}

    def _events(self, conn: Connection, run_id: str) -> list[dict]:
        rows = conn.execute(select(db.events).where(db.events.c.run_id == run_id)
                            .order_by(db.events.c.seq)).all()
        return [{"event_id": r.event_id, "seq": r.seq, "type": r.type, "ts": r.ts,
                 "data": json.loads(r.data)} for r in rows]

    def _limit_finding(self, conn: Connection, task, before_model_call: bool) -> Finding | None:
        totals = self._task_totals(conn, task.id)
        if task.budget_micros is not None and totals["estimated_micros"] >= task.budget_micros:
            return Finding("budget", f"Estimated task spend {pricing.usd(totals['estimated_micros'])} "
                                     f"reached the budget of {pricing.usd(task.budget_micros)}.", [],
                           details={"spend": totals["estimated_micros"], "budget": task.budget_micros})
        if task.max_calls is not None and before_model_call and totals["calls"] >= task.max_calls:
            return Finding("call_cap", f"The task already made {totals['calls']} model calls "
                                       f"(cap {task.max_calls}).", [], details={"calls": totals["calls"]})
        return None

    def _judge(self, conn: Connection, run_id: str, task, before_model_call: bool = False) -> list[dict]:
        run = self._run(conn, run_id, task.workspace_id)
        policy = self._policy(conn, task)
        findings = evaluate(self._events(conn, run_id), policy, since_seq=run.baseline_seq or 0)
        limit = self._limit_finding(conn, task, before_model_call)
        if limit:
            findings.append(limit)
        created = []
        for f in findings:
            action = action_for(f, policy)
            key = f"{run.baseline_seq or 0}|" + (f.dedup_key if f.rule not in ENFORCED_RULES else f.rule)
            spend = self._task_totals(conn, task.id)["estimated_micros"]
            item = self._record(conn, run_id, f.rule, action, f.message, f.evidence, f.confidence,
                                "supervisor", dedup_key=key, spend=spend)
            if item is None:
                continue
            if action == "pause" and self._run(conn, run_id, task.workspace_id).state == "running":
                conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                    state="paused", reason=f"{f.rule}: {f.message}", updated_at=time.time()))
            created.append({**item, "workspace_id": task.workspace_id, "task_id": task.id,
                            "objective": task.objective})
        return created

    def _record(self, conn: Connection, run_id, rule, action, message, evidence, confidence, actor,
                reason=None, dedup_key=None, spend=None) -> dict | None:
        # Time-ordered ID so interventions sort in the order they happened.
        item = {"id": f"int_{time.time_ns():016x}{uuid.uuid4().hex[:6]}", "run_id": run_id, "rule": rule, "action": action,
                "message": message, "evidence": json.dumps(evidence), "confidence": confidence,
                "actor": actor, "reason": reason, "dedup_key": dedup_key, "spend_micros": spend,
                "created_at": time.time()}
        if dedup_key is None:
            conn.execute(db.interventions.insert().values(**item))
            return item
        return item if db.insert_ignore(conn, self.engine, db.interventions, **item) else None

    # ------------------------------------------------------------------ decisions
    def _decision(self, conn: Connection, run_id: str, ws: str) -> dict:
        run = self._run(conn, run_id, ws)
        return {"run_id": run_id, "allowed": run.state == "running", "state": run.state,
                "reason": run.reason}

    def decide(self, run_id: str, before_model_call: bool = False, workspace_id: str | None = None) -> dict:
        """May this run take its next step? Called by the SDK before every step."""
        ws = self._ws(workspace_id)
        created: list[dict] = []
        with self._tx() as conn:
            run = self._run(conn, run_id, ws)
            if run.state == "running" and before_model_call:
                run, task = self._lock_run(conn, run_id, ws)
                created = self._judge(conn, run_id, task, before_model_call=True)
            decision = self._decision(conn, run_id, ws)
        self._notify(created)
        return decision

    # ------------------------------------------------------------------ reviewer actions
    def pause(self, run_id: str, actor: str, reason: str, workspace_id: str | None = None) -> dict:
        ws = self._ws(workspace_id)
        reason = (reason or "").strip()[:1000] or "paused by reviewer"
        with self._tx() as conn:
            run, _ = self._lock_run(conn, run_id, ws)
            if run.state != "running":
                raise Conflict(f"cannot pause a run that is {run.state}")
            conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                state="paused", reason=f"manual: {reason}", updated_at=time.time()))
            self._record(conn, run_id, "manual", "pause", reason, [], "high", actor, reason)
            return self._decision(conn, run_id, ws)

    def continue_run(self, run_id: str, actor: str, reason: str, add_budget_usd: float = 0.0,
                     workspace_id: str | None = None) -> dict:
        """Let a paused run go on. Needs a reason; detection restarts from here."""
        ws = self._ws(workspace_id)
        reason = (reason or "").strip()[:1000]
        if not reason:
            raise SupervisorError("a continuation needs a reason")
        if add_budget_usd and add_budget_usd < 0:
            raise SupervisorError("budget can only be increased here")
        with self._tx() as conn:
            run, task = self._lock_run(conn, run_id, ws)
            if run.state != "paused":
                raise Conflict(f"cannot continue a run that is {run.state}")
            if add_budget_usd:
                if task.budget_micros is None:
                    raise SupervisorError("this task has no budget to increase")
                conn.execute(update(db.tasks).where(db.tasks.c.id == task.id).values(
                    budget_micros=db.tasks.c.budget_micros + pricing.to_micros(add_budget_usd)))
                task = self._task(conn, run.task_id, ws)
            if self._limit_finding(conn, task, before_model_call=False) is not None:
                raise Conflict("still over budget: add budget to continue")
            last = conn.execute(select(func.coalesce(func.max(db.events.c.seq), 0))
                                .where(db.events.c.run_id == run_id)).scalar_one()
            conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                state="running", reason=None, baseline_seq=int(last), updated_at=time.time()))
            note = reason + (f" (+{pricing.usd(pricing.to_micros(add_budget_usd), 2)} budget)"
                             if add_budget_usd else "")
            self._record(conn, run_id, "manual", "continue", note, [], "high", actor, reason)
            return self._decision(conn, run_id, ws)

    def stop(self, run_id: str, actor: str, reason: str, workspace_id: str | None = None) -> dict:
        ws = self._ws(workspace_id)
        reason = (reason or "").strip()[:1000] or "stopped by reviewer"
        with self._tx() as conn:
            run, _ = self._lock_run(conn, run_id, ws)
            if run.state in TERMINAL:
                raise Conflict(f"run is already {run.state}")
            conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                state="stopped", reason=f"stopped: {reason}", updated_at=time.time()))
            self._record(conn, run_id, "manual", "stop", reason, [], "high", actor, reason)
            return self._decision(conn, run_id, ws)

    # ------------------------------------------------------------------ read models
    def events(self, run_id: str, workspace_id: str | None = None) -> list[dict]:
        with self._read() as conn:
            self._run(conn, run_id, self._ws(workspace_id))
            return self._events(conn, run_id)

    def _interventions(self, conn: Connection, run_id: str) -> list[dict]:
        rows = conn.execute(select(db.interventions).where(db.interventions.c.run_id == run_id)
                            .order_by(db.interventions.c.id)).all()
        return [{**r._asdict(), "evidence": json.loads(r.evidence)} for r in rows]

    def interventions(self, run_id: str, workspace_id: str | None = None) -> list[dict]:
        with self._read() as conn:
            self._run(conn, run_id, self._ws(workspace_id))
            return self._interventions(conn, run_id)

    def task_spend(self, task_id: str, workspace_id: str | None = None) -> dict:
        with self._read() as conn:
            self._task(conn, task_id, self._ws(workspace_id))
            return self._task_totals(conn, task_id)

    def run_spend(self, run_id: str, workspace_id: str | None = None) -> int:
        with self._read() as conn:
            return int(self._run(conn, run_id, self._ws(workspace_id)).spend_micros)

    def runs(self, task_id: str | None = None, workspace_id: str | None = None, state: str | None = None,
             limit: int = 200, offset: int = 0) -> list[dict]:
        ws = self._ws(workspace_id)
        q = (select(db.runs, db.tasks.c.objective).join(db.tasks, db.tasks.c.id == db.runs.c.task_id)
             .where(db.runs.c.workspace_id == ws))
        if task_id:
            q = q.where(db.runs.c.task_id == task_id)
        if state:
            q = q.where(db.runs.c.state == state)
        q = q.order_by(db.runs.c.created_at.desc()).limit(min(limit, 500)).offset(offset)
        with self._read() as conn:
            rows = conn.execute(q).all()
        return [r._asdict() for r in reversed(rows)] if task_id else [r._asdict() for r in rows]

    def stats(self, workspace_id: str | None = None, since: float | None = None) -> dict:
        ws = self._ws(workspace_id)
        since = since if since is not None else time.time() - 7 * 86400
        with self._read() as conn:
            rows = conn.execute(select(db.runs.c.state, func.count(), func.coalesce(func.sum(db.runs.c.spend_micros), 0))
                                .where(and_(db.runs.c.workspace_id == ws, db.runs.c.created_at >= since))
                                .group_by(db.runs.c.state)).all()
            flagged = conn.execute(
                select(func.count(func.distinct(db.interventions.c.run_id)))
                .select_from(db.interventions.join(db.runs, db.runs.c.id == db.interventions.c.run_id))
                .where(and_(db.runs.c.workspace_id == ws, db.runs.c.created_at >= since,
                            db.interventions.c.actor == "supervisor"))).scalar_one()
        by_state = {r[0]: int(r[1]) for r in rows}
        return {"runs": sum(by_state.values()), "by_state": by_state,
                "spend_micros": sum(int(r[2]) for r in rows), "flagged_runs": int(flagged)}

    def timeline(self, run_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        ws = self._ws(workspace_id)
        with self._read() as conn:
            run = self._run(conn, run_id, ws)._asdict()
            task = self._task(conn, run["task_id"], ws)._asdict()
            task["policy"] = json.loads(task["policy"] or "{}")
            totals = self._task_totals(conn, run["task_id"])
            events = self._events(conn, run_id)
            interventions = self._interventions(conn, run_id)
        return {"run": run, "task": task, "events": events, "interventions": interventions,
                "run_spend_micros": run["spend_micros"], "task_spend": totals}

    def prune(self, older_than_days: int) -> int:
        """Delete runs (and their events) older than the retention window."""
        cutoff = time.time() - older_than_days * 86400
        with self._tx() as conn:
            old = select(db.runs.c.id).where(db.runs.c.created_at < cutoff).scalar_subquery()
            conn.execute(db.events.delete().where(db.events.c.run_id.in_(old)))
            conn.execute(db.interventions.delete().where(db.interventions.c.run_id.in_(old)))
            result = conn.execute(db.runs.delete().where(db.runs.c.created_at < cutoff))
            conn.execute(db.tasks.delete().where(and_(
                db.tasks.c.created_at < cutoff,
                ~db.tasks.c.id.in_(select(db.runs.c.task_id)))))
        return result.rowcount
