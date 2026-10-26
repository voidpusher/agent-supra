# Our invoice-reconciliation agent

The [live verification report](live-agent-verification.md) covers production
Gemini runs, dashboard controls, limits, recovery, and alert-verification limits.

This is a working Gemini tool loop that reads a synthetic invoice ledger,
requests deterministic calculator results, chooses corrections, writes the
ledger, and validates it. It is separate from the earlier coding-agent fixtures.
It never makes payments or touches real invoices.

The model chooses between `calculate_total`, `set_amount`, and `remove_duplicate`. Python computes
money in integer cents; model text is parsed as bounded JSON, never executed.
Only the three fixture invoice IDs can be edited. The invoice facts and
validation requirements remain immutable. Reading a calculator does not count
as another failed repair attempt; validation is reported after actual edit
attempts or rejected proposals.

The [broader reliability matrix](reconciliation-reliability.md) adds rounding,
declared duplicate records, missing amounts, partial progress, malformed output,
and provider-error controls. Its planner controls and real-Gemini results are
reported separately.

## Run the isolated assessment

From a checkout with its virtual environment active:

```sh
python -m pip install -e ".[dev]"
python -m scripts.reconciliation_test
```

That runs deterministic controls with synthetic token counts. For actual Gemini
decisions, put `GEMINI_API_KEY` in the ignored `.env` or export it privately:

```sh
python -m scripts.reconciliation_test --gemini
```

The real assessment allows at most 28 model calls: 12 for the healthy scenario,
8 for conflict enforcement, and 8 for conflict observation. Each task has a
$0.05 estimated spend threshold. These are estimates, not invoice guarantees;
an admitted request can overshoot its threshold. Provider charges apply.

No Agent Supra production key is needed. The harness starts a loopback HTTP
service with a separate SQLite database and a temporary internal account/key.
Alerts are captured locally, all workspaces are classified internal, and the
key is revoked at normal shutdown. The model sees only the fixture invoices.
Provider errors fail the scenario instead of being reported as a successful test.

Reports and ledgers live under ignored `.local/reconciliation-test/`. Checkpoints
preserve completed scenario evidence if a process is interrupted. An interrupted
checkpoint does not claim that key cleanup or the assessment completed.

To repeat only one scenario:

```sh
python -m scripts.reconciliation_test --gemini --scenario observe
```

## What we test

| Scenario | Task | Expected Supra behavior |
| --- | --- | --- |
| Healthy | Fix three incorrect invoice totals using calculator quotes | Complete with all checks passing and no loop flags |
| Stuck | Persist while one invoice has two contradictory requirements | Pause for a loop rule and refuse a subsequent model call |
| Observe | The same deliberately persistent conflict | Record a loop flag while allowing all eight calls |

The conflict cases explicitly inject persistent behavior. They test the
supervisor's response to a known failure, not whether Gemini naturally loops on
every impossible task. Observation ends as **failed** at the agent's own attempt
ceiling; it is a passing assessment when no automatic Supra pause occurs.

Real Gemini checks completed on 26 October 2026:

| Scenario | Model calls | Result | Calls reaching provider after pause |
| --- | --- | --- | --- |
| Healthy | 6 | Completed; zero validation failures and no flags | 0 |
| Stuck | 6 | Paused by `no_progress`; next call blocked | 0 |
| Observe | 8 | Eight calls allowed; `no_progress` flagged; local ceiling reached | 0 |

The healthy/stuck results and observation result came from separate assessments:
one earlier assessment received a provider API error during observation, then
the isolated observation repeat passed. Its failed report is retained. An initial
agent version also made incorrect monetary calculations; the calculator-tool
design fixed that problem. These are internal tests, not outside adoption or
evidence that the product has no remaining bugs.

## Run it with your own Supra workspace

Export `AGENT_SUPRA_API_KEY`, `AGENT_SUPRA_URL`, and `GEMINI_API_KEY` privately,
then run from the checkout:

```sh
python -m examples.reconciliation_agent --scenario healthy
python -m examples.reconciliation_agent --scenario stuck
python -m examples.reconciliation_agent --scenario observe
```

These commands create real dashboard tasks in the configured workspace. They
still use synthetic invoices, and they do not launch the isolated service or
change your workspace's cohort classification. Never commit credentials or
paste raw provider errors into public issues.
