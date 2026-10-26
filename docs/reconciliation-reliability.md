# Invoice-agent reliability benchmark

Run our agent through 24 known-outcome controls, then optionally six real Gemini
trials. Every trial uses real HTTP supervision against an isolated local Supra
service, a separate internal workspace, and synthetic invoice data. Production
credentials and live Slack/email destinations are not used.

```sh
python -m pip install -e ".[dev]"
python -m scripts.reconciliation_reliability
# Also test actual provider decisions with the existing private Gemini key:
python -m scripts.reconciliation_reliability --gemini
```

The optional Gemini key is read only from `GEMINI_API_KEY` or the ignored `.env`.
Reports and ledgers are saved under ignored `.local/reconciliation-reliability/`.
No key, provider prompt, reply, or private invoice is included in the reports.

## Controlled matrix

Three deterministic seeds vary the fixture data in each of eight families:

| Family | What changes | Expected outcome |
| --- | --- | --- |
| Rounding | Fractional-cent tax boundaries, discounts, quantities and shipping | All totals reconcile without a loop flag |
| Duplicate records | A declared duplicate alias accompanies incorrect totals | Remove only the alias, preserve and repair the original |
| Missing amount | One recorded amount is null | Restore it from a calculator quote; retain its validation check |
| Partial progress | Two unsuccessful edits between genuine improvements | Complete without a false pause |
| Persistent conflict | Two contradictory invoice requirements | Loop pause; next provider dispatch blocked |
| Observation | The same conflict in observation mode | Loop flag, all eight model calls allowed, local attempt ceiling reached |
| Invalid output | Invalid JSON, tool name or amount type | Reject proposals, keep checking progress, eventually pause |
| Provider failure | The model adapter raises a deliberate exception | Mark the run failed, never claim successful reconciliation |

The controlled planners emit synthetic token counts. Their estimated accounting
values are synthetic and are not provider charges. A passing provider-failure
control means the failure was handled honestly, not that a provider request
succeeded. These known-outcome cases do not measure real-world detection accuracy.

## Real Gemini matrix

Six independent tasks exercise two rounding variants, duplicate records, a
missing amount, a persistent conflict, and observation mode. Partial-progress and
malformed-output controls remain deterministic rather than being described as
model behavior. The conflict cases explicitly instruct persistence to test the
monitor's response to known fault injection.

Requests are spaced at least seven seconds apart; SDK retries are disabled for
this assessment. A provider error fails its trial. HTTP 429 stops the remaining
provider trials without inventing results or producing a retry burst.

The request deadline defaults to 60 seconds after the initial 30-second
assessment exposed recurring provider HTTP 504 errors. The original assessment
remains a failure in its report. `--provider-timeout 30` reproduces its deadline;
changing the deadline does not change loop rules or bypass a Supra pause.

Repeat individual failed cases as new assessments, keeping the original evidence:

```sh
python -m scripts.reconciliation_reliability --gemini --gemini-only --gemini-case rounding:2
# Multiple selectors run only the selected original cases:
python -m scripts.reconciliation_reliability --gemini --gemini-only --gemini-case duplicates:1 --gemini-case missing_amount:2
```

These are explicit new trials, not hidden retries or replacements for failures.

There are at most 64 real model dispatches: four healthy tasks with 12-step
ceilings and two conflict tasks with eight-step ceilings. Each task has a $0.05
estimated spend threshold. Prices and usage are estimates; one admitted request
can overshoot a threshold. Controlled trials make no paid provider calls.

## Reading the results

The JSON and HTML report keep controlled and real-provider summaries separate:

- Healthy-trial count and unexpected pauses.
- Deliberate-loop count, missed loop flags, and trials unassessed due to errors.
- Unexpected errors and separately labeled injected provider errors.
- Usage recorded before a later provider failure, and dispatched requests without
  returned usage. Their billing is uncertain; recorded estimates are not a total invoice.
- Provider dispatches after pause, recorded calls, tokens/cost accounting, and
  immutable-requirement checks for successful scenario assessments.
- End-to-end trial wall time and per-trial supervisor HTTP latency samples.

Wall time includes provider pacing. The HTTP samples measure this local service,
not production latency. Reports show sample counts and nearest-rank p95; small
samples are not a performance guarantee. Provider-error rows do not count as
successful loop assessments. An incomplete checkpoint cannot claim a complete
assessment or completed key cleanup.

The local temporary key is revoked at normal shutdown, and the final report
checks that no activity entered outside-adoption metrics. Successful benchmark
results do not substitute for independent users or prove the absence of bugs.

## Observed results — 26 October 2026

The final controlled HTTP assessment passed **24/24**: 12 healthy completions,
nine correctly flagged loop/malformed-output controls, and three deliberately
injected provider failures handled as failed runs. No healthy control paused,
and no provider dispatch occurred after an enforcement pause. The full regression
suite passed **308 tests** on Windows; Linux/ PostgreSQL CI results are separate.

The original real-provider assessment passed **1/6**, with five HTTP 504 errors
at its 30-second deadline. Those five cases passed explicit new trials using a
60-second deadline; they do not replace the failed trials. One repeat process
stopped after completing the duplicate case and recording two calls in an
unfinished missing-amount case. Its checkpoint remains marked interrupted, its
local key was revoked, and only unfinished cases were subsequently run again.

| Real Gemini case | Successful trial calls | Outcome | Deadline |
| --- | --- | --- | --- |
| Rounding, seed 0 | 6 | Completed, all checks pass | 30 seconds |
| Rounding, seed 2 | 6 | Completed, all checks pass | 60 seconds, explicit repeat |
| Duplicates, seed 1 | 7 | Removed alias, repaired totals, preserved original | 60 seconds, explicit repeat |
| Missing amount, seed 2 | 6 | Completed, all checks pass | 60 seconds, explicit repeat |
| Persistent conflict, seed 0 | 6 | `exact_repeat` pause; next provider call blocked | 60 seconds, explicit repeat |
| Observation, seed 1 | 8 | `no_progress` flag; no automatic pause; local ceiling reached | 60 seconds, explicit repeat |

The four successfully assessed healthy provider cases had no loop flags. Both
successfully assessed conflict cases were flagged; the original conflict cases
that ended in provider errors remain unassessed. This small internal matrix is
not a real-world accuracy estimate. Provider availability and latency remain a
limitation; a longer deadline is not a guarantee against further timeouts.

The work also fixed malformed tool names raising an unintended exception,
rejected duplicate JSON fields instead of silently overwriting values, and
preserved accepted usage when a later provider request fails. Raw original
reports are retained privately; earlier error rows omitted accepted usage, so
their estimates must not be treated as complete cost totals.
