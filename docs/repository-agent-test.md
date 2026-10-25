# A supervised coding agent on Agent Supra's own repository

## Verified 25 October 2026

We reproduced a genuine bug in `agent_supra.demo.run_tests`: an old passing JUnit
report could survive a failed pytest invocation and be interpreted as current
success. Missing/malformed reports and empty suites also had misleading behavior.
Six new regression cases failed before the fix; ordinary passing and failing
test results were also protected by two positive cases.

Gemini 3.5 Flash Lite selected the complete repair in one model call. All 14
targeted tests passed, followed by the copied repository's full 145-test suite.
The successful repair was reviewed and applied to the main working copy. It
removes stale reports before pytest, checks runner exit codes, reports malformed
or missing XML clearly, and rejects empty test suites.

A separate scripted agent repeatedly selected an incomplete repair on another
copy. Four tests remained failing; Supra paused it after four model calls. The
next wrapped call was blocked. The HTTP continue action permitted exactly one
additional call; the HTTP stop action then blocked the next call and left the
run stopped. These controls did not change the healthy run.

Agent-triggered alerts were captured locally, including their run-review links.
A separate production test-alert action returned `alert-sent` with both saved
Slack and email destinations present. This verifies the application's successful
Slack/SMTP submission path, not independently observed recipient inbox delivery.
No new production API key was created. The Settings page hid the success banner;
that separate UI bug was reproduced and fixed, with regression coverage.

## Reproduce the benchmark

Run from a Git checkout containing historical commit `275cc35`, with dev
dependencies installed:

```bash
python -m scripts.repository_agent_test --gemini
```

Without `--gemini`, the healthy case uses a scripted complete repair plan and
does not call an LLM. `--model` selects the Gemini model. The existing ignored
`.env` or environment supplies `GEMINI_API_KEY`. Each task has a $0.05 estimated
supervision budget and a six-call cap; an in-flight provider call can exceed
that estimate. The script does not send live Slack or email alerts.

This is a **bounded coding-agent benchmark**, not a general-purpose coding CLI.
The model reads the real function and failing test IDs, then selects JSON repair
operations. Reviewed tools perform edits only inside that one approved function.
The model cannot choose file paths, edit tests, or execute generated commands.

Git archive creates separate copies of the real historical source. `.env`,
`.local`, Git credentials and production data are excluded. Child test processes
have credential-related environment variables removed. Tests are immutable, and
the complete copied suite checks that the repair does not break existing behavior.
The pinned historical revision makes the bug reproducible after the main branch
is fixed; it is not a claim that current main still has the bug.

## Evidence

The ignored timestamped `.local/repository-agent/` directory contains:

- `report.html` and `report.json`, with the actual baseline, attempts, run IDs,
  pause reason and control checks.
- `healthy-repo/` and `stuck-repo/`, with the source snapshots and pytest XML/output.
- `supervisor.db`, the isolated supervision timelines.

The temporary HTTP server stops and its API key is revoked on exit. All runs
are internal and excluded from outside-user beta counts. A passing benchmark
validates these exercised flows; it does not establish general coding-agent
reliability or satisfy the private-beta usage gate.
