"""First-party beta measurement. No browser tracking or third-party analytics.

Only operator-confirmed outside workspaces count toward the beta gate. Activity
uses server receipt times, never agent-supplied timestamps. Older runs without a
receipt are deliberately unmeasured; no historical activity is fabricated.
"""
from __future__ import annotations

import statistics
import time

from sqlalchemy import case, delete, func, select

from . import db
from .core import NotFound, SupervisorError


class Beta:
    def __init__(self, engine, operator_email: str):
        self.engine = engine
        self.operator_email = operator_email.casefold()

    def _operator_workspaces(self):
        return (select(db.memberships.c.workspace_id).join(db.users)
                .where(func.lower(db.users.c.email) == self.operator_email))

    def _outside(self):
        return (select(db.beta_cohorts.c.workspace_id)
                .where(db.beta_cohorts.c.kind == "external",
                       db.beta_cohorts.c.workspace_id.not_in(self._operator_workspaces())))

    def classify(self, workspace_id: str, kind: str) -> None:
        if kind not in {"external", "internal", "unreviewed"}:
            raise SupervisorError("choose external, internal, or unreviewed")
        with self.engine.begin() as conn:
            if not conn.execute(select(db.workspaces.c.id).where(
                db.workspaces.c.id == workspace_id).with_for_update()).first():
                raise NotFound("workspace not found")
            if kind == "external" and conn.execute(
                select(db.workspaces.c.id).where(db.workspaces.c.id == workspace_id,
                                               db.workspaces.c.id.in_(self._operator_workspaces()))
            ).first():
                raise SupervisorError("the operator workspace cannot count as an outside user")
            conn.execute(delete(db.beta_cohorts).where(db.beta_cohorts.c.workspace_id == workspace_id))
            if kind != "unreviewed":
                conn.execute(db.beta_cohorts.insert().values(workspace_id=workspace_id, kind=kind,
                                                           updated_at=time.time()))

    def feedback(self, workspace_id: str, user_id: str, run_id: str,
                 intervention_id: str, verdict: str) -> None:
        if verdict not in ({"useful", "too_early", "false_alarm"} if intervention_id else {"missed"}):
            raise SupervisorError("choose a valid review")
        with self.engine.begin() as conn:
            # Lock the run so review replacement is serialized with retention/deletion
            # and other members' submissions. Tenant ownership is checked first.
            run = conn.execute(select(db.runs.c.id).where(
                db.runs.c.id == run_id, db.runs.c.workspace_id == workspace_id).with_for_update()).first()
            member = conn.execute(select(db.memberships.c.user_id).where(
                db.memberships.c.user_id == user_id, db.memberships.c.workspace_id == workspace_id)).first()
            if not run or not member:
                raise NotFound("run not found")
            if intervention_id and not conn.execute(select(db.interventions.c.id).where(
                db.interventions.c.id == intervention_id, db.interventions.c.run_id == run_id,
                db.interventions.c.actor == "supervisor", db.interventions.c.action.in_(["flag", "pause"])
            )).first():
                raise NotFound("flag not found")
            review_id = intervention_id or f"missed:{run_id}"
            conn.execute(delete(db.flag_feedback).where(db.flag_feedback.c.id == review_id))
            conn.execute(db.flag_feedback.insert().values(id=review_id, run_id=run_id,
                intervention_id=intervention_id or None, user_id=user_id, verdict=verdict, updated_at=time.time()))

    def run_feedback(self, workspace_id: str, run_id: str) -> dict:
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.flag_feedback).join(db.runs).where(
                db.runs.c.workspace_id == workspace_id, db.runs.c.id == run_id)).mappings().all()
        return {r["intervention_id"] or "missed": r["verdict"] for r in rows}

    def dashboard(self, now: float | None = None, offset: int = 0) -> dict:
        now = time.time() if now is None else now
        since = now - 7 * 86400
        outside = self._outside()
        activity = (select(db.runs.c.workspace_id,
                           func.min(db.run_activity.c.first_received_at).label("first_event"),
                           func.max(db.run_activity.c.last_received_at).label("last_event"),
                           func.count().label("runs"))
                    .join(db.run_activity).group_by(db.runs.c.workspace_id).subquery())
        keys = (select(db.api_keys.c.workspace_id, func.min(db.api_keys.c.created_at).label("first_key"))
                .group_by(db.api_keys.c.workspace_id).subquery())
        flags = (select(db.runs.c.workspace_id, func.min(db.interventions.c.created_at).label("first_flag"))
                 .join(db.interventions).where(db.interventions.c.actor == "supervisor",
                                              db.interventions.c.action.in_(["flag", "pause"]))
                 .group_by(db.runs.c.workspace_id).subquery())
        workspace_query = (select(db.workspaces.c.id, db.workspaces.c.name, db.workspaces.c.created_at,
                                  db.beta_cohorts.c.kind, activity.c.first_event, activity.c.last_event,
                                  activity.c.runs, keys.c.first_key, flags.c.first_flag)
                           .outerjoin(db.beta_cohorts).outerjoin(activity).outerjoin(keys).outerjoin(flags))
        with self.engine.connect() as conn:
            started_at = conn.execute(select(db.beta_measurement.c.started_at)
                                      .where(db.beta_measurement.c.id == 1)).scalar_one()
            operator_ids = set(conn.execute(self._operator_workspaces()).scalars())
            total_workspaces = conn.execute(select(func.count()).select_from(db.workspaces)).scalar_one()
            rows = [dict(r) for r in conn.execute(workspace_query.order_by(db.workspaces.c.created_at.desc())
                                                 .limit(51).offset(offset)).mappings()]
            activated = (select(activity.c.workspace_id).where(activity.c.workspace_id.in_(outside)))
            outside_users = conn.execute(select(func.count(func.distinct(db.memberships.c.user_id)))
                .where(db.memberships.c.workspace_id.in_(activated))).scalar_one()
            funnel = dict(conn.execute(select(
                func.count().label("signups"), func.count(keys.c.first_key).label("keys"),
                func.count(activity.c.first_event).label("activated"), func.count(flags.c.first_flag).label("flagged"))
                .select_from(db.workspaces).outerjoin(keys).outerjoin(activity).outerjoin(flags)
                .where(db.workspaces.c.id.in_(outside))).mappings().one())
            active_teams, weekly_runs, measured_runs = conn.execute(select(
                func.count(func.distinct(case((db.run_activity.c.last_received_at >= since, db.runs.c.workspace_id)))),
                func.count(case((db.run_activity.c.last_received_at >= since, db.runs.c.id))),
                func.count()).select_from(db.runs).join(db.run_activity)
                .where(db.runs.c.workspace_id.in_(outside))).one()
            latencies = conn.execute(select(activity.c.first_event - db.workspaces.c.created_at)
                .select_from(db.workspaces).join(activity).where(db.workspaces.c.id.in_(outside),
                    db.workspaces.c.created_at >= started_at)).scalars().all()
            review_query = (select(db.interventions.c.rule, db.flag_feedback.c.verdict, func.count().label("count"))
                            .select_from(db.flag_feedback).join(db.runs).outerjoin(db.interventions)
                            .where(db.runs.c.workspace_id.in_(outside))
                            .group_by(db.interventions.c.rule, db.flag_feedback.c.verdict))
            reviews = [dict(r) for r in conn.execute(review_query).mappings()]
            total_flags = conn.execute(select(func.count()).select_from(db.interventions).join(db.runs)
                .where(db.runs.c.workspace_id.in_(outside), db.interventions.c.actor == "supervisor",
                       db.interventions.c.action.in_(["flag", "pause"]))).scalar_one()
        counts = {v: sum(r["count"] for r in reviews if r["verdict"] == v)
                  for v in ("useful", "too_early", "false_alarm", "missed")}
        reviewed = counts["useful"] + counts["too_early"] + counts["false_alarm"]
        for row in rows:
            row["kind"] = "internal" if row["id"] in operator_ids else row["kind"] or "unreviewed"
            row["operator"] = row["id"] in operator_ids
        return {"started_at": started_at, "activation_sample": len(latencies),
                "workspaces": rows[:50], "more": len(rows) > 50, "offset": offset,
                "total_workspaces": total_workspaces, "funnel": funnel, "outside_users": outside_users,
                "weekly_active_teams": active_teams, "weekly_runs": weekly_runs, "measured_runs": measured_runs,
                "activation_minutes": statistics.median(max(0, n) for n in latencies) / 60 if latencies else None,
                "counts": counts, "reviews": reviews, "reviewed": reviewed, "total_flags": total_flags,
                "useful_percent": round(100 * counts["useful"] / reviewed) if reviewed else None,
                "gate_met": outside_users >= 5 and measured_runs >= 50 and counts["useful"] >= 1}
