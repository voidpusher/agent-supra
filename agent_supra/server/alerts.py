"""Slack and email alerts when the supervisor pauses (or, optionally, flags) a run.

Alerts are sent from a small background pool so a slow webhook never delays
the agent. Failures are logged and dropped.
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

from ..pricing import usd
from .accounts import Accounts
from .settings import Settings

log = logging.getLogger("agent_supra.alerts")
_SLACK_PATH = re.compile(r"/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9_-]+\Z")


def valid_slack_webhook(url: str | None) -> bool:
    if not url:
        return True
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "hooks.slack.com"
                and parts.port is None and not parts.username and not parts.password
                and not parts.query and not parts.fragment
                and bool(_SLACK_PATH.fullmatch(parts.path)))
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, "Slack webhook redirected", headers, fp)


def compose(item: dict, settings: Settings) -> tuple[str, str]:
    verb = "paused" if item["action"] == "pause" else "flagged"
    link = f"{settings.base_url}/app/runs/{item['run_id']}"
    subject = f"Run {verb}: {item.get('objective', '')[:80]}"
    spend = f" Estimated spend so far: {usd(item.get('spend_micros'), 2)}." if item.get("spend_micros") is not None else ""
    body = f"{item['message']}{spend}\nRule: {item['rule']}\nReview it: {link}"
    return subject, body


class Alerter:
    def __init__(self, accounts: Accounts, settings: Settings, sender=None):
        self.accounts = accounts
        self.settings = settings
        self._pool = None if settings.serverless else ThreadPoolExecutor(max_workers=4, thread_name_prefix="alerts")
        self._send = sender if sender is not None else self._deliver

    def __call__(self, item: dict) -> None:
        if item.get("actor") != "supervisor":
            return
        if self._pool is None:
            # Complete delivery within the request; serverless instances can freeze afterward.
            self._safe(item)
        else:
            self._pool.submit(self._safe, item)

    def send_test(self, item: dict) -> None:
        """Deliver a dashboard test synchronously so errors can be reported."""
        ws = self.accounts.workspace(item["workspace_id"])
        self._send(ws, item)

    def _safe(self, item: dict) -> None:
        try:
            ws = self.accounts.workspace(item["workspace_id"])
            if item["action"] != "pause" and not ws["alert_on_flags"]:
                return
            self._send(ws, item)
        except Exception:
            log.exception("alert delivery failed for run %s", item.get("run_id"))

    def _deliver(self, ws: dict, item: dict) -> None:
        subject, body = compose(item, self.settings)
        if ws.get("slack_webhook") and valid_slack_webhook(ws["slack_webhook"]):
            send_slack(ws["slack_webhook"], f"*{subject}*\n{body}")
        if ws.get("alert_email") and self.settings.smtp_host:
            send_email(self.settings, ws["alert_email"], subject, body)

    def flush(self, timeout: float = 5.0) -> None:
        """Wait for queued alerts (used by tests and shutdown)."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alerts")


def send_slack(webhook: str, text: str) -> None:
    if not valid_slack_webhook(webhook):
        raise ValueError("invalid Slack webhook URL")
    request = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=5) as response:
        response.read()


def send_email(settings: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user or f"alerts@{settings.base_url.split('//')[-1]}"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)
