"""Wrappers for model clients. Each one checks with the supervisor before a call
(so a paused run never reaches the provider) and records token usage after it.

Duck-typed: nothing here imports the provider SDKs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .sdk import SupervisedRun


def _get(obj: Any, name: str) -> int:
    if obj is None:
        return 0
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return int(value or 0)


# ---------------------------------------------------------------------------- Gemini
def gemini_usage(response: Any) -> tuple[int, int] | None:
    """(input, output) tokens from a Gemini response. Thinking tokens count as
    output, as Google bills them; tool-use prompt tokens count as input."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    inp = _get(usage, "prompt_token_count") + _get(usage, "tool_use_prompt_token_count")
    out = _get(usage, "candidates_token_count") + _get(usage, "thoughts_token_count")
    return inp, out


class _Recorder:
    def __init__(self, run: "SupervisedRun"):
        self.run = run

    def before(self) -> None:
        self.run.check(before_model_call=True)

    def after(self, model: str, usage: tuple[int, int] | None) -> None:
        if usage is None:
            # The provider gave no usage: still record the call so call caps and
            # timelines stay right. Cost is unknown, shown as "unpriced".
            self.run.record_model_call(model, 0, 0, cost_micros=None, usage_missing=True)
        else:
            self.run.record_model_call(model, usage[0], usage[1])

    def stream(self, iterator, model: str, usage_of):
        last = None
        try:
            for chunk in iterator:
                found = usage_of(chunk)
                if found is not None:
                    last = found
                yield chunk
        finally:
            self.after(model, last)

    async def astream(self, iterator, model: str, usage_of):
        last = None
        try:
            async for chunk in iterator:
                found = usage_of(chunk)
                if found is not None:
                    last = found
                yield chunk
        finally:
            self.after(model, last)


class _GeminiModels:
    def __init__(self, models, rec: _Recorder):
        self._models, self._rec = models, rec

    def generate_content(self, *, model: str, **kwargs):
        self._rec.before()
        response = self._models.generate_content(model=model, **kwargs)
        self._rec.after(model, gemini_usage(response))
        return response

    def generate_content_stream(self, *, model: str, **kwargs):
        self._rec.before()
        return self._rec.stream(self._models.generate_content_stream(model=model, **kwargs), model, gemini_usage)

    def __getattr__(self, name):
        return getattr(self._models, name)


class _GeminiAsyncModels:
    def __init__(self, models, rec: _Recorder):
        self._models, self._rec = models, rec

    async def generate_content(self, *, model: str, **kwargs):
        self._rec.before()
        response = await self._models.generate_content(model=model, **kwargs)
        self._rec.after(model, gemini_usage(response))
        return response

    async def generate_content_stream(self, *, model: str, **kwargs):
        self._rec.before()
        iterator = await self._models.generate_content_stream(model=model, **kwargs)
        return self._rec.astream(iterator, model, gemini_usage)

    def __getattr__(self, name):
        return getattr(self._models, name)


class _GeminiChat:
    def __init__(self, chat, model: str, rec: _Recorder):
        self._chat, self._model, self._rec = chat, model, rec

    def send_message(self, *args, **kwargs):
        self._rec.before()
        response = self._chat.send_message(*args, **kwargs)
        self._rec.after(self._model, gemini_usage(response))
        return response

    def send_message_stream(self, *args, **kwargs):
        self._rec.before()
        return self._rec.stream(self._chat.send_message_stream(*args, **kwargs), self._model, gemini_usage)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _GeminiChats:
    def __init__(self, chats, rec: _Recorder):
        self._chats, self._rec = chats, rec

    def create(self, *, model: str, **kwargs):
        return _GeminiChat(self._chats.create(model=model, **kwargs), model, self._rec)

    def __getattr__(self, name):
        return getattr(self._chats, name)


class _GeminiAio:
    def __init__(self, aio, rec: _Recorder):
        self._aio = aio
        self.models = _GeminiAsyncModels(aio.models, rec)

    def __getattr__(self, name):
        return getattr(self._aio, name)


class GeminiClient:
    """Wraps ``google.genai.Client()``: ``models``, ``chats`` and ``aio.models``."""

    def __init__(self, client, run: "SupervisedRun"):
        rec = _Recorder(run)
        self._client = client
        self.models = _GeminiModels(client.models, rec)
        self.chats = _GeminiChats(client.chats, rec)
        if getattr(client, "aio", None) is not None:
            self.aio = _GeminiAio(client.aio, rec)

    def __getattr__(self, name):
        return getattr(self._client, name)


class LegacyGeminiModel:
    """Wraps the older ``google.generativeai.GenerativeModel``."""

    def __init__(self, model, run: "SupervisedRun"):
        self._model, self._rec = model, _Recorder(run)
        self._name = str(getattr(model, "model_name", "gemini"))

    def generate_content(self, *args, **kwargs):
        self._rec.before()
        response = self._model.generate_content(*args, **kwargs)
        if kwargs.get("stream"):
            return self._rec.stream(response, self._name, gemini_usage)
        self._rec.after(self._name, gemini_usage(response))
        return response

    def __getattr__(self, name):
        return getattr(self._model, name)


def wrap_gemini(client, run: "SupervisedRun"):
    if hasattr(client, "models") and hasattr(client, "chats"):
        return GeminiClient(client, run)
    if hasattr(client, "generate_content"):
        return LegacyGeminiModel(client, run)
    raise TypeError("expected google.genai.Client() or google.generativeai.GenerativeModel")


# ---------------------------------------------------------------------------- OpenAI
def openai_usage(response: Any) -> tuple[int, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if getattr(usage, "prompt_tokens", None) is not None:          # chat.completions
        return _get(usage, "prompt_tokens"), _get(usage, "completion_tokens")
    return _get(usage, "input_tokens"), _get(usage, "output_tokens")  # responses API


class _OpenAICreate:
    def __init__(self, target, rec: _Recorder):
        self._target, self._rec = target, rec

    def create(self, *, model: str, **kwargs):
        self._rec.before()
        response = self._target.create(model=model, **kwargs)
        if kwargs.get("stream"):
            return self._rec.stream(response, model, openai_usage)
        self._rec.after(model, openai_usage(response))
        return response

    def __getattr__(self, name):
        return getattr(self._target, name)


class _OpenAIChat:
    def __init__(self, chat, rec: _Recorder):
        self._chat = chat
        self.completions = _OpenAICreate(chat.completions, rec)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class OpenAIClient:
    """Wraps ``openai.OpenAI()``: ``chat.completions.create`` and ``responses.create``.
    For streamed chat completions pass ``stream_options={"include_usage": True}``."""

    def __init__(self, client, run: "SupervisedRun"):
        rec = _Recorder(run)
        self._client = client
        self.chat = _OpenAIChat(client.chat, rec)
        if getattr(client, "responses", None) is not None:
            self.responses = _OpenAICreate(client.responses, rec)

    def __getattr__(self, name):
        return getattr(self._client, name)


# ---------------------------------------------------------------------------- Anthropic
def anthropic_usage(response: Any) -> tuple[int, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    inp = (_get(usage, "input_tokens") + _get(usage, "cache_creation_input_tokens")
           + _get(usage, "cache_read_input_tokens"))
    return inp, _get(usage, "output_tokens")


class _AnthropicStreamUsage:
    """Usage arrives in pieces: input in message_start, output in message_delta."""

    def __init__(self):
        self.inp = self.out = 0
        self.seen = False

    def __call__(self, event) -> tuple[int, int] | None:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            found = anthropic_usage(getattr(event, "message", None))
            if found:
                self.inp, self.out, self.seen = found[0], found[1], True
        elif kind == "message_delta" and getattr(event, "usage", None) is not None:
            self.out = _get(event.usage, "output_tokens")
            self.seen = True
        return (self.inp, self.out) if self.seen else None


class _AnthropicMessages:
    def __init__(self, messages, rec: _Recorder):
        self._messages, self._rec = messages, rec

    def create(self, *, model: str, **kwargs):
        self._rec.before()
        response = self._messages.create(model=model, **kwargs)
        if kwargs.get("stream"):
            return self._rec.stream(response, model, _AnthropicStreamUsage())
        self._rec.after(model, anthropic_usage(response))
        return response

    def __getattr__(self, name):
        return getattr(self._messages, name)


class AnthropicClient:
    """Wraps ``anthropic.Anthropic()``: ``messages.create``."""

    def __init__(self, client, run: "SupervisedRun"):
        self._client = client
        self.messages = _AnthropicMessages(client.messages, _Recorder(run))

    def __getattr__(self, name):
        return getattr(self._client, name)
