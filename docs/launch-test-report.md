# Launch verification — 13–14 October 2026

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

## External failures and open launch work

- The default Gemini 3.8 Flash test returned Google's 503. A 3.5 Flash-Lite
  impossible run stalled after two calls and timed out; its run was stopped.
  These were provider failures, not successful supervisor tests. The stable
  3.1 Flash-Lite retry above passed the real pause test.
- No Slack webhook or SMTP provider is configured, so real alert delivery has
  not been verified. The dashboard now reports a failed test alert instead of
  claiming success when delivery fails.
- `SENTRY_DSN` is absent. Error notifications have not been verified.
- Neon backups and a restore have not been verified. A production Postgres
  connection is working, but a recoverability check is still needed.
- PyPI returns 404 for `agent-supra`. The site now uses a tested public GitHub
  install command until the package is published.
- Terms are still labeled a private-beta draft. A domain, final terms review,
  demo video, and the planned beta feedback week remain outside this technical
  verification.
- The first live wrapper-check attempt with Gemini 3.1 Flash-Lite returned a
  Google 503 before any call completed. OpenAI and Anthropic still need
  live-provider verification with their own credentials.

No credentials are included here. Detailed local logs and screenshots are in
ignored `.local` files.
