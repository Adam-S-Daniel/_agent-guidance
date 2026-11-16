# 0044 — The fleet worker removes skills the eval verdict retires

**Status:** Accepted (2026-11-13). Widens
[0041](0041-the-fleet-worker-merges-reviewed-agent-made-changes.md) to one
more kind of PR, including in cms-platform and adamdaniel.ai.

## Context

On 2026-11-13 the owner found the skill `consumer-repo-provisioning` on the
evals dashboard with "Official decision: remove it", a re-check booked for
Dec 5, and the skill still shipping. Two things had stopped the removal:

- skills-evals' verdict rule made a `retire` verdict a *proposal* for a
  person whenever a cell reached its run cap with no clear effect, had only
  one cell with headroom, or sat beside a parked cell. The removal bot filed
  [skills-evals#998](https://github.com/Adam-S-Daniel/skills-evals/issues/998)
  for a human, and nothing told the owner.
- The removal bot (`scripts/retire_skill.py`) writes only adam-agentskills.
  The skill lives in cms-platform, where other files cite it, so the bot
  could not have removed it even on stronger evidence. Its mechanical path
  also refuses adam-agentskills removals that need judgment: a skill other
  files cite, a waived finding, a plugin's last skill.

The owner's response: "I want removals to occur without requiring my
intervention/approval." His standing rule is a closed loop that puts as few
decisions to him as possible.

## Decision

1. skills-evals makes every `retire` verdict an automatic removal
   (skills-evals ADR 0014, 2026-11-13 addendum, in the companion
   skills-evals pull request from branch `claude/removal-always-acts`).
   The bot still opens and arms the removal itself where it can.
2. Where it cannot, it files a **skill removal issue** in skills-evals,
   labeled `agent-ready` and marked
   `<!-- skills-evals:retirement-issue:agent -->`, naming the registry repo
   and the skill's path.
3. The fleet worker's issue pass works that issue in the registry repo it
   names (adam-agentskills, cms-platform or adamdaniel.ai), the one
   exception to "act only in the issue's own repo": a `retire-skill/<skill>`
   branch that deletes the skill, moves any fact other files rely on into
   `docs/`, repoints every reference, and closes the issue
   ([`agent-issue-worker.md`](../reference/agent-issue-worker.md), "Skill
   removal issues").
4. Its PR pass merges that PR under a new step 2e
   ([`fleet-worker.md`](../routines/fleet-worker.md)): the verdict re-read
   fresh and still a removal, deletions confined to the skill's directory,
   no sync-managed file touched, every other changed file still under the
   eval-protection gate, in adamdaniel.ai only non-site files changed, every
   check green, nothing waiting on an environment, and a `CLEAN`
   independent review that also checks no reference or relied-on fact was
   left dangling. Step 2d no longer claims `retire-skill/*` PRs, and step 3
   does not report one to the owner while step 2e may still merge it.
   cms-platform and adamdaniel.ai squash, as they always do.

## Consequences

- A removal reaches cms-platform and adamdaniel.ai without the owner, which
  0041 excluded because a merge there can publish the live site. A skill
  removal changes no site content; a run that waits on `regression-review`
  or `plugin-runtime-review` still goes to the owner, since approving those
  is his.
- The worker fires every few hours (`fleet-worker-fire.yml`), so a removal
  through this path lands a day or so after the verdict, not within the
  bot's own run.
- The routine's prompt gains authorization to push `retire-skill/*`
  branches and to close the PRs and issues the spec says to close. The
  live task's prompt changes only when someone edits it to match.
- The worker edits only the registry repo. Mentions of a removed skill in
  other repos and in sync-managed files are listed on the issue, not fixed
  in the same PR.
- A cms-platform removal leaves the plugin manifests' version alone, so it
  reaches marketplace users with the next release rather than at merge.
- A removal is reversible: reverting the merge restores the skill, and
  adam-agentskills has `scripts/restore_skill.py`.
- What it leaves open: a skill whose removal must pass
  `plugin-runtime-review` is still a proposal for the owner, by his own rule
  (adam-agentskills ADR 0016).
