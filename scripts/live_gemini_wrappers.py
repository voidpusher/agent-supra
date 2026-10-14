"""Verify supported Gemini wrapper paths against Google with tiny live calls.

Run after ``pip install -e '.[dev]'`` and set GEMINI_API_KEY in the environment
or the ignored project .env file. This uses an in-memory supervisor and never
prints the API key, prompts, or model replies. It does make billable API calls.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from google import genai

from agent_supra import SupervisedRun, SupervisorPaused
from agent_supra.server.core import Supervisor


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    key = os.environ.get("GEMINI_API_KEY") or dotenv_values(root / ".env").get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is missing")
        return 2
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    supervisor = Supervisor()
    task = supervisor.create_task("Live Gemini wrapper verification", max_calls=8)
    run = SupervisedRun(supervisor, task)
    raw_client = genai.Client(api_key=key, http_options={"timeout": 30000})
    client = run.wrap_gemini(raw_client)
    outcomes: list[dict] = []
    current_path = ""

    def calls() -> list[dict]:
        return [e["data"] for e in supervisor.events(run.run_id) if e["type"] == "model.call"]

    def verify(label: str, before: int) -> None:
        recorded = calls()
        if len(recorded) != before + 1 or recorded[-1]["input_tokens"] <= 0:
            raise AssertionError(f"{label}: missing model usage")
        outcomes.append({"path": label, "input_tokens": recorded[-1]["input_tokens"],
                         "output_tokens": recorded[-1]["output_tokens"]})

    def check(label: str, action) -> None:
        nonlocal current_path
        current_path = label
        before = len(calls())
        action()
        verify(label, before)

    async def async_checks() -> None:
        nonlocal current_path
        current_path = "async_model"
        before = len(calls())
        await client.aio.models.generate_content(model=model, contents="Reply with OK.")
        verify(current_path, before)

        current_path = "async_stream"
        before = len(calls())
        stream = await client.aio.models.generate_content_stream(model=model, contents="Reply with OK.")
        async for _ in stream:
            pass
        verify(current_path, before)

    try:
        check("sync_model", lambda: client.models.generate_content(model=model, contents="Reply with OK."))
        check("sync_stream", lambda: list(client.models.generate_content_stream(
            model=model, contents="Reply with OK.")))
        check("chat", lambda: client.chats.create(model=model).send_message("Reply with OK."))
        check("chat_stream", lambda: list(client.chats.create(model=model).send_message_stream("Reply with OK.")))
        asyncio.run(async_checks())

        current_path = "paused_pre_call"
        supervisor.pause(run.run_id, "test", "pre-call block verification")
        try:
            client.models.generate_content(model=model, contents="This must be blocked locally.")
        except SupervisorPaused:
            outcomes.append({"path": current_path, "blocked": True})
        else:
            raise AssertionError("paused call was not blocked")
    except Exception as exc:
        print(json.dumps({"model": model, "status": "failed", "path": current_path,
                          "error_type": type(exc).__name__, "http_status": getattr(exc, "code", None),
                          "checks": outcomes}))
        return 1
    finally:
        raw_client.close()

    print(json.dumps({"model": model, "status": "passed", "checks": outcomes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
