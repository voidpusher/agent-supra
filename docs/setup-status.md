# Setup status — 20 October 2026

Agent Supra 1.0.0 is published on PyPI and deployed from `main` at
<https://agentsupra.in>. Vercel hosts the FastAPI app and Neon hosts its
production PostgreSQL database. The public `/healthz` endpoint, live Gemini
completed and paused runs, Slack and Resend alerts, and operator-only server
error monitoring have been verified. The external `production health` GitHub
Actions workflow checks `/healthz` every 15 minutes.

Neon's point-in-time recovery and manual snapshot each passed an isolated
restore. A dedicated read-only database role feeds an encrypted daily GitHub
Actions backup. The first scheduled run passed on 20 October, retained an
encrypted artifact, and a previously retained artifact was independently
restored on a local PostgreSQL 16 server. The schedule ran over five hours
later than its configured time, so it should not be treated as a fixed
recovery-point guarantee. A separate private copy of the encryption key is
still needed. The user elected to keep the snapshot-restored Neon branch for
now; it contains a copy of production data.

The privacy page now describes the actual processors and explains that
recovery copies outlive deletion from the active database. Remaining launch
work includes live outage verification, final legal review, a demo video,
and feedback from 3–5 private-beta users. See
[launch-test-report.md](launch-test-report.md) for evidence and limitations.
