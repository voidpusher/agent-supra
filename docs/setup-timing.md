# First three outside setup trials

No participant results have been recorded. Under five minutes remains a target.
Recruit a willing developer who already uses Claude Code and has a disposable
Python project with pytest. They should follow the published instructions
without founder intervention until they finish or ask for help. Do not use a
founder account or synthetic internal workspace as an outside participant.

## Run the session

1. Ask permission to observe. No recording is required; never capture passwords,
   keys, private code or raw test output. The tester controls their own terminal.
2. Start the full stopwatch before signup/key creation. Record each milestone
   below using elapsed time, not estimates. Separately mark the moment account/key
   setup ends; it must not disappear from the full duration.
3. Use a fresh terminal/environment and the actual released SDK/plugin versions.
   Follow [Claude setup](claude-code.md) in observation mode. Keep keys private.
4. Let Claude run a small real project test command such as `pytest -q`. Verify
   the dashboard received `tests.reported` evidence, not just a tool event or an
   empty connection-check run. Use a harmless repair with a known outcome.
5. If help is needed, stop the unassisted timer and record the exact blocking
   step. Continue with an assisted timer. Do not call assisted completion an
   unassisted success. Avoid intentional failures in the participant's real work.
6. Record the run ID and ask the operator to classify this consenting participant's
   workspace External. Review useful/early/false flags and missed loops during
   the week. Do not change classification to make the gate look better.

## Blank result record — copy once per participant

Use anonymous participant labels in public summaries. Keep contact details and
screen contents private.

```text
Participant label: [not yet enrolled]
Existing relationship to founder:
Date / OS / Python / Claude Code version:
SDK version / plugin version:
Prior Claude Code setup already complete? yes/no
Disposable project uses pytest? yes/no

Elapsed from full start:
Account and key ready:
Virtual environment / pip install complete:
Marketplace added:
Plugin installed and loaded:
Key and observation mode set; Claude started:
Session attachment notice seen:
First actual pytest tool execution:
First dashboard tests.reported evidence verified:
Full end-to-end duration:
Account-ready to verified test evidence duration:

Completed without help? yes/no
Time of first help request / blocking step:
Assisted completion time:
Exact sanitized setup problem:
Run ID:
Expected / actual test outcome:
First flag verdict or suspected miss:
Fix / retest result:
```

Publish sample counts and each assisted/unassisted outcome before calculating a
median. Three trials are a small usability check, not a broad performance claim.
