# 0011 — Skills-bootstrap scope is exhaustive, and an unclassified repo is a nudge

**Status:** Accepted (2026-08-29)

## Context

`skills_bootstrap.repos` says which repos the sync delivers the SessionStart
hook to. It has never said anything about the repos it does not deliver to —
and that is where the decisions live: `agentskills` authors the hook,
`skills-evals` and `scratch-claude-001` would be contaminated by it, this repo
self-hosts, five are dormant.

Those reasons existed, and were good, but they were PROSE: a 43-line comment
block headed "DELIBERATELY NOT LISTED". Prose is unreadable to every tool and
unfalsifiable by every gate. A name silently present in both halves, an
exclusion whose reason nobody wrote, or a new repo that is simply absent from
the file all look identical to the next reader — a settled decision — and
identical to CI: nothing at all.

This is ADR 0003's finding one key over. There, a cron audit run against
whatever happened to be checked out printed a clean tail line over 14 of 25
repos, byte-identical to the line a complete audit prints. The remedy was a
two-key registry (`cron_coverage.fleet` + `out_of_scope`) plus a discovery-time
`CRON_UNCLASSIFIED` finding. `skills_bootstrap` had only the allowlist.

The operator's requirement, stated 2026-08-29, is that guidance and skills
apply to the same set of repos, and that a repo created in future should
produce a prominent nudge to decide — rather than defaulting silently either
way.

## Decision

1. **`skills_bootstrap` gets an `out_of_scope:` key** beside `repos:`, each
   entry a mapping carrying its own `reason:`. The two keys together are a
   partition of the repos the sync REACHES.

2. **`scripts/check-registry.js` asserts the partition offline** — disjoint,
   well-formed, no name written twice, every exclusion carrying a non-empty
   reason. It refuses (exit 2) rather than certifying when the complement key
   is missing or empty, because a gate that can find nothing reads exactly like
   a gate that found nothing. That is the same never-pass-vacuously rule
   `check-cron-coverage.js` applies to an empty `cron_coverage.fleet`.

3. **`drift-report.sh` flags what neither key claims**, as `CRON_UNCLASSIFIED`
   already does, and the nudge is a `ci`-labelled issue rather than a louder log
   line. ADR 0003 pre-authorised exactly this, under its "The list can go stale —
   but not quietly" consequence: "if that turns out to be too quiet, the next
   move is a `ci`-labelled issue, not a louder log line." Scheduled runs fail silently, so a log line is not a notification.

## The denominator is NOT `cron_coverage`'s, and the numbers invite the mistake

Measured 2026-08-29: both keys hold exactly **nine** `out_of_scope` entries and
share only **five** members.

- `cron_coverage` counts over repos a cron could exist in — 22, including the
  account's two forks (`OctopusDeploy-Api`, `SonosAmpJuicePi`) and the
  third-owner `superoutrigger`.
- `skills_bootstrap` counts over what the sync visits: `gh repo list --source
  --no-archived` across `SYNC_OWNERS`, **19**. Forks and archived repos are not
  excluded here, they are outside the denominator — their exclusion is not a
  choice this key can express.
- `scratch-claude-002` runs the other way: out of scope for cron, deliberately
  allowlisted for skills.

Equal counts over unequal sets is the shape that makes a "let's just reconcile
these two lists" refactor look like tidying. ADR 0003 ruled against collapsing
the scope keys because "collapsing them would silently couple a future change
to one into a change to the other." This measurement is what that ruling was
protecting, so it is recorded here rather than left to be re-derived.

## Consequences

- Dropping a repo from delivery now costs a sentence somebody can later
  disagree with, and CI enforces that the sentence exists.
- A new repo in either account is unclassified until somebody classifies it,
  and says so out loud rather than defaulting to silence.
- What the gate CANNOT do, and must not be widened to claim: that a reason is
  TRUE or still true — "dormant since 2026-03" is bytes, and nothing offline
  can age it. Freshness is a discovery question and belongs with the nightly
  report, not with the lint.
