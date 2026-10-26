# Changelog

## 1.1.1 — 2026-10-26

- Inject test-progress instructions into Claude Code's session and subagent context.
- Add per-tool local JUnit report arguments to direct pytest commands that lack one,
  preserving the original command and tool permissions. Chained shell commands,
  Jest and Go remain unsupported test-progress inputs.
- Retain explicit report destinations, freshness checks, private fingerprints and
  pause enforcement. Separate tool calls cannot overwrite each other's auto reports.
- Extend the internal invoice-agent matrix to rounding, duplicate and missing
  records, partial progress, malformed actions and provider failures.
- Reject duplicate JSON fields and malformed action types without executing model text.

## 1.1.0 — 2026-10-26

- Add experimental Claude Code hooks for tool and test progress, with observation
  by default and opt-in denial of the next tool call when a run is paused.
- Add `agent-supra watch` to supervise existing commands using fresh JUnit reports,
  with opt-in cancellation of the owned process tree and private telemetry replay.
- Add `agent-supra proxy` with the optional `proxy` extra for local OpenAI-compatible
  Chat Completions, including text streaming, provider token accounting, estimated
  spend thresholds, call limits, and duplicate-request protection.
- Keep uncertain provider usage unpriced and pause further proxy requests for review.
- Preserve paused/stopped states when late completion events arrive, and describe
  missing code revisions without claiming an exact repeat.

The core SDK remains dependency-free. These integrations are experimental. Hooks
and CLI watch do not measure model spend. Proxy monetary limits are estimates;
one admitted request can overshoot the threshold. Native Claude Code and Gemini
tests passed; an actual OpenAI-account request has not been tested.

## 1.0.0

Initial published SDK and service release.
