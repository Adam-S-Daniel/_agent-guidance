# 0038 — Memory curation checks claims against their evidence and keeps the index short

**Status:** Accepted (2026-11-07). Amends step 2 of [ADR 0026](0026-memory-is-curated-by-a-routine-and-promoted-through-the-fleet-worker.md); the rest of it stands.

## Context

ADR 0026's routine finds stale memory by age and by the state of the PRs and
issues a note names. It flags everything else for the owner and changes
nothing. A note that is wrong but recent, or wrong about something other than
a PR, is never caught, and every later session reads it and trusts it. A wrong
line in `MEMORY.md` is worse: that file is loaded into every session.

Anthropic's ["The components of agent memory"](https://www.anthropic.com/research/components-of-agent-memory)
(2026-11-06) measured both problems:

- An agent wrote a wrong note and acted on it three times. An offline pass
  that checked the note against the session record fixed it, and the corrected
  run beat both the uncorrected run and a run with no memory.
- Large, sprawling memory went stale and cost the agent time; a small set of
  notes with one short top-level file did better.
- Several writers editing one shared file lost 28% of the facts; one page per
  fact lost none.
- Claude Code loads only the first 200 lines or 25 KB of `MEMORY.md`.

On 2026-11-07 this project's own `MEMORY.md` was 8.9 KB, eleven of its lines
ran past 200 characters, 55 of its 59 notes were not linked from it, and at
least three of its lines were contradicted by the repos (a PR described as in
progress had merged, workflows described as off were on).

## Decision

1. **The routine checks claims, not just dates** (new step 2b of
   [`memory-curation.md`](../routines/memory-curation.md)). Each run re-reads
   the source of each checkable claim, in priority order: `MEMORY.md` lines,
   then notes changed since the last run, then other indexed notes. A claim
   the source contradicts is corrected in place, keeping the old value as
   `(corrected <date>: was <old>)`. Opinions, decisions and the owner's rules
   are not checked. The summary reports `Claims: checked, corrected,
   unchecked`.
2. **The index stays a short list of pointers.** `memory_audit.py` reports
   `index.long_entries` (lines over `--index-line-chars`, 200 by default), and
   the routine moves their detail into notes. The audit also accepts
   `[[name]]` wiki links as indexing a note, which is how project memory links
   notes.

## Consequences

- Correcting a claim is a change the routine makes with no human, where ADR
  0026 only flagged. The old value stays in the line and the note is never
  deleted, so a wrong correction is visible and reversible.
- A check costs a read per claim. The priority order spends the run on what
  every session loads first; the rest is counted as unchecked, not dropped.
- Whether a claim is "checkable" is the routine's judgment, so a wrong call
  either skips a wrong note (no worse than before) or edits a decision line
  (caught by the kept old value).
