# Launch checklist

Current technical status is recorded in [launch-test-report.md](launch-test-report.md).
The remaining boxes require live outage verification, launch materials, or beta feedback.

## 1. Name: Agent Supra (decided)

- [x] Product name: **Agent Supra**. Package: `agent-supra` on PyPI. Command: `agent-supra`. Python import: `agent_supra`.
- [x] Published version 1.0.0 to PyPI on 19 Oct 2026 through GitHub Trusted Publishing.
- [x] Connect `agentsupra.in` to Vercel, redirect `www` to the apex, and set production `BASE_URL` to `https://agentsupra.in`.
- [ ] Claim the name on X and GitHub.

## 2. Test it (docs/testing-with-gemini.md)

- [x] Local regression suite and synthetic supervisor flows pass.
- [x] Live solvable and stuck Gemini runs complete and pause as expected.
- [x] Real Slack alert and Resend email delivery are verified with configured destinations.
- [x] Exercise SDK outage behavior against the production API using a simulated 503: fail-open buffered and replayed events; fail-closed stopped. The service stayed online.

## 3. Deploy (docs/deploying.md)

- [x] Deployed on Vercel with Neon Postgres; `/healthz` returns ok.
- [x] The app uses its production HTTPS URL for links and secure cookies.
- [x] First-party server-error inbox deployed; protected self-test recorded an error and delivered Slack and email alerts.
- [x] External GitHub Actions health check calls production `/healthz` every 15 minutes; check the workflow run history for outages.
- [x] Resend SMTP set; the production test alert was delivered to `sanchitp498@gmail.com`.
- [x] Neon point-in-time recovery and a manual snapshot were each restored to an isolated branch and verified against production reference data (19 Oct 2026).
- [x] Replaced the failed backup login with a verified read-only role; the first scheduled encrypted backup succeeded and a retained artifact restored outside GitHub Actions.

## 4. Publish the SDK

- [x] Trusted PyPI publisher bound to `voidpusher/agent-supra` and the `pypi` GitHub environment; no API token is stored.
- [x] Version 1.0.0 wheel and source distribution published; `pip install agent-supra==1.0.0` and SDK imports worked in a fresh virtual environment.
- [x] Hosted quickstart now offers `pip install agent-supra`.
- [ ] Before a subsequent release, increment the package version and follow [the release procedure](pypi-release.md).

## 5. Before people arrive

- [x] Audited the privacy page against the deployed host, database, email service, and backup retention; corrected its account-deletion claim. Final legal review remains.
- [x] Terms page and contact email are linked from the footer. The private-beta terms still need final review before public launch.
- [x] Produced a 60-second captioned edited replay from the verified Gemini attempts and paused dashboard, followed by the completed run and quickstart. Review the local `final.mp4` in `.local/demo-video/edit/` before publishing it.
- [x] Public GitHub repository is connected; CI passed for the deployed commit.

## 6. Private beta (3 to 5 people, about a week)

- [ ] Give them accounts; ask them to run in observation mode for a few days.
- [ ] Review every flag with them: was it a real loop? Write down false alarms and misses.
- [ ] Fix what they hit before going public.

## 7. Public launch

- [ ] X thread with the demo video.
- [ ] Show HN post ("Show HN: <name>, pauses AI agents that are stuck in a loop").
- [ ] r/LocalLLaMA and relevant agent-framework communities, written as a builder, not an ad.
- [ ] Watch the server-error inbox and Vercel function logs for the first 48 hours.
