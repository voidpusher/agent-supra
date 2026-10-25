# Roadmap execution

Source: the owner-supplied Agent Supra Roadmap dated 25 October 2026.
Work one phase at a time. The PDF's account counts are a snapshot, not a live
measurement. Do not use founder tests as evidence of outside adoption.

## Current phase: 0 — private beta

Implemented in this release:

- Operator-only `/app/beta`: signup → key → first received event → first flag;
  rolling seven-day active workspaces and supervised runs; median activation time.
- Explicit Internal / External / Unreviewed workspace classification. Only confirmed
  External workspaces count. The operator's workspace is always excluded.
- Flag feedback in each run: Useful / Too early / False alarm; missed-loop reports.
  One current verdict per flag, editable by workspace members; no free-text field
  collecting prompts, code or keys. Feedback never changes enforcement.
- Phase 0 gate: at least 5 activated outside users, 50 measured outside runs with
  accepted events, and at least one outside flag reviewed as useful.
- First-party measurement disclosure in Privacy and feedback instructions in Quickstart.

### Measurement definitions

Counts are based on retained records, not lifetime claims. Empty runs do not count.
Server receipt timestamps establish activity; client timestamps cannot invent usage.
An identical event retry does not move the activity timestamp. Both parent and child
runs with accepted events count as supervised runs. A workspace is the present proxy
for a team; this is not yet an organization or seat product.

Signup/key/flag timestamps already exist. First event receipt tracking starts with
this deployment. Older workspaces are excluded from signup-to-first-event timing:
their true first-ever activity is unknown. The dashboard shows its tracking start
and activation sample size. Funnel steps count distinct confirmed outside workspaces;
the gate's user count counts members of activated outside workspaces.

Useful percentage is useful / (useful + too early + false alarm), with review counts
and per-rule verdicts shown. It is user-reported usefulness, not independently
measured accuracy. Missed-loop reports are separate. Removing expired runs also
removes their activity and reviews; deleting an account removes its cohort metadata.
No external analytics service, new tracking cookies, prompts or code are collected.
Records remain workspace-linked; they are not claimed to be anonymous.

### Beta execution checklist

- [ ] Operator signs in and classifies existing test workspaces Internal.
- [x] Confirmed three previously approved invitation messages in Gmail Sent on
  21 October 2026 (checked 25 October). One invitation was to the sender's own
  account, so this is not evidence of three outside users. Recipient addresses
  are intentionally omitted from the public repository. Do not resend duplicates;
  delivery, acceptance and actual outside activation remain unverified.
- [ ] Prepare 30 relevant outreach prospects and review the messages before sending.
  No new outreach was sent as part of this engineering release.
- [ ] Post the existing edited-replay demo, clearly labeled as a replay, when the owner
  approves the accounts and final post text.
- [ ] Onboard 5 outside users on their own agents; record setup friction below.
- [ ] Review every flag and suspected miss during the week using the run's feedback forms.
- [ ] Fix the top 3 observed setup blockers and the worst observed false positive.
- [ ] Meet the measured Phase 0 gate before starting Phase 1.

| Setup step | Evidence / run ID | Expected | Actual | Priority | Resolution |
| --- | --- | --- | --- | --- | --- |

No user friction or false-positive results are assumed until outside feedback exists.

### Owner-dependent items

- Final legal review remains open in `legal-review-notes.md`. Copy changes are not a
  legal sign-off. The product remains labeled beta.
- The GitHub recovery workflow already provides a laptop-loss recovery path using
  its repository secret. An independent private key copy is still unverified;
  never publish or export the key to an issue, log, or public artifact.
- The restored Neon branch contains production data. It was previously requested
  to be kept; deletion is not performed silently as part of this release.

## Later phases — gated

1. After Phase 0: Claude Code hooks first, then CLI watch, provider proxy, framework
   adapters and test-output parsers. Validate setup under 5 minutes with 3 new users.
   Consult current official hook/API docs when implementation starts.
2. After Phase 1: risky-action guards, more progress signals, approvals, nudges,
   opt-in rollback and repository policy. New rules begin in observation mode.
   Gate: one beta team enables real enforcement.
3. After Phase 2: organizations, roles, shared policies, audit trail and billing.
   Gate: 3 paying teams. Prices in the PDF are hypotheses to validate, not live plans.
4. Fundraising: 10 weekly active teams, 3 paying teams, and growing supervised runs.
   Verify current program criteria and deadlines when the usage gate is near.

Do not market native Claude Code support, dangerous-action blocking, team billing,
money saved, or testimonials as existing functionality before they are implemented
and measured. Infrastructure expansion stays frozen unless a real beta issue needs it.
