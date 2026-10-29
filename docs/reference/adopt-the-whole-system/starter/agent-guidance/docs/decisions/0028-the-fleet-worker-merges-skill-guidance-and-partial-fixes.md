# 0028 — The fleet worker also merges skill, fleet guidance and partial fixes

**Status:** Accepted (2026-10-27)

## Context

[ADR 0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md) let
the fleet worker merge a prompt-audit sweep fix only when it closed its issue
and changed `CLAUDE.md` files, the repo-specific half of `AGENTS.md` files
and new files under `docs/evidence/`.
[ADR 0026](0026-memory-is-curated-by-a-routine-and-promoted-through-the-fleet-worker.md)
held memory promotions to the same files. Skills and the fleet guidance
stayed with the owner.

The sweep audits every `SKILL.md` and the fleet guidance too
([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md), step 1), so
most of its findings landed with the owner. On 2026-10-27 two fixes waited on
him only because of those limits, each with every check green and no
deployment waiting:

- `Adam-S-Daniel/cms-platform#888` fixed all 15 findings of sweep issue
  #885: nine `SKILL.md` bodies, twelve removed lines in
  `skills/.freshness-allow.yml` whose text the same PR removed, and the
  repo-specific half of `AGENTS.md`. No frontmatter changed.
- `_agent-guidance#352` fixed one of the six findings of #348 in this repo's
  `AGENTS.md`. Because it left the rest open, it said `Refs`, not `Closes`.
  #348's other findings are in `agents-md/base.md`, `stub.md` and
  `sections/`, which the worker left for the owner.

The owner then asked that PRs like #888 merge without him, and on the same
day that skill text, the fleet guidance, partial fixes and memory promotions
into those files do too, with the AI review gating every merge.

## Decision

1. **Partial fixes.** A fix PR whose body starts with
   `Refs <owner>/<repo>#<n>` and names the findings it fixes qualifies like
   a `Closes` PR. The issue stays open for the rest.
2. **Skill text.** For a sweep or memory-promotion issue, a `SKILL.md` at
   any depth qualifies when its frontmatter is byte-identical in the base
   and the head. A skill's name, description, allowed tools and other
   settings decide when it loads and what it may run, so they still need the
   owner. No other file in a skill's directory (scripts, references, assets)
   qualifies. For a sweep issue, a `.freshness-allow.yml` whose diff only
   removes lines also qualifies.
3. **Fleet guidance.** In `_agent-guidance`, for a sweep or
   memory-promotion issue, `agents-md/base.md`, `agents-md/stub.md` and
   `agents-md/sections/*.md` qualify, together with what such an edit
   requires in the same PR and CI already enforces: the byte-identical
   `.claude/hooks/fleet-guidance.md` mirror, `agents-md/eval-coverage.yml`
   `bytes` and `heading` values, an append-only `docs/guidance-impact.md`
   entry, and this repo's regenerated `AGENTS.md`.
4. **The AI review gates every merge.** Nothing merges without the separately
   prompted subagent's `CLEAN` stamp on the exact head. If it cannot run or
   gives no verdict, no stamp is posted, so the PR goes to the owner. The
   review now also requires untouched frontmatter and no added command,
   script, tool use or code-running step in a `SKILL.md` beyond what the
   finding asked; that each removed allow-list line's text is removed in the
   same diff; that no fleet guidance rule is weakened or dropped beyond what
   its finding asked and each changed section has its impact entry; and
   that a `Refs` PR names the findings it fixes and fixes only those.
5. Everything else in ADR 0025 holds: the `agent/issue-<n>-<slug>` branch,
   the issue title, green checks, no deployment waiting on an environment,
   `mergeable_state` `clean`, `on-hold` respected, and the worker never
   approves anything. Code, workflows, hooks outside the lesson-check pass
   ([ADR 0027](0027-lessons-become-agent-checks-through-the-fleet-worker.md))
   and any other file still go to the owner.

## Consequences

- Most of what the sweep and memory curation find now lands without the
  owner.
- A wrong `CLEAN` reaches further. A platform skill such as example-site-platform's
  ships through the `agentskills` marketplace to every machine that
  installs the bundle, and the fleet guidance is delivered by the sync to
  every fleet repo and loaded in every session. Each is still Markdown,
  reverted with one PR, and re-read by the next sweep.
- The independent review is now the main guard on what agents read in every
  session. Item 6 of the merge rule makes it a hard gate, and a missing or
  stale stamp sends the PR to the owner rather than merging it.
- A skill body can hold commands an agent runs. The review clause is the only
  guard that a fix did not add one; the frontmatter rule keeps
  `allowed-tools` out of reach.
- Where a repo makes a skill change wait on an environment such as
  `plugin-runtime-review`, merge rule item 4 still holds the PR, and the
  worker tells the owner rather than approving it.

## Addendum (2026-10-28)

Two gaps showed up once the rule was live.

First, a `SKILL.md` edit in `agentskills` never qualified. That repo's
`AGENTS.md` and CI also require a bundle `version` raise in both
`plugin.json` files, a `docs/skill-impact.md` entry, a `PURPOSE.md` beside
the skill, and a met eval touch gate. Merge rule item 2 now allows those
files, in `agentskills` only and only beside a qualifying `SKILL.md`
change in the same bundle: the manifests when only `version` changes, the
impact file when its diff only adds lines, and the `PURPOSE.md` in the
changed skill's directory. The independent review answers `FINDINGS` unless
each changed skill has a new impact entry and the PR body states how the
touch gate was met.

Second, a merged `Refs` PR left its issue open on `agent-working`, so no run
took the remaining findings. After merging a `Refs` PR the fleet worker now
swaps `agent-working` for `agent-ready` (skipping an `on-hold` issue) and
comments which findings the PR fixed. The issue worker then does only the
findings no merged PR fixed, and closes the issue as completed when none
are left. If it cannot do the rest, the existing `agent-blocked` stop
applies.
