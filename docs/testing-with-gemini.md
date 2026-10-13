# Testing with Gemini

A test plan for checking the product on a real Gemini agent before launch. Each test says what to do and what should happen. Write down anything that doesn't match.

## Setup (once)

1. Run the service: `agent-supra serve` locally, or deploy it (see deploying.md).
2. Sign up at `/signup`. You land on the API keys page; create a key.
3. In a terminal:

```bash
pip install -e ".[dev]"            # from the repo; includes google-genai and pytest
export GEMINI_API_KEY=...          # https://aistudio.google.com/apikey
export AGENT_SUPRA_API_KEY=as_live_...
export AGENT_SUPRA_URL=http://localhost:8000     # or your deployment
```

Each run of the example costs well under $0.05 on `gemini-2.5-flash`.

## Test 1: a solvable bug is left alone

```bash
python examples/gemini_agent.py
```

**Expect:** Gemini fixes `apply_discount` in a few attempts and the script prints "Fixed". In the dashboard the run is **Completed**, with no flags. Spend shows a small amount (not "unpriced").

## Test 2: an impossible task gets paused

```bash
python examples/gemini_agent.py --impossible --pause
```

`--impossible` adds a test that contradicts another one, so no fix can pass everything.

**Expect:** after about 4 attempts the script prints "Paused by the supervisor" with a reason. In the dashboard: a **Paused** run, an amber banner on the Runs page, the reason at the top of the run, and the supporting attempts highlighted. No model calls appear after the pause.

## Test 3: observation mode only flags

Make sure observation mode is on in Settings (it is for new workspaces), then:

```bash
python examples/gemini_agent.py --impossible
```

**Expect:** the agent keeps going until `--max-steps` (10). The run gets a grey "Flagged" note instead of being paused.

## Test 4: continue and stop

Open the paused run from Test 2.

1. Press **Continue run** without a reason. **Expect:** the browser asks for one.
2. Enter a reason and continue. **Expect:** "Continued by you@..." appears; the run is Running.
3. Press **Stop run**. **Expect:** Stopped, and it can't be continued.

## Test 5: budget

```bash
python examples/gemini_agent.py --impossible --budget 0.001
```

**Expect:** paused for **Budget reached** after a call or two, even in observation mode.

## Test 6: alerts

1. Create a Slack incoming webhook (Slack: Apps, Incoming Webhooks) and paste it in Settings. Save.
2. Press **Send test alert**. **Expect:** a message in Slack within seconds.
3. Run Test 2 again. **Expect:** a Slack message with a link to the paused run.

## Test 7: service outage

1. Start an `--impossible` run without `--pause`, then stop the server partway through.
2. **Expect:** the agent keeps going (fail-open) and logs a warning once, not on every step.
3. Start the server again. **Expect:** the buffered events show up on the run.

Repeat with `Client(fail_mode="closed")` in the script: the agent should stop instead.

## Test 8: your own agent

Wrap one of your own Gemini scripts the way the example does. The things to check:

- Every model call appears on the run (count them against your own logs).
- Spend roughly matches Google AI Studio's usage page for the same period. It won't match exactly; it's an estimate.
- If you use `client.chats`, `generate_content_stream` or `client.aio`, those calls show up too.

## What to send back

For anything that didn't match: which test, what you expected, what happened, and the run ID from the dashboard URL.
