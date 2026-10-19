"""The web application: agent API, dashboard, public pages.

    uvicorn --factory agent_supra.server.app:create_app --host 0.0.0.0 --port 8000

Configuration comes from environment variables (see settings.py and the README).
"""

from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from . import api, db, web
from .accounts import Accounts
from .alerts import Alerter
from .core import Supervisor, SupervisorError
from .errors import ErrorMonitor
from .ratelimit import RateLimiter
from .settings import Settings

log = logging.getLogger("agent_supra")

CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def create_app(settings: Settings | None = None, supervisor: Supervisor | None = None,
               alert_sender=None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sup = supervisor or Supervisor(db.make_engine(settings.database_url, serverless=settings.serverless))
    accounts = Accounts(sup.engine)
    alerter = Alerter(accounts, settings, sender=alert_sender)
    monitor = ErrorMonitor(sup.engine, settings)
    sup.on_intervention = alerter
    state = SimpleNamespace(settings=settings, supervisor=sup, accounts=accounts, alerter=alerter,
                            monitor=monitor,
                            event_limiter=RateLimiter(settings.events_per_minute),
                            auth_limiter=RateLimiter(settings.auth_attempts_per_minute,
                                                     burst=settings.auth_attempts_per_minute))

    stop = threading.Event()

    def retention_loop():
        # Prune at start-up, then every 6 hours, until shutdown.
        while True:
            try:
                removed = sup.prune(settings.retention_days)
                monitor.prune(settings.retention_days)
                if removed:
                    log.info("retention: removed %d run(s) older than %d days", removed, settings.retention_days)
            except Exception:
                log.exception("retention cleanup failed")
            if stop.wait(6 * 3600):
                return

    @asynccontextmanager
    async def lifespan(_app):
        if settings.retention_days > 0 and not settings.serverless:
            threading.Thread(target=retention_loop, name="retention", daemon=True).start()
        yield
        stop.set()
        alerter.flush()

    app = FastAPI(title=settings.app_name, version=__version__, docs_url="/api/docs",
                  redoc_url=None, openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.ctx = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length")
        if request.method in ("POST", "PUT", "PATCH"):
            if length is None and request.url.path.startswith("/v1"):
                return JSONResponse({"error": "Content-Length header required"}, status_code=411)
            if length is not None and (not length.isdigit() or int(length) > settings.max_body_bytes):
                return JSONResponse({"error": "request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if settings.secure_cookies:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith(("/app", "/v1", "/login", "/signup")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/v1") or "application/json" in request.headers.get("accept", "")

    @app.exception_handler(SupervisorError)
    async def supervisor_error(request: Request, exc: SupervisorError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"error": f"invalid request: {where} {first.get('msg', '')}".strip()}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if wants_json(request):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        if exc.status_code == 404:
            return PlainTextResponse("Not found", status_code=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        # A route pattern avoids storing user-supplied URL segments. Never log
        # exception messages here; they may contain credentials or user data.
        route = request.scope.get("route")
        path = getattr(route, "path", "/unmatched")
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, path)
        try:
            monitor.record(exc, path)
        except Exception:
            log.error("server error monitoring failed")
        return JSONResponse({"error": "internal error"}, status_code=500)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        with sup.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "version": __version__}

    @app.get("/api/cron/prune", include_in_schema=False)
    def prune_expired(request: Request):
        expected = f"Bearer {settings.cron_secret}"
        if not settings.cron_secret or not secrets.compare_digest(
                request.headers.get("authorization", "").encode(), expected.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        removed = sup.prune(settings.retention_days) if settings.retention_days > 0 else 0
        monitor.prune(settings.retention_days)
        return JSONResponse({"ok": True, "removed": removed}, headers={"Cache-Control": "no-store"})

    app.include_router(api.build_router(state))
    app.include_router(web.build_router(state))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    return app
