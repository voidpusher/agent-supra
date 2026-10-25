# Try Agent Supra with your own agent

The goal is to find setup problems, incorrect flags, and missed loops. Start in
observation mode. The beta is free; your model provider's usual charges apply.
Read the [terms](https://agentsupra.in/terms) and
[privacy page](https://agentsupra.in/privacy) before signing up.

## 1. Prepare a clean environment

Use Python 3.10 or newer, your own disposable project, and a new virtual environment:

```sh
python -m venv .venv
```

Activate it with `source .venv/bin/activate` in Bash, or
`.\.venv\Scripts\Activate.ps1` in PowerShell. If PowerShell blocks activation,
use `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\agent-supra.exe` directly;
changing machine security settings is not needed.

```sh
python -m pip install --upgrade "agent-supra>=1.1.0"
python -c "import agent_supra; print(agent_supra.__version__)"
```

Record your OS, Python version, and package version. Keep account/key setup time
separate from installation-to-first-agent-run time. Under five minutes is our
setup target, not a measured promise. Note every point where you needed help.

## 2. Confirm your connection

Create an account at [agentsupra.in/signup](https://agentsupra.in/signup), create
an API key, and follow the [quickstart](https://agentsupra.in/docs/quickstart)
to set it privately in your terminal and run the connection check. Expect
`Verified completed run: run_...` and the matching dashboard run.

This uses no model provider and is only a connection check. A login in your
browser does not configure the terminal. Never paste keys into chat, issue
reports, command arguments, recordings, or source files.

## 3. Choose one integration

| Your agent | Start here | What to verify |
| --- | --- | --- |
| Claude Code | [Claude hooks](claude-code.md), mode `observe` | Tool activity and fresh pytest JUnit reports appear |
| Existing command with JUnit output | [CLI watch](cli-watch.md), default observation mode | Process exit and fresh test reports appear |
| Python model client | [SDK quickstart](https://agentsupra.in/docs/quickstart) | Model calls and your reported test results appear |

Do not double-wrap one agent with multiple integrations. Hooks and CLI watch do
not track model spend. The separate [provider proxy](provider-proxy.md) tracks
compatible model requests and estimated spend, but does not detect test progress.
Budget and call limits can pause runs even in observation mode. Keep a small
provider-side spending limit while experimenting.

Start with a harmless task where you know the expected result. Open its run in
the dashboard and confirm the received events match what your agent actually
did. A completed connection check alone does not establish a working integration.

## 4. Use observation mode for a week

On each run page, review flags as **Useful**, **Too early**, or **False alarm**.
Use **Report a missed loop** for a run that looked stuck but was not flagged.
Record noticeably slow agent steps and differences from your provider's usage
report. Estimates may differ from invoices.

Do not enable loop-pause enforcement during the initial trial. After reviewing
the observation results together, one willing participant can try enforcement
on a disposable task. Work already in flight can finish, and CLI watch cancels
the process tree rather than suspending it for automatic resume.

## 5. Report what happened

Use the run-page feedback controls first. For a setup problem, report through
the product's contact link or [GitHub issues](https://github.com/voidpusher/agent-supra/issues):

```text
Integration and package version:
OS and Python version:
Account/key setup time:
Installation to first real-agent run time:
Step that blocked me / help I needed:
Expected behavior:
Actual behavior:
Run ID, if created:
```

GitHub issues are public. Leave out email addresses, tokens, provider keys,
prompts, private code, connection strings, and raw logs. Share only sanitized
error descriptions. Operator test accounts and scripted fixtures do not count
as outside adoption.
