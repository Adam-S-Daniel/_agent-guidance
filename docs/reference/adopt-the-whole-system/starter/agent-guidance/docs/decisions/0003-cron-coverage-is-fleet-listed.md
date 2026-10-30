# 0003 — Cron coverage is measured against a declared fleet, not against a disk

**Status:** Accepted (2026-08-18)

## Context

`scripts/check-cron-coverage.js` exists because a `schedule:`-triggered
workflow fails silently: there is no PR to go red and nothing notifies anyone.
This repo's own `drift-report.yml` was red for 26 consecutive nights (runs
147–172, 2026-07-22 → 2026-08-16) before a human happened to open the Actions
tab.

The gate is deliberately offline — pure filesystem, no network, no wall-clock —
so it audits directories. Six revisions hardened *what counts as a repo it may
certify*: it now refuses a path that is not a real git repository, one whose git
dir does not point back, and one whose index lists workflow files the checkout
never materialized. Every one of those revisions was chasing the same rule —
**a verdict may only be about files the process actually opened.**

Issue #37 found the rule broken one level up. The multi-repo form required
`--require`, so the caller supplied the list, and a list that omits a repo is
indistinguishable from a fleet that does not contain it. Measured 2026-08-17: a
session with 14 of the account's 25 repositories checked out audited its 14,
found three uncovered, and said **nothing whatsoever** about the other 11 — not
"unknown", not "not checked", nothing. The clean tail line is byte-identical to
the one a genuinely complete audit prints.

Three of those 11 were later confirmed uninteresting (forks, an org outside
`SYNC_OWNERS`). That is not the point. The point is that the audit's own output
could not tell you which case you were in.

## Decision

**`repos.yml` declares the fleet; the gate audits that, and a declared repo the
disk lacks is an error.**

- `cron_coverage.fleet` — the repos a disk-root run must find and read. It
  defaults `--require`, which survives as an explicit-subset override.
- `cron_coverage.out_of_scope` — every other repo in the account, each with the
  reason. Being listed here is **not** a claim that a repo has no failing cron;
  it is a claim that nobody is promising to watch one. Promotion to `fleet:` is
  the entire reversal — no other file changes.
- Together the two keys classify all 25 repositories the account holds
  (13 + 12, verified 2026-08-18).
- A registry that cannot answer the question — missing, unparseable, no
  `cron_coverage:`, an **empty** `fleet:`, a name under both keys, an entry
  carrying a path separator — exits 2 and audits nothing. An empty fleet is
  singled out because it is the exact vacuous-pass shape: zero repos audited,
  zero failures, "All audited repos covered", exit 0.

Item 3 of #37, settled here so it is not re-argued each audit: **scratch and
fork repos are out of scope.** Scratch repos still receive the AGENTS.md sync —
that is cheap and harmless — but a coverage guarantee is an operational promise
and there is nothing there anyone is operating. If a scratch repo grows a cron
somebody depends on, it has stopped being scratch; move it, don't special-case
it.

**The list's own staleness is `drift-report.sh`'s job.** That script already
discovers every repo in both owners nightly, so it now flags any repo neither
key classifies. This is the deliberate division: the gate stays offline and
deterministic and knows the fleet it was told about; the report has the network
and knows the account. Neither grows the other's dependency.

The rejected alternative was #37's option (a) — declare the audit disk-scoped
and document it. It was rejected because documenting a silence does not make
the silence legible at the moment someone reads a green run, and this file's
whole history is revisions of "the gate must not certify what it did not read".

## Consequences

- **This repo now hand-maintains a repo inventory, which it had avoided.**
  `sync.sh` and `drift-report.sh` both discover; nothing else here holds a list
  of repos. That is a real cost and the reason this is an ADR: the same
  argument would justify a second list, and a second list is where they start
  disagreeing.
- **The list can go stale — but not quietly.** A repo created and never
  classified appears in the nightly drift report. It does not fail anything,
  because the drift report is a report; if that turns out to be too quiet, the
  next move is a `ci`-labelled issue, not a louder log line.
- **Two keys now answer "is this repo in scope?"** — `exclude:` for the
  AGENTS.md sync and `cron_coverage.out_of_scope` for this. A repo can land in
  both, spelled out twice on purpose: they are different questions that can
  share the same current answer, and collapsing them would silently couple a
  future change to one into a change to the other.
- **A repo cloned but not listed is now invisible in the opposite direction.**
  The gate audits the declared fleet, so an unlisted repo sitting on the disk is
  skipped rather than reported. Discovery is what closes that, and discovery is
  the report's.
- **CI is unaffected.** `ci.yml` runs the argument-less cwd form, which reads no
  registry at all — a runner has exactly one repo checked out. That is also why
  the suite now exercises the real `repos.yml` directly: nothing else would
  notice the fleet key being emptied or deleted.
