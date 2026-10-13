"""Users, workspaces, sessions and API keys.

* Passwords: scrypt with a random salt, compared in constant time.
* Sessions and API keys: random tokens; only their SHA-256 hash is stored, so a
  database leak doesn't hand out working credentials.
* API keys are shown once at creation. They look like ``as_live_<prefix>_<secret>``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import db
from .core import NotFound, SupervisorError, new_id

SESSION_DAYS = 14
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(check, _unb64(digest))


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise SupervisorError("password must be at least 10 characters")
    if len(password) > 200:
        raise SupervisorError("password is too long")


@dataclass
class Session:
    user_id: str
    email: str
    workspace_id: str
    workspace_name: str
    csrf: str


@dataclass
class KeyAuth:
    key_id: str
    workspace_id: str
    name: str


class Accounts:
    def __init__(self, engine: Engine):
        self.engine = engine
        # Same password-hash cost as a real login, so unknown emails aren't faster.
        self._dummy_hash = hash_password(secrets.token_urlsafe(12))

    # ------------------------------------------------------------------ users
    def signup(self, email: str, password: str, workspace_name: str | None = None) -> tuple[str, str]:
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise SupervisorError("enter a valid email address")
        validate_password(password)
        user_id, ws_id, now = new_id("usr"), new_id("ws"), time.time()
        try:
            with self.engine.begin() as conn:
                conn.execute(db.users.insert().values(id=user_id, email=email,
                                                      password_hash=hash_password(password), created_at=now))
                conn.execute(db.workspaces.insert().values(
                    id=ws_id, name=(workspace_name or email.split("@")[0])[:200], default_policy="{}",
                    prices="{}", alert_on_flags=False, created_at=now))
                conn.execute(db.memberships.insert().values(user_id=user_id, workspace_id=ws_id, role="owner"))
        except IntegrityError:
            raise SupervisorError("an account with this email already exists")
        return user_id, ws_id

    def authenticate(self, email: str, password: str) -> str | None:
        email = (email or "").strip().lower()
        with self.engine.connect() as conn:
            row = conn.execute(select(db.users).where(db.users.c.email == email)).first()
        if row is None:
            verify_password(password or "", self._dummy_hash)
            return None
        return row.id if verify_password(password or "", row.password_hash) else None

    def change_password(self, user_id: str, current: str, new: str) -> None:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.users).where(db.users.c.id == user_id)).first()
        if row is None or not verify_password(current, row.password_hash):
            raise SupervisorError("current password is wrong")
        validate_password(new)
        with self.engine.begin() as conn:
            conn.execute(update(db.users).where(db.users.c.id == user_id).values(password_hash=hash_password(new)))
            conn.execute(delete(db.sessions).where(db.sessions.c.user_id == user_id))

    # ------------------------------------------------------------------ sessions
    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(delete(db.sessions).where(db.sessions.c.expires_at < time.time()))
            conn.execute(db.sessions.insert().values(
                token_hash=sha256(token), user_id=user_id, csrf=secrets.token_urlsafe(24),
                expires_at=time.time() + SESSION_DAYS * 86400))
        return token

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        q = (select(db.sessions.c.user_id, db.sessions.c.csrf, db.sessions.c.expires_at, db.users.c.email,
                    db.workspaces.c.id.label("ws_id"), db.workspaces.c.name.label("ws_name"))
             .join(db.users, db.users.c.id == db.sessions.c.user_id)
             .join(db.memberships, db.memberships.c.user_id == db.users.c.id)
             .join(db.workspaces, db.workspaces.c.id == db.memberships.c.workspace_id)
             .where(db.sessions.c.token_hash == sha256(token)))
        with self.engine.connect() as conn:
            row = conn.execute(q).first()
        if row is None or row.expires_at < time.time():
            return None
        return Session(row.user_id, row.email, row.ws_id, row.ws_name, row.csrf)

    def end_session(self, token: str | None) -> None:
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(db.sessions).where(db.sessions.c.token_hash == sha256(token)))

    # ------------------------------------------------------------------ API keys
    def create_key(self, workspace_id: str, name: str) -> tuple[str, str]:
        name = (name or "").strip()[:200] or "API key"
        prefix = secrets.token_hex(4)
        token = f"as_live_{prefix}_{secrets.token_urlsafe(32)}"
        key_id = new_id("key")
        with self.engine.begin() as conn:
            conn.execute(db.api_keys.insert().values(
                id=key_id, workspace_id=workspace_id, name=name, prefix=f"as_live_{prefix}",
                key_hash=sha256(token), created_at=time.time()))
        return key_id, token

    def authenticate_key(self, token: str | None) -> KeyAuth | None:
        if not token or not token.startswith("as_live_"):
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(db.api_keys).where(and_(
                db.api_keys.c.key_hash == sha256(token), db.api_keys.c.revoked_at.is_(None)))).first()
        if row is None:
            return None
        if not row.last_used_at or time.time() - row.last_used_at > 300:  # avoid a write per request
            with self.engine.begin() as conn:
                conn.execute(update(db.api_keys).where(db.api_keys.c.id == row.id).values(last_used_at=time.time()))
        return KeyAuth(row.id, row.workspace_id, row.name)

    def list_keys(self, workspace_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.api_keys).where(db.api_keys.c.workspace_id == workspace_id)
                                .order_by(db.api_keys.c.created_at.desc())).all()
        return [{k: v for k, v in r._asdict().items() if k != "key_hash"} for r in rows]

    def revoke_key(self, workspace_id: str, key_id: str) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(update(db.api_keys).where(and_(
                db.api_keys.c.id == key_id, db.api_keys.c.workspace_id == workspace_id,
                db.api_keys.c.revoked_at.is_(None))).values(revoked_at=time.time()))
        if result.rowcount == 0:
            raise NotFound("unknown key")

    # ------------------------------------------------------------------ workspace settings
    def workspace(self, workspace_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.workspaces).where(db.workspaces.c.id == workspace_id)).first()
        if row is None:
            raise NotFound("unknown workspace")
        data = row._asdict()
        data["default_policy"] = json.loads(data["default_policy"] or "{}")
        data["prices"] = json.loads(data["prices"] or "{}")
        return data

    def update_workspace(self, workspace_id: str, **values) -> None:
        allowed = {"name", "default_policy", "prices", "slack_webhook", "alert_email", "alert_on_flags"}
        clean = {}
        for key, value in values.items():
            if key not in allowed:
                raise SupervisorError(f"unknown setting {key}")
            clean[key] = json.dumps(value) if key in ("default_policy", "prices") else value
        with self.engine.begin() as conn:
            conn.execute(update(db.workspaces).where(db.workspaces.c.id == workspace_id).values(**clean))

    def delete_workspace(self, workspace_id: str, user_id: str) -> None:
        """Delete the workspace, all its data and the user. Irreversible."""
        with self.engine.begin() as conn:
            run_ids = select(db.runs.c.id).where(db.runs.c.workspace_id == workspace_id).scalar_subquery()
            conn.execute(delete(db.events).where(db.events.c.run_id.in_(run_ids)))
            conn.execute(delete(db.interventions).where(db.interventions.c.run_id.in_(run_ids)))
            conn.execute(delete(db.runs).where(db.runs.c.workspace_id == workspace_id))
            conn.execute(delete(db.tasks).where(db.tasks.c.workspace_id == workspace_id))
            conn.execute(delete(db.api_keys).where(db.api_keys.c.workspace_id == workspace_id))
            conn.execute(delete(db.memberships).where(db.memberships.c.workspace_id == workspace_id))
            conn.execute(delete(db.workspaces).where(db.workspaces.c.id == workspace_id))
            conn.execute(delete(db.sessions).where(db.sessions.c.user_id == user_id))
            conn.execute(delete(db.users).where(db.users.c.id == user_id))
