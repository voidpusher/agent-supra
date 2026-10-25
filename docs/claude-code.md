# Claude Code integration — experimental

Observe tool activity and pytest progress without changing your agent loop.
Pause mode can deny the **next tool call** when Supra pauses or stops a run.
This is a preview integration, not a security sandbox or a model-request proxy.

## Install from the current source

The hook command is newer than the published `agent-supra==1.0.0` package.
Use the current GitHub source, with Python 3.10+ and a terminal where its
`agent-supra` command is on PATH. Activate your virtual environment first.

```sh
pip install --upgrade --force-reinstall "git+https://github.com/voidpusher/agent-supra.git"
agent-supra claude-hook --help
claude plugin marketplace add voidpusher/agent-supra
claude plugin install agent-supra@agent-supra
```

Create a key in your [Supra dashboard](https://agentsupra.in/app/keys). Keep it
out of project settings, command arguments, commits, screenshots and chat.
Set these variables in the terminal **before launching Claude Code**.

macOS/Linux:

```sh
read -rs -p "Agent Supra API key: " AGENT_SUPRA_API_KEY
export AGENT_SUPRA_API_KEY
export AGENT_SUPRA_URL="https://agentsupra.in"
export AGENT_SUPRA_CLAUDE_MODE="observe"
claude
```

Windows PowerShell:

```powershell
$env:AGENT_SUPRA_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Agent Supra API key' -AsSecureString)).Password
$env:AGENT_SUPRA_URL = 'https://agentsupra.in'
$env:AGENT_SUPRA_CLAUDE_MODE = 'observe'
claude
```

For a source checkout, install with `pip install -e .` and launch
`claude --plugin-dir /absolute/path/to/agent-supra/plugins/claude-code` instead
of installing the marketplace plugin. Use one loading method, not both.

SessionStart displays an attachment notice. A task named **Claude Code session**
appears after successful attachment. Its run starts in observation mode.
Changing observation/pause mode uses a separate local session mapping.

## Give Supra actual test results

Have Claude run a direct pytest command with an explicit JUnit argument:

```sh
python -m pytest --junitxml=.local/supra-tests.xml
```

Ensure `.local/` is ignored by Git in the project being tested. The report itself
can contain private test output; it stays local. Use the same report argument
on subsequent attempts. No test is launched, command rewritten, or file modified
by the hook. Bare `pytest`, chained shell commands, Jest and Go are not test-progress
inputs in this first version. Their supported tool failures can still be observed.

PreToolUse records the report's existing file marker. PostToolUse or
PostToolUseFailure reads it only if that marker changed. Missing, stale, malformed,
oversized and out-of-project reports produce **no test-progress event**. Successful
collection is not inferred from terminal text. Test names become keyed fingerprints;
skipped or removed failing tests remain unresolved rather than counting as fixes.

In observation mode, repeat failures produce evidence and a flag while Claude
continues. Inspect your own runs before enabling enforcement.

## Opt in to pausing

Exit Claude, set `AGENT_SUPRA_CLAUDE_MODE=pause`, then start a fresh session.
For example in PowerShell:

```powershell
$env:AGENT_SUPRA_CLAUDE_MODE = 'pause'
claude
```

Pause mode defaults to **fail closed** when supervision is unavailable. Observation
mode defaults to **fail open**. Override explicitly with
`AGENT_SUPRA_CLAUDE_FAIL_MODE=open` or `closed` if your requirements differ.
A server authentication/configuration rejection always denies PreToolUse. Missing
local configuration follows the configured fail mode and prints a generic warning.
Open the
dashboard to continue with a reason, or stop the run. A parent pause also denies
tool calls from children whose hook payload contains their `agent_id`.

The bridge never returns a permission grant. Claude's existing permission checks
still apply. Already-running tools and model requests cannot be cancelled by this
integration. Hooks do not provide complete per-request token/cost telemetry, so
**spend, context-growth and model-call caps are not measured or enforced here**.
Use Supra's provider SDK wrappers where those controls are needed.

## Session state, privacy and recovery

- Session/agent identifiers are hashed for local lookup. The endpoint, API key and
  mode isolate mappings, preventing one account from reusing another's runs.
- The default state directory is `~/.agent-supra/claude-code`, outside repositories.
  `AGENT_SUPRA_CLAUDE_STATE_DIR` can select another private directory. Do not point
  it at a publicly tracked folder.
- SQLite serializes concurrent hooks. It stores run IDs, a local fingerprint salt,
  report markers and sanitized events. It never stores the API key, raw tool
  arguments, commands, file paths, errors, stdout or conversation transcripts.
- After initial attachment, pending events survive process exits and are retried
  with stable IDs/sequences. A bounded replay and a 500-event queue prevent
  unbounded hook work. Events before initial attachment cannot be recovered in
  fail-open mode. A task may exist without a run if attachment fails midway.
- JUnit is bounded to 2 MB / 5,000 test identities; hook input is bounded to 2 MB.
  Git revision hashing includes tracked changes and at most 5 MB of nonignored
  untracked files. Unavailable revisions are reported without claiming code was
  unchanged.
- SessionEnd closes an active run. Outcome follows the latest effective test
  failures, or is `unknown` with no verified report. A completed run is not proof
  that the user's broader objective was achieved. Paused/stopped runs retain
  their state. A resumed completed session starts a new run on the same task.

Disable with `claude plugin disable agent-supra@agent-supra`. For local
`--plugin-dir` loading, omit that argument on the next launch. Removing the private
state directory loses unsent events and mappings; it does not revoke the API key.
Revoke the key in Supra when you stop using the integration.

## Verification status

Checked on 2026-10-25: **185 regression tests passed**, both manifests validated
with Claude Code **2.1.177**, and the dependency-free hook command loaded with
Python site packages disabled. The final loopback smoke check completed a repaired
fixture after two actual pytest executions, paused an unchanged failure after
four, and recorded flags without blocking five observation-mode executions.
No production keys or model requests were used. Wheel and source-package checks
confirmed that local secrets and state were excluded.

The installed Claude Code CLI validates the plugin/marketplace manifests. API tests
replay documented hook payloads against the real local Supra application, including
pause/observe, parent pauses, concurrent duplicates, report freshness, lifecycle,
privacy and outage replay. A separate smoke script runs actual pytest through the
hook command and a loopback server.

Authenticated native-host verification also passed on 2026-10-25 with the installed
Claude Code CLI and its existing login, model alias `sonnet`, actual Bash tools,
the unmodified plugin and isolated local Supra:

| Scenario | Actual pytest executions | Supra result |
| --- | --- | --- |
| Reviewed repair | 2 | Completed, both reports recorded, final tests passing |
| Unchanged failure | 4 | Paused; Claude requested a fifth tool call and received Supra's denial before execution |
| Observation | 5 | Flagged without blocking; session ended failed because the fixture remained broken |

These were real provider conversations using synthetic fixtures, not replayed hook
inputs. Normal Claude permissions remained enabled with only the fixture's pytest
and reviewed repair commands allowed. No production API key or database was used;
temporary local Supra keys were revoked on cleanup. The CLI's estimated token-cost
values are test diagnostics, not evidence that the hooks measure or enforce spend.
Three new-user setup trials and broader platform validation remain open. No
adoption or setup-time claim is made.

To repeat the authenticated test with dev extras installed:

```sh
claude auth login
python -m scripts.claude_host_test
```

It consumes your Claude quota/provider usage and caps CLI-reported usage at $0.50
per scenario, with eight turns and a 150-second process timeout. Choose one case
with `--scenario healthy`, `stuck` or `observe`. Sanitized evidence is saved under
ignored `.local/claude-code-host/`; raw conversation output is not a report artifact.

Run the local smoke check with dev extras installed:

```sh
python -m scripts.claude_hook_smoke
```

Its sanitized report is written under ignored `.local/claude-code-smoke/`.

Contracts: [Claude Code hooks](https://code.claude.com/docs/en/hooks) and
[plugin reference](https://code.claude.com/docs/en/plugins-reference).
