# 0045 — The fleet worker merges its own reviewed issue fixes

**Status:** Accepted (2026-11-15). Widens
[0041](0041-the-fleet-worker-merges-reviewed-agent-made-changes.md) to the
issue worker's own PRs.

## Context

The issue worker turns each `agent-ready` issue into a PR on an
`agent/issue-<n>-<slug>` branch. 0041 let the fleet worker merge reviewed
agent-made PRs, but left every `agent/issue-*` PR to
[0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md)'s
narrow rule, which covers only prompt-audit sweep and memory-promotion
issues that change instruction text. So a fix for any other issue, such as
a health-audit `ci` issue or an issue a thread filed, was listed as "ready
for his merge" and labeled `needs-owner`, even when CI was green.

On 2026-11-15 a sweep of the open `needs-owner` queue across the loop repos
found most items were agent-doable. The owner's standing rule is a closed
loop that puts as few decisions to him as it can, and 0041 itself says a
change that widens merge authority goes to the owner. He was asked on
2026-11-15 whether the fleet worker should merge its own reviewed fixes for
regular issues, and answered yes.

## Decision

An `agent/issue-<n>-<slug>` PR whose issue is neither a sweep nor a
memory-promotion issue (the title patterns in the narrow rule's item 1) is
an agent-made change under step 2d of `docs/routines/fleet-worker.md`, and
merges when every step 2d item holds: not a draft, no `on-hold`, a head at
least an hour old, all checks green with a required check and a passing
test run, the eval-protection gate, the merge window for fleet guidance,
and a `CLEAN` verdict from the run's own fresh-context reviewer.

The issue is the one the branch names, in the same repo, and the body's
first line after `## TLDR` must close or refer to that same issue; anything
else goes to the owner, so a sweep fix cannot pass as a regular one by
naming another issue. After the merge the worker handles the issue as the
narrow rule does: a `Closes` issue is confirmed closed, and a `Refs` issue
goes back to `agent-ready` for the findings left.

Sweep and memory-promotion PRs stay under the narrow rule alone: their
issues ask for instruction edits only, and the narrow rule's file list is
the stricter check for them.

## Consequences

- A green, reviewed issue fix merges within one fleet worker interval after
  its head is an hour old, with no owner step.
- What bounds a wrong `CLEAN` is unchanged from 0041: required CI with a
  passing test run, the review's refusal list (which still sends any change
  that widens merge or push authority to the owner), a merge commit that
  one revert undoes, and the next health audit.
- The cms-platform-managed repos stay excluded, as in 0041: a merge there
  publishes the live site.
