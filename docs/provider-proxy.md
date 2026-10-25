# Local provider proxy — experimental

Connect an existing OpenAI-compatible Chat Completions client to a local endpoint.
Supra checks its run before forwarding requests, records provider-reported token
usage and estimated cost, and blocks new calls at the configured limits.

This first version serves **POST /v1/chat/completions** with text, function tools,
ordinary JSON responses and SSE streaming. OpenAI and Gemini-compatible upstream
profiles share this protocol. Responses, Anthropic Messages, native Gemini SDK
routes, audio/image input, hosted search tools, non-default service tiers and
multiple completions (`n > 1`) are not supported. Unsupported routes/fields are
rejected rather than forwarded without supervision. The proxy does not observe
tests or prove agent progress; use the existing SDK/hooks for those signals.

## Install

The proxy requires Agent Supra 1.1.0 or newer. Activate your virtual environment:

```sh
pip install --upgrade "agent-supra[proxy]>=1.1.0"
agent-supra proxy --help
```

For development in a repository checkout:

```sh
pip install -e ".[proxy]"
```

The optional proxy extra adds FastAPI, uvicorn and httpx. The core SDK remains
dependency-free. Install the OpenAI SDK separately in your client environment
if that is what your agent uses.

## Three separate credentials

Set these privately in the server terminal's environment before launch:

| Variable | Purpose |
| --- | --- |
| `AGENT_SUPRA_API_KEY` | Reports sanitized metadata to your Supra workspace |
| `OPENAI_API_KEY` or `GEMINI_API_KEY` | Authenticates only to the selected upstream provider |
| `AGENT_SUPRA_PROXY_TOKEN` | A separate random 24+ character token used only by local clients |
| `AGENT_SUPRA_URL` | Optional supervisor URL; defaults to `https://agentsupra.in` |

Generate the local token privately and give the same token to the client terminal
through your normal local secret configuration. Do not reuse either service key
as the proxy token. No key is accepted as a CLI argument or printed at startup.
The CLI does not auto-load `.env`; an ignored environment file can be loaded by
your own launcher. Do not put keys in source, prompts, screenshots or public logs.

For Gemini's compatible endpoint:

```sh
agent-supra proxy --provider gemini --model gemini-3.5-flash-lite --budget-usd 0.50 --max-calls 20
```

For OpenAI, replace the uppercase placeholders with your model and current
standard text prices in USD per million tokens:

```sh
agent-supra proxy --provider openai --model YOUR_MODEL --input-rate INPUT_RATE --output-rate OUTPUT_RATE --budget-usd 0.50 --max-calls 20
```

Unlisted models require both explicit rates. Known Gemini models can use the
repository's dated price table; verify or override it for your account. Custom
rates are included in sanitized event metadata so estimates are auditable.
One configured model is permitted per proxy; requests for another are rejected.

The server binds only to **127.0.0.1:8790** (`--port` changes the port). Do not
expose it through a tunnel/reverse proxy. Browser Origin requests and unexpected
Host values are rejected, and paid routes require the local bearer token. It is
not a public multi-tenant gateway. `/healthz` reveals only `{"ok": true}`.

## Point your client at the proxy

Only the local token goes in the client's `api_key`; the provider key stays with
the proxy. Basic integration changes the client configuration, not its agent loop:

```python
import os
import uuid
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8790/v1",
    api_key=os.environ["AGENT_SUPRA_PROXY_TOKEN"],
    max_retries=0,
)
logical_request_id = uuid.uuid4().hex
reply = client.chat.completions.create(
    model="gemini-3.5-flash-lite",
    messages=[{"role": "user", "content": "Reply with OK."}],
    extra_headers={"Idempotency-Key": logical_request_id},
)
```

For streaming, use `stream=True`. The proxy requests upstream usage internally
but preserves the client's `stream_options.include_usage` choice: usage-only
chunks are hidden unless explicitly requested. This avoids adding empty-choice
chunks to existing streaming loops. Streaming output is never accumulated into
a stored transcript. Missing completion markers produce a stream error and
unknown usage rather than a silent successful truncation.

The default output limit is 1,024 tokens (`--max-output-tokens`, up to 32,000).
Requests above that configured limit are rejected. Incoming request bodies are
bounded to 2 MB, responses/streams to 16 MB, and individual SSE events to 1 MB.
Provider network operations have a 90-second timeout; this is not a guaranteed
whole-stream wall-clock deadline. A slow stream occupies the serialized slot.

## Limits, uncertainty and retries

One proxy owns one task/run and serializes requests through usage recording.
Before each dispatch it flushes pending telemetry and asks the supervisor using
`before_model_call=true`. A pause, Stop, exhausted call cap or observed estimated
spend threshold prevents the next upstream request. Local counters provide an
additional check. Limits are per proxy task, not across independent proxies,
other SDK tasks or requests sent directly to a provider.

**The monetary budget is an estimated-spend threshold, not an invoice guarantee.**
One request admitted below the threshold can overshoot it. There is no exact
pre-call token reservation or distributed budget allocator in this version.
Cached input is conservatively charged at the configured normal input rate;
discounts, taxes, provider routing and account-specific fees can differ. Rates
may become stale. The call cap and output ceiling are separate controls.

No automatic provider retry occurs. Dispatched attempts consume a call slot,
including uncertain failures where the provider might not have received the call.
If usage is absent, invalid, interrupted or lost to disconnect, the journal records
`usage_missing=true`, cost remains **unpriced**, and the run is paused for review.
Subsequent paid requests stay blocked. A dashboard Continue does not clear the
proxy's uncertainty or local limits. Reconcile the provider's records before
deliberately starting a new proxy session.

For duplicate protection, supply a stable `Idempotency-Key` for each logical
request and reuse it on retries. Any already-dispatched ID gets **409**, even
with different content; it is never executed again or served from a response
cache. Without an ID, repeated HTTP requests are distinct calls. Disable client
automatic retries as above. Protection is scoped to this proxy journal; a fresh
proxy instance has a new task and namespace. The upstream's own idempotency
behavior is not assumed. Lost outputs cannot be reconstructed from this journal.

If the provider succeeds while Supra is unavailable, the successful response is
returned and sanitized usage stays queued. New paid requests are blocked until
the queue is delivered and the supervisor permits them. There is no fail-open
supervision mode for this proxy. Provider error bodies, caller headers and
upstream response headers are not relayed into logs or journal metadata.

## Privacy, shutdown and recovery

Messages/completions go only between your client and configured provider through
the local process. Supra receives model identity, tokens, estimated cost/rates,
run lifecycle and uncertainty flags. Provider keys and the local token remain
in process memory/environment. Journals contain no raw request/response bodies,
keys, errors, or plaintext idempotency IDs; request fingerprints are keyed hashes.
No HTTP access log is enabled, redirects are not followed, and environment HTTP
proxy settings are ignored for upstream calls. Provider-side data policies still
apply; OpenAI requests explicitly set `store=false`.

Each invocation creates a private journal under `~/.agent-supra/proxy/<id>`;
`--state-dir` chooses another private base. Never track or publicly upload this
directory. A single-writer lock prevents simultaneous owners. Ctrl+C shuts down
the service and records its outcome where possible. It does not automatically
restart, and the next invocation creates a new task.

After a crash/outage, replay metadata with the original Supra key and URL:

```sh
agent-supra proxy --replay "/private/path/to/proxy/id"
```

Replay loads the saved limits/rates and contacts **only Supra**. It needs neither
provider credentials nor a local proxy token, never resends a provider request,
and converts a dispatched-but-unrecorded attempt to unknown usage exactly once.
It does not invent a completed-run outcome after a crash. Paused/stopped states
are preserved. Key rotation/cross-key recovery and resuming request serving from
an old journal are not implemented.

## Verification

Checked on 2026-10-25: **240 regression tests passed**. Final proxy checks cover
ordinary/streamed accounting, fragmented SSE, duplicate usage chunks, client
disconnects, local authentication, concurrent call caps, monetary thresholds,
outages, replay, crash uncertainty, unsupported inputs and privacy.

Two actual Gemini calls through a loopback proxy passed (one ordinary, one streamed):
both reported usage, the duplicate returned 409, and the over-cap request returned
403 with no third dispatch. This used a synthetic prompt and isolated local Supra;
temporary Supra keys were revoked. The official OpenAI Python SDK **3.37.0** also
passed the same flow against a scripted upstream. Real OpenAI account/model access
and other provider protocols remain unverified. These internal checks do not count
as outside-user adoption or the three-user setup trial.

With dev extras installed:

```sh
python -m scripts.proxy_smoke
python -m scripts.proxy_smoke --gemini
```

The Gemini check reads the ignored `.env` or environment and makes at most two
calls with a 64-token output ceiling. Results under ignored `.local/proxy-smoke/`
contain sanitized evidence, not provider output or credentials.

Contracts: [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[streaming usage](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events),
and [Gemini compatibility](https://ai.google.dev/gemini-api/docs/openai).
