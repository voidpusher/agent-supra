# Agent Supra

Notice when an unattended AI agent is stuck, pause it before more money goes into a loop, and show exactly why.

A spend cap can't tell a stuck agent from one working hard on a tough bug. Agent Supra watches **progress**: if an agent keeps changing code while its failing tests don't get any fewer, the run is flagged (or paused, once you opt in) with the evidence.

- **SDK** (`pip install agent-supra`): no dependencies. Wraps Gemini, OpenAI and Anthropic clients.
- **Hosted service**: dashboard, API keys, alerts to Slack and email, Postgres-backed. One Docker image.

## Try it in two minutes (no accounts, no API keys)

```bash
pip install -e ".[dev]"
agent-supra demo            # or: python -m agent_supra.demo
```

Two scripted agents fix a real bug in a tiny repo, running real pytest. One edits the code four times while the same tests fail and gets **paused**. The other fails twice, changes approach, passes, and is **left alone**. A timeline report is written to `supervisor_report.html`.

## Run the service locally

On Windows, see [the local PowerShell guide](docs/windows-local.md). The helper
`python scripts/local.py serve` loads the repository's `.env` file; the bare
`agent-supra serve` command reads exported environment variables.

```bash
cp .env.example .env
agent-supra serve           # SQLite by default; http://localhost:8000
```

Or with Postgres, the way production runs:

```bash
docker compose up --build
```

Sign up, create an API key, then follow the quickstart at `/docs/quickstart`.

## Connect an agent

For Claude Code, try the [experimental hook integration](docs/claude-code.md).
It tracks tool/test activity and can deny the next tool call when paused.
It requires the current source installation; model spend is not measured by hooks.

The following shows where supervision fits into your own agent loop; provide
your own `prompt`, `apply_fix`, `run_tests`, and `git_rev` functions. For a
copy-paste first-run check, use the [hosted quickstart](https://agentsupra.in/docs/quickstart).

```python
from google import genai
from agent_supra import Client, SupervisedRun, SupervisorPaused

sup = Client()   # AGENT_SUPRA_API_KEY and AGENT_SUPRA_URL from the environment
task = sup.create_task("Fix failing checkout tests", budget_usd=3)

with SupervisedRun(sup, task) as run:
    gemini = run.wrap_gemini(genai.Client())
    try:
        while True:
            run.check()                                   # raises SupervisorPaused when paused
            reply = gemini.models.generate_content(model="gemini-3.5-flash-lite", contents=prompt)
            run.tool("edit_file", apply_fix, reply.text, rev=git_rev())
            result = run.tool("run_tests", run_tests, rev=git_rev())
            run.report_tests(result.all_ids, result.failing_ids, rev=git_rev())
            if not result.failing_ids:
                run.finish("success")
                break
    except SupervisorPaused as paused:
        print("Paused:", paused.reason)                   # don't retry; review it in the dashboard
```

| Call | What it does |
| --- | --- |
| `Client(fail_mode="open")` | Hosted backend. If the service is down, `"open"` keeps the agent running and buffers events; `"closed"` treats it as paused |
| `run.wrap_gemini(client)` | `google.genai.Client()`: `models.generate_content`, `generate_content_stream`, `chats`, `aio`. Also the older `GenerativeModel` |
| `run.wrap_openai(client)` | `chat.completions.create` and `responses.create`. Streaming needs `stream_options={"include_usage": True}` |
| `run.wrap_anthropic(client)` | `messages.create`, including streams |
| `run.record_model_call(model, in, out)` | Any other model |
| `run.check()` | Refuses the next step if the run is paused or stopped |
| `run.tool(name, fn, ..., rev=)` | Runs a tool and records success or failure (fingerprints only, not the raw arguments or errors) |
| `run.report_tests(suite, failing, rev=)` | The main progress signal |
| `run.child()` | A sub-agent run sharing the task's budget |
| `run.finish("success" / "failed" / "unknown")` | Final outcome |

Wrapped clients check with the supervisor **before** each model call, so a paused run never reaches the provider.

A complete, runnable Gemini agent is in [`examples/gemini_agent.py`](examples/gemini_agent.py). See [docs/testing-with-gemini.md](docs/testing-with-gemini.md).

## Detection rules

| Rule | Fires when | Default |
| --- | --- | --- |
| No progress | 4 attempts in a row without the number of failing tests reaching a new low, while the code changes. Swapping one failure for another doesn't count as progress | Pause, once observation mode is off |
| Exact repeat | The same failure with no code change, or a tool failing with the same arguments and error | Pause, once observation mode is off |
| Going in circles | Flipping between two versions of the code with the same failures | Flag |
| Context running away | Input tokens rising call after call (2x over 5 calls) with no progress, or over a ceiling you set | Flag |
| Budget | Estimated task spend reaches the budget, across all runs and sub-agents | Always enforced |
| Call cap | The next model call would exceed the task's cap | Always enforced |

Safeguards: deleting a failing test isn't progress; network and rate-limit errors never count; if events go missing, the run is only flagged, never paused. New workspaces start in **observation mode** (flag only) so people can check the rules on their own agents before anything gets paused.

## Configuration

All server settings are environment variables. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///supervisor.db` | Postgres in production (`postgres://` URLs work) |
| `BASE_URL` | `http://localhost:8000` | Public URL; `https://` turns on secure cookies and HSTS |
| `APP_NAME` | `Agent Supra` | Product name shown everywhere |
| `ALLOW_SIGNUP` | `true` | `false` for invite-only (`agent-supra create-user`) |
| `RETENTION_DAYS` | `30` | Run and server-error data older than this is deleted every 6 hours or by the daily cron |
| `SMTP_*` | unset | Email alerts |
| `AGENT_SUPRA_PRICES` | unset | Extra model prices, `{"model": [input, output]}` USD per 1M tokens |

## Deploying

See [docs/deploying.md](docs/deploying.md). The hosted beta runs on Vercel with Neon Postgres at [agentsupra.in](https://agentsupra.in).

## Security

- Passwords: scrypt. API key and session secrets are stored as SHA-256 hashes; API keys are shown once.
- Every API request is scoped to the key's workspace; another workspace's runs return 404.
- Dashboard forms are CSRF-protected; cookies are HttpOnly and SameSite.
- Rate limits on events and on login/sign-up; request size limits; strict security headers.
- Slack webhooks must point at `hooks.slack.com`, so the setting can't be used to reach internal URLs.
- Prompts, model replies and code are never sent; tool arguments and errors are sent only as keyed fingerprints. Set `AGENT_SUPRA_FP_KEY` in your agents to make them unguessable.

## Development

```bash
pip install -e ".[dev]"
python -m pytest                                  # SQLite test suite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/test python -m pytest   # on Postgres
```

CI (`.github/workflows/tests.yml`) runs both.

## Limits worth knowing

- **Costs are estimates** from token counts and the price table (Gemini prices built in, checked 13 Oct 2026). Not your invoice.
- **Pausing is cooperative**: in-flight calls and tools finish. Agents that call the provider directly, bypassing the wrapper, are not stopped.
- **Budgets are checked, not reserved**: several sub-agents running in parallel can each make one more call after the budget is crossed.
- Rate limits are per server process.
- Thresholds are starting points. Tune them in observation mode on real runs.

MIT licensed.
