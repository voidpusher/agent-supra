"""Python SDK: wrap an agent so the supervisor can watch it and pause it.

    from agent_supervisor import Client, SupervisedRun, SupervisorPaused

    sup = Client()                                   # hosted service (API key from env)
    task = sup.create_task("Fix failing checkout tests", budget_usd=3)
    with SupervisedRun(sup, task) as run:
        gemini = run.wrap_gemini(genai.Client())     # or wrap_openai / wrap_anthropic
        try:
            while True:
                run.check()                          # raises SupervisorPaused when paused
                reply = gemini.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                run.tool("edit_file", apply_fix, reply.text, rev=git_rev())
                result = run.tool("run_tests", run_tests, rev=git_rev())
                run.report_tests(result.all_ids, result.failing_ids, rev=git_rev())
                if not result.failing_ids:
                    run.finish("success"); break
        except SupervisorPaused as paused:
            print(paused.reason)                     # don't retry; a reviewer decides

Pausing is cooperative: once a run is paused the SDK refuses new steps and the
wrapped clients refuse new model calls. Work already in flight finishes.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import uuid
from typing import Any, Callable, Iterable

from . import integrations

TRANSIENT_NAMES = ("Timeout", "ConnectionError", "RateLimit", "Overloaded", "ServiceUnavailable",
                   "TemporaryFailure", "APIConnectionError", "InternalServerError", "ResourceExhausted",
                   "DeadlineExceeded")


class SupervisorPaused(Exception):
    """The supervisor paused or stopped this run. Don't retry: exit the step loop."""

    retryable = False

    def __init__(self, decision: dict):
        self.decision = decision
        self.run_id = decision.get("run_id")
        self.state = decision.get("state")
        self.reason = decision.get("reason")
        super().__init__(f"run {self.run_id} is {self.state}: {self.reason}")


def _fp_key() -> bytes:
    return os.environ.get("AGENT_SUPERVISOR_FP_KEY", "agent-supervisor-default-key").encode()


def fingerprint(value: Any, key: bytes | None = None) -> str:
    """Keyed hash, so raw arguments and errors never leave your machine.
    Set AGENT_SUPERVISOR_FP_KEY to your own secret to make them unguessable."""
    return hmac.new(key or _fp_key(), repr(value).encode(), hashlib.sha256).hexdigest()[:16]


_VOLATILE = re.compile(r"0x[0-9a-fA-F]+|\b\d+(\.\d+)?\b|/[^\s:'\"]+")


def error_fingerprint(exc: BaseException | str, key: bytes | None = None) -> str:
    """Normalise away numbers, addresses and paths so the 'same' error matches."""
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return fingerprint(_VOLATILE.sub("#", text), key)


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(t in name for t in TRANSIENT_NAMES):
        return "transient"
    if isinstance(status, int) and status in (408, 429, 500, 502, 503, 504):
        return "transient"
    return "permanent"


class SupervisedRun:
    """One attempt at a task. ``backend`` is a :class:`Client` (hosted) or a local
    :class:`agent_supervisor.server.Supervisor`."""

    def __init__(self, backend, task_id: str, parent_run_id: str | None = None,
                 fingerprint_key: bytes | None = None):
        self.backend = backend
        self.task_id = task_id
        self.run_id: str = backend.create_run(task_id, parent_run_id)
        self._seq = 0
        self._key = fingerprint_key
        self._finished = False
        self.last_decision: dict | None = None
        self._emit("run.started", {"parent_run_id": parent_run_id})

    # ------------------------------------------------------------------ plumbing
    def _emit(self, type_: str, data: dict) -> dict:
        self._seq += 1
        self.last_decision = self.backend.ingest(self.run_id, {
            "event_id": uuid.uuid4().hex, "seq": self._seq, "type": type_, "ts": time.time(), "data": data})
        return self.last_decision

    def check(self, before_model_call: bool = False) -> None:
        """Raise :class:`SupervisorPaused` if the run may not take another step."""
        decision = self.backend.decide(self.run_id, before_model_call)
        self.last_decision = decision
        if not decision["allowed"]:
            raise SupervisorPaused({**decision, "run_id": self.run_id})

    def child(self) -> "SupervisedRun":
        """A sub-agent run that shares this task's budget."""
        return SupervisedRun(self.backend, self.task_id, parent_run_id=self.run_id, fingerprint_key=self._key)

    # ------------------------------------------------------------------ recording
    def record_model_call(self, model: str, input_tokens: int, output_tokens: int,
                          cost_micros: int | None = None, usage_missing: bool = False) -> dict:
        data = {"model": model, "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
                "cost_micros": cost_micros}
        if usage_missing:
            data["usage_missing"] = True
        return self._emit("model.call", data)

    def tool(self, name: str, fn: Callable, *args, rev: str | None = None, **kwargs):
        """Run a tool under supervision. Refuses to start if the run is paused."""
        self.check()
        started = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self._emit("tool.finished", {
                "tool": name, "args_fp": fingerprint((args, kwargs), self._key), "ok": False,
                "error_fp": error_fingerprint(exc, self._key), "error_class": classify_error(exc),
                "rev": rev, "elapsed_s": round(time.time() - started, 3)})
            raise
        self._emit("tool.finished", {"tool": name, "args_fp": fingerprint((args, kwargs), self._key),
                                     "ok": True, "rev": rev, "elapsed_s": round(time.time() - started, 3)})
        return result

    def report_tests(self, suite: Iterable[str], failing: Iterable[str], rev: str | None = None,
                     transient: bool = False) -> dict:
        """Report a test run: every test ID that ran, and the ones that failed.
        ``transient=True`` when the runner itself broke (network, timeout)."""
        data = {"suite": sorted(set(map(str, suite))), "failing": sorted(set(map(str, failing))), "rev": rev}
        if transient:
            data["error_class"] = "transient"
        return self._emit("tests.reported", data)

    def progress(self, note: str) -> None:
        self._emit("progress.updated", {"note": str(note)[:300]})

    def finish(self, outcome: str = "success") -> None:
        """outcome: "success" (verified), "failed" or "unknown"."""
        if not self._finished:
            self._finished = True
            self._emit("run.finished", {"outcome": outcome})

    # ------------------------------------------------------------------ model clients
    def wrap_gemini(self, client):
        """Wrap ``google.genai.Client()`` (models, chats, aio) or a legacy GenerativeModel."""
        return integrations.wrap_gemini(client, self)

    def wrap_openai(self, client):
        """Wrap ``openai.OpenAI()`` (chat.completions and responses)."""
        return integrations.OpenAIClient(client, self)

    def wrap_anthropic(self, client):
        """Wrap ``anthropic.Anthropic()`` (messages)."""
        return integrations.AnthropicClient(client, self)

    # ------------------------------------------------------------------ context manager
    def __enter__(self) -> "SupervisedRun":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is SupervisorPaused:
            return False          # leave the run paused/stopped for a reviewer
        try:
            if not self.backend.decide(self.run_id)["allowed"]:
                return False      # paused or stopped mid-loop: a reviewer decides
        except Exception:
            pass
        self.finish("failed" if exc_type is not None else "unknown")
        flush = getattr(self.backend, "flush", None)
        if callable(flush):
            flush()
        return False
