# Setup status — 13 October 2026

Agent Supra is deployed at <https://agent-supra.vercel.app> from
`voidpusher/agent-supra` on `main`. Vercel serves the FastAPI app in Singapore
and uses Neon Postgres. The local environment, demo, and full 98-test suite work.
GitHub Actions passes for the deployed commit.

The production dashboard and API have been exercised with labeled test runs.
A solvable real Gemini run completed; an impossible real Gemini run paused after
four calls with evidence visible in the dashboard. The temporary verification
API key was revoked and then rejected with 401. The package builds cleanly,
and a fresh install from GitHub works.

The hosted beta still needs real Slack or email alert delivery, Sentry error
notifications, a Neon backup restore test, PyPI publication, final terms review,
and beta-user feedback before a public launch. See [launch-test-report.md](launch-test-report.md)
for the exact checks, run IDs, and limitations. See [deploying.md](deploying.md)
for current Vercel operations.
