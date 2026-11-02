# 0033 — Audits leave eval-protected subjects alone except for correctness fixes

**Status:** Accepted (2026-11-01)

## Context

skills-evals judges a skill or guidance section by runs on its current text.
A with-arm run counts only while its `tested_digest` equals the subject's
digest on main (`harness/gates.py`, `pool_cells`), so any edit to a skill's
directory or to a section's extent starts its evidence over. That includes a
new `PURPOSE.md`.

The prompt-audit sweep ([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md))
reports wording and padding findings as readily as broken commands, and the
fleet worker ([`fleet-worker.md`](../routines/fleet-worker.md)) merges their
fixes within the hour ([ADR 0028](0028-the-fleet-worker-merges-skill-guidance-and-partial-fixes.md)).
On 2026-10-31 `Adam-S-Daniel/adam-agentskills#96` applied sweep findings to
16 skills. One of them, `writing-adrs`, was a one-clause cut, and it reset
45 runs of evidence.

The owner asked on 2026-11-01 that sweeps stop suggesting minor changes to
subjects whose evals look good, with no new decisions put to him.

## Decision

A subject is **eval-protected** when skills-evals' published
`explorer/latest.json` gives it the verdict `keep`, or a `current_text_runs`
above 0, unless its verdict is `retire` or `shorten-or-merge`.
`scripts/eval-protected.js` makes that call from the file. It does not use
the network, and its tests cover every branch.

- The sweep files only correctness findings on a protected subject:
  `wrong-command`, `broken-path`, `contradiction` or `safety-gap`, each
  tagged with its class. Everything else is a count in the run log.
- The fleet worker merges an edit to a protected subject only when the
  gate's `pr` mode passes. That mode needs a
  `Correctness fix: <subject> — <class>: ...` line in the PR body for each
  protected subject the PR touches, and the independent review must confirm
  the class. A refused PR is closed as parked, not sent to the owner.
- Wording changes to a protected subject come only from skills-evals'
  eval-backed improve lane, which this does not touch.

## Consequences

- Evidence keeps accumulating on subjects that are doing well. A protected
  subject's wording stays as it is, even when the audit is right that it
  could be tighter. The bytes still cost context in every session, and the
  ablation and improve lanes, not the audit, are what remove that cost.
- The gate is only as current as the explorer, which rebuilds after every
  ingest. If the explorer cannot be read, everything is treated as protected
  for that run, so the gate fails closed.
- It does not cover private-registry skills, whose results stay out of the
  public explorer. It also does not cover PRs a person or a project thread
  merges by hand, which is how #96 landed. Since the sweep stops filing
  those findings, such PRs mostly lose their source.
