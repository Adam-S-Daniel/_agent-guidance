# ADR 0001: The roster the harness runs on is committed on `main`; a computed roster is a proposal

- **Status:** accepted (2026-09-13); amended by [ADR 0003](0003-roster-merges-automatically.md) (2026-09-28)
- **Amended by [ADR 0004](0004-eval-runs-on-the-roster-its-run-merged.md) (2026-09-30):** the eval runs on the roster its own run merged, when that merge lands and verifies within the run's bounded wait; otherwise on the committed roster, as below.
- **Issue:** [#147](https://github.com/Adam-S-Daniel/skills-evals/issues/147), superseding the fix-round approach on [#67](https://github.com/Adam-S-Daniel/skills-evals/issues/67) / [PR #129](https://github.com/Adam-S-Daniel/skills-evals/pull/129)
- **Deciders:** the evals orchestrator session, under the owner's decision 11 (redesign the denominator around a trusted history)

## Context

Before this decision, `harness/roster.py` computed the running models (arms,
judge, preflight) from the Models API listing, the usage census
(`usage/latest.json`) and `roster/latest.json`. The last two came from the
unprotected `eval-results` branch, written by other jobs on other machines, so
the old design treated both as untrusted input.

Fourteen review rounds on PR #129 kept a live arm from being silently retired or
a planted arm from being seated only by adding sharper local checks over those
two documents, and each check was defeated by varying the thing it keyed on.
The round-14 park comment states the reason in one sentence: with no second copy
of what the harness has observed, "this id was never an arm" and "the record was
tampered with" are the same input, and so are "this model is unused" and "its
attribution chain is broken". No rule evaluated over `eval-results` alone can
tell them apart, so every survivor of round 14 (BLOCKERS 1, 2, 3, round 13's
BLOCKER B, the declared open cell) is the same defect.

The fleet's branch-protection rule (fleet guidance, "Automation vs branch
protection") closes the obvious repair: a bot cannot write a trusted file to
`main` ad hoc, and a ruleset bypass actor is a separate private-repo change plus
a one-way-door review of the writer.

## Decision

1. **The roster the harness RUNS ON is a file committed on `main`:
   `evals/roster.yml`.** `main` is ruleset-protected and PR-only, so an arm, a
   judge, a preflight or a `catalogue_seen` entry cannot appear or vanish there
   without a reviewed commit. `run_eval.select_models` reads this file and
   nothing else for the roster rung of its precedence (`--model` > fixture pin >
   `evals/roster.yml` > error). `$EVAL_ROSTER` and `--roster` remain as
   overrides for tests and local runs; `eval.yml` no longer materialises a
   roster from `eval-results` for selection.
2. **`harness/roster.py` computes a PROPOSAL, not the running set.** Its
   `previous` input is the committed `evals/roster.yml` (trusted), never
   `roster/latest.json` off `eval-results`. Its census input stays
   `usage/latest.json` off `eval-results` (untrusted). Its output is still
   published to `eval-results` as `roster/latest.json` for the explorer, and
   when it differs from the committed roster in any seat, arm order,
   `catalogue_seen` membership, or a `catalogue_seen` entry's committed
   `last_seen` that is at least half of `catalogue_seen_max_age_days` old,
   `eval.yml` admits the rendered proposal against the committed-roster
   contract. A `last_seen` refresh younger than that half-window still rides
   along in the proposal's `changes` once something else makes a proposal,
   but does not by itself make the run differ — otherwise routine weekly date
   churn alone would file a tracking issue every run (#200). A valid proposal is
   pushed as one commit on the
   bot-owned branch `roster/proposal` (recreated from `main` every run; never a
   shared branch) and upserts one tracking issue (marker
   `<!-- skills-evals:roster-proposal -->`) carrying the diff, every seat's
   reason in words with its numerator and denominator, and the compare link.
   An invalid proposal instead has a “needs review” tracking issue listing its
   admission failures; it does not update the branch or compare link, while the
   paid eval result is still published from the committed roster. A human opens
   the PR from a valid branch and merges it after CI.
3. **What each store is trusted for.** `evals/roster.yml` is trusted for the
   running set and for the observation history (`catalogue_seen` with
   `last_seen`, refreshed only through a merged proposal). The Models API
   response is trusted for availability within the run that fetched it. The
   census is trusted for nothing: it can shape a proposal and nothing else.
   `roster/latest.json` on `eval-results` is an exhibit, read by no decision.
4. **Every local check that existed to approximate a trusted history is
   deleted, each in its own commit with the measurement that shows it now
   decides nothing:** `RETIREMENT_ANCHOR_TOLERANCE` and the anchor veto,
   `is_needed_hop` and the two ageing exemptions, `CATALOGUE_SEEN_CAP` /
   `UNCAPPED_CARRY_CEILING` and the previous-arms cap and their tier ordering,
   `_clean_previous_arms`' never-evict clause. The zero-numerator refusal is
   already gone (reverted under decision 11, phase 1). What stays: schema
   validation of every untrusted field with a named one-line skip (an input,
   not an invariant), the census freshness window and its degradation reasons
   (properties 2 and 3 of DESIGN.md), the `judge.is_arm` refusal (now a lint
   over the committed file, run in `ci.yml`), a size bound on the census
   document so an untrusted input cannot exhaust the runner.

## Consequences

- **The five #147 defects stop being decisions.** Each becomes, at worst, a
  wrong proposal that a reviewer sees with its evidence beside it: a planted
  `arms` line or a deleted `catalogue_seen` entry on `eval-results` cannot
  reach the committed file, because the harness no longer reads `previous`
  from that branch; a census plant can lower a share in the proposal, and the
  proposal prints the share it lowered. Each defect carries a regression row
  whose assertion is "the running set is unchanged by this input" — RED on
  `424eebf`, where `select_models` reads the published roster.
- **An untrusted census can also produce no proposal.** If attributable but
  unavailable historical ids dilute every available model below the entry bar,
  and no previous arm is held, the computed roster has no arms. The roster
  computation returns `rc 3` without a file; `eval.yml` warns and runs on the
  committed roster, so no empty roster is proposed or used to run the eval. This is a
  second fail-safe outcome alongside a wrong proposal with its evidence, and
  is expected to be loud rather than silently repaired from untrusted history
  ([issue 168](https://github.com/Adam-S-Daniel/skills-evals/issues/168)).
- **The trusted history costs a human merge per meaningful change.** Model
  releases and retirements need review, and once a `catalogue_seen` entry's
  committed `last_seen` is at least half of `catalogue_seen_max_age_days`
  old, a Models API observation can also make a proposal on its own,
  refreshing that date even when no seat changes. That history-only proposal
  is intentional: it keeps the 180-day window anchored to a reviewed
  observation rather than an unmerged ephemeral result — but only at the
  cadence the window actually needs (about every 90 days), not on every
  week's routine re-observation, which would otherwise make the tracking
  issue nothing but date churn (#200). Automation
  is kept where it is cheap
  (noticing, computing, proposing) and removed where it was expensive
  (deciding). This is the fleet's sanctioned bot-write path — a branch and a
  PR a human merges — not `PR + auto-merge` and not a bypass actor.
- **`eval.yml` widens by `issues: write`.** That is a change to a key-bearing
  workflow and gets the one-way-door review (code half, adversarial half, the
  security-header read). The branch push needs no new permission: the job
  already pushes `eval-results`. GitHub can create approval-required workflow
  runs for `pull_request` opened, synchronized, or reopened by `GITHUB_TOKEN`.
  This design still pushes a branch and files an issue so a human opens and
  reviews the PR; that route makes the proposed change and its admission state
  explicit without adding a bot-created PR lifecycle.
- **Migration.** `evals/roster.yml` is seeded by hand in the same PR from what
  the committed fixtures pin today: arms `claude-sonnet-5` (all 13 fixtures),
  judge `claude-opus-4-8` (12 of the 13 — `evals/github-actions-sha-pinning`
  pins `claude-opus-5`), and preflight `claude-haiku-4-5`, which is not a
  fixture pin at all but the marked ROSTER FALLBACK literal in `eval.yml`'s
  own preflight step; with an empty `catalogue_seen` and a `provenance` block
  naming this ADR. The first real run
  after merge proposes the difference between that seed and what the Models API
  and census say, exactly as a first run always did; `roster/latest.json`
  already on `eval-results` is not read.
- **What this does not buy.** A proposal is only as honest as the census, and
  the census stays attacker-writable. A reviewer who merges a proposal without
  reading its shares merges the plant. The defence is that the plant is visible
  in a diff on `main`'s history rather than silent in a branch nobody reads, and
  that reverting the merge undoes it — the two properties `eval-results` never
  had.

## Alternatives considered

- **A `main`-committed store written by the job through a ruleset bypass
  actor** (the shape #147 names first). Rejected: a private `repo-settings`
  change, a new writer to a protected branch, and its own one-way-door review;
  priced at 6 to 15 points on top. The proposal branch buys the same trust for
  the price of one human merge per change.
- **A signature over the `eval-results` copy.** Rejected: the job has no
  signing secret (WIF mints an Anthropic bearer, not a repo secret), so this
  needs a new secret, and a signature proves who wrote the file, not that what
  they wrote was right — the census would still be signed by the machine that
  planted it.
- **Require every fixture to pin both models and delete the roster** (the
  estimate's option C). Rejected as the whole answer: it gives up the one thing
  #67 was for, noticing that a model shipped or retired. It is kept as the
  floor: an unpinned fixture with no usable committed roster is still an error.
- **A fifteenth fix round of local checks.** Not authorised (decision 11) and
  not recommended by either review half of round 14.
