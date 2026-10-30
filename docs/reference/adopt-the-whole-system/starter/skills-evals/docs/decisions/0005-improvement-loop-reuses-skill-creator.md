# ADR 0005: The improvement loop reuses skill-creator and measures through local_eval

- **Status:** accepted (2026-10-06, the owner), with two amendments (see
  [Acceptance amendments (2026-10-06)](#acceptance-amendments-2026-10-06)).
  Proposed 2026-10-04. Built and tested with fakes; watched real runs on
  2026-10-05 and 2026-10-06 (see the first-trial and serial trigger eval
  addenda). Decision items 4 and 6 are superseded in part, marked in place.
- **Issues:** [#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71)
  (the improvement loop; this is its smallest slice),
  [#232](https://github.com/Adam-S-Daniel/skills-evals/issues/232) (reuse
  Anthropic's skill-creator), and later
  [#122](https://github.com/Adam-S-Daniel/skills-evals/issues/122) (the same
  loop for guidance sections, which must extend this script, not fork it).
- **Deciders:** the owner, 2026-10-04: "I would like any pertinent built in or
  anthropic-published Claude skills or plugins related to evals to be
  leveraged where appropriate." the owner, 2026-10-06, accepting the record with
  the two amendments below.

## Context

#71 asks for a loop that turns "the skill still fails check X" into a better
SKILL.md: propose an edit from the failing train fixtures, measure it on a
held-out fixture, and accept it only when validation held and train
improved. Anthropic publishes a plugin that already does half of that.
`skill-creator` (plugin `skill-creator@claude-plugins-official`, author
Anthropic, installed at user scope on the owner's machine; read at commit
`d182ca456ca09d31d139f7d3818d1d333b103cce`) ships, under
`skills/skill-creator/`:

| Path | What it does |
|---|---|
| `scripts/run_loop.py` | The description-optimization loop. Splits a `[{query, should_trigger}]` set 60/40 into train and held-out, stratified by `should_trigger` with a fixed seed (`split_eval_set`); evaluates the description on both; asks for a better one from the train failures only (held-out scores are stripped from what the proposer sees); repeats up to `--max-iterations`; returns JSON whose `best_description` is chosen by **held-out** score. |
| `scripts/run_eval.py` | The trigger measurement `run_loop.py` calls: plants the description as a project command file under the nearest `.claude/commands/`, runs `claude -p <query>` `--runs-per-query` times, and counts a trigger when the model reaches for that skill. |
| `scripts/improve_description.py` | The proposal step `run_loop.py` calls: one `claude -p` on stdin, with skill-creator's own guidance on writing descriptions and its 1024-character limit. |
| `scripts/aggregate_benchmark.py` | Mean/stddev and with-vs-without delta over its own `grading.json` runs. |
| `agents/grader.md`, `agents/comparator.md`, `agents/analyzer.md` | Subagent prompts that grade expectations against a transcript, compare two outputs blind, and explain the winner. |

The rest of #71 has no counterpart there. skill-creator grades behavior with
an LLM grader reading expectations (`agents/grader.md`); it has nothing
equivalent to `harness/scorers/objective.py`, which replays a workspace's
changeset and decides a check by its files, git state and command log rather
than by a model's reading of them. DESIGN.md's rule that decidable facts are
never pushed onto a judge applies here as it did to `claude plugin eval`
(DESIGN.md, "`claude plugin eval`"): the behavior half has to be measured by
this harness or it is not measured the way every other number here is.

## Decision

`scripts/propose_skill_edit.py <skill>` composes the two:

1. **Trigger half — skill-creator, unmodified.** The script runs
   `scripts/run_loop.py` from the installed plugin (found through
   `~/.claude/plugins/installed_plugins.json`, or `--skill-creator` /
   `$SKILL_CREATOR_DIR`) with its `scripts` package on `PYTHONPATH`, the
   same module its documented `python -m scripts.run_loop` runs. It is
   reached through a link planted in the scratch project so the script path
   is a literal the suite's fork scan can classify. It gets its documented
   arguments (`--max-iterations 5 --runs-per-query 3 --holdout 0.4`, browser
   report off), plus `--num-workers 1` (serial only, see the serial
   trigger eval addendum). It keeps skill-creator's split, its held-out selection and its
   proposer; nothing of it is copied into this repo. Its working directory is
   a scratch project holding `.claude/settings.json`, so the command files
   it plants never land in a real checkout. Settings disable every local
   registry plugin providing the skill (see the hardening addendum). The
   query set is
   `--trigger-eval-set FILE` when given, else derived from fixtures: the
   train fixtures' prompts should trigger and every other skill's fixture
   prompts should not. **The validation fixture's prompt is always removed**,
   so the trigger loop never trains on the fixture that decides acceptance.
2. **Body half — one custom proposal call.** skill-creator has no step that
   edits a SKILL.md body from failed checks, so this is ours: one headless
   call to the committed roster's judge model, prompt on stdin, `--tools ""`,
   `--permission-mode default`, fed SKILL.md, the skill's `PURPOSE.md` when
   it has one, and each train fixture's failed checks with the tail of that
   trial's reply. It must answer `{rationale, unified_diff}`. The diff may
   name `SKILL.md` only and may not touch the frontmatter (the name is fixed;
   the description belongs to the trigger half). A diff that breaks either
   rule is recorded as `invalid-proposal` and nothing is measured.
3. **Measurement — this harness through local_eval.** Two scratch registries are extracted from
   `git archive <ref>` of the local registry checkout; they carry no `.git`,
   so there is no remote and no push path in them. Baseline and candidate
   are each one `scripts/local_eval.py evals/<skill> --arm with_skill
   --trials 3` against their scratch copy. The wrapper invokes
   `harness/run_eval.py` for each trial, with a fixed `--timestamp` so the
   loop can read every trial summary. Each invocation gets its own empty
   directory under `runs/<phase>/<skill>/<timestamp>/`.
4. **Split and acceptance.** The skill needs at least three nested fixtures
   (else exit 2, "needs more fixtures"). In name order, validation is the
   fixture at `rotation % n`, where `rotation` defaults to the number of
   records already written for the skill, so consecutive runs hold out a
   different fixture, excluding an optional fixed `--holdout`. A candidate
   is **accepted** only when validation held
   (objective pass rate not lower; judge mean not lower by more than 0.5)
   **and** train met `--min-gain` (default .10 on the normalized 0..1 scale:
   objective pass rate, or judge mean divided by 10 when objective is equal).
   The fixed holdout must not regress on either score. Any errored or
   unscored fixture, or missing expected judge score, rejects as inconclusive.
   **Superseded in part (2026-10-06):** the acceptance basis becomes
   real-work fixtures plus a token and cost metric (Amendment 2). This
   decision's objective and judge rule is what the code implements today
   (see "Not yet implemented"). The trigger half only picks the candidate
   description; it does not decide acceptance.
5. **Output stays local.** `improvements/<skill>/<ts>.json` is written every
   time (rejections are the high-value record), `<ts>.patch` whenever a
   candidate existed (rooted at the registry, `git apply`-able), and
   `<ts>.pr-body.md` on accept. The default root is outside the repository
   (`$XDG_STATE_HOME/skills-evals`, else `~/.local/state/skills-evals`). The
   script opens no pull request, pushes nothing and holds no credential.
6. **A local exhibit.** It runs under the operator's own login, so under
   [ADR 0002](0002-runs-bill-the-api-org-not-the-subscription.md) decision 4
   its numbers are not badge input, and it never runs in CI. There is no
   workflow: #71 says scheduling waits for three merged human-reviewed pull
   requests from it, and dispatch waits for the same evidence.
   **Superseded in part (2026-10-06):** "never runs in CI" and "there is no
   workflow" no longer hold for routine runs. The run path moves to the
   ADR 0010 routine (Amendment 1). What stands: the numbers are not badge
   input, and scheduling still waits for the three-PR gate
   ([ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md),
   decision 4).
7. **Every model call goes through one injectable `Runner`** (`run_eval`,
   `run_description_loop`, `propose`), so `test/issues/test_issue_71.py` runs
   the whole pipeline offline with a fake, and `--dry-run` prints the plan
   (split, models, call counts) with no model call and no write.

### What was considered and not used

| skill-creator part | Why not |
|---|---|
| `agents/grader.md` | An LLM grader of expectations. The behavior half is scored by `scorers/objective.py` and this harness's judge, which already run with N trials. |
| `scripts/aggregate_benchmark.py` | Reads skill-creator's `grading.json` layout. `run_eval.aggregate_trials` already aggregates this harness's trials, and the decision reads that. |
| `agents/comparator.md` / `analyzer.md` | Blind A/B needs subagents and a reader; a candidate here is accepted on numbers, and the record carries both tables for the human reviewing it. |
| skill-creator's own query-writing step (a reviewed ~20-query set) | Not automated. The fixture-derived default is thin (the train fixtures' distinct prompts as positives, other skills' prompts as negatives, few of them near-misses) and is refused when either of skill-creator's splits lacks a class (first-trial addendum); a reviewed set can be passed with `--trigger-eval-set`; `writing-adrs` ships one at `evals/writing-adrs/trigger-eval-set.json`. |

### Alternatives considered

| Alternative | Verdict |
|---|---|
| Reimplement the description loop here | Rejected: it would drift from Anthropic's, and the owner asked for the published tool to be used. |
| Let skill-creator measure the body edit too | Rejected: no objective scorer (see Context). |
| `claude plugin eval` for the trigger half | Not now: [#232](https://github.com/Adam-S-Daniel/skills-evals/issues/232) measured one run; it has a `tool_used: Skill` grader but no held-out description search. |
| One candidate per half, measured separately | Deferred: it doubles the paid measurement. The record says which halves changed, so a rejected combined candidate can be split by hand. |

## Consequences

- The number of model calls is bounded and visible before it starts (`--dry-run`
  prints it): 2 × fixtures × trials agent calls (plus as many judge calls),
  at most `5 × 3 × queries + 8` skill-creator calls, and one proposal call.
  There is no implemented per-skill spend cap or budget configuration file;
  the dollar cost is not bounded by these call counts. A spend-cap guardrail
  remains part of [#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71).
- Only two skills qualify today: `writing-adrs` (three fixtures) and
  `acsi-writing-style` (three, but its pairwise judge mode is refused by
  `run_eval.py`, so it needs `--no-judge`).
- The trigger split and the fixture split are independent: skill-creator
  holds out 40% of its query set with its own seed; this script holds out a
  fixture. Removing the validation prompt from the query set is what keeps
  the two from leaking into each other.
- **Guarded measurements.** After [PR #230](https://github.com/Adam-S-Daniel/skills-evals/pull/230)
  merged, `Runner.run_eval` routes both measurements through `local_eval.py`.
  Credential-variable refusal, the allow-listed child environment, the
  launch-time settings guard, the outside-repository results check,
  contamination probe and `LOCAL_EXHIBIT` marking apply to these eval runs.
  Exit 2 records the phase and refusal once, without retry or a later phase.
  An errored or missing trial remains inconclusive rather than being averaged
  away. The skill-creator trigger loop and body proposal use the same
  environment policy and guard launcher directly (see Review round 1 below).
- Not done here, from #71: the registry pull request itself, the
  `docs/skill-impact.md` entry, `improve.yml`, the idempotency check on an
  open `eval-improve/<skill>` branch, the budget refusal, and publishing
  records to `persistent/eval-results`.
- skill-creator's trigger runs retain the operator's real login. Scratch
  project settings disable the local registry plugins providing the target
  skill so those installed copies do not compete with the planted command.
  Federated marketplace sources are not fetched; their providers cannot be
  inferred from this registry archive.
- skill-creator is an external dependency read from the operator's plugin
  install. A plugin update can change `run_loop.py`'s output shape; the
  contract test in `test/issues/test_issue_71.py` runs the installed copy
  with a stand-in `claude` and fails if it does, but it is skipped wherever
  the plugin is not installed, CI included.

## Pre-real-run hardening addendum (2026-10-04)

The review of [PR #236](https://github.com/Adam-S-Daniel/skills-evals/pull/236)
identified five requirements before any real run. The implementation still
has only offline test evidence; no real evaluation was run for this change.

- **Trigger isolation uses project configuration.** The archived registry's
  `.claude-plugin/marketplace.json` supplies the marketplace and entry names
  for `enabledPlugins` keys. Local plugin manifests and marketplace entries
  supply additive skill directories, beside default `skills/`; an explicit
  skills list for a marketplace-root plugin limits that default scan. A
  plugin manifest is optional. Paths stay inside their archive and plugin
  roots, malformed manifests refuse the run, and remote sources are skipped.
  This follows the [plugin manifest reference](https://code.claude.com/docs/en/plugins-reference).
  Each provider is set to `false` in the scratch project's settings before
  the trigger loop starts. The run keeps the operator's login through the
  real `HOME`; a throwaway `HOME` belongs only to offline tests.
- **Acceptance requires material gain.** `--min-gain` must be finite and
  greater than zero, at most one; its default .10 means ten percentage points
  of objective pass rate, or one judge point on the 0..10 scale when the
  objective rate is exactly equal. The boundary is inclusive (with only
  1e-12 absolute tolerance for floating-point subtraction), neutral edits
  reject, and judge gains cannot rescue an insufficient objective gain.
  This conservative default reduces acceptance of tiny movements; it is an
  empirical heuristic, not calibrated statistical significance. The rotating
  validation judge tolerance remains .5, also uncalibrated, as requested in
  [#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71).
- **A fixed holdout protects against rotation leakage.** `--holdout` names
  one nested fixture excluded from training and the rotating validation
  population on every run. Three total fixtures still suffice: one train,
  one rotating validation, one fixed holdout. Both harness runs measure it,
  and objective rate and judge mean must not regress (zero judge tolerance).
  Its prompt is removed from every trigger query set, including overrides;
  its prompt and failure evidence never enter the body proposal. Missing or
  errored measurements reject as inconclusive. Choose a fixed fixture with
  a prompt distinct from every train fixture, select a previously untrained
  fixture, and keep the same `--holdout`
  on subsequent runs; a duplicate train prompt refuses before calls or
  writes. If a train prompt duplicates
  validation's prompt, exclusion can remove all positive trigger queries;
  that case refuses and requires a reviewed `--trigger-eval-set`, rather than
  leaking the validation prompt. `writing-adrs`' bootstrap and
  existing-convention fixtures currently share that prompt.
- **Bad descriptions reject without crashing.** Control characters,
  including DEL and C1 controls, or an unrenderable YAML description produce
  the ordinary `invalid-proposal` record and no candidate measurement.
- **Dry-run preflight is executable.** Every fixture's model selection uses
  the actual `--no-judge` flag. Unsupported judge modes or unavailable judge
  models refuse before calls or output writes and name `--no-judge` as the
  corrective option. The deferred spend cap is a documentation fact, not a
  mechanism this change implements: no script reads a budget file, and the
  plan reports call bounds rather than a dollar ceiling.

## Guarded measurement routing addendum (2026-10-04)

The deferred routing is done. Both baseline and candidate measurements call
`scripts/local_eval.py` with the selected registry archive, arm, trial count,
judge setting and fixed timestamp. The wrapper owns the credential and
settings refusals, child environment, guard launcher and local exhibit stamps.
The loop checks its persistent output root and write subdirectories with the
same outside-repository guard before creating a record. Each measurement uses
an empty directory, so a later improvement run can reuse the root without
mixing trials. A wrapper refusal records its phase and exit code and stops;
every requested trial must have a readable summary for a scored decision.
This was tested with fake CLI responses only; no paid evaluation was run.

### Review round 1: guard the trigger and proposal launches

Both remaining launch paths now build their child environment with
`local_eval.child_environment` and install `local_eval.install_guard_launcher`
in a private temporary directory. That directory leads the child's `PATH`,
and `CLAUDE_BIN` names its launcher. This covers skill-creator's literal
`claude` calls as well as the body proposal. Only skill-creator's selected
directory is added to `PYTHONPATH`; inherited Python and XDG configuration
variables, GitHub credentials and other unlisted variables are excluded.
The caller's environment remains unchanged, and the launcher directory is
removed on success or refusal.

The loop explicitly refuses credential or provider environment variables
before its first model launch, with exit 2 and a refusal record. Each Runner
launch also checks the original environment before filtering it. The launcher
checks user, harness and managed settings plus the actual launch directory
and its parent chain at every call, so a scratch project's `apiKeyHelper`
cannot depend on an earlier baseline check for refusal. A recorded guard
refusal ends the phase even if the external loop returns successful JSON.
Trigger and proposal refusals write their phase and stop without retry or a
candidate measurement. Offline stand-ins exercise both launch paths; mutation
checks prove the environment policy, launcher routing, settings preflight and
refusal records are enforced. No real evaluation was run.

## First watched trial addendum (2026-10-05)

The first real run (`writing-adrs`, rotation 0, `--trials 1 --no-judge`,
skills-evals `a41dfb0`, registry `f6bd36a`) ended `no-candidate` with every
guard holding, but its trigger half measured nothing. Of 21 fixture-derived
queries only one should trigger: existing-convention's prompt equals
bootstrap's, the validation prompt, so it was removed. skill-creator holds
out `max(1, int(n * 0.4))` queries of each class, so a lone positive always
lands in the held-out split. The train split was negatives only, passed
12/12 on the first iteration, and the loop stopped; the one positive
triggered 0 of 3 times. Three changes follow.

- **A fixture-derived set must give both splits both classes.** The script
  computes skill-creator's split counts (the arithmetic of `split_eval_set`;
  a contract test compares them with the installed function) and refuses a
  fixture-derived set whose train or held-out split lacks a should-trigger
  or should-not-trigger query. The refusal is status
  `trigger-set-unusable`, phase `trigger-set`, exit 2, with a record
  carrying the split counts, before any model call; `--dry-run` reports the
  same and exits 2. The held-out case is refused too: without a positive,
  held-out selection cannot notice a description that stopped triggering.
  Under skill-creator's arithmetic it cannot occur when the train split is
  sound, so the check is a guard against a changed split, not a second
  rule. A reviewed `--trigger-eval-set` is the operator's call: it runs, and
  its split problems are recorded and printed as a warning. Queries are now
  distinct (the first label wins), because skill-creator separates train
  from held-out results by query text; two copies of one prompt would sit
  on both sides. With today's three fixtures, every `writing-adrs` rotation
  is refused without a reviewed set; a fourth fixture with a distinct
  prompt is the fix on the fixture side.
- **The record counts the trigger loop's calls.** skill-creator reports no
  tokens or cost: `run_eval.py` stops reading each `stream-json` call once it
  has a verdict, and `improve_description.py` asks for text output. The
  record's `description_half.usage` therefore counts calls from the loop's
  output (each history entry's per-query `runs`, one proposer call between
  consecutive iterations, one per improve log recording a length rewrite)
  and sets `tokens` and `cost_usd` to null with that reason. The trial's
  count was 63 calls (21 queries, 3 runs, one iteration). A spend cap is
  still not implemented.
- **Parallelism is the operator's, default 4.** skill-creator's
  `--num-workers` (its default 10) is passed through from
  `--num-workers N`, default 4, and recorded as
  `description_half.num_workers`. Ten parallel CLI calls pushed the
  five-minute load average to about 28 on the operator's machine.

All three were built red-first against fakes; the regression test replays
the trial's rotation-0 split from the repository's own fixtures. No further
real run was made for this change.

## Serial trigger eval addendum (2026-10-06)

The first real `writing-adrs` loop (`--num-workers 4`, 307 skill-creator
calls) scored every rewrite exactly 7/12 train and 4/8 held-out, with
positives at 0-1 of 3 even for "add an ADR". That was the measurement, not
the descriptions.

- **Mechanism, from skill-creator's code (commit `d4226d06`).** `run_loop.py`
  calls `find_project_root()` once and hands that one directory to
  `run_eval.py`. Each worker plants `.claude/commands/<skill>-skill-<uuid>.md`
  there with the same description and counts a hit only when the `Skill` or
  `Read` call names its own uuid. With N workers the model sees up to N
  identical skills, and picking a sibling's copy, or declining to choose,
  scores as a miss.
- **A/B.** Three writing-adrs positives from `trigger-eval-set.json`, 3 runs
  each, same description and model, through `run_eval.py` in a scratch
  project like the script's: 7/9 triggers with 1 worker, 0/9 with 4, 7/9
  with 1 again (27 calls).
- **Decision: serial only.** `run_loop.py` takes no per-worker directory, so
  isolating workers would mean modifying skill-creator, which decision 1
  rules out. `--num-workers` now defaults to 1 and anything else is refused
  at argument parsing, before any call. This supersedes the "Parallelism is
  the operator's, default 4" bullet above; serial also removes its load
  concern. A serial run takes roughly 7-12 seconds per call, so about
  40-60 minutes for the ~300 calls of a five-iteration loop on a 20-query
  set.
- Records written with `num_workers` above 1 (the first real run's
  `description_half`) undercount triggers and should not be compared with
  serial ones.

## Acceptance amendments (2026-10-06)

The owner, 2026-10-06, accepting this record, verbatim: "I approve ADR 0005 with
the amendments you recommended:
1. Move the run path to the ADR 0010 routine with auto permission mode.
2. Record real-work fixtures plus a token/cost metric as the acceptance basis,
instead of trigger rate alone."

### Amendment 1: the run path is the ADR 0010 routine, in auto permission mode

The loop's measurement is to run in the eval runner routine described by
[ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md)
(fired by Actions through
[`routine-eval-fire.yml`](../../.github/workflows/routine-eval-fire.yml)),
not only under the operator's own login. Arms and the judge launch with
`--permission-mode auto`, which is the harness default since
[PR #304](https://github.com/Adam-S-Daniel/skills-evals/pull/304). The CLI
refuses `bypassPermissions` as root in the routine sandbox; that auto mode
runs as root there is expected but, per ADR 0010 ("Resulting direction" and
its open questions), not yet shown. The baseline and candidate measurements
already share one mode, because both are built by the same `run_eval_argv`
helper and pass no `--permission-mode`
(`scripts/propose_skill_edit.py:805-810`).

This supersedes the "local exhibit ... never runs in CI ... no workflow"
framing of decision 6 for routine runs only. Decisions 3 and 4 of
[ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md#decisions-2026-10-06)
carry over unchanged: routine results stay a local exhibit and do not feed
the badge, and no scheduled loop runs until three human-reviewed pull
requests from it have merged.

### Amendment 2: the acceptance basis is real-work fixtures plus a token/cost metric

A candidate is to be judged on how the agent does real work, with an
efficiency metric beside it, instead of on the objective and judge rule of
decision 4 alone. The basis is:

- **Real-work fixtures**, drawn from merged fleet pull requests and admitted
  only when they carry a FAIL_TO_PASS test: the design and checker are in
  [PR #303](https://github.com/Adam-S-Daniel/skills-evals/pull/303) and the
  read-only miner is
  [PR #305](https://github.com/Adam-S-Daniel/skills-evals/pull/305)
  (merged as `9086eef4`).
- **A token and cost metric.** the owner chose tokens as the primary efficiency
  KPI: Q7, "Tokens (Recommended)" (`DESIGN.md:185`, in "Real-work fixture
  decisions (the owner, 2026-10-06)"). The per-arm efficiency aggregates the
  decision can read, with the with-vs-without delta, are in
  [PR #301](https://github.com/Adam-S-Daniel/skills-evals/pull/301).

One serial trial, run by the orchestrating session at skills-evals `6817d6d`
and not recorded in the addenda above, ended with the best description equal
to the original after 183 skill-creator calls (three iterations, reported as
10/12 and 7/8, 10/12 and 6/8, then 12/12 and 6/8). Its raw record is not in
this repository.

### Not yet implemented

Both are as of `origin/main` at `ec9cf33`.

- **`propose_skill_edit.py`'s acceptance does not read tokens.** `decide`
  compares objective pass rate and judge mean only
  (`scripts/propose_skill_edit.py:862-921`), and the per-fixture metrics it
  reads carry `passed`, `total` and `judge_mean`
  (`scripts/propose_skill_edit.py:813-848`). The trigger half records
  `tokens` and `cost_usd` as null (`scripts/propose_skill_edit.py:129-132`,
  `:596-599`). PR #301 says the same of its aggregates: tokens are not wired
  into accept/reject yet, and `DESIGN.md` says so of Q7. Decision 4's objective and judge
  rule is therefore what the code implements today; this amendment records
  the intended basis, not a behavior change.
- **The loop is not wired into the routine.** No file under `.github/`
  mentions `scripts/propose_skill_edit.py`, and `routine-eval-fire.yml`
  fires a fixture, arms and trials (its payload carries `mode: eval`,
  `run_id`, `fixture`, `arms`, `trials`) or, in scaffold mode, one miner
  candidate, not the improvement loop. The loop still runs from an operator's shell.

## Token-aware acceptance addendum (2026-10-06)

Amendment 2's first "Not yet implemented" bullet is done; the text above is
left as it was written, so read it with this note. Decision 4's rule is now
the quality input of `decide`, with tokens read beside it, and the line
references above (`scripts/propose_skill_edit.py:805-810`, `:862-921`,
`:813-848`, `:129-132`, `:596-599`) predate this change. The owner answered the
rule question as "Keep as built (Recommended)": quality first, tokens veto
only a candidate with no quality gain, cache tokens count, and `cost_usd` is
not decided on.

`decide` (`scripts/propose_skill_edit.py:903`) reads `tokens` from
`fixture_metrics` (`:832`): the mean per trial of the four `usage` counts
(input, output, cache creation, cache read) from the per-arm efficiency
aggregate of
[PR #301](https://github.com/Adam-S-Daniel/skills-evals/pull/301), computed
by `mean_tokens` (`:875`). It is null when any trial lacks any count or a
figure is negative or not finite.

- **Missing or invalid token data is inconclusive.** A null `tokens` on either
  side for any measured fixture (train, validation, holdout) rejects with
  "inconclusive: missing token data" naming the side and fixture. It never
  passes as zero.
- **More tokens without a quality gain is not accepted.** Tokens are summed
  over the measured fixtures. A candidate above the baseline by more than
  `TOKEN_INCREASE_TOLERANCE` (0, so any rise) without a quality gain is
  rejected with that reason. The quality gain is the train gain of
  `--min-gain`, which acceptance already required, so a candidate that costs
  more tokens is accepted only alongside one, with validation and holdout
  held, and the rise is recorded in the reasons.
- **Cost is recorded, not decided on.** Q7 names tokens the primary KPI.

The trigger half still records `tokens` and `cost_usd` as null
(`scripts/propose_skill_edit.py:618`): skill-creator reports neither, so they
are not part of the decision. The second "Not yet implemented" bullet (the
loop is not wired into the routine) still holds.

## Total tokens addendum (2026-10-07)

The token-aware addendum above is left as written; read its definition of
`tokens` with this note. The CLI's own schema (Claude Code 2.1.292) defines a
result's `usage` as the main loop's alone, excluding subagents and auxiliary
calls, while `modelUsage` holds per-model totals for "every model call made
through the query pipeline ... main loop, Task subagents, sidechains, and
internal calls such as compaction". A skill that moves work into a subagent
could therefore look cheaper while costing more. The owner answered the basis
question as "Switch to total (Recommended)" (the owner, 2026-10-07).

- **What `tokens` measures.** `fixture_metrics` sets a fixture's `tokens` to
  the mean per trial of `run_eval.model_usage_total`: the same four counts
  (input, output, cache read, cache creation) summed over every model in
  the trial's `model_tokens`. The main loop's figure is still recorded, as
  `main_loop_tokens`, and decided on by nothing.
- **Thresholds and rules are unchanged.** `TOKEN_INCREASE_TOLERANCE` is
  still 0 and the veto still applies only without a quality gain. Because
  acceptance already requires that gain, the veto changes a rejected
  candidate's reasons rather than its outcome.
- **Incomplete is unknown.** A trial whose per-model accounting is
  incomplete (an entry dropped, more than 16 models, a count missing,
  invalid or past `run_eval.MAX_TOKEN_COUNT`) makes the fixture's `tokens`
  null, which `decide` rejects as "inconclusive: missing token data", the
  path the token-aware addendum defined. No new rule.
- **The basis is recorded.** Every record and every fixture's metrics carry
  `tokens_basis: "model_usage_total"`. Metrics and records without one
  predate this addendum and measured `usage_main_loop`. `decide` refuses a
  comparison whose baseline and candidate fixtures name different bases
  ("inconclusive: token bases differ ..."). `scripts/improve_gate.py`
  accepts records with either basis or none, and rejects any other value.

## Routine improve mode addendum (2026-10-06)

The second "Not yet implemented" bullet (the loop is not wired into the
routine) is half done: the branch contract and its gate are built; the
routine's saved prompt and the fire workflow's `mode` input are not changed
here. Built red-first with fakes; no routine run and no real pull request.
The fire workflow's input has since landed (the last bullet below); the
routine's saved prompt is still unchanged.

- **The routine opens no pull request.** In improve mode it runs
  `scripts/propose_skill_edit.py` and, only when the candidate is
  `accepted`, pushes one branch `claude/eval-improve-<run id>` that adds
  exactly `eval-improve/<run id>/summary.json` (the record),
  `report.md` (the record's `<ts>.pr-body.md`) and `skill.patch` (its
  `<ts>.patch`). A rejected or refused run pushes nothing.
- **A gate in skills-evals validates it**, on the pattern of the results
  ingest: `routine-improve-pushed.yml` (no permissions) and
  `routine-improve-gate.yml` on its `workflow_run`, the default branch's copy
  only. `scripts/improve_gate.py` reads the branch through git plumbing and
  accepts it only as an accepted schema-1 record for acsi-agentskills at a
  full sha, a patch of that skill's `SKILL.md` alone whose hunks parse, and
  a report with the loop's title and no closing keyword or @mention
  (checked with its Markdown emphasis and link syntax stripped). The
  root is `eval-improve/`, not `eval-results/`: the improve branch also
  matches the results signal's `claude/eval-*`, and the two path filters
  are what keep each push to its own gate.
- **The draft pull request is opened with a GitHub App token.** It is
  opened in example-adopter/acsi-agentskills, which this repository's
  `GITHUB_TOKEN` cannot write. The owner chose the credential on 2026-10-06:
  "GitHub App (Recommended)". After re-validating the judged sha, the
  `draft-pr` job mints an installation token with
  `actions/create-github-app-token` from repository secrets
  `EVAL_IMPROVE_APP_CLIENT_ID` and `EVAL_IMPROVE_APP_PEM`. The token is
  limited to acsi-agentskills, with contents and pull-requests write only,
  and one later step uses it. While either secret is unset the job is
  skipped with a summary note. It does not run until the owner creates the App
  (Contents: write and Pull requests: write, installed on acsi-agentskills
  only) and adds both secrets to skills-evals. When it runs, it checks the
  measured registry sha is on acsi-agentskills' default branch and applies
  the patch there. It refuses any frontmatter change other than the
  description, and any second file. It pushes `eval-improve/<skill>`
  without force, or does nothing if that branch exists (one candidate per
  skill waits for a person), and opens a draft. It never merges. A pull
  request the App opens starts acsi-agentskills' CI.
- **#71's gate stays.** Nothing here is scheduled; a suite test fails if any
  workflow that fires the routine or handles an improve branch gains a
  schedule. The three human-reviewed loop pull requests are the draft pull
  requests this gate opens, once a person merges them.
- **The launch guard and the routine's environment.** In a routine
  session `propose_skill_edit.py`'s guard (`local_eval_guard.refused_env_names`)
  refuses ten variables the sandbox sets, `ANTHROPIC_BASE_URL` and
  `CLOUDSDK_AUTH_ACCESS_TOKEN` among them. ADR 0010's Probe 3 (2026-10-06)
  showed a nested `claude -p` still authenticates with every one of them
  unset. The owner chose "Probe unset first (Recommended)", so the guard is not
  changed: the routine launches the loop under an `env -u` prefix built at
  run time from `refused_env_names(os.environ)`, not from a fixed list. The
  loop also needs skill-creator installed in the session.
- **The fire workflow's improve mode.** `routine-eval-fire.yml` takes
  `mode: improve` with a `skill` and an optional `holdout`. Before the
  bearer is in any step's env it checks that `skill` matches
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and passes
  `scripts/improve_gate.py fire-check`: the loop's own `skill_fixtures` and
  `load_fixtures` (every nested `evals/<skill>/<name>` fixture names this
  skill and one registry), `harness/registries.yml` mapping that registry
  to acsi-agentskills (the one the routine checks out), and at least
  `MIN_FIXTURES` fixtures. So `guidance`, `real-work`, a flat fixture and
  another registry's skill are refused before a session starts. Only the
  registry-side `SKILL.md` lookup needs the registry checkout and stays
  with the routine. It also checks that `holdout` is empty or one of those
  names (at most 128 characters of `[A-Za-z0-9._-]`), that neither input
  has a control character, and that `fixture` and `candidate` are empty
  (and `skill` and `holdout` empty in the other modes). It sends
  exactly five keys built by jq: `run_id`, `mode`, `skill`, `trials` and
  `holdout` (null when empty). Still dispatch only, with no schedule.

## Automatic merge addendum (2026-10-26)

The owner, 2026-10-26: the loop merges accepted changes automatically, with no
human in the loop. This supersedes, for an accepted improvement, every
"a person reviews and merges" above: the routine improve mode addendum's
"opens a draft. It never merges", and the "Review before merging" line of the
report `propose_skill_edit.py` writes (the gate's pull-request body says it is
superseded). The registry side of the exception is acsi-agentskills
[ADR 0021](https://github.com/example-adopter/acsi-agentskills/blob/main/docs/decisions/0021-automated-skill-changes-merge-without-review.md),
which covers removals ([ADR 0014](0014-removal-pull-requests-merge-without-review.md))
too. Built red-first with fakes and against a real acsi-agentskills checkout;
no routine run and no real pull request.

- **One setting.** `evals/improve-policy.yml` holds
  `improve_merge_mode: auto|draft`, committed as `auto`. A missing key is
  `auto`; an unreadable file, an unknown value or another key is `draft`.
  It is a separate file because `evals/gate-policy.yml` is the retirement
  verdict's, parsed strictly by `harness/gates.py` and copied into every
  gates file.
- **The gate builds the whole registry change, with no credential.**
  `scripts/improve_gate.py prepare` works on a checkout of acsi-agentskills'
  default branch tip (fetched anonymously, before the App token is minted):
  the validated patch, a `PURPOSE.md` stub when the skill has none, the
  plugin's patch version in both manifests (acsi-agentskills ADR 0009), a
  `docs/skill-impact.md` `edit` entry carrying the record's objective scores
  per fixture, and the README table (the registry's own
  `scripts/generate_readme_table.py`); then, when the registry's
  `generate_skills_lock.py --check-current` says the lock no longer
  describes the bundle, a second commit re-pinning `skills.lock` to the
  first. That is what the registry's required checks and its AGENTS.md ask
  of a skill edit; run against a real checkout, its consistency, manifest,
  README, lock, version, privacy and agent checks pass, as does its pytest
  suite. The pull request is based on the tip rather than the measured sha
  so the version bump is above what it merges into; `prepare` refuses when
  the skill's directory changed between the two, since what would merge is
  then not what was measured.
- **Auto mode opens a ready pull request and enables auto-merge.** The
  pull request is labeled `agent-authored` and `eval-improve`, its body
  carries the record's report and ends with the fleet's agent marker, and
  `gh pr merge --auto --merge` is enabled after the labels and read back.
  acsi-agentskills' required checks still gate the merge.
- **Draft when the plugin runs code.** A skill whose plugin has a `hooks/`
  or `bin/` directory, a settings or package file, or a manifest `hooks` or
  `settings` key always gets a draft: a hook can name any file under the
  plugin root, and plugin-runtime-review needs the owner's approval, which
  an automatic merge must never wait on or bypass. Every path the change
  touches must be the skill's directory, `docs/skill-impact.md`, the two
  manifests, `README.md` or `skills.lock`, or `prepare` refuses.
- **One loop pull request at a time.** Improvements and removals write the
  same bundle version, `docs/skill-impact.md` and `skills.lock`, so a
  second open one would conflict once the first merged. The gate opens
  nothing while any `eval-improve/*` or `retire-skill/*` pull request is
  open in acsi-agentskills (`scripts/retire_skill.py` checks only its own
  prefix).
- **The credential is unchanged.** Contents: write and Pull requests: write
  on acsi-agentskills cover pushing, opening, labeling and enabling
  auto-merge. The `eval-improve` label must exist there first. While either App
  secret is unset, the job still skips.
- **The same switch drives the draft-skill gate.** `improve_gate.py
  merge-mode` prints the same decision (`effective_mode`) for
  `routine-draft-skill-gate.yml`: in `auto` its skill pull request in
  acsi-agentskills is ready, labeled and auto-merging (draft instead while
  another loop pull request is open there), and its fixtures pull request
  here is ready with auto-merge only when the skill's is. The fixtures pull
  request is opened with the job token, which starts no workflow, so its
  required checks (`test`, `agent-checks`) start only when someone pushes
  or runs them; that keeps it behind the skill's, but it does not merge on
  its own until a credential that starts workflows opens it.
- **Not changed here.** Decision 4 of
  [ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md) ties
  any scheduled loop to three human-reviewed loop pull requests; an
  auto-merged one does not count toward them, so that gate needs its own
  decision. The fleet rule "PR + auto-merge is not a sanctioned bot-write
  path" needs a repo-settings ADR.

## Routine guidance improve mode addendum (2026-10-27)

The improve mode above, for a fleet-guidance section instead of a skill:
the top-ranked eligible section is fired, measured and landed in
example-adopter/_agent-guidance with no human, under the same switch. The
registry side is repo-settings ADR 0012 (loop changes merge without review)
and ADR 0014 (the eval-improve App writes _agent-guidance for the guidance
loop). Built red-first with fakes and a scratch _agent-guidance checkout; no
routine run and no real pull request.

- **The fire.** `routine-eval-fire.yml` takes `mode: guidance-improve` with
  a `section` and an optional `holdout` (a fixture key relative to
  `evals/`, such as `guidance/<section>/<name>`). Before the bearer is in any
  step's env it checks the section against `^[a-z0-9][a-z0-9-]{0,63}$` (so
  `_delivery` and every other `_` directory are refused) and runs
  `scripts/guidance_improve_gate.py fire-check`: at least the proposer's
  `MIN_FIXTURES` fixtures declaring the `full` arm
  (`propose_guidance_edit.fixture_layout_discover`), a holdout empty or one
  of them, and, against the _agent-guidance checkout, a manifest row that is
  not skipped. It sends exactly `run_id`, `mode`, `section`, `trials` and
  `holdout` (null when empty), and its title is `Fire eval routine:
  guidance-improve <section>`.
- **Charged and paused as an improve fire.** `improve_outcomes` reads those
  titles and `claude/eval-guidance-improve-<run id>` branches as records
  keyed by `section`; the allowance guard charges them `improve_charge` for
  the week (`charged_fires`). The weekly plan makes the highest-value
  eligible section an improve-band fire, one per plan, with `trials=3`.
  Rule C applies per section (an open `eval-guidance-improve/<section>` pull
  request, an attempt in flight, the 14-day cooldown, three rejections),
  and a guidance lane holds the rest: no fire while another section's
  guidance-loop pull request (`eval-guidance-improve/*`, `retire-guidance/*`)
  is open or another section's attempt is in flight, since every guidance
  edit writes `AGENTS.md` and `docs/guidance-impact.md`. A section id the
  fire workflow's pattern refuses stays a work item for a local run.
- **The branch and its gate.** On an accepted candidate only (exit 0: the
  candidate was accepted and its patch passed _agent-guidance's own checks)
  the routine pushes `claude/eval-guidance-improve-<run id>`, adding exactly
  `eval-guidance-improve/<run id>/summary.json`, `report.md` and
  `guidance.patch`. The root is its own: the branch also matches the results
  signal's `claude/eval-*`, and the path filters keep each push to its own
  gate (a suite test parses the three). `routine-guidance-improve-pushed.yml`
  holds no permissions; `routine-guidance-improve-gate.yml` runs on its
  `workflow_run` from the default branch's copy and validates without
  running anything: the record is accepted for one section at a full sha
  with a split equal to that section's fixtures on the default branch, the
  patch touches only the row's file and the proposer's bookkeeping files
  (`agents-md/eval-coverage.yml`, `docs/guidance-impact.md`, `AGENTS.md`),
  and the report has the proposer's title and no closing keyword or
  @mention.
- **The commit is built with no credential.** `prepare` works on an
  anonymous checkout of _agent-guidance's default branch tip, refuses unless
  the measured sha is on it and `agents-md/` and `AGENTS.md` are unchanged
  since, applies the patch, re-copies `agents-md/base.md` to
  `.claude/hooks/fleet-guidance.md` when the section is in base.md (that
  repository's byte-identical fleet-memory payload), and refuses any changed
  path outside ADR 0014's allowlist. It checks what changed, not only where,
  since base.md reaches every repository: the section's file must keep its
  `##` headings and every byte outside the section's extent (the proposer's
  own extent finder), the manifest may change only the row's `bytes`, to the
  new extent's size, and the impact file may gain only one inserted entry
  naming the section. AGENTS.md is left to _agent-guidance's staleness check.
- **The pull request.** Only then is the eval-improve App's token minted, for
  _agent-guidance alone, under `continue-on-error`; one step uses it. The
  pull request comes from `eval-guidance-improve/<section>`, labeled
  `agent-authored`; in `auto` (`evals/improve-policy.yml`) it is ready with
  auto-merge by merge commit and `--match-head-commit`, read back, otherwise
  a draft. Nothing is opened while another guidance-loop pull request is
  open there or the target branch exists. With either App secret unset, or a
  failed mint (the App not yet installed on _agent-guidance), the job opens
  nothing, says why in its summary, and succeeds.
- **The routine's saved prompt** needs a GUIDANCE-IMPROVE MODE section (not in
  this repository; the owner pastes it). Each run is at most 4 fixtures x 2
  sides x 3 trials = 24 nested agent arms, 48 when the proposer re-runs a
  noisy or near-threshold measurement, plus one proposal call: 49 agent
  sessions for a four-fixture section, 73 at six. The prompt raises this
  mode's nested-agent limit to 100 and keeps the 90-minute stop.
