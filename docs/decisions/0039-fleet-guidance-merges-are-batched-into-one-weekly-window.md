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
   from 17:00 up to 19:00 UTC (noon to 2 PM US Eastern since the 2026-11-13
   addendum below). The bounds live in
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

## Addendum (2026-11-13): the window is in US Eastern time, and evals pause in it

The owner asked that evals pause Mondays from noon to 2 PM Eastern, so that no
eval run starts on guidance that is half merged. Two changes follow.

1. **The bounds are wall-clock times in a named zone.** The bounds file gains
   a required `timezone` key (`America/New_York`) and the window becomes
   12:00 to 14:00 there. In winter (EST) that is 17:00 to 19:00 UTC, the
   same instants as before; in summer (EDT) it is 16:00 to 18:00 UTC, so the
   window then opens an hour before the weekly allowance reset (17:00 UTC)
   instead of at it. The owner's stated hours win over the alignment.
2. **The same file is the eval pause.** skills-evals'
   `scripts/guidance_merge_window.py eval-pause` reads it, and every
   automated eval launcher there refuses to start a run inside the window
   (skills-evals ADR 0017's addendum of the same date). There is no second
   copy of the bounds.
