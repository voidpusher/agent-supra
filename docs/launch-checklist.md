# Launch checklist

Current technical status is recorded in [launch-test-report.md](launch-test-report.md).
The remaining boxes require a provider account, external delivery, or beta feedback.

## 1. Name: Agent Supra (decided)

- [x] Product name: **Agent Supra**. Package: `agent-supra` (free on PyPI on 13 Oct 2026). Command: `agent-supra`. Python import: `agent_supra`.
- [ ] Register the PyPI name early by publishing a first release (step 4), so nobody else takes it.
- [x] Connect `agentsupra.in` to Vercel, redirect `www` to the apex, and set production `BASE_URL` to `https://agentsupra.in`.
- [ ] Claim the name on X and GitHub.

## 2. Test it (docs/testing-with-gemini.md)

- [x] Local regression suite and synthetic supervisor flows pass.
- [x] Live solvable and stuck Gemini runs complete and pause as expected.
- [x] Real Slack alert and Resend email delivery are verified with configured destinations.
- [ ] Exercise outage recovery against the production service.

## 3. Deploy (docs/deploying.md)

- [x] Deployed on Vercel with Neon Postgres; `/healthz` returns ok.
- [x] The app uses its production HTTPS URL for links and secure cookies.
- [x] First-party server-error inbox deployed; protected self-test recorded an error and delivered Slack and email alerts.
- [x] Resend SMTP set; the production test alert was delivered to `sanchitp498@gmail.com`.
- [x] Neon point-in-time recovery and a manual snapshot were each restored to an isolated branch and verified against production reference data (19 Oct 2026).
- [ ] Configure recurring, independently stored database backups. Neon's current Free plan shows a six-hour history window and no snapshot schedule; the single manual snapshot is a recovery point, not an ongoing backup policy.

## 4. Publish the SDK

```bash
pip install build twine
python -m build
twine upload dist/*            # needs a PyPI account and API token
```

- [ ] `pip install <name>` works in a fresh virtualenv.

## 5. Before people arrive

- [ ] Read the privacy page and make sure it's true for how you deployed (host, retention).
- [ ] Add terms of service and a contact email to the footer.
- [ ] Record a 60-second demo: the impossible-task run getting paused, then the dashboard.
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
