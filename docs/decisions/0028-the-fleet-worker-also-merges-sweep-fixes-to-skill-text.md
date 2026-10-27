# 0028 — The fleet worker also merges sweep fixes to skill text

**Status:** Accepted (2026-10-27)

## Context

[ADR 0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md) let
the fleet worker merge a prompt-audit sweep fix only when it changed
`CLAUDE.md` files, the repo-specific half of `AGENTS.md` files and new files
under `docs/evidence/`. Skills stayed with the owner.

The sweep audits every `SKILL.md` too
([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md), step 1), so
most of its findings land in skills. On 2026-10-27,
`Adam-S-Daniel/cms-platform#888` fixed all 15 findings of sweep issue #885:
nine `SKILL.md` bodies, twelve removed lines in `skills/.freshness-allow.yml`
whose text the same PR removed, and the repo-specific half of `AGENTS.md`.
No frontmatter changed. Every check was green and no deployment waited. The
only thing that kept the worker from merging it was the file list, so it
labeled the PR `needs-owner` and the owner merged it by hand.

The owner then asked that PRs like it merge without him.

## Decision

1. For a sweep issue, the merge rule in
   [`fleet-worker.md`](../routines/fleet-worker.md) also allows:
   - a `SKILL.md` at any depth whose frontmatter is byte-identical in the
     base and the head. A skill's name, description, allowed tools and other
     settings decide when it loads and what it may run, so they still need
     the owner. No other file in a skill's directory (scripts, references,
     assets) qualifies.
   - a `.freshness-allow.yml` whose diff only removes lines.
2. The independent review answers `CLEAN` on such a PR only when the
   frontmatter is untouched, no hunk adds a command, script, tool use or step
   that runs code beyond what its finding asked for, and every removed
   allow-list line's text is removed in the same diff.
3. Everything else in ADR 0025 holds: the `agent/issue-<n>-<slug>` branch,
   the sweep issue title, green checks, no deployment waiting on an
   environment, `mergeable_state` `clean`, the `CLEAN` stamp on the exact
   head, `on-hold` respected, and the worker never approves anything.
   Memory-promotion fixes stay limited to `CLAUDE.md` and `AGENTS.md`.

## Consequences

- A sweep fix to skill text no longer waits on the owner, which is most of
  what the sweep finds.
- Skill text reaches more places than a repo's own `CLAUDE.md`. A platform
  skill such as cms-platform's ships through the `adam-agentskills`
  marketplace to every machine that installs the bundle, so a wrong `CLEAN`
  spreads further than under ADR 0025. It is still Markdown, reverted with
  one PR, and the next sweep re-reads it.
- A skill body can hold commands an agent runs. The review's extra clause is
  the only guard that a fix did not add one; the frontmatter rule keeps
  `allowed-tools` out of reach.
- Where a repo makes a skill change wait on an environment such as
  `plugin-runtime-review`, merge rule item 4 still holds the PR, and the
  worker tells the owner rather than approving it.
