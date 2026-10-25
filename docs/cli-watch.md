# CLI watch — experimental

Launch an existing command under Supra without editing the agent loop:

```sh
agent-supra watch -- python my_agent.py
```

The wrapper creates a dashboard run, forwards the command's normal terminal I/O,
and records its exit. JUnit reports provide test-progress detection. Model calls,
tokens, context growth and spend are **not measured or enforced** by this wrapper.
Use the SDK wrappers for those signals; the provider proxy is still a later item.

## Install and connect

This command is newer than the published `agent-supra==1.0.0` package. Install the
current source in your virtual environment:

```sh
pip install --upgrade --force-reinstall "git+https://github.com/voidpusher/agent-supra.git"
agent-supra watch --help
```

Set `AGENT_SUPRA_API_KEY` and `AGENT_SUPRA_URL` as in the
[quickstart](https://agentsupra.in/docs/quickstart). The wrapper defaults to
`https://agentsupra.in`. Keys are read from the environment, never command flags
or project configuration files. It does not automatically load `.env` files.
The child inherits your normal environment with **AGENT_SUPRA_API_KEY removed**.
Use existing SDK/hook instrumentation directly rather than wrapping it again.

## Track tests

For a single pytest command:

```sh
agent-supra watch --junit ".local/results.xml" -- python -m pytest --junitxml=.local/results.xml
```

For an agent that repeatedly runs tests, configure it to generate JUnit reports,
then point the watcher at them:

```sh
agent-supra watch --junit ".local/results/*.xml" -- python my_agent.py
```

Keep reports in a Git-ignored directory: they may contain private test details.
For fast attempts, write a separate report per attempt to preserve each snapshot.
A single overwritten file can collapse multiple attempts between polls. Paths
and globs must remain inside `--cwd` (the current directory by default); symlinks
pointing outside are rejected. Matching more than 200 files stops the watcher;
rotate old reports or narrow the pattern. Repeat `--junit` for multiple sources.

Existing reports establish a baseline and are not counted as new attempts.
Missing, malformed, oversized or still-changing reports are not interpreted as
passing tests. Reports are bounded to 2 MB and 5,000 test identities each. Names
are keyed fingerprints; output and failure messages are discarded locally.
Skipped or removed failing cases remain unresolved. The final report is checked
again after process cleanup so a result written just before exit is not lost.

The watcher cannot prove the code revision used by an independently running test.
It records `rev=null`, allowing **no-progress** detection without claiming an exact
repeat or a verified code-version cycle. These are observed test snapshots, not
model calls or agent turns.

## Observation, enforcement and Stop

Observation is the default. Heuristic findings create flags while the process
continues. Once you have reviewed your agent's results, opt into cancellation:

```sh
agent-supra watch --mode enforce --junit ".local/results/*.xml" -- python my_agent.py
```

When Supra pauses a run, enforcement **terminates the owned process tree**. It
does not suspend or resume an arbitrary program. Termination can interrupt work
in progress. The dashboard keeps the paused state and evidence. A dashboard Stop
cancels the owned process tree in either mode. Continue records review state;
it does **not** restart the cancelled OS process. Start a new `watch` invocation
when you want to run the command again. There is no automatic retry or restart.

Polling defaults to 0.5 seconds (`--interval`, allowed range 0.1–60 seconds).
Network latency and in-flight work mean cancellation is not a tool-call boundary
guarantee; additional work may occur before a pause is observed. Use the Claude
Code plugin or SDK checks for interception at their supported boundaries.

Windows uses a Job Object and a trusted bootstrap that joins it before launching
the command. No process-name scan or unrelated-process termination is used. On
POSIX, a new session/process group is terminated. Remote jobs, processes that
escape those OS mechanisms, and detached external services are not covered.
Abrupt monitor death on POSIX can leave a process group alive; use a service
manager/container where stronger lifetime guarantees are required. This is
process supervision, not a security sandbox. Interactive job control and other
platforms need further validation; native Windows behavior has been exercised.

`--timeout 300` applies a local five-minute wall-clock limit. Ctrl+C cancels the
owned process tree. Timeout/interruption records a Stop when the service is
reachable. A normal exit also cleans up descendants left behind by the command.
Native executables/Python commands are tested; shell pipelines require an
explicit shell command with its own quoting and platform behavior.

## Service outages and private recovery

Observation defaults to fail open; enforcement defaults to fail closed. Override
with `--fail-mode open` or `closed`. Fail closed refuses to launch if supervision
is unavailable and cancels a running process when the service becomes unavailable.
Authentication/configuration rejections always refuse launch or cancel the child.
Fail open continues locally, retaining events until they can be delivered.

Each invocation creates a private journal under `~/.agent-supra/watch/<id>`.
`--state-dir` selects another private base directory. Journals contain sanitized
events, run/task IDs, a fingerprint salt and the explicit dashboard label (`--name`).
Command arguments, stdout/stderr, report paths, raw test names and API keys are
not journaled or uploaded. Output still appears in your local terminal normally.

The outbox retains stable event IDs and sequences across retries. A single-writer
lock prevents simultaneous live/replay owners. It holds up to 5,000 pending events,
plus a reserved final outcome; reaching capacity cancels the command rather than
silently discarding telemetry. Acknowledged history remains local until you remove
the completed journal. Only remove it after pending events have been delivered.

If the last flush cannot finish, the CLI prints the recovery location. After the
service recovers, use the original endpoint and API key:

```sh
agent-supra watch --replay "/private/path/to/watch/id"
```

Replay sends existing sanitized events only. It never launches or restarts a
command, or invents an outcome for an abruptly interrupted journal. A different
key/endpoint is rejected; cross-key recovery after rotation is not implemented.
Late outcome delivery preserves existing paused/stopped states. Initial attachment
can leave an empty server task if the response or subsequent run creation fails.

| Exit | Meaning |
| --- | --- |
| Child's exit code | Normal process completion (negative signals mapped to 128+signal) |
| 69 | Supervision unavailable / replay incomplete |
| 75 | Supra paused the run |
| 78 | Configuration, report scope, or telemetry-capacity error |
| 124 | Local timeout |
| 125 | Reviewer Stop / process containment failure |
| 130 | User interruption |

Codes can overlap with the child program's own exit codes; inspect the dashboard
and CLI status. Exit 0 without verified tests means the process ended normally,
not proof that its broader objective was achieved.

## Verification

On 2026-10-25, **209 regression tests passed**. Native Windows smoke checks ran the
actual CLI, real pytest subprocesses and a loopback Supra API: a repaired fixture
completed after two reports, a stuck fixture was cancelled with four failing
reports observed, and observation mode allowed five attempts and recorded a flag.
No production data, external alerts or model calls were used; temporary local
keys were revoked. Additional checks cover grandchild cleanup, immediate Stop,
outages, private replay, stale reports and the final-report exit race.

Repeat locally with dev extras installed:

```sh
python -m scripts.watch_smoke
```

Sanitized results are saved under ignored `.local/watch-smoke/`. New-user setup
trials, macOS/Linux native validation, provider spend telemetry and richer output
parsers remain open.

Process-control references: [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
and [Python subprocess](https://docs.python.org/3/library/subprocess.html).
