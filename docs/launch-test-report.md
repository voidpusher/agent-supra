# Launch verification — 13–15 October 2026

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
  existing sessions after reset. The 107-test local suite passed; live
  recovery and monitoring checks are still pending.

## External failures and open launch work

- The default Gemini 3.8 Flash test returned Google's 503. A 3.5 Flash-Lite
  impossible run stalled after two calls and timed out; its run was stopped.
  These were provider failures, not successful supervisor tests. The stable
  3.1 Flash-Lite retry above passed the real pause test.
- Production outage recovery has not yet been exercised against the live
  service. Slack and email deliveries above verify the configured destinations.
- First-party server-error monitoring is deployed, but its production error
  notifications have not yet been verified.
- Neon backups and a restore have not been verified. A production Postgres
  connection is working, but a recoverability check is still needed.
- PyPI returns 404 for `agent-supra`. The site now uses a tested public GitHub
  install command until the package is published.
- Terms are still labeled a private-beta draft. Final terms review, the demo
  video, and the planned beta feedback week remain outside this technical
  verification.
- The first live wrapper-check attempt with Gemini 3.1 Flash-Lite returned a
  Google 503 before any call completed. OpenAI and Anthropic still need
  live-provider verification with their own credentials.

No credentials are included here. Detailed local logs and screenshots are in
ignored `.local` files.
