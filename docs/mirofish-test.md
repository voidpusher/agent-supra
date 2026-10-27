# MiroFish client compatibility check

Checked 27 October 2026 against public MiroFish commit
`7657031ac01184afe2cb220f5ee3545573b5e843`:
[upstream repository](https://github.com/666ghj/MiroFish).
This is an internal third-party compatibility test, not an outside-user activation.

## What passed

| Check | Evidence |
| --- | --- |
| Upstream LLM-client tests | 26 selected tests passed: JSON response handling and Chat Completions compatibility |
| JSON success | Actual MiroFish `LLMClient.chat_json` parsed a controlled valid JSON response through the real loopback Supra proxy |
| Content retry | A controlled truncated response caused one content-regeneration attempt; both calls were recorded |
| Call-limit enforcement | The first controlled response was truncated; Supra denied MiroFish's next attempt with HTTP 403, with only one upstream dispatch |
| Real provider | One Gemini request through the actual MiroFish client and Supra proxy returned valid JSON; one call and known usage were recorded |

The real request used `gemini-3.5-flash-lite`, OpenAI SDK 3.39.0 and a 60-second
proxy upstream deadline. Reported cost was 18 microdollars under the current
price table; this is an estimate, not a provider invoice. The controlled cases
use synthetic token counts, not paid-provider usage.

The harness loads MiroFish's actual utility modules using isolated package
namespaces. It does not execute the Flask application/package initialization,
substitute an implementation of its LLM client, or modify MiroFish source.
The controlled upstream responses are deliberate fixtures. For this bounded
assessment only, OpenAI SDK transport retries are set to zero; MiroFish's own
`chat_json(max_attempts=2)` content retry is still exercised. Its default SDK
retry behavior and JSON-mode provider capability fallback are not verified
through the proxy by these cases.

## Scope limits

This does **not** verify the MiroFish web UI, graph generation, Zep Cloud,
OASIS simulation, report-agent tool loop, simulation quality or agent-loop
detection. The [upstream setup instructions](https://github.com/666ghj/MiroFish#-quick-start)
require a Zep Cloud API key for the complete workflow. No Zep account/key was
configured in this check. A proxy accounting test is not evidence of progress
monitoring: that needs an explicit progress signal from the actual simulation.

The proxy's successful client checks leave their local run active until a caller
finishes it; they are not claimed as completed simulations. The controlled cap
case is paused. Temporary local Supra keys were revoked when the service closed.
No production Supra key, database, Slack destination or email destination was used.
The Gemini key stayed in the proxy process; MiroFish received only a random local
proxy token. Local resource isolation is not an OS security sandbox.

## Repeat the check

From the Agent Supra checkout, with dev extras installed:

```sh
git clone --depth 1 https://github.com/666ghj/MiroFish.git .local/mirofish/source
python -m pip install --target .local/mirofish/dependencies openai
python -m scripts.mirofish_compatibility
# Optional: exactly one paid Gemini request, using the existing private key:
python -m scripts.mirofish_compatibility --gemini
```

Use an existing clone with `--source PATH`; the report records the commit actually
used. The tested revision above is a point-in-time result, not a guarantee for
future MiroFish revisions. Do not overwrite an existing checkout just to run this.

Keep the optional Gemini key in `GEMINI_API_KEY` or the ignored Agent Supra `.env`.
Reports, synthetic JUnit output, dependencies and the upstream checkout stay under
ignored `.local/mirofish/`. The child receives only basic system environment
variables and explicitly disables dotenv discovery, so it cannot accidentally
load Agent Supra's project `.env`. Reports exclude keys, prompts and replies.

For a full simulation next: configure a private Zep Cloud credential, use a small
synthetic seed and a strict round/agent/call budget, check each simulation's model
configuration for bypass paths, and define a real progress signal before claiming
loop detection. Provider calls and Zep usage may incur charges; the one-call client
assessment is not permission for an unrestricted simulation.
