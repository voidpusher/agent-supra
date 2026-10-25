"""Dashboard and public pages (server-rendered, cookie sessions, CSRF-checked forms)."""

from __future__ import annotations

import logging
import math
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import pricing
from ..detector import HEURISTIC_RULES, merge_policy
from ..present import RULE_HELP, describe_event, rule_label
from .accounts import Session
from .alerts import compose, send_email, valid_slack_webhook
from .beta import Beta
from .core import SupervisorError

SESSION_COOKIE = "as_session"
PRE_CSRF_COOKIE = "as_csrf"


class MonitoringSelfTest(Exception):
    """Operator-triggered error for checking the production capture path."""


NOTICES = {
    "saved": ("success", "Settings saved."),
    "continued": ("success", "Run continued."),
    "paused": ("success", "Run paused."),
    "stopped": ("success", "Run stopped."),
    "reviewed": ("success", "Feedback saved. Thank you for helping improve detection."),
    "cohort-saved": ("success", "Beta classification saved."),
    "revoked": ("success", "Key revoked. Agents using it will get a 401 on their next request."),
    "password": ("success", "Password changed. Other sessions were logged out."),
    "alert-sent": ("success", "Test alert delivered to configured channels."),
    "alert-sent-slack-only": ("success", "Slack test alert delivered. Email requires SMTP settings on the server."),
    "alert-none": ("error", "Add a Slack webhook or alert email first, then save."),
}


def _ago(ts: float | None) -> str:
    if not ts:
        return ""
    seconds = max(0, time.time() - ts)
    for limit, unit, size in ((60, "s", 1), (3600, "min", 60), (86400, "h", 3600), (86400 * 30, "d", 86400)):
        if seconds < limit:
            return f"{int(seconds // size)} {unit} ago" if unit != "s" else "just now"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y")


def _datetime(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y, %H:%M UTC") if ts else ""


def build_router(state) -> APIRouter:
    router = APIRouter()
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    env = templates.env
    env.filters.update({
        "usd2": lambda m: pricing.usd(m, 2) if m is None or m >= 10_000 or m == 0 else pricing.usd(m, 4),
        "ago": _ago, "datetime": _datetime, "rule_label": rule_label,
        "describe": lambda e: Markup(describe_event(e)),
    })
    settings, sup, accounts = state.settings, state.supervisor, state.accounts
    beta = Beta(sup.engine, settings.contact_email)

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("session", getattr(request.state, "session", None))
        session = ctx["session"]
        ctx.setdefault("operator", bool(session and session.email.casefold() == settings.contact_email.casefold()))
        code = request.query_params.get("notice")
        if code in NOTICES and "notice" not in ctx:
            kind, text = NOTICES[code]
            ctx["notice"] = {"kind": kind, "text": text}
        return templates.TemplateResponse(request, name, {
            "app_name": settings.app_name, "base_url": settings.base_url,
            "contact_email": settings.contact_email,
            "allow_signup": settings.allow_signup, **ctx}, status_code=status)

    def current(request: Request) -> Session | None:
        session = accounts.session(request.cookies.get(SESSION_COOKIE))
        request.state.session = session
        return session

    def login_redirect() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    def check_csrf(session: Session, token: str) -> bool:
        return bool(token) and secrets.compare_digest(session.csrf, token)

    def forbidden(request: Request) -> HTMLResponse:
        return page(request, "error.html", 403, title="That form expired",
                    message="Reload the page and try again.")

    def set_session(response, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, max_age=14 * 86400, httponly=True,
                            secure=settings.secure_cookies, samesite="lax", path="/")

    def auth_limited(request: Request) -> bool:
        ip = request.client.host if request.client else "unknown"
        return not state.auth_limiter.allow(f"auth:{ip}")

    # ------------------------------------------------------------------ public
    @router.get("/terms", response_class=HTMLResponse)
    def terms(request: Request):
        current(request)
        return page(request, "terms.html")

    @router.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        current(request)
        return page(request, "landing.html")

    @router.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        current(request)
        return page(request, "privacy.html", retention_days=settings.retention_days)

    @router.get("/docs/quickstart", response_class=HTMLResponse)
    def quickstart(request: Request):
        current(request)
        return page(request, "quickstart.html", nav="docs")

    def auth_page(request: Request, mode: str, error: str | None = None, email: str = "", status: int = 200):
        token = request.cookies.get(PRE_CSRF_COOKIE) or secrets.token_urlsafe(24)
        response = page(request, "auth.html", status, mode=mode, error=error, email=email, csrf=token,
                        heading="Create your account" if mode == "signup" else "Log in",
                        button="Create account" if mode == "signup" else "Log in",
                        action=f"/{mode}")
        response.set_cookie(PRE_CSRF_COOKIE, token, max_age=3600, httponly=True,
                            secure=settings.secure_cookies, samesite="strict", path="/")
        return response

    def pre_csrf_ok(request: Request, token: str) -> bool:
        cookie = request.cookies.get(PRE_CSRF_COOKIE)
        return bool(cookie and token) and secrets.compare_digest(cookie, token)

    @router.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if current(request):
            return RedirectResponse("/app", status_code=303)
        return auth_page(request, "login")

    @router.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form(""), csrf: str = Form("")):
        request.state.session = None
        if not pre_csrf_ok(request, csrf):
            return auth_page(request, "login", "That form expired. Try again.", email, 403)
        if auth_limited(request):
            return auth_page(request, "login", "Too many attempts. Wait a minute and try again.", email, 429)
        user_id = accounts.authenticate(email, password)
        if not user_id:
            return auth_page(request, "login", "That email and password don't match an account.", email, 401)
        response = RedirectResponse("/app", status_code=303)
        set_session(response, accounts.create_session(user_id))
        return response

    def recovery_page(request: Request, mode: str, *, token: str = "", message: str = "",
                      status: int = 200):
        csrf = request.cookies.get(PRE_CSRF_COOKIE) or secrets.token_urlsafe(24)
        response = page(request, "password_reset.html", status, mode=mode, token=token,
                        message=message, csrf=csrf)
        response.set_cookie(PRE_CSRF_COOKIE, csrf, max_age=3600, httponly=True,
                            secure=settings.secure_cookies, samesite="strict", path="/")
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password_form(request: Request):
        return recovery_page(request, "request")

    @router.post("/forgot-password", response_class=HTMLResponse)
    def forgot_password(request: Request, email: str = Form(""), csrf: str = Form("")):
        if not pre_csrf_ok(request, csrf):
            return recovery_page(request, "request", message="That form expired. Try again.", status=403)
        if auth_limited(request):
            return recovery_page(request, "request", message="Too many attempts. Wait a minute.", status=429)
        token = accounts.issue_password_reset(email)
        if token and settings.smtp_host:
            try:
                send_email(settings, email.strip().lower(), "Reset your Agent Supra password",
                           f"Open this link within 30 minutes to reset your password:\n"
                           f"{settings.base_url}/reset-password?token={token}\n\n"
                           "If you did not request this, you can ignore this email.")
            except Exception:
                logging.getLogger("agent_supra.web").warning("password reset email delivery failed")
        return recovery_page(request, "sent")

    @router.get("/reset-password", response_class=HTMLResponse)
    def reset_password_form(request: Request, token: str = ""):
        return recovery_page(request, "reset", token=token[:200])

    @router.post("/reset-password", response_class=HTMLResponse)
    def reset_password(request: Request, token: str = Form(""), password: str = Form(""),
                       csrf: str = Form("")):
        if not pre_csrf_ok(request, csrf):
            return recovery_page(request, "reset", token=token[:200],
                                 message="That form expired. Try again.", status=403)
        if auth_limited(request):
            return recovery_page(request, "reset", token=token[:200],
                                 message="Too many attempts. Wait a minute.", status=429)
        try:
            reset = accounts.reset_password(token, password)
        except SupervisorError as exc:
            return recovery_page(request, "reset", token=token[:200],
                                 message=str(exc).capitalize() + ".", status=400)
        if not reset:
            return recovery_page(request, "reset", token="",
                                 message="This link is invalid or expired. Request a new one.", status=400)
        response = recovery_page(request, "complete")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request):
        if current(request):
            return RedirectResponse("/app", status_code=303)
        if not settings.allow_signup:
            return page(request, "error.html", 403, title="Sign-ups are closed",
                        message="This server isn't accepting new accounts right now.")
        return auth_page(request, "signup")

    @router.post("/signup")
    def signup(request: Request, email: str = Form(""), password: str = Form(""), csrf: str = Form("")):
        request.state.session = None
        if not settings.allow_signup:
            return page(request, "error.html", 403, title="Sign-ups are closed",
                        message="This server isn't accepting new accounts right now.")
        if not pre_csrf_ok(request, csrf):
            return auth_page(request, "signup", "That form expired. Try again.", email, 403)
        if auth_limited(request):
            return auth_page(request, "signup", "Too many attempts. Wait a minute and try again.", email, 429)
        if email.strip().casefold() == settings.contact_email.casefold():
            return auth_page(request, "signup", "This address is reserved for the site operator.", email, 403)
        try:
            user_id, _ = accounts.signup(email, password)
        except SupervisorError as exc:
            return auth_page(request, "signup", str(exc).capitalize() + ".", email, 400)
        response = RedirectResponse("/app/keys", status_code=303)
        set_session(response, accounts.create_session(user_id))
        return response

    @router.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        session = current(request)
        if session and check_csrf(session, csrf):
            accounts.end_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ------------------------------------------------------------------ runs
    @router.get("/app", response_class=HTMLResponse)
    def runs_page(request: Request, state_: str = "", offset: int = 0):
        session = current(request)
        if not session:
            return login_redirect()
        state_filter = request.query_params.get("state", "")
        if state_filter not in ("", "paused", "running", "completed", "failed", "stopped"):
            state_filter = ""
        offset = max(0, offset)
        rows = sup.runs(workspace_id=session.workspace_id, state=state_filter or None, limit=101, offset=offset)
        paused = len(sup.runs(workspace_id=session.workspace_id, state="paused", limit=500))
        return page(request, "runs.html", nav="runs", runs=rows[:100], more=len(rows) > 100,
                    next_offset=offset + 100, state=state_filter, paused_count=paused,
                    stats=sup.stats(workspace_id=session.workspace_id))

    @router.get("/app/beta", response_class=HTMLResponse)
    def beta_page(request: Request, offset: int = 0):
        session = current(request)
        if not session:
            return login_redirect()
        if session.email.casefold() != settings.contact_email.casefold():
            return page(request, "error.html", 403, title="Access denied",
                        message="Only the site operator can view beta measurements.")
        return page(request, "beta.html", nav="beta", metrics=beta.dashboard(offset=max(0, offset)))

    @router.post("/app/beta/cohort")
    def beta_cohort(request: Request, workspace_id: str = Form(""), kind: str = Form(""),
                    csrf: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if session.email.casefold() != settings.contact_email.casefold():
            return page(request, "error.html", 403, title="Access denied",
                        message="Only the site operator can classify beta workspaces.")
        if not check_csrf(session, csrf):
            return forbidden(request)
        try:
            beta.classify(workspace_id, kind)
        except SupervisorError as exc:
            return page(request, "error.html", exc.status, title="Classification not saved", message=str(exc))
        return RedirectResponse("/app/beta?notice=cohort-saved", status_code=303)

    @router.post("/app/runs/{run_id}/feedback")
    def run_feedback(request: Request, run_id: str, intervention_id: str = Form(""),
                     verdict: str = Form(""), csrf: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        try:
            beta.feedback(session.workspace_id, session.user_id, run_id, intervention_id, verdict)
        except SupervisorError as exc:
            return page(request, "error.html", exc.status, title="Feedback not saved", message=str(exc))
        return RedirectResponse(f"/app/runs/{run_id}?notice=reviewed", status_code=303)

    @router.get("/app/errors", response_class=HTMLResponse)
    def errors_page(request: Request):
        session = current(request)
        if not session:
            return login_redirect()
        if session.email.casefold() != settings.contact_email.casefold():
            return page(request, "error.html", 403, title="Access denied",
                        message="Only the site operator can view server errors.")
        return page(request, "errors.html", nav="errors", errors=state.monitor.list_recent())

    @router.post("/app/errors/test")
    def test_error_monitor(request: Request, csrf: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if session.email.casefold() != settings.contact_email.casefold():
            return page(request, "error.html", 403, title="Access denied",
                        message="Only the site operator can test server errors.")
        if not check_csrf(session, csrf):
            return forbidden(request)
        raise MonitoringSelfTest()

    @router.get("/app/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        session = current(request)
        if not session:
            return login_redirect()
        try:
            tl = sup.timeline(run_id, workspace_id=session.workspace_id)
        except SupervisorError:
            return page(request, "error.html", 404, title="Run not found",
                        message="It may have been deleted, or it belongs to another workspace.")
        running = 0
        events = []
        for e in tl["events"]:
            cost = e["data"].get("cost_micros") if e["type"] == "model.call" else None
            running += int(cost or 0)
            is_call = e["type"] == "model.call"
            events.append({**e, "cost": (pricing.usd(cost) if is_call else ""),
                           "running": pricing.usd(running) if is_call else ""})
        evidence = {eid for i in tl["interventions"] for eid in i["evidence"]}
        return page(request, "run.html", nav="runs", run=tl["run"], task=tl["task"], events=events,
                    interventions=tl["interventions"], evidence=evidence, task_spend=tl["task_spend"],
                    feedback=beta.run_feedback(session.workspace_id, run_id))

    def run_action(request: Request, run_id: str, csrf: str, fn, notice: str):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        try:
            fn(session)
        except SupervisorError as exc:
            return page(request, "error.html", exc.status, title="That didn't work",
                        message=str(exc).capitalize() + ".")
        return RedirectResponse(f"/app/runs/{run_id}?notice={notice}", status_code=303)

    @router.post("/app/runs/{run_id}/continue")
    def continue_run(request: Request, run_id: str, csrf: str = Form(""), reason: str = Form(""),
                     add_budget_usd: float = Form(0.0)):
        return run_action(request, run_id, csrf, lambda s: sup.continue_run(
            run_id, s.email, reason, add_budget_usd, workspace_id=s.workspace_id), "continued")

    @router.post("/app/runs/{run_id}/pause")
    def pause_run(request: Request, run_id: str, csrf: str = Form(""), reason: str = Form("")):
        return run_action(request, run_id, csrf, lambda s: sup.pause(
            run_id, s.email, reason, workspace_id=s.workspace_id), "paused")

    @router.post("/app/runs/{run_id}/stop")
    def stop_run(request: Request, run_id: str, csrf: str = Form(""), reason: str = Form("")):
        return run_action(request, run_id, csrf, lambda s: sup.stop(
            run_id, s.email, reason, workspace_id=s.workspace_id), "stopped")

    # ------------------------------------------------------------------ keys
    @router.get("/app/keys", response_class=HTMLResponse)
    def keys_page(request: Request):
        session = current(request)
        if not session:
            return login_redirect()
        return page(request, "keys.html", nav="keys", keys=accounts.list_keys(session.workspace_id))

    @router.post("/app/keys", response_class=HTMLResponse)
    def create_key(request: Request, csrf: str = Form(""), name: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        _, token = accounts.create_key(session.workspace_id, name)
        response = page(request, "keys.html", nav="keys", new_key=token,
                        keys=accounts.list_keys(session.workspace_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/app/keys/{key_id}/revoke")
    def revoke_key(request: Request, key_id: str, csrf: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        try:
            accounts.revoke_key(session.workspace_id, key_id)
        except SupervisorError:
            pass
        return RedirectResponse("/app/keys?notice=revoked", status_code=303)

    # ------------------------------------------------------------------ settings
    def settings_page(request: Request, session: Session, error: str | None = None, status: int = 200):
        ws = accounts.workspace(session.workspace_id)
        prices_text = "\n".join(f"{m} {v[0] if isinstance(v, list) else v['input']} "
                                f"{v[1] if isinstance(v, list) else v['output']}" for m, v in ws["prices"].items())
        return page(request, "settings.html", status, nav="settings", ws=ws,
                    policy=merge_policy(ws["default_policy"]), rules=RULE_HELP,
                    prices_text=prices_text, smtp=bool(settings.smtp_host),
                    **({"notice": {"kind": "error", "text": error}} if error else {}))

    @router.get("/app/settings", response_class=HTMLResponse)
    def settings_get(request: Request):
        session = current(request)
        if not session:
            return login_redirect()
        return settings_page(request, session)

    @router.post("/app/settings", response_class=HTMLResponse)
    async def settings_post(request: Request):
        session = current(request)
        if not session:
            return login_redirect()
        form = await request.form()
        if not check_csrf(session, str(form.get("csrf", ""))):
            return forbidden(request)
        try:
            threshold = int(form.get("threshold") or 4)
            if not 2 <= threshold <= 50:
                raise ValueError
        except ValueError:
            return settings_page(request, session, "Attempts before a flag must be a whole number from 2 to 50.", 400)
        ceiling_raw = str(form.get("context_token_ceiling") or "").strip()
        try:
            ceiling = int(ceiling_raw) if ceiling_raw else None
            if ceiling is not None and ceiling < 1000:
                raise ValueError
        except ValueError:
            return settings_page(request, session, "The context ceiling must be at least 1,000 tokens, or empty.", 400)
        rules = {}
        for rule, _ in RULE_HELP:
            value = str(form.get(f"rule_{rule}", ""))
            if value in ("pause", "observe", "off"):
                rules[rule] = value
        policy = {"observation_mode": form.get("observation_mode") == "1", "threshold": threshold,
                  "context_token_ceiling": ceiling, "rules": rules}
        prices = {}
        for number, line in enumerate(str(form.get("prices", "")).splitlines(), start=1):
            parts = line.split()
            if not parts:
                continue
            try:
                model, inp, out = parts[0], float(parts[1]), float(parts[2])
                if len(parts) != 3 or not all(math.isfinite(v) and v >= 0 for v in (inp, out)):
                    raise ValueError
            except (ValueError, IndexError):
                return settings_page(request, session, f"Price line {number} should look like: my-model 1.25 10", 400)
            prices[pricing.normalize(model)] = [inp, out]
        slack = str(form.get("slack_webhook", "")).strip() or None
        if not valid_slack_webhook(slack):
            return settings_page(request, session, "Enter a Slack incoming webhook URL under https://hooks.slack.com/services/.", 400)
        email = str(form.get("alert_email", "")).strip() or None
        if email and ("@" not in email or len(email) > 320):
            return settings_page(request, session, "Enter a valid alert email, or leave it empty.", 400)
        name = str(form.get("name", "")).strip()[:200] or session.workspace_name
        accounts.update_workspace(session.workspace_id, name=name, default_policy=policy, prices=prices,
                                  slack_webhook=slack, alert_email=email,
                                  alert_on_flags=form.get("alert_on_flags") == "1")
        return RedirectResponse("/app/settings?notice=saved", status_code=303)

    @router.post("/app/settings/test-alert")
    def test_alert(request: Request, csrf: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        ws = accounts.workspace(session.workspace_id)
        if not ws.get("slack_webhook") and not ws.get("alert_email"):
            return RedirectResponse("/app/settings?notice=alert-none", status_code=303)
        if ws.get("alert_email") and not settings.smtp_host and not ws.get("slack_webhook"):
            return settings_page(request, session, "Email alerts need SMTP settings on the server.", 400)
        sample = {"run_id": "run_example", "rule": "no_progress", "action": "pause", "actor": "supervisor",
                  "message": "Test alert: the same 2 tests failed on 4 attempts across 4 code versions.",
                  "spend_micros": 140_000, "objective": "Example task", "workspace_id": session.workspace_id}
        try:
            state.alerter.send_test(sample)
        except Exception:
            logging.getLogger("agent_supra.alerts").exception("test alert delivery failed")
            return settings_page(request, session, "Test alert delivery failed. Check the server logs and alert settings.", 502)
        notice = "alert-sent-slack-only" if ws.get("alert_email") and not settings.smtp_host else "alert-sent"
        return RedirectResponse(f"/app/settings?notice={notice}", status_code=303)

    @router.post("/app/settings/password", response_class=HTMLResponse)
    def change_password(request: Request, csrf: str = Form(""), current_: str = Form("", alias="current"),
                        new: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        try:
            accounts.change_password(session.user_id, current_, new)
        except SupervisorError as exc:
            return settings_page(request, session, str(exc).capitalize() + ".", 400)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.post("/app/settings/delete", response_class=HTMLResponse)
    def delete_account(request: Request, csrf: str = Form(""), confirm: str = Form("")):
        session = current(request)
        if not session:
            return login_redirect()
        if not check_csrf(session, csrf):
            return forbidden(request)
        if confirm.strip() != session.workspace_name:
            return settings_page(request, session, "The name you typed doesn't match the workspace name.", 400)
        accounts.delete_workspace(session.workspace_id, session.user_id)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    return router


__all__ = ["build_router", "compose"]
