# Test Agent Supra with our own repair agent

The internal repair agent reads a tiny broken discount function, proposes a fix,
edits a real file, runs real pytest tests, and reports each attempt to Agent Supra
through its HTTP client. A disposable loopback server uses a separate SQLite
database. It does not use the production API or production credentials.

From the repository with dev dependencies installed:

```bash
python -m scripts.self_test_agent
python -m scripts.self_test_agent --gemini
```

The first command is free and repeatable. It uses **scripted fault fixtures**, not
an LLM: a healthy repair, a stuck loop, observation mode, and a synthetic budget
limit. The synthetic model and token usage are explicitly labeled in the report.

The second command also runs **real Gemini** against a solvable bug and mutually
contradictory tests. It reads `GEMINI_API_KEY` from the environment or the ignored
project `.env`. Set `GEMINI_MODEL` or pass `--model` to select another available
model. Each real run has a $0.05 estimated supervision budget and at most six
model calls; an already in-flight call can exceed that estimate. Provider charges
are separate, and actual results and pause reasons are recorded rather than assumed.

## What is verified

- Healthy repairs complete with passing tests and no spurious flag.
- Repeated failed attempts trigger a loop pause.
- Observation mode records a flag while allowing the loop to continue to its step limit.
- A deliberate synthetic budget breach pauses even without useful progress.
- The wrapped model refuses the next call after a pause, and no model-call event
  is accepted after the pause during this test.
- Events flush, local alert callbacks run, and internal tests never count as outside users.

Proposals are limited to a small, bounded arithmetic expression language. Imports,
calls, attributes, conditionals and executable statements are rejected before writing
the file. Tests are still run and reported after a rejected proposal: a failed proposal
must not hide an attempt without progress. This is a constrained toy repair agent,
not a sandbox for arbitrary generated programs.

## Evidence and cleanup

Each invocation creates a timestamped directory under ignored `.local/self-test/`:

- `report.html`: readable scenario results.
- `report.json`: actual run IDs, states, call counts, reasons, attempts and checks.
- `supervisor.db`: isolated run timelines for inspection.
- Scenario folders: the toy repositories and JUnit test results.

The local server stops and its temporary API key is revoked on exit. No API key
or provider credential is written into the report. A nonzero command exit means
a scenario failed its expectation or an error occurred; do not use a failed report
as evidence of success. These tests complement outside-user feedback and do not
satisfy the roadmap's private-beta gate.

## Verified internal run — 25 October 2026

The full HTTP self-test with Gemini passed all six scenarios:

| Scenario | Source | Result | Model calls | Detection |
| --- | --- | --- | --- | --- |
| Healthy repair | Scripted fixture | Completed | 3 | No flag |
| Repeated bad edits | Scripted fixture | Paused | 4 | No progress |
| Observation mode | Scripted fixture | Continued to configured failure limit | 6 | Flag only |
| Budget breach | Synthetic usage fixture | Paused | 1 | Budget |
| Solvable bug | Real Gemini 3.5 Flash Lite | Completed, tests passing | 1 | No flag |
| Contradictory tests | Real Gemini 3.5 Flash Lite | Paused | 3 | Exact repetition |

In the real impossible case, all three model proposals were rejected by the
arithmetic validator, so the original implementation stayed unchanged. Real tests
were rerun and reported each time; the supervisor paused the unchanged failures.
This is evidence of detecting an unproductive agent, not evidence of four distinct
Gemini-generated edits. The scripted stuck case separately verifies changing code
without test progress. All paused cases rejected the next wrapped model call,
and their timelines contained zero model-call events after the pause.

The temporary HTTP key was revoked, the server stopped, alert callbacks were
captured locally, and the beta dashboard excluded every internal run. The exact
run evidence is in the ignored timestamped report directory on the test laptop.
Future Gemini runs can take a different path or fail; trust the report's actual
states and nonzero exit code rather than assuming these counts will repeat.
