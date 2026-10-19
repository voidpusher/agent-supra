# Setup status — 19 October 2026

Agent Supra is deployed at <https://agentsupra.in> from
`voidpusher/agent-supra` on `main`. Vercel serves the FastAPI app in Singapore
and uses Neon Postgres. The local environment, demo, and full 107-test suite work.
Vercel deployed the operator-only server-error inbox, self-test, and password
recovery at commit `cffb79b`; `/healthz` returned
`{"ok":true,"version":"1.0.0"}`.

The production dashboard and API have been exercised with labeled test runs.
A solvable real Gemini run completed; an impossible real Gemini run paused after
four calls with evidence visible in the dashboard. The temporary verification
API key was revoked and then rejected with 401. The package builds cleanly,
and a fresh install from GitHub works.

Live Slack and Resend run alerts were delivered on 15 October. On 19 October,
the operator recovery email and first-party error email were delivered. A
protected production self-test recorded two 500s in one error row and sent a
single Slack alert to `#all-orrico-labs` and email to `sanchitp498@gmail.com`.
On 19 October, a Neon point-in-time branch and a manual snapshot were each
restored into isolated branches and checked against production reference data.
The snapshot is kept, and the user chose to keep its restored branch for now.
Recurring, independently stored backups remain unconfigured: the Free plan
shows six hours of history and no snapshot schedule. The hosted beta also
needs PyPI publication, final terms review, and beta-user feedback before a
public launch. See [launch-test-report.md](launch-test-report.md)
for the exact checks, run IDs, and limitations. See [deploying.md](deploying.md)
for current Vercel operations.
