# 0041 — The fleet worker merges reviewed agent-made changes and the hook pin bump

**Status:** Accepted (2026-11-12). Supersedes, in part,
[0010](0010-the-hook-pin-is-re-pinned-from-here.md): its "proposes and stops"
property. Widens [0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md)
and [0028](0028-the-fleet-worker-merges-skill-guidance-and-partial-fixes.md).

## Context

On 2026-11-12 three green PRs in this repo sat waiting for the owner:

- [#510](https://github.com/Adam-S-Daniel/_agent-guidance/pull/510), a
  workflow fix opened by a project thread. The fleet worker had skipped every
  fire since 2026-11-10, so the fix for the one routine that could have
  merged other PRs was itself waiting on a person.
- [#507](https://github.com/Adam-S-Daniel/_agent-guidance/pull/507), a
  fleet worker spec change opened by a project thread two days earlier.
- [#504](https://github.com/Adam-S-Daniel/_agent-guidance/pull/504), the
  nightly hook pin bump, open two days.

Each waited by design, not by accident:

- The fleet worker's merge rule (0025, 0028) covers only instruction text:
  `CLAUDE.md`, the repo half of `AGENTS.md`, `SKILL.md` bodies and the fleet
  guidance, on `agent/issue-*` branches fixing sweep or memory-promotion
  issues. Anything else an agent opens is listed as "ready for his merge".
- The threads that open PRs end with "ready for your merge", because that
  is what the merge rule says about their PRs.
- 0010 kept the hook pin bump for a human review on purpose, and its "Needs
  you" listing only counted PRs authored by `Adam-S-Daniel`, so a bot-opened
  bump was never even announced; 0010's own *What it leaves open* named that.

The owner merged all three by hand and asked that PRs like them stop needing
him. His standing rule is a closed loop that puts as few decisions to him as
possible.

## Decision

**The fleet worker merges two more kinds of PR, behind the same independent
review that guards instruction fixes** (spec: step 2d of
[`fleet-worker.md`](../routines/fleet-worker.md)):

1. **An agent-made change:** a non-draft PR authored by `Adam-S-Daniel` and
   labeled `agent-authored`, in any fleet repo except the three
   cms-platform-managed repos, whose merges publish a live site.
2. **The hook pin bump:** `agents-md-sync[bot]`'s `hook-pin-bump/update` PR
   in this repo, touching exactly `repos.yml` and the self-hosted hook.

Both need every check green, no run waiting on an environment, a clean merge
state, the eval-protection gate (0033), the guidance merge window (0039), a
head commit at least an hour old, and a `CLEAN` stamp at the exact head from
a separately prompted subagent told to break the change. The review refuses
any change that widens what an agent, routine or bot may merge, approve or
push to a default branch (or a token's or authorization's scope), any weakened
control (permissions, pins, rulesets, required checks, environment gates,
skipped tests), any secret or personal data, and any new behavior without a
test. For the hook it also refuses new network access, writes outside the old
hook's paths, unverified downloads and dropped checks.

**Opting out is a draft or `on-hold`.** A thread that wants the owner's call
leaves its PR a draft; the PR pass lists only non-draft PRs.

**Environment approvals do not move.** `plugin-runtime-review` and
`regression-review` still wait for the owner, and the worker still never
approves anything.

## Consequences

- A green agent PR merges within one fleet worker interval (6 hours at the
  current setting) after its head is an hour old, with no owner step.
- The merge rests on an LLM review of code, not only of Markdown. A wrong
  `CLEAN` lands a bad change. What bounds it: required CI including the
  repo's suite, the refusal list above, a merge commit that one revert PR
  undoes, and the next run's health audit. The owner accepted that cost on
  2026-11-12.
- The hook pin bump now lands without a person, so a hook change in
  `adam-agentskills` reaches every allowlisted repo's sessions within about
  a day of the bump. The hook was already reviewed and merged by PR in
  `adam-agentskills`; this review is a second, narrower pass.
- A change that widens merge or push authority, this ADR's kind, still goes
  to the owner, because the review refuses it.
- A bump the review refuses is now announced: "ready for his merge" counts
  the hook pin bump, which closes 0010's unread-proposal gap.
