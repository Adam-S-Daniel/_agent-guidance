# 0039 — Fleet guidance merges by automation are batched into one weekly window

**Status:** Accepted (2026-11-07).

## Context

Every edit to `agents-md/base.md` changes the "rest digest" the evals compute
for every guidance section, so each merge restarts every section's eval
evidence. Several automated paths merge such edits, each on its own clock, so
the evidence never settles. On 2026-11-07 the owner chose to batch the merges
into one weekly window, right after the weekly allowance reset and before the
weekly eval plan fills.

Paths that change `agents-md/base.md` or `agents-md/sections/**`, with where
each is enforced:

| Path | Where it merges | Gate |
|---|---|---|
| skills-evals' guidance improvement loop (`eval-guidance-improve/<section>` heads), `routine-guidance-improve-gate.yml` | auto-merge armed at open | skills-evals ADR 0017 |
| skills-evals' guidance retirement loop (`retire-guidance/<section>` heads), `scripts/retire_skill.py` | auto-merge armed at open | skills-evals ADR 0017 |
| The fleet worker (`docs/routines/fleet-worker.md`), including lesson-check PRs | merges a reviewed PR; arms auto-merge on lesson-check PRs | this ADR |
| A person | any time | none, by design |

`vendor-changelog-merge.yml` (its gate allowlists only the changelog files),
`dependabot-auto-merge.yml`, `skills-lock-bump.yml` and `sync.yml` never
touch these paths.

## Decision

1. **One window.** Automation merges a pull request that changes
   `agents-md/base.md` or a file under `agents-md/sections/` only on Monday
   from 17:00 up to 19:00 UTC. The bounds live in
   `scripts/base-md-merge-window.yml` and nowhere else.
2. **A check automation runs, not a required status check.** A required check
   that fails outside the window would block the owner too and would need a
   re-run inside it. Instead `scripts/base-md-merge-window.js` answers
   "may automation merge this now?" (exit 0, 2 or 1) and each automated path
   asks it, or the skills-evals port of it, before it merges or arms
   auto-merge. The script reads no clock of its own.
3. **Outside the window a pull request is queued, not dropped.** It stays
   open and unarmed. The skills-evals loops arm it from a Monday schedule
   inside the window; the fleet worker merges it on its first run inside it.
4. **A person may merge any time.** A manual merge outside the window resets
   the evidence of every guidance section, exactly as before; the window
   exists to spare that, not to forbid it.
5. **The fleet worker's prompt is this repo's doc.** The routine reads
   `docs/routines/fleet-worker.md` at run time, so its merge rule changes by
   a pull request here; the existing "all seven items" wording is unchanged
   and the window is a step before it.

## Consequences

- A guidance edit by automation waits up to a week. That is the point.
- The skills-evals loops already allow one open guidance-loop pull request at
  a time, so at most one of their edits lands per window.
- A cloud routine whose prompt is stored outside any repo cannot be gated
  from here; the one such prompt change is the owner's to make.
- A new automated path that can change these files must call the check, and
  gets a row in the table above.
