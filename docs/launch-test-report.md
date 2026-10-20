# Launch verification — 13–20 October 2026

Status: **the hosted beta works end to end, but public launch is not complete**.

## Verified

- Production commit `12cc79a` deployed successfully on Vercel in Singapore (`sin1`).
  `/healthz` returned `{"ok":true,"version":"1.0.0"}`. Public landing and
  quickstart pages returned 200, and the GitHub install instructions appeared.
- Requests to `/v1/whoami` without a key and `/api/cron/prune` without the cron
  secret returned 401. The daily cron's actual scheduled execution has not yet
  been observed.
- A temporary production key authenticated. Live API runs completed, paused
  after four repeated failures, flagged in observation mode without pausing,
  and paused on budget even in observation mode. The dashboard showed the
  correct states and evidence. Continue with a reason and Stop worked there.
- Real Gemini 3.5 Flash-Lite fixed the solvable example and the run completed
  (`run_df5188d7c7c747ed`). Real Gemini 3.1 Flash-Lite made four calls on the
  impossible example; production paused it for no progress
  (`run_26d831f1a037439e`). The run remains paused for dashboard review.
- The temporary production key was revoked, a follow-up API request returned
  401, and its ignored local credential file was removed. Other temporary
  running or paused smoke-test runs were stopped.
- Local regression suite: 98 passed, one upstream Starlette deprecation warning.
  The source distribution and wheel passed `twine check`. A fresh virtual
  environment installed the SDK from the public GitHub repository and ran its
  command successfully. GitHub Actions for commit `12cc79a` passed.
- On 14 October, a live Gemini 3.5 Flash-Lite check verified sync model,
  sync stream, chat, chat stream, async model, and async stream. Each path
  recorded one call with input tokens; a paused run blocked the next call
  locally. The repeatable check is `scripts/live_gemini_wrappers.py`.
- On 15 October, a live Slack test alert appeared in Orrico Labs
  `#all-orrico-labs`. Resend marked two test alerts to `sanchitp498@gmail.com`
  delivered. The second message, sent after the custom-domain deployment,
  linked to `https://agentsupra.in/app/runs/run_example`.
- `agentsupra.in` and `www.agentsupra.in` show valid Vercel configurations.
  `https://agentsupra.in/healthz` returned `{"ok":true,"version":"1.0.0"}`,
  and `www` redirected to the apex. The quickstart now advertises
  `AGENT_SUPRA_URL="https://agentsupra.in"`.
- The Slack/email fanout fix in commit `2b51ef0` passed 100 local tests and
  deployed successfully. Slack delivery remains independent of SMTP errors.
- On 19 October, first-party server-error monitoring and an operator-only
  self-test deployed at `d4132a7`. The 105-test local suite passed, Vercel
  marked the production deployment Ready, and the custom-domain `/healthz`
  returned `{"ok":true,"version":"1.0.0"}`. Live error capture and alert
  delivery still await the operator session test.
- Email-based password recovery was added for the inaccessible production
  operator account. It uses a single-use, 30-minute token and invalidates
  existing sessions after reset. The 107-test local suite passed. The
  recovery email was delivered through Resend, and the operator signed in.
- On 19 October, the operator-only production self-test returned the expected
  generic 500 and created a `MonitoringSelfTest` entry in `/app/errors`.
  Repeating it increased the occurrence count to 2 without a second email.
  The first alert appeared in Orrico Labs `#all-orrico-labs`, and Resend
  marked the email to `sanchitp498@gmail.com` delivered. Unauthenticated
  requests to `/app/errors` redirected to `/login`.
- On 19 October, Neon project `agent-supra` in the Kampher organization was
  confirmed as the production database by the known live run
  `run_26d831f1a037439e`. Its `production` branch had 2 users, 8 runs, and
  schema version 1. An isolated point-in-time branch from approximately
  11:25 IST returned the same values and the expected `errors`,
  `password_resets`, and `sessions` tables. That temporary branch was removed.
- A manual snapshot of `production` was created at 11:31 IST. Neon shows it
  as non-expiring. A multi-step restore created a separate branch, leaving
  production unchanged; the restored branch returned the same counts, known
  run, schema version, and tables. The user chose to keep the restored branch
  `branch_from_snapshot_Oct 19, 2026 11:31 AM GMT+5:30` for now. The live
  `/healthz` check remained healthy after the recovery drill.
- On 19 October, GitHub Actions `production health` run
  [#1](https://github.com/voidpusher/agent-supra/actions/runs/39242870800)
  successfully checked the public `/healthz` endpoint. The first scheduled
  [run #2](https://github.com/voidpusher/agent-supra/actions/runs/39294772027)
  also passed. Both encrypted-backup repository secret names are
  present. Its first manual run
  [#1](https://github.com/voidpusher/agent-supra/actions/runs/39246192766)
  failed because Neon rejected the `agent_supra_backup` login password. No
  encrypted backup artifact exists yet. A proposed `agent_supra_backup_v2`
  login is not present in Neon's production Roles page as of the latest check.
- Later on 19 October, `agent_supra_backup_v2` was created on the production
  branch and authenticated with SELECT on all 13 public relations, no INSERT
  grants, and no administrative role attributes. The GitHub
  `BACKUP_DATABASE_URL` secret was updated to this login. Manual backup
  [run #2](https://github.com/voidpusher/agent-supra/actions/runs/39300257444)
  succeeded, including encrypted export, decryption, isolated restore, and
  table checks. GitHub retained encrypted artifact
  `agent-supra-db-39300257444-1` (27.4 KB). The first scheduled run has not
  yet occurred.
- The backup login password was subsequently rotated, the GitHub secret was
  updated again, and manual
  [run #3](https://github.com/voidpusher/agent-supra/actions/runs/39300792844)
  passed with encrypted artifact `agent-supra-db-39300792844-1` (27.4 KB).
  This run verifies the credential currently configured in GitHub.
- On 20 October, the first scheduled backup
  [run #4](https://github.com/voidpusher/agent-supra/actions/runs/39387501599)
  succeeded and retained encrypted artifact `agent-supra-db-39387501599-1`.
  It started at 13:05 IST rather than the configured 07:47 IST, showing that
  GitHub's schedule can be delayed substantially. The downloaded run #3
  artifact was independently decrypted and restored to a disposable local
  PostgreSQL 16 server. The restored database had 2 users, 8 runs, schema
  version 1, 12 base tables, and the known paused Gemini run. The local
  plaintext dump and test database were removed.
- On 20 October, a production API outage-recovery drill used a temporary key
  and a transport that returned a simulated 503 while leaving the hosted
  service online. Fail-open created a pending task/run, buffered the start and
  progress events, then replayed both to the live API and completed run
  `run_2d1352b5e64e4b8e`. Fail-closed refused the next step; after the
  transport recovered, test run `run_df49d54d89b94a29` was stopped. The
  temporary key was revoked in the dashboard. This verifies SDK behavior
  against the production API after recovery, not a real Vercel outage.
- A 60-second, 1920×1080 captioned edited replay was rendered locally from
  the verified Gemini attempt log and paused-run screenshot. It clearly labels
  itself an edited replay, masks the operator account label, includes the
  separately verified completed run, and contains no keys. Duration and
  representative cut-boundary frames were checked. The file is local at
  `.local/demo-video/edit/final.mp4`; it has not been posted publicly.
- On 19 October, the current 109-test local regression suite passed, the
  source distribution and wheel rebuilt and passed `twine check`, and the
  production `/healthz` endpoint returned `{"ok":true,"version":"1.0.0"}`.
- On 19 October, the manual [PyPI release workflow](https://github.com/voidpusher/agent-supra/actions/runs/39298525140)
  passed its build, test, metadata-check, and Trusted Publishing jobs.
  PyPI lists `agent-supra` 1.0.0 with both the wheel and source distribution.
  A fresh virtual environment downloaded the wheel from the public PyPI index,
  imported `Client`, `SupervisedRun`, and `SupervisorPaused`, and reported
  version 1.0.0. The live quickstart shows `pip install agent-supra`.

## External failures and open launch work

- The default Gemini 3.8 Flash test returned Google's 503. A 3.5 Flash-Lite
  impossible run stalled after two calls and timed out; its run was stopped.
  These were provider failures, not successful supervisor tests. The stable
  3.1 Flash-Lite retry above passed the real pause test.
- A real hosting or database outage has not been induced. The safe simulated
  503 drill above verifies the SDK's buffer and fail-closed behavior while
  preserving production availability. Slack and email deliveries above verify
  the configured destinations.
- Because this first-party inbox uses the app database and email/Slack
  integrations, a total database or hosting outage can also interrupt error
  recording. The `production health` GitHub Actions workflow checks public
  `/healthz` every 15 minutes, independently of the application. GitHub's
  scheduled runs can be delayed, and a failed run is only visible through
  GitHub's workflow status unless notifications are configured.
- Neon's Free plan currently shows a six-hour point-in-time history window,
  one manual snapshot, and no snapshot schedule. The encrypted GitHub backup
  now provides a separately verified recovery path. The scheduled run was
  over five hours late, so it does not guarantee a fixed daily recovery point.
  Keep the encryption key in a separate private recovery location. The kept restored branch
  contains a copy of production data and should be removed when no longer
  needed.
- The privacy page was corrected to name Vercel, Neon, GitHub encrypted
  backups, Resend, and retained recovery copies after active-account deletion.
  Terms are still labeled a private-beta draft. Final legal review and the
  planned beta feedback week remain outside this technical
  verification.
- The first live wrapper-check attempt with Gemini 3.1 Flash-Lite returned a
  Google 503 before any call completed. OpenAI and Anthropic still need
  live-provider verification with their own credentials.

No credentials are included here. Detailed local logs and screenshots are in
ignored `.local` files.
