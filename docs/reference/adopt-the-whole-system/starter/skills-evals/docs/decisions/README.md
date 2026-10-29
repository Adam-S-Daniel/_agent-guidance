# Architecture Decision Records

Lightweight [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
records of the non-obvious, load-bearing decisions this repo has made — one file
per decision, numbered in the order they were taken, never rewritten once
accepted (a decision that stops holding is superseded by a later record rather
than edited into agreement with the present). Each states the context that
forced the choice, the choice itself, and the consequences the repo now lives
with, so the next session can tell a deliberate constraint from an accident.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-roster-trusted-on-main.md) | The roster the harness runs on is committed on `main`; a computed roster is a proposal | accepted (2026-09-13); amended by 0003 and 0004 |
| [0002](0002-runs-bill-the-api-org-not-the-subscription.md) | Real runs bill the API organisation; subscription credentials are not adopted (shares number 0002 with the next row; distinct decisions) | accepted (2026-09-23); decision 1 superseded for routine runs only by 0010 |
| [0002](0002-roster-follows-vendor-defaults.md) | The roster seats each tier's vendor-default model at once and retires the version it superseded after a buffer (shares number 0002 with the previous row; distinct decisions) | accepted (2026-09-27; decision 1 revised 2026-09-28) |
| [0003](0003-roster-merges-automatically.md) | The roster proposal merges automatically, behind `roster_mode` | accepted (2026-09-28; round 6, the roster App, 2026-09-29); amended by 0004; extended 2026-10-26 to fixture scaffold and promotion PRs |
| [0004](0004-eval-runs-on-the-roster-its-run-merged.md) | The scheduled eval runs on the roster its own run just merged | accepted (2026-09-30) |
| [0005](0005-improvement-loop-reuses-skill-creator.md) | The improvement loop reuses skill-creator's description loop and measures with this harness | accepted (2026-10-06); amended 2026-10-06: run path moves to the ADR 0010 routine, acceptance basis becomes real-work fixtures plus tokens/cost; amended 2026-10-07: tokens are the total across every model; amended 2026-10-26: accepted improvements auto-merge behind `improve_merge_mode`; amended 2026-10-27: a guidance-improve routine mode lands guidance sections in _agent-guidance the same way |
| [0006](0006-run-objective-commands-with-isolated-process-state.md) | Run objective commands with isolated process state | accepted (2026-10-04); amended 2026-10-07: isolate workspace Git |
| [0007](0007-parse-config-and-staged-shell-guards.md) | Parse configuration values and staged shell guards before scoring | accepted (2026-10-04); amended 2026-10-05 |
| [0008](0008-run-ready-fixtures-on-the-weekly-schedule.md) | Run ready fixtures on the weekly schedule | proposed (2026-10-04) |
| [0009](0009-fixture-followup-turns.md) | A fixture may send scripted follow-up turns, identically in both arms | proposed (2026-10-05) |
| [0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md) | Run the AI steps of skill and guidance evals in a routine fired by Actions, and measure agent effectiveness on real work | accepted (2026-10-06); supersedes 0002 (billing) decision 1 for routine runs only |
| [0011](0011-sandbox-agent-arm-network.md) | Agent arms run behind Claude Code's network sandbox, delivered by `--settings` | accepted (2026-10-07); reads, and hooks/Chrome/managed-policy addenda 2026-10-07 |
| [0012](0012-ab-runs-in-the-deployed-context-minus-the-subject.md) | An A/B runs both arms in the context the subject deploys into, and removes only the subject | accepted (2026-10-07); not built |
| [0013](0013-judge-policy.md) | Judge a pair only when the objective checks do not decide it, with a judge one tier above the arm | accepted (2026-10-10); supersedes the single-judge reading of the roster in 0001 and 0002 |
| [0014](0014-removal-pull-requests-merge-without-review.md) | A skill removal pull request merges automatically, with no human review; behind `retirement_mode: auto`, committed | accepted (2026-10-26); addenda 2026-10-26: agentskills ADR 0021, and auto mode wired (App token, runtime and no-token fallback to an issue, one loop PR at a time, major bump) |
| [0015](0015-mine-real-sessions-only-on-the-owners-own-computer.md) | Real sessions are mined only on the owner's own computer, into redacted records and proposal files in the private registry | accepted (2026-10-26); built, not yet scheduled |
