"""Server settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    database_url: str = "sqlite:///supervisor.db"
    base_url: str = "http://localhost:8000"
    app_name: str = "Agent Supra"
    allow_signup: bool = True
    secure_cookies: bool = False
    retention_days: int = 30
    events_per_minute: int = 1200       # per API key
    auth_attempts_per_minute: int = 10  # per IP, login + signup
    max_body_bytes: int = 1_000_000
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    sentry_dsn: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        return cls(
            database_url=os.environ.get("DATABASE_URL", os.environ.get("AGENT_SUPRA_DB_URL",
                                                                       "sqlite:///supervisor.db")),
            base_url=base_url,
            app_name=os.environ.get("APP_NAME", "Agent Supra"),
            allow_signup=_bool("ALLOW_SIGNUP", True),
            secure_cookies=_bool("SECURE_COOKIES", base_url.startswith("https://")),
            retention_days=int(os.environ.get("RETENTION_DAYS", "30")),
            events_per_minute=int(os.environ.get("RATE_LIMIT_EVENTS_PER_MIN", "1200")),
            auth_attempts_per_minute=int(os.environ.get("RATE_LIMIT_AUTH_PER_MIN", "10")),
            max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", "1000000")),
            smtp_host=os.environ.get("SMTP_HOST") or None,
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER") or None,
            smtp_password=os.environ.get("SMTP_PASSWORD") or None,
            smtp_from=os.environ.get("SMTP_FROM") or None,
            sentry_dsn=os.environ.get("SENTRY_DSN") or None,
        )
