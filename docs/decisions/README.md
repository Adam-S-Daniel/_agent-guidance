# Architecture Decision Records

Lightweight [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
ADRs for decisions in this repo that are **non-obvious, load-bearing, and
would rot if the reasoning lived only in a PR description**.

Most of what this repo does is explained well enough by the code and the
README. An ADR earns its place when the next reader's most likely instinct is
to *undo* the decision — because the code shows the "what" but not the
counterfactual that was rejected, and re-litigating it costs more than reading
a page.

## Index

| ADR | Title |
|-----|-------|
| [0001](0001-skills-bootstrap-delivery-is-opt-in.md) | skills-bootstrap delivery is opt-in and double-keyed, not fleet-wide |
| [0002](0002-unconditional-rules-live-in-the-guidance-not-a-skill.md) | Unconditional rules live in the managed guidance, not in a skill |
| [0003](0003-cron-coverage-is-fleet-listed.md) | Cron coverage is measured against a declared fleet, not against a disk |
| [0004](0004-skills-bootstrap-adopted-where-sessions-happen.md) | skills-bootstrap is adopted wherever sessions happen, and this repo self-hosts |
| [0005](0005-consumer-locks-are-re-pinned-from-here.md) | Consumer locks are re-pinned from here, and only when the bundle moved |
| [0006](0006-bump-prs-land-on-a-sweep.md) | The bumper's own pull requests land on a sweep, not on a reviewer |
| [0007](0007-sha-pins-carry-no-version-comment.md) | A SHA pin carries no version comment |

## Format

`NNNN-kebab-title.md`, numbered sequentially, with:

- **Status** — Accepted / Superseded by NNNN
- **Context** — what was true when the decision was made, with the numbers
- **Decision** — what was chosen, stated so it can be checked against the code
- **Consequences** — including what this makes *worse*, and what it leaves open

An ADR is immutable once merged. Changing course means a new ADR that
supersedes it, not an edit — the record of what we believed, and why, is the
point.
