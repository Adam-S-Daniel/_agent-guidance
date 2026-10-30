# ADR 0008: Run ready fixtures on the weekly schedule

- **Status:** proposed (2026-10-04)
- **Issue:** [#68](https://github.com/Adam-S-Daniel/skills-evals/issues/68), partially implemented.
- **Deciders:** the owner requested that scheduled runs include additional ready evals; this package fixes the initial four entries and then appends the fixtures that qualified locally on 2026-10-05. On 2026-10-06, the owner decided the weekly routine replaces `eval.yml`'s Tuesday schedule after the [weekly routine PR](https://github.com/Adam-S-Daniel/skills-evals/pull/331) merged.

## Context

The manual API workflow in [`eval.yml`](../../.github/workflows/eval.yml)
supports one-fixture dispatch. The owner's 2026-10-06 decision, following the
[weekly routine PR merge](https://github.com/Adam-S-Daniel/skills-evals/pull/331),
retired that workflow's Tuesday schedule and made the weekly routine the
scheduled path. Three more fixtures have local qualification evidence.
Automatically discovering every fixture would also run unfinished or
unqualified work, and readiness must remain an explicit reviewed decision.

The manual API workflow runs committed fixture content with a short-lived,
spend-capped federated bearer. Each agent runner has read-only repository
access and its own credential exchange. Publishing requires a separate fresh
runner with write access. [ADR 0004](0004-eval-runs-on-the-roster-its-run-merged.md)
requires the roster update and confirmed disarm to finish before any manual
API eval. GitHub matrix job outputs do not provide a reliable per-fixture
result map, and simultaneous publishers would compete for the same results
branch. These guarantees describe `eval.yml`'s manual API path, not the
weekly routine.

## Amendment (2026-10-06): weekly routine replaces the Tuesday schedule

The owner decided that the weekly routine replaces `eval.yml`'s Tuesday schedule
after the [weekly routine PR merged](https://github.com/Adam-S-Daniel/skills-evals/pull/331).
The [`routine-eval-weekly.yml` caller](../../.github/workflows/routine-eval-weekly.yml)
plans the reviewed list and fires eval-mode routine runs through
[`routine-eval-fire.yml`](../../.github/workflows/routine-eval-fire.yml).
Routine runs use subscription usage; their results arrive on the routine
branch and are separately validated and ingested by Actions as local exhibits.
They do not use the manual workflow's OIDC exchanges, roster jobs, badge
publishing, or API workspace spending ceiling. Routine results are not badge
input, as [ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md)
records.

## Decision

1. Commit `evals/scheduled.yml` as a nonempty `fixtures` list. Each entry has
   exactly `path` and a nonempty, single-line `readiness` note. The initial
   entries are `evals/workflow-path-audit`, `evals/embeddable-tool-pages`,
   `evals/review-bash-ci-reliability`, and
   `evals/skills-doctor/bucketed-account-store`. Qualification on 2026-10-05
   appended `evals/github-actions-sha-pinning`,
   `evals/writing-adrs/bootstrap`, `evals/disarm-inherited-reach`, and
   `evals/windows-elevation-from-wsl`, eight in all. Preserve the supplied local evidence in each note without
   inventing a measurement date or claiming a paid CI run.
   An entry is admitted only when all of these hold: a local N=3 judged run
   with both arms; zero errored trials and zero judge errors; separation, meaning at least one
   scorer (objective or judge) where every with-skill trial scores above
   every without-skill trial, so the difference is not within trial noise;
   and no open scorer-bug issue or pull request naming the fixture. A
   judge error is a failed criterion, not a footnote. Fixtures that miss a criterion stay off the list until a
   better local run: `evals/consumer-repo-provisioning` (objective 9.67 vs
   7.67 and judge 5.23 vs 4.9, both with overlapping trial ranges) and
   `evals/github-actions-repo-settings` (objective 5 vs 3.67 with
   overlapping ranges; judge 8.8 vs 8.23, ranges touching at 8.7). The other
   `evals/writing-adrs` fixtures, `existing-convention` and `supersede`,
   are listed per path and were not added: their with-skill margins are
   small and `supersede` lost judge scores on one trial.
   `evals/vendor-release-impact-issues` was also not added: its 2026-10-05
   qualification recorded three judge errors (two with the skill, one
   without; each "judge CLI call timed out after 120s"), which fails the
   zero-judge-errors criterion until a clean local run.
   `evals/post-failure-comment` and `evals/cms-stuck-pr-triage` each had an
   errored without-skill trial (a timeout and invalid JSON), and
   `evals/rename-pdfs` shows no with-skill advantage (objective 4 vs 4,
   judge 3.4 vs 3.97); all three stay off.
2. The weekly routine's read-only planning job reads the reviewed schedule
   list and dispatches each fixture through `routine-eval-fire.yml`.
   `eval.yml`'s `workflow_dispatch` still reads `fixture` from
   `GITHUB_EVENT_PATH`, with today's single-fixture default and unchanged
   `roster_only` behavior. This manual API dispatch remains separate from the
   scheduled routine path.
   Reject malformed schemas, duplicate entries, invalid path characters,
   and paths outside the committed fixture set before emitting the matrix.
   The following matrix and publishing guarantees apply to manual API evals.
3. For manual API evals, run one matrix leg per fixture, `fail-fast: false`,
   `max-parallel: 2`.
   Each leg selects and validates again before its own OIDC exchange, uses
   its own runner and temporary files, and uploads a uniquely named artifact.
   The fixture-relative path remains its distinct `eval_key`; numeric matrix
   slots identify artifacts without flattening nested fixture paths.
4. Keep roster, disarm, roster-pr, and roster-wait as single jobs per run.
   Every eval and publisher retains ADR 0004's confirmed-clear gate and the
   same permissions and credential sources. No agent runner receives a write
   credential; no token is passed between matrix legs.
5. Match publishing legs to the planned matrix and serialize them with
   `max-parallel: 1`, `fail-fast: false`. Each publisher has a fresh checkout,
   validates its fixture/key independently, downloads only its matching
   successful payload, and builds that fixture's badge against accumulated
   history before its commit. Failed evals keep diagnostic artifacts but
   cannot publish partial results; their missing success payload is reported.
   An aggregate matrix failure must not suppress successful sibling publishes.
   Retain workflow concurrency `real-eval`, `cancel-in-progress: false`, so
   separate runs cannot compete with these serialized publishers.
   Nested skill badges read `results/<skill>/<timestamp>/<fixture>/`, the
   harness's existing layout, and include only that fixture's pairs in their
   window; flat skill and guidance badges retain their existing layouts.
6. Retain the per-fixture harness budgets and the existing guidance-fixture
   timeout check; also check every scheduled fixture against the eval job's
   timeout, including setup, agent, and judging costs with defaults.

## Consequences

For manual API evals, the workflow's estimate is about $0.30–0.90 per skill
fixture. A manual dispatch selects one fixture; actual usage varies, and the
API workspace spend limit remains the hard ceiling for that path. Adding a
fixture to the weekly routine's reviewed list is a subscription-usage
decision, not an API-spend estimate.
Readiness evidence does not require a positive objective delta: the
account-store fixture qualifies with a stronger judge score even though its
supplied objective average is lower.

For manual API evals, successful siblings publish despite an errored eval.
Publishing is serialized and therefore adds runner time; a missing or
unavailable payload is reported and never substituted with another fixture's
output. Dispatch remains one fixture and gains no bulk input. Fixtures are
admitted only through committed paths, not through results-branch content or
an API response.

This implements the owner-requested ready list, not all of #68: rotation,
monthly sweeps, model products, trial changes, budget enforcement, and a
published aggregate index remain outside this package. Scheduled local
qualification evidence is not substituted for CI badge input.

## Alternatives considered

- Discover all `fixture.yaml` files: rejected because existence is not readiness.
- Put four paths directly in workflow YAML: rejected because readiness evidence
  and additions belong in a small reviewed list with a validated schema.
- Reuse a matrix job's final `eval_key` output: rejected because completion
  order can select a sibling's key and mislabel published results.
- Publish concurrently or from the eval runner: rejected because the former
  races the results branch and the latter exposes write auth to agent code.
- One batch publisher gated on aggregate eval success: rejected because one
  failed fixture would discard all successful siblings' results.

## References

- [Matrix runner issue](https://github.com/Adam-S-Daniel/skills-evals/issues/68).
- [Workflow security contract](../../.github/workflows/eval.yml).
- [Reviewed schedule](../../evals/scheduled.yml) and
  [planner and admission checks](../../scripts/plan_scheduled_evals.py).
- [Shared matrix and artifact pattern](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations#using-an-output-to-define-two-matrices):
  GitHub's documented producer and consumer matrix pattern.
- [Design and budget](../../DESIGN.md).
- [Decision index](README.md).
