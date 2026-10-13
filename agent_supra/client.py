"""HTTP client for the hosted (or self-hosted) supervisor. Standard library only.

    sup = Client()   # reads AGENT_SUPRA_API_KEY and AGENT_SUPRA_URL

If the service can't be reached, ``fail_mode`` decides what your agent does:

* ``"open"`` (default): keep working. Events are buffered in memory and sent
  when the service is reachable again; tasks and runs are created then too.
* ``"closed"``: treat it as paused (``SupervisorPaused`` is raised on the next
  step). Use this when an unsupervised agent is worse than a stopped one.

Authentication or permission errors (401/403) always raise immediately: they
mean the configuration is wrong, and retrying won't fix them.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from typing import Any, Callable

from . import __version__

log = logging.getLogger("agent_supra")
DEFAULT_URL = "http://localhost:8000"
BATCH = 500


class SupervisorUnavailable(Exception):
    """The service couldn't be reached (network error, timeout, 5xx, rate limit)."""


class SupervisorAPIError(Exception):
    """The service rejected the request (bad key, bad input, unknown run)."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"{status}: {message}")


Transport = Callable[[str, str, dict | None, dict], tuple[int, dict]]


class Client:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, fail_mode: str = "open",
                 timeout: float = 5.0, retries: int = 2, max_buffer: int = 5000,
                 transport: Transport | None = None):
        self.api_key = api_key or os.environ.get("AGENT_SUPRA_API_KEY")
        if not self.api_key:
            raise ValueError("No API key: pass api_key= or set AGENT_SUPRA_API_KEY")
        if fail_mode not in ("open", "closed"):
            raise ValueError("fail_mode must be 'open' or 'closed'")
        self.base_url = (base_url or os.environ.get("AGENT_SUPRA_URL") or DEFAULT_URL).rstrip("/")
        self.fail_mode = fail_mode
        self.timeout = timeout
        self.retries = retries
        self.max_buffer = max_buffer
        self._transport = transport or self._urllib
        self._lock = threading.RLock()
        self._tasks: dict[str, str] = {}             # local id -> server id
        self._pending_tasks: dict[str, dict] = {}
        self._runs: dict[str, str] = {}
        self._pending_runs: dict[str, tuple[str, str | None]] = {}
        self._buffers: dict[str, deque] = {}
        self._warned = False
        self._down_until = 0.0
        self.cooldown = 30.0   # after an outage, skip the network for this long
        self.dropped_events = 0

    # ------------------------------------------------------------------ transport
    def _urllib(self, method: str, path: str, body: dict | None, params: dict) -> tuple[int, dict]:
        url = self.base_url + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": f"agent-supra/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"{}")
            except ValueError:
                payload = {}
            return exc.code, payload

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        if time.monotonic() < self._down_until:
            raise SupervisorUnavailable("in cooldown after a recent outage")
        delay = 0.25
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                status, payload = self._transport(method, path, body, params or {})
            except (OSError, ValueError) as exc:   # URLError, timeouts, bad JSON
                last = SupervisorUnavailable(str(exc))
            else:
                if status < 400:
                    return payload
                message = payload.get("error") or payload.get("detail") or "request failed"
                if status in (429, 500, 502, 503, 504):
                    last = SupervisorUnavailable(f"{status}: {message}")
                else:
                    raise SupervisorAPIError(status, str(message))
            if attempt < self.retries:
                time.sleep(delay)
                delay *= 3
        self._down_until = time.monotonic() + self.cooldown
        raise last or SupervisorUnavailable("unknown error")

    def _degraded(self, why: Exception) -> dict:
        if not self._warned:
            log.warning("Agent Supra unreachable (%s); fail_mode=%s", why, self.fail_mode)
            self._warned = True
        if self.fail_mode == "open":
            return {"allowed": True, "state": "unknown", "reason": "supervisor unreachable", "degraded": True}
        return {"allowed": False, "state": "unreachable",
                "reason": "supervisor unreachable and fail_mode is 'closed'", "degraded": True}

    # ------------------------------------------------------------------ id resolution
    def _resolve_task(self, task_id: str) -> str:
        if task_id in self._pending_tasks:
            real = self._call("POST", "/v1/tasks", self._pending_tasks[task_id])["task_id"]
            self._tasks[task_id] = real
            del self._pending_tasks[task_id]
        return self._tasks.get(task_id, task_id)

    def _resolve_run(self, run_id: str) -> str:
        if run_id in self._pending_runs:
            task_id, parent = self._pending_runs[run_id]
            body = {"parent_run_id": self._resolve_run(parent) if parent else None}
            real = self._call("POST", f"/v1/tasks/{self._resolve_task(task_id)}/runs", body)["run_id"]
            self._runs[run_id] = real
            del self._pending_runs[run_id]
        return self._runs.get(run_id, run_id)

    # ------------------------------------------------------------------ backend interface
    def create_task(self, objective: str, budget_usd: float | None = None, max_calls: int | None = None,
                    policy: dict | None = None) -> str:
        body = {"objective": objective, "budget_usd": budget_usd, "max_calls": max_calls, "policy": policy}
        with self._lock:
            try:
                return self._call("POST", "/v1/tasks", body)["task_id"]
            except SupervisorUnavailable as exc:
                self._degraded(exc)
                local = f"pending_task_{uuid.uuid4().hex[:12]}"
                self._pending_tasks[local] = body
                return local

    def create_run(self, task_id: str, parent_run_id: str | None = None) -> str:
        with self._lock:
            try:
                parent = self._resolve_run(parent_run_id) if parent_run_id else None
                return self._call("POST", f"/v1/tasks/{self._resolve_task(task_id)}/runs",
                                  {"parent_run_id": parent})["run_id"]
            except SupervisorUnavailable as exc:
                self._degraded(exc)
                local = f"pending_run_{uuid.uuid4().hex[:12]}"
                self._pending_runs[local] = (task_id, parent_run_id)
                return local

    def ingest(self, run_id: str, event: dict) -> dict:
        with self._lock:
            buffer = self._buffers.setdefault(run_id, deque())
            buffer.append(event)
            while len(buffer) > self.max_buffer:
                buffer.popleft()
                self.dropped_events += 1
            try:
                decision = self._flush(run_id)
            except SupervisorUnavailable as exc:
                return self._degraded(exc)
            return decision if decision is not None else self.decide(run_id)

    def _flush(self, run_id: str) -> dict | None:
        buffer = self._buffers.get(run_id)
        if not buffer:
            return None
        real = self._resolve_run(run_id)
        decision = None
        while buffer:
            chunk = [buffer[i] for i in range(min(BATCH, len(buffer)))]
            try:
                decision = self._call("POST", f"/v1/runs/{real}/events", {"events": chunk})
            except SupervisorAPIError as exc:
                if exc.status in (401, 403):
                    raise
                log.error("Agent Supra rejected %d event(s): %s", len(chunk), exc)
                decision = None
            for _ in chunk:
                buffer.popleft()
        self._warned = False
        return decision

    def decide(self, run_id: str, before_model_call: bool = False) -> dict:
        with self._lock:
            try:
                self._flush(run_id)
                real = self._resolve_run(run_id)
                decision = self._call("GET", f"/v1/runs/{real}/decision",
                                      params={"before_model_call": "true" if before_model_call else "false"})
                self._warned = False
                return decision
            except SupervisorUnavailable as exc:
                return self._degraded(exc)

    def flush(self) -> bool:
        """Try to send everything buffered. True if nothing is left."""
        with self._lock:
            for run_id in list(self._buffers):
                try:
                    self._flush(run_id)
                except SupervisorUnavailable:
                    return False
            return not any(self._buffers.values())

    # ------------------------------------------------------------------ reviewer actions
    def pause(self, run_id: str, reason: str = "") -> dict:
        return self._call("POST", f"/v1/runs/{self._resolve_run(run_id)}/pause", {"reason": reason})

    def continue_run(self, run_id: str, reason: str, add_budget_usd: float = 0.0) -> dict:
        return self._call("POST", f"/v1/runs/{self._resolve_run(run_id)}/continue",
                          {"reason": reason, "add_budget_usd": add_budget_usd})

    def stop(self, run_id: str, reason: str = "") -> dict:
        return self._call("POST", f"/v1/runs/{self._resolve_run(run_id)}/stop", {"reason": reason})

    def timeline(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/runs/{self._resolve_run(run_id)}/timeline")

    def whoami(self) -> dict:
        return self._call("GET", "/v1/whoami")
