"""Small first-party inbox for unexpected server errors.

Store only error type, route pattern and stack locations. Exception messages,
request data, headers and local variables can contain secrets, so they never
enter the monitoring table or its alerts.
"""

from __future__ import annotations

import hashlib
import logging
import time
import traceback
from pathlib import Path

from sqlalchemy import and_, case, delete, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine

from . import db
from .alerts import send_email, send_slack, valid_slack_webhook
from .settings import Settings

log = logging.getLogger("agent_supra.errors")


class ErrorMonitor:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings

    def record(self, exc: Exception, route: str) -> dict:
        kind = type(exc).__name__[:120]
        path = route[:200]
        frames = traceback.extract_tb(exc.__traceback__)[-12:]
        locations = [f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
                     for frame in frames]
        stack = "\n".join(locations)[:4000] or "Stack unavailable"
        fingerprint = hashlib.sha256(f"{kind}\n{path}\n{stack}".encode()).hexdigest()
        now = time.time()
        dialect = postgresql if self.engine.dialect.name == "postgresql" else sqlite
        insert = dialect.insert(db.errors).values(
            fingerprint=fingerprint, exception_type=kind, path=path, stack=stack,
            occurrences=1, first_seen=now, last_seen=now, last_alert_attempt=now)
        retry_due = and_(db.errors.c.notified_at.is_(None),
                         db.errors.c.last_alert_attempt < now - 300)
        upsert = insert.on_conflict_do_update(
            index_elements=[db.errors.c.fingerprint],
            set_={"occurrences": db.errors.c.occurrences + 1, "last_seen": now,
                  "last_alert_attempt": case((retry_due, now),
                                             else_=db.errors.c.last_alert_attempt)},
        ).returning(db.errors.c.occurrences, db.errors.c.last_alert_attempt)
        with self.engine.begin() as conn:
            occurrences, alert_attempt = conn.execute(upsert).one()
        item = {"fingerprint": fingerprint, "exception_type": kind, "path": path,
                "stack": stack, "occurrences": occurrences, "last_seen": now}
        if alert_attempt == now and self._notify(item):
            with self.engine.begin() as conn:
                conn.execute(update(db.errors).where(db.errors.c.fingerprint == fingerprint)
                             .values(notified_at=now))
        return item

    def list_recent(self, limit: int = 100) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(
                select(db.errors).order_by(db.errors.c.last_seen.desc()).limit(limit)
            ).mappings()]

    def prune(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        with self.engine.begin() as conn:
            result = conn.execute(delete(db.errors).where(
                db.errors.c.last_seen < time.time() - retention_days * 86400))
            return result.rowcount

    def _notify(self, item: dict) -> bool:
        """Best-effort delivery; an alert failure must not mask the original 500."""
        settings = self.settings
        subject = f"Agent Supra server error: {item['exception_type']}"
        body = (f"A new server error occurred on {item['path']}.\n"
                f"Stack locations:\n{item['stack']}\n\n"
                f"Review: {settings.base_url}/app/errors")
        email = settings.contact_email
        webhook = None
        delivered = False
        try:
            with self.engine.connect() as conn:
                operator_ws = conn.execute(
                    select(db.workspaces.c.alert_email, db.workspaces.c.slack_webhook)
                    .select_from(db.workspaces.join(db.memberships).join(db.users))
                    .where(db.users.c.email == settings.contact_email.lower())
                    .limit(1)
                ).first()
            if operator_ws:
                email = operator_ws.alert_email or email
                webhook = operator_ws.slack_webhook
        except Exception:
            log.warning("could not read operator alert settings")
        if webhook and valid_slack_webhook(webhook):
            try:
                send_slack(webhook, f"*{subject}*\n{body}")
                delivered = True
            except Exception:
                log.warning("server error Slack alert failed")
        if email and settings.smtp_host:
            try:
                send_email(settings, email, subject, body)
                delivered = True
            except Exception:
                log.warning("server error email alert failed")
        return delivered
