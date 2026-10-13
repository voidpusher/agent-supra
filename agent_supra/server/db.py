"""Database schema and connection. Works on PostgreSQL (production) and SQLite (local).

Set ``DATABASE_URL``, e.g. ``postgresql+psycopg://user:pass@host/db`` or
``sqlite:///supervisor.db``. ``postgres://`` URLs from hosting providers are
accepted and rewritten for psycopg.
"""

from __future__ import annotations

import time

from sqlalchemy import (BigInteger, Boolean, Column, Float, ForeignKey, Index, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine, event, select, text)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, StaticPool

SCHEMA_VERSION = 1

metadata = MetaData()

schema_info = Table(
    "schema_info", metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("applied_at", Float, nullable=False),
)

users = Table(
    "users", metadata,
    Column("id", String(40), primary_key=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("password_hash", String(300), nullable=False),
    Column("created_at", Float, nullable=False),
)

workspaces = Table(
    "workspaces", metadata,
    Column("id", String(40), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("default_policy", Text, nullable=False, default="{}"),
    Column("prices", Text, nullable=False, default="{}"),
    Column("slack_webhook", Text),
    Column("alert_email", String(320)),
    Column("alert_on_flags", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
)

memberships = Table(
    "memberships", metadata,
    Column("user_id", String(40), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("workspace_id", String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(20), nullable=False, default="owner"),
)

api_keys = Table(
    "api_keys", metadata,
    Column("id", String(40), primary_key=True),
    Column("workspace_id", String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("prefix", String(32), nullable=False),
    Column("key_hash", String(64), nullable=False, unique=True),
    Column("created_at", Float, nullable=False),
    Column("last_used_at", Float),
    Column("revoked_at", Float),
)

sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(40), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf", String(64), nullable=False),
    Column("expires_at", Float, nullable=False),
)

tasks = Table(
    "tasks", metadata,
    Column("id", String(40), primary_key=True),
    Column("workspace_id", String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("objective", Text, nullable=False),
    Column("budget_micros", BigInteger),
    Column("max_calls", Integer),
    Column("policy", Text, nullable=False, default="{}"),
    Column("created_at", Float, nullable=False),
    Index("ix_tasks_ws_created", "workspace_id", "created_at"),
)

runs = Table(
    "runs", metadata,
    Column("id", String(40), primary_key=True),
    Column("workspace_id", String(40), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("task_id", String(40), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("parent_run_id", String(40)),
    Column("attempt", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("reason", Text),
    Column("outcome", String(20)),
    Column("baseline_seq", Integer, nullable=False, default=0),
    Column("spend_micros", BigInteger, nullable=False, default=0),
    Column("model_calls", Integer, nullable=False, default=0),
    Column("unpriced_calls", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Index("ix_runs_ws_created", "workspace_id", "created_at"),
    Index("ix_runs_task", "task_id"),
)

events = Table(
    "events", metadata,
    Column("run_id", String(40), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("event_id", String(64), primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("type", String(40), nullable=False),
    Column("ts", Float, nullable=False),
    Column("data", Text, nullable=False),
    Index("ix_events_run_seq", "run_id", "seq"),
)

interventions = Table(
    "interventions", metadata,
    Column("id", String(40), primary_key=True),
    Column("run_id", String(40), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("rule", String(40), nullable=False),
    Column("action", String(20), nullable=False),
    Column("message", Text),
    Column("evidence", Text, nullable=False, default="[]"),
    Column("confidence", String(10), nullable=False, default="high"),
    Column("actor", String(320), nullable=False),
    Column("reason", Text),
    Column("dedup_key", String(200)),
    Column("spend_micros", BigInteger),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("run_id", "dedup_key", name="uq_intervention_dedup"),
    Index("ix_interventions_run", "run_id", "created_at"),
)


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str, *, serverless: bool = False) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
        return engine
    if serverless:
        return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def migrate(engine: Engine) -> int:
    """Create missing tables and record the schema version. Safe to run on every start."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            # Serialize cold-start schema setup across serverless instances.
            conn.execute(text("SELECT pg_advisory_xact_lock(734829105)"))
        metadata.create_all(conn)
        row = conn.execute(select(schema_info.c.version).where(schema_info.c.id == 1)).first()
        if row is None:
            conn.execute(schema_info.insert().values(id=1, version=SCHEMA_VERSION, applied_at=time.time()))
        elif row.version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema v{row.version} is newer than this code (v{SCHEMA_VERSION})")
        # Future migrations: `if row.version < 2: ...` then update the version.
    return SCHEMA_VERSION


def insert_ignore(conn, engine: Engine, table: Table, **values) -> bool:
    """INSERT that does nothing on a primary-key/unique conflict. Returns True if a row
    was written. Uses RETURNING because drivers disagree on rowcount here."""
    dialect = postgresql if engine.dialect.name == "postgresql" else sqlite
    key = list(table.primary_key.columns)[0]
    stmt = dialect.insert(table).values(**values).on_conflict_do_nothing().returning(key)
    return conn.execute(stmt).first() is not None
