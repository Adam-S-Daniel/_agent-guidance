# 0030 — A section's worth is measured by ablation and weighed against its bytes, and retirement is automatic

**Status:** Accepted (2026-10-31), recording what [skills-evals#124](https://github.com/Adam-S-Daniel/skills-evals/issues/124) built. It extends [0002](0002-unconditional-rules-live-in-the-guidance-not-a-skill.md), which put rules in the guidance and left open how a rule earns its place there.

## Context

A skill costs context only when it is invoked. A `##` section of `agents-md/base.md` is delivered into every session of every fleet repo, so each byte is paid for in every session whether or not the section matters to that task. [`agents-md/eval-coverage.yml`](../../agents-md/eval-coverage.yml) records each section's byte extent in its `bytes` column.

"Does the section teach the behavior" is the wrong question. The section can teach it and still be worth nothing, because the rest of the guidance already does. Two pairs answer different questions, and the skills-evals harness runs both per fixture: `section` against `none` (does it teach, alone) and `full` against `full-minus-section` (what it adds inside the whole file).

## Decision

1. **`full` against `full-minus-section` decides retirement.** A section whose in-situ pair shows no benefit, to the confidence and run counts in skills-evals' [`evals/gate-policy.yml`](https://github.com/Adam-S-Daniel/skills-evals/blob/main/evals/gate-policy.yml) `guidance:` block, gets the verdict `retire`. Those thresholds are separate from the `skills:` block so they can diverge. The pair is the guidance side of skills-evals' [ADR 0012](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md), which makes the in-place pair the default.
2. **`section` against `none` decides only whether a section teaches.** It is a diagnostic and a graduation signal for a new fixture, never a removal reason.
3. **`helps-only-alone` is its own state, `shorten-or-merge`.** A section that would retire in situ but shows benefit alone in any cell is not removed: the rest of the guidance covers what it teaches, so the verdict opens a skills-evals issue to shorten it or fold it in.
4. **Retirement is automatic, for sections and skills alike.** With `retirement_mode: auto` (committed for both blocks), a `retire` verdict opens a removal pull request that auto-merges once the target repository's own checks pass, with no human review. For a section the pull request is in this repo, written with the eval-improve App's token minted for this repo alone: it removes the extent from `base.md`, re-copies the mirror, deletes the manifest row, regenerates `bytes` and `AGENTS.md`, and adds a typed `remove` entry to [`docs/guidance-impact.md`](../guidance-impact.md) whose `Eval:` line carries the pooled run count. With no token, or in `proposal` mode, the verdict files an issue instead. For a skill the decision is skills-evals' [ADR 0014](https://github.com/Adam-S-Daniel/skills-evals/blob/main/docs/decisions/0014-removal-pull-requests-merge-without-review.md) and adam-agentskills' [ADR 0021](https://github.com/Adam-S-Daniel/adam-agentskills/blob/main/docs/decisions/0021-automated-skill-changes-merge-without-review.md). At most one section is acted on per evaluation.
5. **Bytes are the cost.** The manifest's `bytes` is what a retained section is weighed against, and the improvement proposer is told that every byte reaches every session. No per-kilobyte score and no cap on growth is computed; the improvement loop's accept rule is the same ablation verdict.

## Consequences

- A section that is true but redundant is removed. Its record survives in `docs/guidance-impact.md` and the closed pull request, not in the guidance.
- The gate guards against honest mistakes (a changed fixture, errored trials, a run ingested twice), not forged results; anyone who can write the results branch can retire a section (skills-evals ADR 0014 says the same).
- A section with no fixture can never be retired. Coverage is therefore tracked as `gap`, `covered` or `skipped` rows in the manifest.
- Ablation costs four agent runs per fire and needs at least five runs per arm at the first look, so the sweep is the most expensive part of the weekly allowance.

## Alternatives considered

- **Decide by `section` against `none`.** Rejected: it credits a section with everything the rest of the guidance already teaches.
- **A human approves each removal.** Rejected: Adam made section removal automatic on 2026-10-10 ([skills-evals#124](https://github.com/Adam-S-Daniel/skills-evals/issues/124)). The verdict is built to make a wrong removal unlikely and a revert is one pull request.
- **Score effect per kilobyte or cap growth.** Not built: the in-situ verdict already weighs a section against the whole file, and a cap would need a measured number nobody has.

## References

- [skills-evals DESIGN.md "Guidance subject"](https://github.com/Adam-S-Daniel/skills-evals/blob/main/DESIGN.md), "Section fixtures, verdicts and removal".
- [`scripts/check-guidance-coverage.js`](../../scripts/check-guidance-coverage.js) and [`scripts/check-guidance-touch.js`](../../scripts/check-guidance-touch.js).
