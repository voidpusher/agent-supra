# Setup status — 19 October 2026

Agent Supra is deployed at <https://agentsupra.in> from
`voidpusher/agent-supra` on `main`. Vercel serves the FastAPI app in Singapore
and uses Neon Postgres. The local environment, demo, and full 107-test suite work.
Vercel deployed the operator-only server-error inbox and self-test at commit
`d4132a7`; `/healthz` returned `{"ok":true,"version":"1.0.0"}`.

The production dashboard and API have been exercised with labeled test runs.
A solvable real Gemini run completed; an impossible real Gemini run paused after
four calls with evidence visible in the dashboard. The temporary verification
API key was revoked and then rejected with 401. The package builds cleanly,
and a fresh install from GitHub works.

Live Slack and Resend test alerts were delivered on 15 October. The new
first-party error-monitoring self-test still needs to be triggered in the
production operator session and its alert delivery verified. The hosted beta
also needs a Neon backup restore test, PyPI publication, final terms review,
and beta-user feedback before a public launch. See [launch-test-report.md](launch-test-report.md)
for the exact checks, run IDs, and limitations. See [deploying.md](deploying.md)
for current Vercel operations.
