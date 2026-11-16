# 0046 — The fleet worker splits into a loop worker and a maintenance worker

**Status:** Accepted (2026-11-15). Splits the single task of
[0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md); the
merge rules of 0028, 0041, 0044 and 0045 are unchanged.

## Context

One scheduled task, `Fleet worker`, did all of the work in
[`docs/routines/fleet-worker.md`](../routines/fleet-worker.md): narrow
instruction fixes, skill removals, the hook pin bump, the lesson-check pass,
and general maintenance (Dependabot, agent-made PRs, health-audit issues).
Part of that work is the continuous improvement system, whose targets are
skills, guidance sections and git hooks, and that system's spend is capped
at 25% of the weekly budget by the eval rules. The rest is not part of the
loop and has no such cap. With one task, one cadence and one bill, neither
share of the cost could be measured, and the loop's share could not be
slowed without slowing maintenance.

## Decision

The task splits in two, each with its own scheduled task, API trigger and
cadence:

- **Loop worker** (new): step 2's narrow instruction fixes, the hook pin
  bump of step 2d, step 2e's skill removals, step 3 for the PRs it handles
  and for failing lesson-check PRs, the issue pass and blocked-issue report
  for loop issues, the lesson-check pass and the `Lessons:` line. It runs
  less often, so its spend fits the eval cap.
- **Fleet worker** (existing name): steps 1 and 1b, step 2d except the hook
  pin bump, step 3 for every other PR, and the issue pass and blocked-issue
  report for every other `agent-ready` issue. No lesson-check pass.

A run's role is named by its prompt. Loop issues are defined by title prefix
(or the `hook-proposal` label) in one table in the spec, taken from the
routines that file them, so every `agent-ready` issue falls to exactly one
role. A run whose prompt names no role does all of both, so the existing
task keeps working until the owner pastes the new prompts.

`fleet-worker-fire.yml` fires both from one matrix job, each with its own
URL variable, bearer secret and cadence variable. The loop's cadence
variable, `LOOP_WORKER_EVERY_HOURS`, is required with no default; each
task has its own concurrency group so neither fire cancels or queues the
other. Each prompt's authorization sentence lists only its role's writes:
the maintenance task is not authorized to withdraw a skill removal
(`retire-skill/*`, `loop-withdrawn`) or to arm auto-merge.

Both tasks run Sonnet 5.5, set explicitly, with the GitHub connector only
and a fresh session per fire.

## Consequences

- The loop's cost and the maintenance cost are separate lines, so the loop
  can be held to the eval cap and maintenance tuned on its own.
- The owner creates the new task and pastes both prompts and the new
  variable, secret and URL; until then the old prompt runs both roles.
- A PR or issue changes role only if the spec's table changes, so a new
  routine that files `agent-ready` issues for the loop adds its prefix there
  in the same change; otherwise its issues default to maintenance.
- The loop worker's missing variable fails its fire loudly rather than
  defaulting to a cadence that could overspend the cap.

Owner decision, 2026-11-15.
