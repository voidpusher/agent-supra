# Live invoice-agent verification

The invoice agent was tested against `https://agentsupra.in` on 26 October 2026,
using real Gemini calls, synthetic invoices, and a dedicated temporary API key.
The automated harness is `scripts/live_reconciliation_test.py`.

## Observed results

| Check | Result |
| --- | --- |
| Healthy invoice reconciliation | Completed after 6 Gemini calls, with no loop flag |
| Persistent conflicting requirements | Paused after 6 calls by `no_progress`; next provider call blocked |
| Observation mode | 8 calls allowed; loop flagged; agent ended at its own local ceiling |
| Usage accounting | API timeline token counts and estimated spend agree with the run totals |
| Dashboard Continue | A waiting agent executed exactly one tool after the button was used |
| Dashboard Stop | The next tool and model-call boundary were blocked |
| Estimated budget and call cap | Enforced even in observation mode, using clearly labeled synthetic accounting fixtures |
| Client outage, fail open | Buffered test events reached the live server after client transport recovery |
| Lost acknowledgement | Retried accepted events were not duplicated |
| Client outage, fail closed | Prevented the tool from executing |
| Slack/email test | Dashboard returned `alert-sent` with both destinations configured |
| Temporary credential cleanup | Replacement test key revoked; subsequent authentication returned HTTP 401 |

Gemini returned HTTP 429 during the first observation attempt. The failed
assessment was retained; observation passed in a separate paced repeat. This
is combined evidence from several assessments, not one uninterrupted pass.
Client-side fault injection never shut down production. No workspace defaults
were changed.

The synchronous alert test confirms that the configured transports accepted
delivery without errors. Actual Slack-channel and recipient-inbox receipt were
not independently verified: the connected Gmail account differs from the alert
recipient, and Resend was signed out. This remains an explicit verification gap.

Temporary test keys were revoked. The dedicated ignored credential file was
removed after authentication rejection was verified. No credential was committed
to Git or included in the reports.

## Reproduce with an authorized dedicated key

Use a checkout with the dev dependencies installed. Export a dedicated
`AGENT_SUPRA_API_KEY` and explicit `AGENT_SUPRA_URL=https://agentsupra.in`, or put
those two values in ignored `.local/live-reconciliation.env`. Do not use a key
from shell history, old scripts, public logs, or chat. Revoke the key after tests.
The Gemini key can come from the existing ignored `.env` or the environment.

```sh
python -m scripts.live_reconciliation_test --run-live --dashboard-controls
```

The command creates internal test runs and can trigger the workspace's configured
Slack/email alerts. It does not create or revoke keys, change workspace defaults,
or verify delivery receipt. It prints the test run URL when waiting for dashboard
Continue and Stop actions. Each wait expires after three minutes and stops only
that test run. Use API controls by omitting `--dashboard-controls`.

Provider dispatches are spaced at least five seconds apart. Explicit SDK retries
are disabled for this assessment; provider errors are reported as failures.
Pacing does not guarantee that project quotas are sufficient. Invoice scenarios
retain their per-task estimated spend thresholds and local step ceilings.

For independent phases:

```sh
# No Gemini calls; includes synthetic limit fixtures and their possible alerts
python -m scripts.live_reconciliation_test --run-live --skip-agent --dashboard-controls

# Only repeat observation; do not repeat the limit/control fixtures
python -m scripts.live_reconciliation_test --run-live --agent-only --scenario observe
```

Reports are saved under ignored `.local/live-reconciliation/`, excluding keys,
provider prompts/replies, webhook URLs, and mailbox contents. A fresh assessment
starts with receipt unverified rather than inheriting earlier alert claims.

Local regression tests run the limit, outage, lost-acknowledgement, control,
credential-target, redirect, and pacing checks against disposable fixtures in
CI. They never use production credentials or send Slack/email messages. These
internal tests do not establish outside-user adoption or prove absence of bugs.
