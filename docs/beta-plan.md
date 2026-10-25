# Private beta preparation

The phase gates and measurement definitions are in [roadmap.md](roadmap.md).
The operator can use `/app/beta` to confirm outside workspaces and track activation.
Participants can review each flag and report missed loops directly on run pages.
Three previously approved invitations were confirmed in Gmail Sent on 21 October
2026 (checked 25 October). One was sent to the sender's own account. This does not
establish outside activation, delivery or acceptance. Do not resend duplicates.
The participant table below still needs actual run IDs and feedback from outside users.

## Before invitations

- Complete the real Gemini tests locally and against the deployed service.
- Verify a real Slack or email alert reaches the chosen destination.
- Review the draft terms at `/terms`, the privacy page, and the contact email.
- Choose 3–5 people who already run AI agents. Record their email addresses and agreement to participate.
- Keep new workspaces in observation mode. Budget and call caps still apply.

## Invitation draft

Subject: Try Agent Supra with one of your AI agents

Hi,

I'm inviting you to a small private beta of Agent Supra. It watches AI agents for repeated
attempts without progress and shows the evidence behind each flag.

Would you try it with one Python agent over the next week? Create an account at
https://agentsupra.in/signup, then follow https://agentsupra.in/docs/quickstart.
New workspaces start in observation mode, so loop rules flag runs for review;
budget and call caps can still pause a run. The beta service is free, but your
model provider's charges still apply.

Please reply with the run IDs, which flags were right or false alarms, any stuck
runs it missed, and anything that made setup difficult. Leave provider keys,
prompts, private code, and customer data out of your reply and run names.
Please review https://agentsupra.in/terms and https://agentsupra.in/privacy
before signing up.

Thanks,
Sanchit

## One-week review

Day 1: connect one agent and confirm model calls appear.
Days 2–6: review flags and suspected misses with each participant.
Day 7: summarize false positives, missed loops, setup friction, and fixes to prioritize.

| Participant | Run ID | Expected behavior | Actual behavior | Flag correct? | Fix / follow-up |
| --- | --- | --- | --- | --- | --- |

Ask each participant at the end of the week:

1. Which run IDs did Agent Supra flag, and was each flag useful, too early, or a false alarm?
2. Which run IDs looked stuck to you but received no flag? What signal would have exposed the loop?
3. Did observation mode change your agent's behavior or latency noticeably?
4. Were model-call counts and estimated spend close enough to your provider logs to trust the dashboard?
5. What blocked setup, and would you use the product again on this agent?

Avoid collecting prompts, source code, provider keys, or private customer data in feedback.

## 60-second video shot list

The verified real Gemini runs are now represented in a 60-second captioned
edited replay at the local ignored path `.local/demo-video/edit/final.mp4`.
It is labeled as an edited replay throughout, uses the authentic paused-run
dashboard screenshot and recorded attempt counts, and shows no API keys.
Review it before sharing publicly. If recording new footage later, keep API
keys and passwords off screen; label any scripted footage as scripted.

| Time | Screen | Narration |
| --- | --- | --- |
| 0–8 s | Task and failing tests | “This agent is trying to fix a task with contradictory tests.” |
| 8–25 s | Real Gemini attempts, edited for time | “It keeps changing the code, but the failures do not improve.” |
| 25–36 s | Terminal pause and dashboard banner | “Agent Supra pauses the run when it detects no progress.” |
| 36–50 s | Paused run timeline and highlighted evidence | “Here are the attempts and failing tests behind the decision.” |
| 50–60 s | Completed solvable run, then quickstart | “A solvable run completes. Start in observation mode and review the flags.” |

If the real run pauses for exact repetition or budget instead of no progress, use its actual
reason in the recording. Never imply that every impossible run pauses after exactly four calls.
