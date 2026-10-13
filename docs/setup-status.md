# Setup status — 13 October 2026

## Completed

- Installed Python 3.12 virtual environment and editable development dependencies.
- Ran the scripted local demo: Run A paused after four attempts; Run B completed.
- Generated `supervisor_report.html`.
- Ran the scripted demo against the live local API; both runs are stored in the dashboard.
- Started the local service on port 8000; `/healthz` returned `ok: true`, version `1.0.0`.
- Created and verified the local account for `sanchitp498@gmail.com` and a development API key.
- Verified login, dashboard, API keys, settings, privacy, and terms pages over HTTP.
- Passed all 95 tests, including serverless coverage (one upstream Starlette test-client deprecation warning).
- Automated coverage includes observation mode, budgets, continue/stop, alert dispatch with
  a test sender, and simulated fail-open/fail-closed outages with event recovery.
- Added a draft `/terms` page and contact links; reused the existing site styling.
- Added a Windows local launcher that reads `.env`, plus a Gemini `--fail-mode` option.
- Built wheel and source distribution; both passed `twine check`. Installed the wheel in
  a fresh virtual environment and verified the dependency-free SDK import and packaged template.
- Prepared beta invitation text, feedback template, and a video shot list.

## Still needs account access or real-world validation

- Real Gemini tests: `GEMINI_API_KEY` is not configured. No paid Gemini calls have been made.
- Real Slack/email delivery: webhook or SMTP credentials and destination are not configured.
- PostgreSQL tests: no local Docker/Postgres instance is available; the existing GitHub CI
  workflow is configured to test SQLite and Postgres after the repository is pushed.
- PyPI publication: release files are local only; PyPI authentication is needed.
- GitHub: intended destination is `voidpusher/agent-supra`; repository access is still needed.
- Vercel: prepared the FastAPI entrypoint, PostgreSQL connection handling, retention cron,
  and deployment configuration. Created the Vercel project and Neon resources; database
  credentials, deployment, and a live health check remain pending. Vercel replaces Render
  as the requested deployment target.
- Domain and social handles: exact domain, purchase budget, and account access are needed.
- Sentry/SMTP: optional services have not been configured or verified.
- Terms: the page is a draft, not a completed legal review.
- Video: shot list prepared; real Gemini footage has not been recorded.
- Beta invitations: no recipients supplied and no invitations sent. The week of observation
  and feedback has not taken place.

See `windows-local.md` for paths, credentials locations, and PowerShell commands.

## Deployment reference checked

The plan IDs already in `render.yaml` match Render's current compute-plan documentation:
<https://render.com/docs/compute-plans>. Deployment has not been performed.
