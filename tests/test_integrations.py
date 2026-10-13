from types import SimpleNamespace

import pytest
from google.genai import types

from agent_supervisor import SupervisedRun, SupervisorPaused
from agent_supervisor.pricing import estimate_cost_micros


def gemini_response(prompt=1000, out=200, thoughts=300, tool_prompt=None):
    return types.GenerateContentResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts,
        tool_use_prompt_token_count=tool_prompt))


class FakeGeminiModels:
    def __init__(self):
        self.calls = 0

    def generate_content(self, *, model, contents, config=None):
        self.calls += 1
        return gemini_response()

    def generate_content_stream(self, *, model, contents, config=None):
        self.calls += 1
        yield types.GenerateContentResponse()
        yield gemini_response(prompt=1000, out=50, thoughts=0)


class FakeChat:
    def send_message(self, message):
        return gemini_response(prompt=2000, out=100, thoughts=0)


class FakeGenai:
    def __init__(self):
        self.models = FakeGeminiModels()
        self.chats = SimpleNamespace(create=lambda *, model, **kw: FakeChat())


def model_calls(sup, run):
    return [e["data"] for e in sup.events(run.run_id) if e["type"] == "model.call"]


def test_gemini_generate_content_records_thinking_as_output(supervisor):
    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    client = run.wrap_gemini(FakeGenai())
    client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    [call] = model_calls(supervisor, run)
    assert (call["input_tokens"], call["output_tokens"]) == (1000, 500)
    assert call["cost_micros"] == estimate_cost_micros("gemini-2.5-flash", 1000, 500) == 300 + 1250


def test_gemini_stream_records_final_usage(supervisor):
    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    client = run.wrap_gemini(FakeGenai())
    chunks = list(client.models.generate_content_stream(model="gemini-2.5-flash", contents="hi"))
    assert len(chunks) == 2
    [call] = model_calls(supervisor, run)
    assert (call["input_tokens"], call["output_tokens"]) == (1000, 50)


def test_gemini_chat_sessions_are_supervised(supervisor):
    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    chat = run.wrap_gemini(FakeGenai()).chats.create(model="gemini-2.5-pro")
    chat.send_message("hi")
    [call] = model_calls(supervisor, run)
    assert call["model"] == "gemini-2.5-pro" and call["input_tokens"] == 2000


def test_paused_run_never_reaches_gemini(supervisor):
    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    fake = FakeGenai()
    client = run.wrap_gemini(fake)
    supervisor.pause(run.run_id, "me", "checking")
    with pytest.raises(SupervisorPaused):
        client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    with pytest.raises(SupervisorPaused):
        client.chats.create(model="gemini-2.5-flash").send_message("hi")
    assert fake.models.calls == 0


def test_real_genai_client_is_blocked_before_any_network_call(supervisor):
    from google import genai
    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    client = run.wrap_gemini(genai.Client(api_key="not-a-real-key"))
    supervisor.pause(run.run_id, "me", "checking")
    with pytest.raises(SupervisorPaused):
        client.models.generate_content(model="gemini-2.5-flash", contents="hi")
    assert hasattr(client, "files")     # everything else passes through to the real client


def test_gemini_budget_blocks_next_call(supervisor):
    run = SupervisedRun(supervisor, supervisor.create_task("g", budget_usd=0.001))
    client = run.wrap_gemini(FakeGenai())
    client.models.generate_content(model="gemini-2.5-flash", contents="hi")   # $0.00155
    with pytest.raises(SupervisorPaused):
        client.models.generate_content(model="gemini-2.5-flash", contents="hi")


def test_legacy_generative_model(supervisor):
    class Legacy:
        model_name = "models/gemini-2.5-flash"

        def generate_content(self, contents, **kw):
            return gemini_response(prompt=100, out=10, thoughts=0)

    run = SupervisedRun(supervisor, supervisor.create_task("g"))
    run.wrap_gemini(Legacy()).generate_content("hi")
    [call] = model_calls(supervisor, run)
    assert call["cost_micros"] is not None   # "models/" prefix still resolves to a price


def test_openai_chat_and_stream(supervisor):
    class Completions:
        def create(self, *, model, messages, stream=False, **kw):
            if stream:
                return iter([SimpleNamespace(usage=None),
                             SimpleNamespace(usage=SimpleNamespace(prompt_tokens=70, completion_tokens=7))])
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    run = SupervisedRun(supervisor, supervisor.create_task("o"))
    client = run.wrap_openai(fake)
    client.chat.completions.create(model="gemini-2.5-flash", messages=[])
    list(client.chat.completions.create(model="gemini-2.5-flash", messages=[], stream=True,
                                        stream_options={"include_usage": True}))
    calls = model_calls(supervisor, run)
    assert [(c["input_tokens"], c["output_tokens"]) for c in calls] == [(100, 20), (70, 7)]


def test_missing_usage_still_counts_the_call(supervisor):
    class Completions:
        def create(self, *, model, messages, **kw):
            return SimpleNamespace(usage=None)

    run = SupervisedRun(supervisor, supervisor.create_task("o", max_calls=1))
    client = run.wrap_openai(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    client.chat.completions.create(model="x", messages=[])
    assert supervisor.task_spend(run.task_id)["unpriced_calls"] == 1
    with pytest.raises(SupervisorPaused):            # call cap still enforced
        client.chat.completions.create(model="x", messages=[])


def test_anthropic_stream_usage(supervisor):
    events = [SimpleNamespace(type="message_start", message=SimpleNamespace(
                  usage=SimpleNamespace(input_tokens=500, output_tokens=1, cache_read_input_tokens=100,
                                        cache_creation_input_tokens=None))),
              SimpleNamespace(type="content_block_delta"),
              SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=42))]

    class Messages:
        def create(self, *, model, stream=False, **kw):
            return iter(events)

    run = SupervisedRun(supervisor, supervisor.create_task("a"))
    client = run.wrap_anthropic(SimpleNamespace(messages=Messages()))
    list(client.messages.create(model="m", max_tokens=10, messages=[], stream=True))
    [call] = model_calls(supervisor, run)
    assert (call["input_tokens"], call["output_tokens"]) == (600, 42)
