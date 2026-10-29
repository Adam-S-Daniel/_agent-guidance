# ADR 0013: Judge a pair only when the objective checks do not decide it, with a judge one tier above the arm

- **Status:** accepted (2026-10-10). Built in
  [#379](https://github.com/Adam-S-Daniel/skills-evals/pull/379) (when the
  judge runs) and in the pull request that adds this record (which model
  judges).
- **Issue:** [#371](https://github.com/Adam-S-Daniel/skills-evals/issues/371);
  scheduled before the calibration sweep in
  [#372](https://github.com/Adam-S-Daniel/skills-evals/issues/372).
- **Decider:** the owner (2026-10-10), as recorded at the top of #371: "three
  rules for when and with what a run is judged. No paired Opus-vs-Fable
  comparison is required first."

## Context

- **One judge graded everything, every time.** `select_models` picked one
  judge for a run: the fixture's `judge.model` pin, else the roster's single
  `judge`. The roster computes that judge as the most capable model at least
  one tier above the *strongest* arm
  ([ADR 0001](0001-roster-trusted-on-main.md),
  [ADR 0002](0002-roster-follows-vendor-defaults.md)), so the cheapest arm
  was graded by the most expensive model on the ladder. The judge was called
  for every scored trial unless `--no-judge` was passed.
- **Most of those calls could not change a verdict.** A pair's verdict is
  "benefit" when the objective checks show a gain or the judge does
  ([#70](https://github.com/Adam-S-Daniel/skills-evals/issues/70)). In the
  2026-10-06 results six of eight fixtures showed an objective gain, so
  their judge calls were paid for and decided nothing.
- **The judge is the expensive call**, and runs now draw on a weekly
  subscription allowance ([ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md)).
- **The harness rule was "judge pinned strong".** `DESIGN.md` "Harness-wide
  rules" read "Arms on a pinned mid-tier model, judge pinned strong". Seven
  of the eight scheduled fixtures pinned both models, so scheduled results
  were Sonnet 5 runs judged by `claude-opus-4-8` or `claude-opus-5`, models
  no longer on the roster
  ([#376](https://github.com/Adam-S-Daniel/skills-evals/pull/376) removed
  those pins).
- **What a judge needs is to outrank the arm it grades.** A model grading
  its own run, or a peer's, is not an independent judgement; nothing on
  record shows that two tiers of headroom grade better than one.

## Decision

1. **Judge only when it can change the verdict.** For a with/without pair,
   no judge call is made while trials run. Once every trial of both arms is
   scored, the arms' objective pass rates are compared exactly. If `with` is
   strictly ahead, no trial of the pair is judged and each scored trial
   records `"judge": {"status": "skipped", "reason": "objective-decided"}`.
   Otherwise every scored trial of both arms is judged: all trials of a pair
   or none. A skip is not a judge error. A single-arm run and
   `--judge always` judge every trial; `--no-judge` judges nothing.
2. **The judge is chosen for the arm, one tier above it.** For an arm model
   in tier T, the judge is the newest roster arm in the lowest tier above T.
   When no roster arm sits above T, the judge is the roster's `judge`. With
   the roster of 2026-10-06 (`claude-sonnet-5`, `claude-sonnet-5-5`,
   `claude-opus-5-5`; judge `claude-fable-5-1`), Sonnet arms are judged by
   `claude-opus-5-5` and the Opus arm by `claude-fable-5-1`.
3. **A judge is never the model it grades and never in its tier.** When the
   rule cannot give such a judge, the run fails closed with a selection
   error. Under rule 2 that replaces the earlier refusal of any roster whose
   `judge` is also an arm: such a roster now stops only the arms that would
   fall to that judge (its own tier and above), and an arm with a roster arm
   above it still runs. The same holds where the newest arm in the tier
   above cannot be told (see "Newest" under Consequences): no judge is
   guessed.
4. **A fixture's `judge.model` pin still wins**, and `--model` still picks
   the arm. A pin is not checked against rule 3.
5. **A judge-carried fixture keeps the strongest judge.** A fixture whose
   `judge.mode` is `pairwise` is never skipped and is always judged by the
   roster's `judge`, whatever the arm. Rule 3 still holds: the run fails
   closed when the roster flags that judge as an arm, when it is the run's
   arm model (an arm from `--model` or the fixture's `model:` need not be a
   roster arm), or when the ladder puts it in that model's tier. A judge
   below the arm's tier is not refused here, and an arm the ladder cannot
   place is served with a null `arm_tier`.
6. **The roster is not changed.** Its schema, and how it is computed,
   rendered, admitted and merged (ADRs 0001 to
   [0004](0004-eval-runs-on-the-roster-its-run-merged.md)), stay as they
   are. The judge for an arm is derived at run time, in the harness, from
   the committed `evals/roster.yml` and the tier ladder in
   `evals/roster-policy.yml` (`harness/judge_policy.py`).
7. **Every summary says which judge and why.** `judge_selection` carries
   `model`, `rule` (`tier-above-arm`, `roster-judge` or `fixture-pin`),
   `arm_tier` and `judge_tier`, on a skipped pair too.

### What this supersedes

- **`DESIGN.md` "Arms on a pinned mid-tier model, judge pinned strong".**
  Arms follow the roster, and the judge follows the arm.
- **The single-judge reading of the roster.** The roster's `judge` entry
  (ADR 0001, and ADR 0002's roster) is no longer "the judge of every run".
  It is the judge of an arm with no roster arm above it, and of pairwise
  fixtures. The entry itself, and the rule that computes it, are unchanged.

## Consequences

- **Most judge calls go away**, and the remaining ones for Sonnet arms run
  on Opus, not Fable.
- **Judge scores for Sonnet arms are not comparable across this change.**
  They were given by `claude-opus-4-8`, `claude-opus-5` or
  `claude-fable-5-1`, and are now given by the newest Opus arm. Regression
  detection must start a new window at the change;
  `judge_selection` and `judge_models_used` say which judge gave a score.
- **A judge figure exists only for pairs the objective checks did not
  decide.** Consumers use the objective result for a decided pair and must
  not read a skipped judge as missing data.
- **The judge for an arm moves when the roster does.** A new Opus arm
  becomes the Sonnet arms' judge at the next run, with no fixture edit, and
  starts another window for their judge scores.
- **An arm model the ladder cannot place does not run judged** unless the
  fixture pins its judge. That includes a `--model` outside the ladder.
- **"Newest" is read from the id, and only where the id can say.** The
  committed roster carries no `created_at`, so the newest arm in a tier is
  the one whose version compares highest (`judge_policy.newest_of`, on
  `roster._version_key`; `roster.py` ranks by `created_at` first and uses
  that key as the tie-break). A `-YYYYMMDD` snapshot date
  (`roster.SNAPSHOT_SUFFIX`) is read as the date of the id before it, not as
  a version, so `claude-opus-4-1` is newer than `claude-opus-4-20250514`.
  Two ids are ordered only when their names differ in nothing but numbers.
  Peer families on one rung (`claude-fable-6` beside `claude-mythos-5`) and
  an older id shape beside a newer one (`claude-3-opus-20240229` beside
  `claude-opus-5-5`) differ in a word, and no number says which is newer:
  the arm below them then gets no judge and the run fails closed until a
  fixture pin or a roster change settles it. Where ids are ordered, this
  disagrees with `created_at` only for a model released later under a lower
  version.
- **A weaker judge may grade differently.** No paired Opus-versus-Fable
  comparison was run first (the owner's decision); the pin remains the override
  for a fixture that needs a specific judge.
- **The roster file does not show the judge chosen for each arm.** #371
  item 3 asked for that; rule 6 keeps the schema fixed, so the record is in
  each summary.

## Alternatives considered

- **Keep one judge, the strongest.** Rejected: it pays the top tier's price
  to grade the cheapest arm, with nothing on record that the extra tier
  improves the grade.
- **Always judge, and only change the judge model.** Rejected: a judge call
  on a pair the objective checks decide cannot change its verdict.
- **Decide judging per trial.** Rejected: a pair with some trials judged
  and others not has no judge figure that describes it.
- **Publish a judge per arm in the roster.** Rejected for this change: it
  alters the roster schema and its renderer, validator and automatic merge
  (ADRs 0001 to 0004) to carry something the harness can derive from the
  file as it is.
- **Run a paired Opus-versus-Fable comparison first.** Declined by the owner;
  the change does not wait for it.
- **Use the newest model in the tier above from the whole catalog, not
  from the arms.** Rejected: an arm is a model the roster already admitted
  on usage and vendor default; `catalogue_seen` is an observation history,
  not an admission.

## References

- [#371](https://github.com/Adam-S-Daniel/skills-evals/issues/371): the
  decision and its eight items.
- [#379](https://github.com/Adam-S-Daniel/skills-evals/pull/379):
  conditional judging and judge cost (items 1, 2 and 6).
- [#376](https://github.com/Adam-S-Daniel/skills-evals/pull/376): the stale
  pins removed from the scheduled fixtures (item 5).
- `harness/judge_policy.py`, `run_eval.select_judged_models`,
  `test/issues/test_issue_371_judge_tier.py`,
  `test/issues/test_issue_371_conditional.py`.
- [`DESIGN.md`](../../DESIGN.md): "Scorers" and "Harness-wide rules".
