# Private beta preparation

## Before invitations

- Complete the real Gemini tests locally and against the deployed service.
- Verify a real Slack or email alert reaches the chosen destination.
- Review the draft terms at `/terms`, the privacy page, and the contact email.
- Choose 3–5 people who already run AI agents. Record their email addresses and agreement to participate.
- Keep new workspaces in observation mode. Budget and call caps still apply.

## Invitation draft

Subject: Try Agent Supra with one of your AI agents

Hi,

I'm testing Agent Supra, which detects when an agent keeps trying without making progress.
Would you try it on one of your agents for a week? Start in observation mode so it flags
suspected loops while your agent keeps running. Provider charges and your configured budget
limits still apply.

I'll send the working signup link and quickstart once the deployment checks are complete.
I'd especially like feedback on false alarms and loops it missed. Please send the run ID,
what the agent was doing, and whether you think the flag was right. Avoid sending secrets
or private source code.

Thanks,
Sanchit

## One-week review

Day 1: connect one agent and confirm model calls appear.
Days 2–6: review flags and suspected misses with each participant.
Day 7: summarize false positives, missed loops, setup friction, and fixes to prioritize.

| Participant | Run ID | Expected behavior | Actual behavior | Flag correct? | Fix / follow-up |
| --- | --- | --- | --- | --- | --- |

## 60-second video shot list

Record this only after a real Gemini run succeeds. Keep API keys and passwords off screen.
The scripted demo is useful for rehearsal but must be labeled as scripted if recorded.

| Time | Screen | Narration |
| --- | --- | --- |
| 0–8 s | Task and failing tests | “This agent is trying to fix a task with contradictory tests.” |
| 8–25 s | Real Gemini attempts, edited for time | “It keeps changing the code, but the failures do not improve.” |
| 25–36 s | Terminal pause and dashboard banner | “Agent Supra pauses the run when it detects no progress.” |
| 36–50 s | Paused run timeline and highlighted evidence | “Here are the attempts and failing tests behind the decision.” |
| 50–60 s | Completed solvable run, then quickstart | “A solvable run completes. Start in observation mode and review the flags.” |

If the real run pauses for exact repetition or budget instead of no progress, use its actual
reason in the recording. Never imply that every impossible run pauses after exactly four calls.
