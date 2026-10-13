"""JSON API for agents (``/v1``). Every request needs ``Authorization: Bearer <API key>``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import KeyAuth
from .core import SupervisorError


class TaskIn(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    budget_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    max_calls: int | None = Field(default=None, ge=1, le=10_000_000)
    policy: dict | None = None


class RunIn(BaseModel):
    parent_run_id: str | None = None


class EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=1)
    type: str = Field(max_length=40)
    ts: float | None = None
    data: dict = Field(default_factory=dict)


class EventsIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=500)


class SingleEventIn(EventIn):
    run_id: str


class ActionIn(BaseModel):
    reason: str = Field(default="", max_length=1000)
    add_budget_usd: float = Field(default=0.0, ge=0, le=1_000_000)


def build_router(app_state) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["agents"])
    sup = app_state.supervisor

    def auth(request: Request) -> KeyAuth:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        key = app_state.accounts.authenticate_key(token)
        if key is None:
            raise HTTPException(401, detail="missing or invalid API key",
                                headers={"WWW-Authenticate": "Bearer"})
        request.state.key = key
        return key

    def limit(request: Request, key: KeyAuth, cost: int = 1) -> None:
        if not app_state.event_limiter.allow(key.key_id, cost):
            raise HTTPException(429, detail="rate limit exceeded; retry shortly",
                                headers={"Retry-After": "5"})

    @router.post("/tasks")
    def create_task(body: TaskIn, request: Request, key: KeyAuth = Depends(auth)):
        limit(request, key)
        return {"task_id": sup.create_task(body.objective, body.budget_usd, body.max_calls,
                                           body.policy, workspace_id=key.workspace_id)}

    @router.post("/tasks/{task_id}/runs")
    def create_run(task_id: str, request: Request, body: RunIn | None = None,
                   key: KeyAuth = Depends(auth)):
        limit(request, key)
        return {"run_id": sup.create_run(task_id, body.parent_run_id if body else None,
                                         workspace_id=key.workspace_id)}

    @router.post("/runs/{run_id}/events")
    def post_events(run_id: str, body: EventsIn, request: Request, key: KeyAuth = Depends(auth)):
        limit(request, key, cost=len(body.events))
        return sup.ingest_many(run_id, [e.model_dump() for e in body.events], workspace_id=key.workspace_id)

    @router.post("/events")
    def post_event(body: SingleEventIn, request: Request, key: KeyAuth = Depends(auth)):
        limit(request, key)
        event = body.model_dump()
        return sup.ingest(event.pop("run_id"), event, workspace_id=key.workspace_id)

    @router.get("/runs/{run_id}/decision")
    def decision(run_id: str, request: Request, before_model_call: bool = False,
                 key: KeyAuth = Depends(auth)):
        limit(request, key)
        return sup.decide(run_id, before_model_call, workspace_id=key.workspace_id)

    @router.post("/runs/{run_id}/pause")
    def pause(run_id: str, body: ActionIn, key: KeyAuth = Depends(auth)):
        return sup.pause(run_id, f"api:{key.name}", body.reason, workspace_id=key.workspace_id)

    @router.post("/runs/{run_id}/continue")
    def cont(run_id: str, body: ActionIn, key: KeyAuth = Depends(auth)):
        return sup.continue_run(run_id, f"api:{key.name}", body.reason, body.add_budget_usd,
                                workspace_id=key.workspace_id)

    @router.post("/runs/{run_id}/stop")
    def stop(run_id: str, body: ActionIn, key: KeyAuth = Depends(auth)):
        return sup.stop(run_id, f"api:{key.name}", body.reason, workspace_id=key.workspace_id)

    @router.get("/runs")
    def runs(state: str | None = None, limit_: int = 50, key: KeyAuth = Depends(auth)):
        return sup.runs(workspace_id=key.workspace_id, state=state, limit=limit_)

    @router.get("/runs/{run_id}/timeline")
    def timeline(run_id: str, key: KeyAuth = Depends(auth)):
        return sup.timeline(run_id, workspace_id=key.workspace_id)

    @router.get("/whoami")
    def whoami(key: KeyAuth = Depends(auth)):
        return {"workspace_id": key.workspace_id, "key_name": key.name}

    return router


def error_payload(exc: SupervisorError) -> tuple[int, dict]:
    return exc.status, {"error": str(exc)}
