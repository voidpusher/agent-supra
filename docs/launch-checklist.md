# Launch checklist

In order. The product is built; these are the things only you can do.

## 1. Name: Agent Supra (decided)

- [x] Product name: **Agent Supra**. Package: `agent-supra` (free on PyPI on 13 Oct 2026). Command: `agent-supra`. Python import: `agent_supra`.
- [ ] Register the PyPI name early by publishing a first release (step 4), so nobody else takes it.
- [ ] Buy a domain (for example agentsupra.com or agentsupra.dev) and set `BASE_URL` to it.
- [ ] Claim the name on X and GitHub.

## 2. Test it (docs/testing-with-gemini.md)

- [ ] Tests 1 to 8 pass on your machine against a local server.
- [ ] Tests 1, 2 and 6 pass again against the deployed service.

## 3. Deploy (docs/deploying.md)

- [ ] Deployed on Render with Postgres; `/healthz` returns ok.
- [ ] `BASE_URL` set to the real https domain.
- [ ] Sentry DSN set; trigger an error once and see it arrive.
- [ ] SMTP set; test alert email arrives (check spam).
- [ ] Database backups on; one restore tested.

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
- [ ] Push the repo to GitHub as public (if open-sourcing); the CI badge should be green.

## 6. Private beta (3 to 5 people, about a week)

- [ ] Give them accounts; ask them to run in observation mode for a few days.
- [ ] Review every flag with them: was it a real loop? Write down false alarms and misses.
- [ ] Fix what they hit before going public.

## 7. Public launch

- [ ] X thread with the demo video.
- [ ] Show HN post ("Show HN: <name>, pauses AI agents that are stuck in a loop").
- [ ] r/LocalLLaMA and relevant agent-framework communities, written as a builder, not an ad.
- [ ] Watch Sentry and the server logs for the first 48 hours.
