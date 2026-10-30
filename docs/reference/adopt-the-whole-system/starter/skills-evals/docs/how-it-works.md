# How the skills evals work, where to look, and what is left

A plain-English guide for someone who knows the fleet but has not worked in
this repository. The technical method is in [`DESIGN.md`](../DESIGN.md), the
operating record, with a link to your live status board, in
[`HANDOFF.md`](../HANDOFF.md).
Last revised 2026-10-26.

## 1. What the system does

Skills are reusable instruction files (recipe cards) that a coding agent
installs to do a class of task better. Fleet guidance is the same idea for
account-wide rules. This repository answers one question with numbers: **does
the agent do the job better with the card than without it?**

For each measured skill there is a **fixture**: a task prompt, a small
throwaway repository to work in, a list of objective checks, and a judge
rubric. A run takes the same model and has it do the task twice in two clean
copies of that repository, once with the skill installed and once without.
Then two scores are produced:

- **Objective checks** are scripted assertions on the finished workspace
  (files exist, YAML parses, a workflow would trigger on the right paths, a
  ruleset was not touched). They pass or fail and cannot be argued with.
  The [`workflow_permissions` check](../DESIGN.md#reusable-workflow-permission-checks)
  also verifies a named reusable-workflow caller's effective permission grant:
  job permissions replace workflow permissions, and missing or malformed
  evidence fails the check.
- **The judge** is a second, stronger model that reads both transcripts and
  diffs and scores each arm on a few named dimensions (for example
  completeness, salience, restraint) from a rubric written into the fixture.
  It is the subjective half, kept honest by the objective half.

A badge summarises the last five runs: green if the with-skill arm beat the
without-skill arm, yellow for a tie or mixed signals, red if the skill hurt.

## 2. Which models run, and who decides

The models that compete (the **arms**), the **judge**, and the cheap
**preflight** model that proves the API token works are listed in
[`evals/roster.yml`](../evals/roster.yml) on `main`. That file is trusted
because `main` is pull-request only: nothing automated can change it.

Every run also *computes* what the roster should be, from two inputs: the
Models API (which models exist and how old they are) and a **usage census**
(which models this account actually runs, per week, published from a
workstation as `usage/latest.json` on the results branch by
`scripts/publish_usage_census.sh`).

**How a model earns an arm's seat** (the owner decided this on 2026-09-22). A
model is an arm if the fleet actually runs on it — at least 10% of the
countable turns of the last two weeks. On top of that, the *newest* model
in a tier gets a seat too, but **only in a tier where some model already
earned one on usage**, and only once it is a week old. So the newest Opus
comes in because the fleet runs Opus; the newest Haiku does not, because it
does not. The rule used to give every tier on the ladder a seat regardless,
which put four arms on a roster where two of them measured tiers carrying
3% and 6% of the work — at four arms' worth of paid spend per run. On
today's census that means two arms (Sonnet 5 and Opus 5); an Opus 5.1
shipped before the next run would make three. The one exception is when
there is no usable census at all: then nothing can qualify, a roster with no
arms is useless, and the newest model of every tier takes a seat. If the census
itself is unusable, each fallback reason names the degradation. If the census
is usable but only its enter window fails a ranked-usage floor, the reason is
the bare newest-per-tier sentence.

The computed roster is only a **proposal**. When it differs from the committed
file the run pushes the proposed file to the bot-owned branch
`roster/proposal` and keeps one tracking issue open with every seat's reason
and the numerator and denominator behind it. With `roster_mode: auto` (the
setting today) the run opens a pull request from that branch itself and sets
it to merge once its `test` check passes; with `roster_mode: proposal` a human
opens and merges it, or does not. See ADR
[0001](decisions/0001-roster-trusted-on-main.md) for why it is shaped this
way, and ADR [0003](decisions/0003-roster-merges-automatically.md) for the
automatic merge.

The eval then runs on that merged roster in the same run, if the merge lands
within about half an hour and checks out: it changes only
`evals/roster.yml`, and that file is exactly what the run proposed. If not,
the eval runs on the committed roster as before, and the run's summary says
why (ADR [0004](decisions/0004-eval-runs-on-the-roster-its-run-merged.md)).

**Fixtures follow the roster; they do not pin models.** A fixture leaves out
`model:` and `judge.model:`, and the weekly sweep runs it once per roster arm
with a judge chosen above that arm. A pin goes stale the moment the roster
moves on (twelve fixtures kept a retired Sonnet and a below-arm Opus judge
until 2026-10-27). A fixture that truly needs one keeps it and says why, in a
parsed field: top-level `model_pin_reason:` beside `model:`, and
`judge.model_pin_reason:` beside `judge.model:` (non-empty strings). The
required `test` check enforces it
(`FixtureModelPinTests` in `test/issues/test_issue_371_judge_tier.py`): any
`model:` or `judge.model:` without its reason key fails, even one naming a
current roster id. That check never reads the roster, so fixtures stay in
step with it automatically: an unreasoned fixture always follows the roster,
and a roster pull request that drops a model (it changes exactly
`evals/roster.yml`, ADR [0003](decisions/0003-roster-merges-automatically.md))
cannot be turned red by a pin. A reasoned pin is a deliberate choice that the
roster does not move. The one deliberate pin is `evals/guidance/_delivery`,
which uses the roster's current Haiku (the `preflight` id), because the
delivery guard runs on the fixture's own model.

## 3. When it runs, and what it costs to run

`.github/workflows/eval.yml` runs every Tuesday at 07:00 UTC and on manual
dispatch (`gh workflow run eval.yml --ref main`, optional input `fixture=`).
It bills the organisation's API workspace `skills-evals-ci` through workload
identity federation, not the Claude Code subscription. A measured run on
2026-09-22 (one fixture, one Sonnet 5 arm, Opus 4.8 judge) took 6 minutes 24
seconds wall clock and cost **$1.71**: Sonnet 5 arms $0.89, Opus 4.8 judge
$0.78, Haiku preflight $0.04. The judge is about 45% of a run.

Running the arms or the judge on the account's Claude subscription instead —
the weekly allowance, not dollars — was evaluated on 2026-09-23 and not
adopted: see ADR
[0002](decisions/0002-runs-bill-the-api-org-not-the-subscription.md) and § 7's
blocking decision 5.

### The weekly allowance guard

Since 2026-10-06 the scheduled evals run as Claude Code routines on the
subscription, so they spend the weekly allowance. The owner's standing decision
([#68, 2026-10-10](https://github.com/Adam-S-Daniel/skills-evals/issues/68#issuecomment-6097945979))
is that all automated evals together stay within **30% of the weekly
allowance**. The numbers live in [`evals/budget.yml`](../evals/budget.yml),
as fractions of the allowance (0.01 is one point, and points = percent of the
weekly allowance):

| Key | Value | Meaning |
|---|---|---|
| `weekly_cap` | 0.30 | all automated evals, in each allowance week of the account (from Monday 17:00 UTC) |
| `per_run_cap` | 0.03 | one run stops starting new work after this much |
| `per_run_overrun` | 0.005 | added to every run: its last call is never measured |
| `stop_when_account_above` | 0.80 | a run stops once the account's weekly meter reads this |

Three layers enforce it:

1. **The in-run stop** (`harness/run_eval.py`). The CLI prints the account's
   weekly meter as each agent call begins. Before each trial and each judge
   call, a run checks the latest reading: once the meter has moved
   `per_run_cap` since the run's first reading (after a window reset, the
   new reading alone counts), or reads `stop_when_account_above`, nothing
   more is started. Skipped trials are written with error `budget_stop`, a
   skipped judge call as a judge error, and every summary of the run carries
   `usage.budget_stop` naming the limit. A guidance run, being one pair, ends
   with exit 2 instead. A run with no reading yet, or an API-billed one, is
   never stopped. `--budget PATH` reads another budget file.
2. **The pre-fire check** (`scripts/allowance_guard.py check`, a step of
   `routine-eval-fire.yml` before the fire, in every mode). It adds up the
   account's current allowance week, from its last weekly reset
   (`ACCOUNT_WEEK_RESET` plus whole weeks: Monday 17:00 UTC, so daylight
   saving does not move it; a rolling seven days until 2026-10-26): each ingested run on `persistent/eval-results` at its
   measured weekly delta plus the overrun (or the full reservation,
   `per_run_cap + per_run_overrun`, when unmeasured), plus every successful
   fire without results: at the full reservation when it is under 2 hours
   old, and at the overrun alone after that (a routine run finishes within
   minutes, so a fire with no results two hours later failed or never
   started). An improve fire (or a guidance-improve fire) never lands
   results, so it is charged its own
   cost for the whole week: twelve eval fires at their measured mean
   (twelve full reservations until anything is measured). The fire goes
   only if one more reservation (an improve fire's charge, in improve mode)
   still fits under `weekly_cap`. If the GitHub API cannot be read, the
   fire is refused. The log shows numbers only.
3. **The weekly plan** (`scripts/plan_week.py`, see "The weekly plan"
   below). The Tuesday sweep spends the room left at each fire's historical
   cost; when nothing fits, the dispatch job is skipped.

Known gaps:

- **Local evals** (`scripts/local_eval.py`) are bounded only by the in-run
  stop until #370's single results pool lands. That wrapper starts run_eval
  once per trial, so in practice only the account limit applies there.
- **Improve mode's** skill-creator trigger evals call `claude -p` directly,
  not through run_eval, so only the pre-fire reservation bounds them.
- **One trial can overshoot**: the stop is checked between trials, so the
  trial in flight when a limit is crossed still finishes.
- **The meter is account-wide**: the owner's own use during a run is counted as
  eval spend. That overcounts, which is the safe direction.
- **Fires racing each other**: a fire counts only fires that have finished
  successfully, so two dispatched at the same moment do not see each other.
  The weekly plan accounts for its own fires up front.

### The weekly plan

`routine-eval-weekly.yml` runs `scripts/plan_week.py` every Tuesday at 07:00
UTC (G5, G6; the owner lifted the three-PR gate on 2026-10-26, see the
[ADR 0010 addendum](decisions/0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md#decision-4-addendum-2026-10-26)).
It computes the retirement verdicts over the results branch, then plans
eval fires (one reviewed fixture from `evals/scheduled.yml` on one roster
model) and improve fires (one skill, with a fixed holdout) in six bands:

1. **Safety**: every cell of a skill with an open removal pull request.
2. **Missing evidence** (at least 30% of the room): cells with no run,
   heaviest-used model first. A cell whose fires twice produced nothing
   becomes a "fixture broken" work item instead.
3. **Near a decision** (at least 20%): cells fewest runs short of their next
   look.
4. **Improvements** (at least 20%): skills with four or more fixtures, train
   headroom, a check that fails often but can pass, no retirement verdict,
   and no pause, ordered by potential value: expected uses over four weeks
   times train headroom, per allowance point an improve run costs (three
   trials of every fixture at its historical fire cost; an unknown use or
   cost is neutral, an unknown headroom ranks last). Fleet-guidance sections
   are ranked in the same list (every session loads the guidance, so its use
   is the census's session count scaled to four weeks). The highest-value
   eligible section is a `guidance-improve` fire, one per plan, at three
   trials and the improve charge
   ([ADR 0005 addendum](decisions/0005-improvement-loop-reuses-skill-creator.md#routine-guidance-improve-mode-addendum-2026-10-27));
   only a section id the fire workflow refuses stays a work item for a
   local `scripts/propose_guidance_edit.py` run.
   The whole ranking is the plan's `improve_ranking`, the job summary's top
   ten, and the `improve-ranking` artifact (30 days). A skill with
   fewer fixtures becomes a "needs fixtures" work item. A skill that already
   passes at least 90% of its checks has no headroom for an edit to gain, so
   it becomes a "needs harder fixtures" work item: write a fixture it fails.
5. **Re-checks**: decided cells not measured for eight weeks.
6. **Bare baselines** (no minimum share, only the room left): one fire with
   `arms: bare` and three trials per scheduled fixture that runs in place
   and roster model, once per model id
   ([ADR 0012 addendum](decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md#bare-baseline-addendum-the-owner-2026-10-27),
   the owner, 2026-10-27). It runs the isolation `without_skill` arm alone,
   nothing of ours loaded, to compare with the in-place `with` arm. A bare
   run already ingested, or a bare fire that succeeded or is still running,
   counts as done; a new roster model gets its own. Bare runs are listed
   apart (`bare_runs` in the verdict document) and never pooled into a cell.

`routine-eval-fire.yml` accepts `arms: bare` in eval mode only, and only for
a fixture whose `fixture.yaml` has a top-level `context` mapping; its run is
titled `Fire eval routine: eval bare <fixture> <model>`. The allowance guard
holds it as one run, like any eval fire; the plan reserves it as one arm
(the cell's cost per arm trial, times three).

A skill is paused while an improve pull request is open, an attempt is in
flight, an open `on-hold` issue names it, for 14 days after a rejection, and
after three rejections in a row until the skill, a fixture or the roster
changes (or 90 days). A failed fire is retried at most twice. After a merged
improvement nothing is attempted until the with arm is measured again. The
routine does not record rejections, so `scripts/improve_outcomes.py` reads
them from GitHub: an improve fire with no `claude/eval-improve-<run id>`
branch twelve hours later counts as a rejection. A guidance section follows
the same rules, read from `claude/eval-guidance-improve-<run id>` branches and
`eval-guidance-improve/<section>` pull requests in _agent-guidance, and one
more: no section is fired while another section's guidance-loop pull request
(`eval-guidance-improve/*` or `retire-guidance/*`) is open there or its
attempt is in flight, because every guidance edit writes `AGENTS.md` and
`docs/guidance-impact.md`.

An accepted guidance candidate is pushed as `claude/eval-guidance-improve-<run
id>`; `routine-guidance-improve-gate.yml` validates it without running it,
builds the _agent-guidance commit with no credential (re-copying base.md to
its fleet-memory payload), and opens the pull request from
`eval-guidance-improve/<section>` with the eval-improve App's token minted for
_agent-guidance alone: ready with auto-merge in `auto`, a draft otherwise, and
nothing (with the reason in the job summary) while the App's secrets are unset
or the mint fails.

Work items (`needs-fixtures`, `needs-harder-fixtures`, `fixture-broken`, and
`agent-check` items read from lesson proposals) are listed in the plan, not
fired; each carries a `change_type`. The dispatch job fires one leg at a time, waits while three
fires are in flight, and spaces fires a minute apart; improve fires go last.

### The trigger lane

Usage tracking (2026-10-26) counted about 55 skill calls in eight weeks over
about 72,000 model turns: most skills almost never trigger, so a skill's
description is probably the biggest lever. Every acsi-agentskills skill whose
usage weight is fresh and at most one call in four weeks (an unknown weight
is neutral and gets nothing) becomes a work item, placed just before the
improve work items:

- **`trigger-optimize`** (change type `trigger`) when
  `evals/<skill>/trigger-eval-set.json` exists. It is routed
  **needs routine prompt change**, not fired: improve mode's payload is
  exactly `run_id`, `mode`, `skill`, `trials` and `holdout`, and the
  routine's saved prompt (not in this repository) decides the loop's flags.
  To fire it, the owner would add to the prompt's IMPROVE MODE: "When
  `evals/<skill>/trigger-eval-set.json` exists in the skills-evals checkout,
  pass `--trigger-eval-set evals/<skill>/trigger-eval-set.json` to
  `scripts/propose_skill_edit.py`." That alone makes every improve fire of
  such a skill use the reviewed set. A trigger-only fire for a skill that is
  not improve-eligible would also need a payload field from
  `routine-eval-fire.yml` and a description-only option in
  `propose_skill_edit.py`; neither exists. Once fired, a trigger fire would
  be charged and ordered as an improve fire.
- **`needs-trigger-set`** (change type `fixture`) when there is no set: write
  one, a JSON list of `{query, should_trigger}` objects.

The plan also lists **hook candidates** (the owner: hooks first): skills whose
SKILL.md has at least half its non-blank body lines under a heading naming a
checklist, rules or checks. That heuristic is cheap and misses rules written
as prose.

### Skill retirement (#70)

Every day, `skill-coverage.yml`'s `retirement` job (schedule only) runs
`scripts/compute_gates.py` over the results branch and the public registries,
then `scripts/retire_skill.py` over its verdicts. A skill whose verdict is
`retire` (no benefit on every fixture and roster model, at two fixed looks)
gets, in `retirement_mode: auto` (the committed value in
`evals/gate-policy.yml`), a ready removal pull request in acsi-agentskills,
labeled `agent-authored`, that auto-merges (merge commit) with no human
review ([ADR 0014](decisions/0014-removal-pull-requests-merge-without-review.md),
acsi-agentskills ADR 0021), one loop pull request at a time: nothing is
opened while an `eval-improve/`, `draft-skill/` or `retire-skill/` pull
request is open there, and a closed `retire-skill/<skill>` one means the
skill is decided. That branch carries everything the registry's required
checks read — the skill gone, the plugin's major version raised, a
`docs/skill-impact.md` entry, the layout pin in its
`scripts/plugin_layout.json`, the README skill table and, when the plugin is
locked, a `skills.lock` re-pin — produced by the registry's own generators
rather than reimplemented here. The job checks out the registry with full
history (the lock re-pin reads the commit the lock pins) and writes with the
improvement gate's GitHub App token. Without that token, or for a skill whose
plugin runs code (its removal would wait on plugin-runtime-review), it falls
back to `proposal`: one skills-evals issue that is filed or updated and that
a human acts on; once that issue is closed, the skill is not proposed
again. Preview either mode with
`python3 scripts/retire_skill.py --gates FILE --registry acsi-agentskills=PATH --dry-run`.

A fixture moved into a named subdirectory with its content unchanged keeps
its evidence: `FIXTURE_ALIASES` in `harness/gates.py` maps the old name (""
for a flat fixture) to the new one, and an old run pools with it only while
its `fixture_digest` equals the new fixture's on main. It lists the three
fixtures nested on 2026-10-26 (#422, #427).

### Draft mode: new skills from lessons, hooks first

The session miner writes redacted lesson proposals to
acsi-agentskills-private's `lessons/proposals/<date>/<subject>__<kind>.json` on its `persistent/lessons` branch (the miner's write target; `main` is PR-only).
Draft mode is what consumes the `new-skill` kind:

1. **Pick** (`routine-draft-skill.yml`, daily at 06:30 UTC). One proposal:
   the most evidence, then the newest, then by name; never a subject that
   is already a skill, already has `evals/<subject>/`, or was drafted
   before (any `claude/draft-skill-*` branch). It dispatches
   `routine-eval-fire.yml` in draft mode only when one fire fits the
   weekly allowance guard.
2. **Triage, hooks first** (the owner, 2026-10-26: "I want the first choice to
   be git hooks whenever possible"). The routine first asks whether the
   lesson can be enforced mechanically: a check in the fleet's
   `.githooks/agent-checks` runner (_agent-guidance ADR 0021, enforced by
   the required `agent-checks` CI job) or a PreToolUse guard. If so it
   drafts **no skill**: it records `decision: hook-first` with a check
   sketch, and the gate opens a `Hook proposal: <subject>` issue here,
   labeled `hook-proposal`, for the agent-checks lane. Only a lesson that
   cannot be checked becomes a skill; guidance text comes last of all.
3. **Draft and measure.** Otherwise the routine drafts the skill with
   Anthropic's skill-creator (pinned commit) plus four fixtures modeled on
   `evals/writing-adrs/`, then `scripts/draft_new_skill.py measure` runs
   both arms on them. It is accepted only if every fixture produced both
   arms, the with-skill arm passed every objective check on at least one
   fixture, and the with-skill pass rate beats the without-skill rate.
   The record says why the lesson was not a hook (`triage`).
4. **Gate** (`routine-draft-skill-gate.yml`). The routine always pushes
   `claude/draft-skill-<run id>`; the gate validates it from the default
   branch without running it. Accepted: a draft pull request in
   acsi-agentskills with the skill, then a draft pull request here with
   only `evals/<subject>/` and its README layout line (merge the skill
   first). Rejected: the record stays on its branch and the subject is not
   picked again.

**The decline path.** When the agent-checks lane finds a hook proposal
cannot be checked at commit time, it closes the issue as not planned. The
picker then re-admits that subject with `hook_declined`, and the routine
skips triage and drafts a skill. The routine's saved prompt for this mode
is in [`routine-prompt-draft-mode.md`](routine-prompt-draft-mode.md); the owner
approves changes to it.

### Fixture supply (#65)

Fixtures are the improvement queue, so they are made and promoted with no
person in the loop (the owner, 2026-10-26; ADR
[0003](decisions/0003-roster-merges-automatically.md)'s 2026-10-26 addendum).
`.github/workflows/routine-fixture-supply.yml` runs daily at 13:17 UTC:

1. **Scaffold.** It mines the fleet's newest merged pull requests
   (`scripts/mine_real_work.py`, REST only), and
   `scripts/scaffold_real_work.py pick` drops every candidate the routine
   would refuse or already has: a body that quotes the diff, several closing
   issues, an existing `evals/real-work/<id>/` or `claude/scaffold-<id>`
   branch, or a successful scaffold fire in the last 30 days (read from the
   fire workflow's run titles). The rest rank by the skill their changed
   files are evidence for (an ADR is writing-adrs, a workflow
   workflow-path-audit, a shell script review-bash-ci-reliability, an e2e
   spec browser-testing), fewest existing fixtures first. At most three
   are fired in scaffold mode, a minute apart, and fewer when fires of the
   last two hours are still counted as running or the allowance guard has
   less room.
2. **Merge.** The routine pushes `claude/scaffold-<id>`;
   `routine-scaffold-gate.yml` validates it (now also that every checker
   file is the merge commit's own) and the roster App opens the pull request
   with auto-merge on. `test` scores the fixture red on the seed and green
   with the fix; ci.yml installs its `deps:` from the fixture itself. Any
   later push turns auto-merge off until it passes the gate again.
3. **Promote.** A merged fixture is `draft: true`. `scaffold_real_work.py
   promote` finds every committed draft (none under an `onboarding` or
   `browser-testing` directory) with at least two ingested runs of its
   current content where both arms ran and none errored, sets `draft: false`,
   adds it to `evals/scheduled.yml` with a readiness note starting
   "Promoted automatically", and the App opens that change as one
   auto-merging pull request; an open one holds the slot.

Known gaps: no workflow runs a draft's agent arms (`--allow-draft`), and a
real-work fixture needs a subject (`--skill`) that the fire's eval mode does
not send, so no draft has ingested runs yet and promotion has nothing to
promote. No per-skill usage count exists, so the ranking uses fixture
counts alone. Real-work fixtures (`subject: any`) are not counted by
improve's fixture minimum.

### Lessons from real sessions (2026-10-26)

Once a day on the owner's own computer, never in the cloud
([ADR 0015](decisions/0015-mine-real-sessions-only-on-the-owners-own-computer.md)),
`scripts/run_session_miner.sh` reads the Claude Code sessions written since
the last run, keeps real work, and records where the agent was corrected,
interrupted, failed the same tool call repeatedly, turned CI red after a push
or stopped unfinished. Each record is a short redacted excerpt with a hashed
session id. Records are grouped into proposal files on acsi-agentskills-private's
`persistent/lessons` branch: a new git-hook check when one could enforce the
lesson, otherwise a skill or guidance edit, a new skill, a memory note, or a
fixture candidate that a fixture writer turns into an eval. The layout and
schema are in that repo's `lessons/README.md`. Commands: DESIGN.md, "Lessons
from real sessions".

### Guidance section retirement (#124)

The same job also reads an `_agent-guidance` checkout. Five sections have
fixtures under `evals/guidance/<section id>/<name>/`, and
`anything-you-name-gets-its-link` has four. Each fixture runs the section
inside the whole guidance against the guidance without it, and the section
alone against no guidance. A section that adds nothing in situ is `retire`.
If it helps only when alone, it is `shorten-or-merge` instead, because the
rest of the guidance already covers it. Both verdicts become one
skills-evals issue in `proposal` mode, which is the committed
`guidance.retirement_mode`. In `auto` mode a `retire` would open an
auto-merging removal pull request in `_agent-guidance` that passes that
repository's own checks. A `shorten-or-merge` verdict never removes the
section. Preview with
`python3 scripts/retire_skill.py --gates FILE --target guidance --guidance PATH --dry-run`.

## 4. Where to look for current results

Everything published lives on the unprotected
[`persistent/eval-results`](https://github.com/example-adopter/skills-evals/tree/persistent/eval-results)
branch, which carries generated data only and is treated as untrusted input:

| Path on `persistent/eval-results` | What it is |
|---|---|
| `results/<fixture>/<timestamp>/report.md` | one page per run: prompt, per-arm objective score, judge score, cost, turns, duration |
| `results/<fixture>/<timestamp>/{with_skill,without_skill}/summary.json` | the raw numbers behind the report, including token usage and every check's detail |
| `badges/<fixture>.json` | the shields.io badge over the last five runs, rendered at the top of the README |
| `roster/latest.json` | the computed roster and proposal from the latest run (an exhibit, read by no decision) |
| `usage/latest.json` | the usage census |
| `propagation/` | historical: the retired account-store audit's last result (no longer updated) |

The most recent real run at the time of writing is
[`results/workflow-path-audit/20260922T125552Z/report.md`](https://github.com/example-adopter/skills-evals/blob/persistent/eval-results/results/workflow-path-audit/20260922T125552Z/report.md).
Raw transcripts are deliberately never published (public repository): each arm's `transcripts/raw.json` stays on the runner, in neither `persistent/eval-results` nor the run's workflow artifact. Each arm's `transcripts/` directory also holds `tool_trace.json`, a bounded, redacted line per tool call and tool result; it stays out of `persistent/eval-results` and reaches only the run's workflow artifact. That artifact (`eval-results-payload-*` on success, `eval-results-diagnostics-*` on failure) carries only `summary.json`, `report.md` and `tool_trace.json` (#289).

## 5. What is measured today

Every fixture below lives under `evals/` on `main`; each entry says what task the agent is given, what a good result looks like, how many scripted objective checks decide pass or fail and what they look at, which judge dimensions score the subjective half (with their weights), the pinned models (a scheduled fixture pins none and names its in-place context instead), and whether a real run against the live CLI has happened yet (most have not: the scheduled run always targets `workflow-path-audit`, and decision 8 stopped the per-skill lanes). Inventory taken on 2026-09-22 at `main` `3515904`, then re-derived from the `fixture.yaml` files under `evals/` on 2026-10-04 at `main` `a1e0b25`: that pass added `skills-doctor` and `vendor-release-impact-issues`, which predate the first inventory but were missing from it, and the third `writing-adrs` fixture, `supersede`, added by [PR #229](https://github.com/Adam-S-Daniel/skills-evals/pull/229). The fourth `writing-adrs` fixture, `why-no-comment-trail`, was added on 2026-10-05. Eighteen fixtures are listed. On 2026-10-26 the flat `workflow-path-audit` fixture moved to `workflow-path-audit/required-check-routing` and three sibling fixtures joined it; they are listed after it, and the count above predates them.

Two `evals/` directories aren't covered below because they aren't skill or guidance-subject fixtures: `evals/guidance-bridge-canary/`, a tool-free magic-word probe of the `CLAUDE.md -> @AGENTS.md` import, and `evals/propagation/`, a skill-delivery probe compared against `acsi-agentskills`' lockfile. Neither has a prompt, objective checks, or a judge rubric.

### acsi-writing-style/proposal-bio

- `acsi-writing-style` (registry: `acsi-agentskills-private`) — [evals/acsi-writing-style/proposal-bio](https://github.com/example-adopter/skills-evals/tree/main/evals/acsi-writing-style/proposal-bio)
- Write a 60-word third-person proposal bio from background notes; good output is concrete (employer/dates, the named accessibility standard), third-person, and free of corporate filler.
- Objective (3): no avoid-list buzzwords survive; third-person with no first-person "I"; both the employment dates and the named standard (Section 508) are cited.
- Judge: pairwise rank (no weights) against references `in-voice`/`generic`; dimensions specificity, register match, absence of corporate filler.
- No pinned models: the roster picks the agent and the judge. Class C. No real run yet.

### acsi-writing-style/recruiter-reply

- `acsi-writing-style` (registry: `acsi-agentskills-private`) — [evals/acsi-writing-style/recruiter-reply](https://github.com/example-adopter/skills-evals/tree/main/evals/acsi-writing-style/recruiter-reply)
- Reply to a recruiter's cold email in the user's voice, declining but leaving the door open; good output greets by name, opens with a hedge rather than an assertion, and cites specifics from the material.
- Objective (4): no avoid-list buzzwords; greets the recruiter by name in the opening; opens with a hedge/apology, not an assertion; cites both the requisition number and the contract end date.
- Judge: pairwise rank (no weights) against `in-voice`/`generic`; dimensions specificity, register match, absence of corporate filler.
- No pinned models: the roster picks the agent and the judge. Class C. No real run yet.

### acsi-writing-style/self-appraisal-opening

- `acsi-writing-style` (registry: `acsi-agentskills-private`) — [evals/acsi-writing-style/self-appraisal-opening](https://github.com/example-adopter/skills-evals/tree/main/evals/acsi-writing-style/self-appraisal-opening)
- Draft the opening paragraph of a quarterly self-appraisal from notes; good output is first-person, narrative, generous toward collaborators, and specific about named systems and numbers.
- Objective (3): no avoid-list buzzwords; genuinely first-person (not narrated about the author); the named repository and pipeline-time figure from the notes are both cited.
- Judge: pairwise rank (no weights) against two references; dimensions specificity, register match, absence of corporate filler.
- No pinned models: the roster picks the agent and the judge. Class C. No real run yet.

### cms-stuck-pr-triage

- `cms-stuck-pr-triage` (registry: `example-site-platform`) — [evals/cms-stuck-pr-triage](https://github.com/example-adopter/skills-evals/tree/main/evals/cms-stuck-pr-triage)
- Diagnose why a CMS publish loop "keeps failing" and say what to do about each open PR, using a fake replay-only `gh` CLI seeded with three PRs (one blocked by a missing required status check, one on a stale base, one that should be left alone). Good output names the right cause per PR and attempts no writes.
- Objective (7): #412's stale base/superseded commit named; #418's missing required status context named; no write aimed at #421; the fake `gh` was used with no write attempted overall; the failing run's log was actually read; the workflow files and the fake's own payloads stayed untouched.
- Judge: weighted — `root_cause` 0.4, `decisions` 0.4, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. Class B. No real run yet.

### disarm-inherited-reach

- `disarm-inherited-reach` (registry: `acsi-agentskills`) — [evals/disarm-inherited-reach](https://github.com/example-adopter/skills-evals/tree/main/evals/disarm-inherited-reach)
- Set up a throwaway copy of a checkout so a destructive test script can't reach the real (prod) repo, then run it. Good output makes a genuine standalone copy (severing the inherited `origin`), runs the script only there, and reports the disarm plus an observed failed push.
- Objective (8): original checkout's remote/HEAD untouched; prod's history never moved; no leaked config anywhere still names prod; the script ran outside the armed locations, in a genuine standalone remote-free repo; the worktree list unchanged; the final reply names the severed remote and the failed push.
- Judge: weighted — `"procedure fidelity"` 0.5, `restraint` 0.2, `explanation` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A. No real run yet.

### github-actions-sha-pinning

- `github-actions-sha-pinning` (registry: `example-site-platform`) — [evals/github-actions-sha-pinning](https://github.com/example-adopter/skills-evals/tree/main/evals/github-actions-sha-pinning)
- Bring a repo's Actions pins into line with the fleet pinning policy. Good output pins every third-party action to the exact SHA `PINS.md` lists, strips trailing version comments, and leaves the repo's own `example-site-platform` references on their release tag rather than converting them to a SHA.
- Objective (9): third-party `uses:` lines SHA-pinned and matching `PINS.md` exactly; no trailing comments; `example-site-platform` refs stay on tag; local/docker refs and `PINS.md` byte-identical to seed; the three workflow/action files not deleted or gutted; everything parses as YAML.
- Judge: weighted — `carve_out` 0.4, `comment_removal` 0.3, `restraint` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-a`. Class A. No real run under this name; a predecessor fixture (`pin-actions-to-sha`, before its rewrite/reintroduction as this one under issue #85) has 6 real runs, latest 2026-08-17.

### guidance/_delivery

- Subject `guidance`, section `security` (`_agent-guidance` checkout) — [evals/guidance/_delivery](https://github.com/example-adopter/skills-evals/tree/main/evals/guidance/_delivery)
- Not a behavioral A/B — a scheduled delivery canary asking a tool-free agent to report any "magic words" in its context, once per delivery mode (`none`, `stub`, `section`, `full`, `full-minus-section`). Good output reports exactly the token it was actually delivered, proving the fleet-memory hook really puts guidance into context the way it claims to.
- Objective (5, one per arm): the control shows its own decoy token and not the treatment token; each of the four treatment arms shows the treatment token for its mode.
- Judge: none — this fixture measures delivery, not quality.
- Model `claude-haiku-4-5` (cheapest model sufficient for a tool-free probe). No class label. 1 real run, latest 2026-09-13T22:16:38Z.

### post-failure-comment

- `post-failure-comment` (registry: `example-site-platform`) — [evals/post-failure-comment](https://github.com/example-adopter/skills-evals/tree/main/evals/post-failure-comment)
- Wire two Playwright CI workflows' failures into the platform's `post-failure-comment` composite action instead of an ad hoc inline `gh pr comment`, following its caller-side-gating convention (including a schedule-only workflow that shouldn't be touched). Good output uses correct `post`/`resolve` step pairs, matching markers, and correct `if:` gating per job shape.
- Objective (11): schedule-only workflow/vendored contract untouched; old inline comment block removed; single-job post/resolve steps wired correctly; multi-job report job wired on `needs:` not `job.status`; composite references resolve validly; markers don't collide; no raw event/input interpolation in `run:`; workflows parse as YAML.
- Judge: weighted — `convention_fidelity` 0.5, `gitleaks_explanation` 0.2, `restraint` 0.3.
- No pinned models: the roster picks the agent and the judge. Class A. No real run yet.

### rename-pdfs

- `rename-pdfs` (registry: `acsi-agentskills`) — [evals/rename-pdfs](https://github.com/example-adopter/skills-evals/tree/main/evals/rename-pdfs)
- Rename six seeded PDFs (a statement, an invoice with a decoy filename date, an already-conventional receipt, an image-only scan, a duplicate bill pair) to the user's `YYYYMMDD-Type-Issuer-Title.pdf` convention. Good output gets every date/type right, prefers the document body's date over a misleading filename date, disambiguates true duplicates, and leaves conventional or unreadable files alone.
- Objective (8): final `inbox/` listing matches exactly; every PDF's bytes survive by digest; five per-file digest checks confirm each file's bytes ended up under the right name (or stayed alone); nothing exists outside `inbox/`.
- Judge: weighted — `convention_fidelity` 0.5, `date_priority` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. Class A. No real run yet.

### review-bash-ci-reliability/release-pipeline

- `review-bash-ci-reliability` (registry: `acsi-agentskills`) — [evals/review-bash-ci-reliability/release-pipeline](https://github.com/example-adopter/skills-evals/tree/main/evals/review-bash-ci-reliability/release-pipeline)
- Review a release pipeline's shell for reliability problems and fix them (seeded with an exit-status swallow via process substitution, a broken-pipe-prone `grep -q`, a swallowed `gh api` failure, missing git identity/signing setup, an unguarded `jq` dependency). Good output fixes the real bugs while leaving two decoys — an already-correct line and an optional cleanup line — untouched.
- Objective (10): workflow parses and exists; `gh run watch` status captured correctly; `grep -q` no longer pipe-fed; failed `gh api` call not silently read as "nothing found"; git identity configured; `jq` guaranteed or replaced; version-read logic survives; commit signing made CI-safe; both decoys untouched.
- Judge: weighted — `correctness` 0.5, `restraint` 0.2, `explanation` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A. No real run yet.

### review-bash-ci-reliability/drift-report-capture

- `review-bash-ci-reliability` (registry: `acsi-agentskills`) — [evals/review-bash-ci-reliability/drift-report-capture](https://github.com/example-adopter/skills-evals/tree/main/evals/review-bash-ci-reliability/drift-report-capture)
- Review a config-drift check whose `diff` and `grep | head | cut` captures abort it under `set -euo pipefail` on a normal result (checklist item 7). Good output makes the first drifted file and an unowned template survivable while drift still fails the job.
- Objective (7): workflow parses and exists; with two of three files drifted every one is reported with its summary; an unowned template is not fatal; a clean tree still passes; drift and a missing file still fail; the config data is untouched. The four run checks execute the final script through `repo_tests` against a hidden probe.
- Judge: weighted — `correctness` 0.5, `restraint` 0.2, `explanation` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A. No real run yet.

### review-bash-ci-reliability/test-runner-assertions

- `review-bash-ci-reliability` (registry: `acsi-agentskills`) — [evals/review-bash-ci-reliability/test-runner-assertions](https://github.com/example-adopter/skills-evals/tree/main/evals/review-bash-ci-reliability/test-runner-assertions)
- Review a green shell test runner whose `grep`, `cmp` and script-under-test captures kill the whole run on a failing assertion (item 7's test-harness paragraph). Good output records one FAIL, runs every later test and prints the Results line.
- Objective (7): workflow parses and exists; a missing README line, a changed golden file and a crashing script under test are each recorded as one FAIL with the other four tests run; the green run and the existing mismatch path are unchanged; the script under test and its fixtures are untouched. The four run checks execute the final runner through `repo_tests` against a hidden probe.
- Judge: weighted — `correctness` 0.5, `restraint` 0.2, `explanation` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A. No real run yet.

### review-bash-ci-reliability/empty-repo-discovery

- `review-bash-ci-reliability` (registry: `acsi-agentskills`) — [evals/review-bash-ci-reliability/empty-repo-discovery](https://github.com/example-adopter/skills-evals/tree/main/evals/review-bash-ci-reliability/empty-repo-discovery)
- Review a nightly visibility audit that reads `gh repo list` through process substitution and exits 0 on an empty result, so a failed listing goes green (item 3). Good output fails the job when the listing fails while an organization with no public repositories still passes.
- Objective (7): workflow parses and exists; a failed listing and a listing that fails halfway both fail the job; an empty organization stays green; listed repositories are still audited; the allowlist is untouched. The four run checks execute the final script through `repo_tests` against a hidden stand-in `gh`; the arms get the shared offline `gh`. The skill's own SAFE snippet, copied verbatim, fails the empty-organization check.
- Judge: weighted — `correctness` 0.5, `restraint` 0.2, `explanation` 0.3.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A. No real run yet.

### skills-doctor/bucketed-account-store

- `skills-doctor` (registry: `acsi-agentskills`) — [evals/skills-doctor/bucketed-account-store](https://github.com/example-adopter/skills-evals/tree/main/evals/skills-doctor/bucketed-account-store)
- Diagnose a captured session where `writing-adrs` arrives from two channels under one name and the account copy is the older one, while the account store looks empty because it sits one directory down in a per-account bucket. Good output opens the bucket, names the duplicated skill and the missing "Alternatives considered" heading, says honestly that nothing on disk records which copy the model read, and repairs nothing.
- Objective (5): a `FINDINGS.md` was written; it names the account bucket and the other skill in it; it names `writing-adrs` as arriving from both channels; it identifies the missing heading; every captured file is unchanged.
- Judge: weighted — `root-cause` 0.5, `which-copy-the-model-reads` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/acsi-agentskills`. Class B. No real run yet.

### vendor-release-impact-issues/issue-hygiene

- `vendor-release-impact-issues` (registry: `acsi-agentskills`) — [evals/vendor-release-impact-issues/issue-hygiene](https://github.com/example-adopter/skills-evals/tree/main/evals/vendor-release-impact-issues/issue-hygiene)
- Turn a vendor CLI's release notes (ExampleCLI 4.1.0 to 4.3.0) into drafted GitHub issues for a small repo that consumes it. Five real findings are baked in, mixed with decoys. Good output files one issue per real finding against the right file, quotes releases with their link and publish time, fences anything GitHub would act on, posts no upstream backlink, and writes nothing outside `issues/`.
- Objective (7): at least one issue drafted; publish times bound to their source and version; PR references fenced rather than quoted; no backlink to the vendor's issue; no placeholder text in a title; the seed repo unchanged; the hook-matcher surface named.
- Judge: weighted — `correctness` 0.5, `hygiene` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. Class A (per the README; the fixture states no class). 1 real run, latest 2026-09-26T18:45:29Z: objective 6/7 with the skill against 4/7 without, judge overall 7.2 against 5.4.

### windows-elevation-from-wsl

- `windows-elevation-from-wsl` (registry: `acsi-agentskills`) — [evals/windows-elevation-from-wsl](https://github.com/example-adopter/skills-evals/tree/main/evals/windows-elevation-from-wsl)
- From inside WSL, move a scheduled Windows backup task's fire time and apply it to the live task, against a fake `powershell.exe`/`pwsh.exe` that simulates elevation prompts. Good output edits the script, never tries to dodge or force elevation from WSL, exports the live task before handoff, and tells the user the exact elevated command to run.
- Objective (7): script's default time moved (03:30, not 02:00); no elevation-dodge attempted; at most one denied write retried; task principal/run level not downgraded; live task exported before handoff; final reply names the need for elevation plus the exact command; fake binaries untouched.
- Judge: weighted — `diagnosis` 0.4, `handoff` 0.4, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/wsl-automation`. Class B. No real run yet.

### workflow-path-audit/required-check-routing

- `workflow-path-audit` (registry: `acsi-agentskills`) — [evals/workflow-path-audit/required-check-routing](https://github.com/example-adopter/skills-evals/tree/main/evals/workflow-path-audit/required-check-routing) (the flat `evals/workflow-path-audit` fixture until 2026-10-26, when the skill's fixtures were nested; its earlier runs are keyed as the flat fixture)
- Audit a repo's Actions workflows so each triggers only on files it depends on. Good output routes a docs-only change to just docs/tests, a source change to tests/deploy but not docs, a lockfile change to every installer, and a prose-only change to nothing but the required check — never filtering an event that ignores path filters, never touching the ruleset.
- Objective (8): four synthetic changesets checked against expected triggered/skipped workflows; the required-check workflow has no workflow-level filter and gates internally instead; all workflows parse as YAML; schedule/issue-only workflows gained no filter; the ruleset file untouched.
- Judge: weighted — `completeness` 0.5, `salience` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. No class label (the repo's original pilot fixture, run weekly). 14 real runs, latest 2026-09-22T12:55:52Z.

### workflow-path-audit/companion-skip-pair

- `workflow-path-audit` (registry: `acsi-agentskills`) — [evals/workflow-path-audit/companion-skip-pair](https://github.com/example-adopter/skills-evals/tree/main/evals/workflow-path-audit/companion-skip-pair)
- Check a repo's path filters where the required `unit-tests` check lives in a path-filtered tests.yml beside tests-skip.yml, which reports the same check under the inverse `paths-ignore:`. Good output deletes the companion and gates tests.yml internally, explaining that a mixed changeset fires both.
- Objective (8): the required check's workflow has no workflow-level filter and gates on a computed output; the companion is gone; two changeset replays; all workflows parse; the schedule-only workflow gained no filter; the ruleset and every file outside `.github/workflows/` untouched.
- Judge: weighted — `correctness` 0.6, `explanation` 0.2, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. No in-place context (isolation pair). Offline `gh` from `harness/fakes/`. No real run yet.

### workflow-path-audit/moved-test-dir

- `workflow-path-audit` (registry: `acsi-agentskills`) — [evals/workflow-path-audit/moved-test-dir](https://github.com/example-adopter/skills-evals/tree/main/evals/workflow-path-audit/moved-test-dir)
- Tighten filters where e2e.yml still watches `e2e/**` after the specs moved to `tests/e2e/`, omits the server script its Playwright config starts, unit.yml omits its own file, and lint.yml watches all of `.github/`. Good output fixes all four from what each workflow's steps consume, without moving any file.
- Objective (10): seven changeset replays (moved spec, server script, own workflow file, sibling workflow file, unit test, prose, lockfile); all workflows parse; the schedule-only workflow gained no filter; nothing outside `.github/workflows/` changed and no `e2e/` came back.
- Judge: weighted — `salience` 0.6, `completeness` 0.2, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. No in-place context (isolation pair). Offline `gh` from `harness/fakes/`. No real run yet.

### workflow-path-audit/workflow-doc-in-step

- `workflow-path-audit` (registry: `acsi-agentskills`) — [evals/workflow-path-audit/workflow-doc-in-step](https://github.com/example-adopter/skills-evals/tree/main/evals/workflow-path-audit/workflow-doc-in-step)
- Filter two always-run workflows in a repo whose AGENTS.md table says `all paths` for both. Good output filters each on what it consumes and brings the existing table's rows in step, creating no second workflow doc.
- Objective (10): four changeset replays; line-anchored checks that the tests.yml row names `src/` and the docs-site.yml row names `docs/` with neither still saying `all paths`, and that the rest of AGENTS.md survives; all workflows parse; the schedule-only workflow gained no filter; nothing else changed and no new workflow doc appeared at the common names.
- Judge: weighted — `docs-in-step` 0.4, `salience` 0.4, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. No in-place context (isolation pair). Offline `gh` from `harness/fakes/`. No real run yet.

### writing-adrs/bootstrap

- `writing-adrs` (registry: `acsi-agentskills`) — [evals/writing-adrs/bootstrap](https://github.com/example-adopter/skills-evals/tree/main/evals/writing-adrs/bootstrap)
- Record a retry-policy decision as an ADR in a repo with no `docs/decisions/` yet. Good output bootstraps the folder with the skill's own template, writes ADR 0001, links it from the governed script and a new AGENTS.md pointer, and touches nothing else.
- Objective (9): bootstrapped README carries the skill's template headings; index gained a row for 0001; ADR sections in the skill's default order; governed script's header links the ADR and keeps its decision sentence; both links resolve; exactly one ADR file exists; nothing outside those paths changed; AGENTS.md gained the pointer.
- Judge: weighted — `decision-with-alternatives-and-consequences` 0.5, `title-is-a-decision-statement` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. In-place context: `example-adopter/example-site-platform`. Class A (format half — no existing convention). No real run yet.

### writing-adrs/existing-convention

- `writing-adrs` (registry: `acsi-agentskills`) — [evals/writing-adrs/existing-convention](https://github.com/example-adopter/skills-evals/tree/main/evals/writing-adrs/existing-convention)
- Same decision, but in a repo with three existing ADRs and its own house section order (Status/Context/Decision/Consequences), different from the skill's default. Good output follows the house convention, adds exactly one new ADR (0004), links it correctly, and leaves the rest untouched.
- Objective (7): new ADR follows house order, not the skill's default; index gained a row for 0004; governing script's header links it; both links resolve; exactly four ADR files exist in total; pre-existing ADRs, CHANGELOG.md, AGENTS.md, and root README.md all byte-identical to seed.
- Judge: weighted — `decision-with-alternatives-and-consequences` 0.5, `title-is-a-decision-statement` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. Class A (format half — matches an existing convention). No real run yet.

### writing-adrs/supersede

- `writing-adrs` (registry: `acsi-agentskills`) — [evals/writing-adrs/supersede](https://github.com/example-adopter/skills-evals/tree/main/evals/writing-adrs/supersede)
- Record a snapshot-retention decision that reverses an accepted ADR. Good output writes ADR 0004, marks 0002 as superseded, updates both index rows and the script's ADR pointer, and preserves the old decision as history.
- Objective (13): a new ADR with the required sections and a mention of 0002; 0002's status and index row point to 0004; the old decision text and unaffected rows stay intact; links resolve; a script comment points to 0004; the retention decision stays intact; exactly four ADR files exist; unrelated files stay byte-identical. These checks establish a format floor; the judge decides whether the prose explains the reversal.
- Judge: weighted — `replacement-explains-the-reversal` 0.5, `audit-trail-preserved` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. No class label in the fixture: its objective checks are a format floor, and the README files every `writing-adrs` fixtures under Class A (format half). No real run yet.

### writing-adrs/why-no-comment-trail

- `writing-adrs` (registry: `acsi-agentskills`) — [evals/writing-adrs/why-no-comment-trail](https://github.com/example-adopter/skills-evals/tree/main/evals/writing-adrs/why-no-comment-trail)
- The user asks why an export script ends every CSV line with CRLF, says nothing in the repo explains it, and relays the reason and two rejected alternatives from the person who wrote it. Good output records that as ADR 0003 in the repo's house format, adds its index row, points the script at it, and leaves the line endings and everything else alone.
- Objective (11): a new house-format ADR that is not a pasted seed ADR; non-heading lines name the line-ending rule and the importer, and each rejected alternative; index gained a row for 0003 and kept the other two; links resolve; a script comment points to 0003; both CRLF printf lines stay; exactly three ADR files exist; unrelated files stay byte-identical.
- Judge: weighted — `adr-records-the-reason` 0.5, `next-reader-finds-it` 0.3, `restraint` 0.2.
- No pinned models: the roster picks the agent and the judge. No class label in the fixture, filed with its siblings under Class A (format half). No real run yet.

## 6. What is finished

- The harness, scorers, roster, propagation audit and seventeen A/B fixtures (the eighteenth section in the inventory, `guidance/_delivery`, is the delivery canary) are
  merged on `main` with CI green.
- The roster redesign (PR #153) merged on 2026-09-21, the first real proposal
  run succeeded, and the census was published on 2026-09-22 so proposals are
  now usage-based.
- The test suite's guard against forking itself (issue #152, PR #163) merged
  after five review rounds; two clean-up follow-ups (PR #165) merged the same
  day.

## 7. What is left, what blocks it, and what it would cost

Costs are ranges. "Points" are percent of the weekly Claude Code allowance,
calibrated on 2026-09-21 when local sessions delivered the #152 merge (five
review rounds and three fix rounds) plus PR #165 for about six points;
earlier orchestrator-driven cloud sessions ran three to five times costlier
per round. Dollars are organisation API spend only; rows below that say "at
one arm" predate the two-arm roster merged on 2026-09-22 as
[PR #172](https://github.com/Adam-S-Daniel/skills-evals/pull/172).

### Blocking decisions (the owner's)

1. **The roster proposal, [#162](https://github.com/Adam-S-Daniel/skills-evals/issues/162): decided, and the approved proposal is now superseded.**
   Usage-based since 2026-09-22: Sonnet 5 (45.4% of rankable usage) and
   Opus 5 (45.2%) seated by share; Haiku 4.5 and Fable 5.1 seated by the
   newest-per-tier rule at 6.3% and 3.0%; judge Fable 5. **The owner approved
   that four-arm proposal on 2026-09-22 and then, the same day, narrowed
   the rule that produced it**: the newest model in a tier is seated only
   where that tier already has a model over the usage bar (§ 2). Haiku 4.5
   and Fable 5.1 lose their seats under that rule, so **the approved
   four-arm proposal is superseded and the next run proposes two arms** —
   Sonnet 5 and Opus 5, both on their own shares. Do not open a pull
   request from the old `roster/proposal` commit (`c4a8bea`): it renders
   the four-arm roster. **Settled on 2026-09-22**: the recomputed proposal
   merged as [PR #172](https://github.com/Adam-S-Daniel/skills-evals/pull/172)
   (`b1bb221`) and #162 is closed. A fixture run therefore costs roughly
   $5–7 (two arms, a judge at twice Opus prices) rather than about $1.7 on
   the one-arm roster or the $10–14 four arms would have cost, and the
   weekly run has stopped re-proposing. No allowance cost.
2. **Whether to reopen the fixture lanes** ([#62](https://github.com/Adam-S-Daniel/skills-evals/issues/62),
   [#96](https://github.com/Adam-S-Daniel/skills-evals/issues/96)), stopped by
   decision 8 on 2026-09-08 ("stop adding and completing evals for specific
   skills"). Each new fixture was about 5 review rounds; at today's local
   rates 3–5 points each, 21 skills and 21 guidance sections outstanding.
3. **The guidance receipt, [_agent-guidance PR #124](https://github.com/Adam-S-Daniel/_agent-guidance/pull/124)**,
   parked with its fix budget spent and two reproduced defects. It holds
   [#139](https://github.com/Adam-S-Daniel/skills-evals/issues/139). Options:
   authorise one more fix round (2–3 points), redesign, or close.
4. **The Batch API step below: decided no-go for now (2026-09-22).** the owner
   chose not to move the judge calls to the Batch API yet. The judge is
   the only batchable call and the saving is about half of judge spend,
   which at the two-arm roster is a dollar or two per run against a 3–6
   point build plus two reviews on a one-way-door workflow. **Revisit
   after a few weeks** (around 2026-10-13), once the two-arm roster with the
   Fable 5.1 judge has produced several weekly runs, by re-projecting the
   saving from the org API usage export against the build cost; tracked in
   [#170](https://github.com/Adam-S-Daniel/skills-evals/issues/170).
5. **Paying for runs out of the weekly allowance instead of the API
   organisation: decided no-go (2026-09-23), recorded as ADR
   [0002](decisions/0002-runs-bill-the-api-org-not-the-subscription.md).**
   Anthropic does permit a subscription credential in CI — `claude
   setup-token` mints a one-year `CLAUDE_CODE_OAUTH_TOKEN` documented for
   exactly that — but the arms run registry content under
   `bypassPermissions` with every `ANTHROPIC_*`/`CLAUDE_*` variable in their
   environment, so that credential must never sit there; today's WIF bearer
   lasts an hour, is workspace-scoped and is spend-capped. Judge-only is
   permitted and buildable, and saves about $6–13 a month at the two-arm
   roster for a 3–6 point build while blinding the dollar accounting, so it
   is folded into the #170 revisit as a third column. Credentials read out of
   a browser session, copied from `~/.claude/.credentials.json`, or obtained
   by automating the OAuth consent screen are prohibited by Anthropic's terms
   and are not to be built. Local runs under the account holder's own
   `/login` stay available for ad-hoc bulk work, as a local exhibit rather
   than badge input.

### Planned steps, in order

| # | Step | Allowance | Org API |
|---|---|---|---|
| 1 | **Move the judge calls to the Batch API from `eval.yml`.** DEFERRED on 2026-09-22 (blocking decision 4 above); revisit around 2026-10-13. Only the judge is batchable: each arm is an agentic loop where every turn depends on the previous tool result, and the Batch API takes independent single requests. The judge today is one `claude -p` call per arm; it would become one `messages.batches.create` with two requests, polled to completion inside the job. `eval.yml` is a one-way door, so it takes a worker, a code review and an adversarial review. | 3–6 points | about $5 of verification runs; then saves ~50% of judge spend, about $0.40 per run today and $1.50–2 per run on the proposed roster |
| 2 | Owed real runs (N=3) for the merged fixtures whose issues stay open for them: #82, #86, #85, #77, #74, #80, #84, #81. Dispatch and record only. **Held by decision 8 until the owner reopens.** | 1–2 points | about $40 at one arm, $250 on the proposed roster |
| 3 | [#166](https://github.com/Adam-S-Daniel/skills-evals/issues/166) suite-fork guard nits (test-only). | 1–2 points | none |
| 4 | [#139](https://github.com/Adam-S-Daniel/skills-evals/issues/139) receipts per arm. Blocked by decision 3 above. | 4–8 points | none |
| 5 | [#66](https://github.com/Adam-S-Daniel/skills-evals/issues/66) several fixtures per skill, N trials per arm, aggregated statistics. | 4–8 points | none to build; multiplies run cost by N |
| 6 | [#64](https://github.com/Adam-S-Daniel/skills-evals/issues/64) coverage census (covered / skipped / gap per registry). | 2–4 points | none |
| 7 | [#68](https://github.com/Adam-S-Daniel/skills-evals/issues/68) matrix runner: every covered skill × every roster model, budgeted, with an index; [#99](https://github.com/Adam-S-Daniel/skills-evals/issues/99) the guidance subject in it. | 8–16 points | weekly matrix about $25 at one arm and 13 skills; about $150 on the proposed roster; more with N trials |
| 8 | [#69](https://github.com/Adam-S-Daniel/skills-evals/issues/69), [#121](https://github.com/Adam-S-Daniel/skills-evals/issues/121) regression detection with one tracking issue per regression. | 6–12 points | none |
| 9 | [#70](https://github.com/Adam-S-Daniel/skills-evals/issues/70) gates that read the numbers; [adam-agentskills#66](https://github.com/Adam-S-Daniel/adam-agentskills/issues/66) the registry CI gate. | 4–7 points | none |
| 10 | [#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71), [#122](https://github.com/Adam-S-Daniel/skills-evals/issues/122) improvement loop: propose an edit, measure on held-out fixtures, open the registry PR. | 12–24 points | $10–50 per proposal measured |
| 11 | [#65](https://github.com/Adam-S-Daniel/skills-evals/issues/65), [#98](https://github.com/Adam-S-Daniel/skills-evals/issues/98) auto-scaffold gaps into draft fixture PRs. Depends on decision 2. | 6–12 points | none |
| 12 | Explorer pages [#72](https://github.com/Adam-S-Daniel/skills-evals/issues/72), [#123](https://github.com/Adam-S-Daniel/skills-evals/issues/123), published via [adamdaniel.ai#3536](https://github.com/Adam-S-Daniel/adamdaniel.ai/issues/3536) and [#3538](https://github.com/Adam-S-Daniel/adamdaniel.ai/issues/3538). | 8–16 points | none |
| 13 | Docs and ADRs [#73](https://github.com/Adam-S-Daniel/skills-evals/issues/73), [#125](https://github.com/Adam-S-Daniel/skills-evals/issues/125). | 2–6 points | none |

Everything not held: roughly **60–120 points**, three to six weekly
allowances at today's local rates, spread over the order above. The
standing weekly run was about $1.70 on the one-arm roster and is $5–7 on
the two-arm roster merged on 2026-09-22, before any matrix or trials
multiplier. **The dollar figures in the table above were priced at the
four-arm roster approved on 2026-09-22**; treat every "on the proposed
roster" figure there as an upper bound worth roughly halving, since the
narrowed rule in § 2 proposes two arms rather than four.

### Standing holds

Decision 8 (no new skill-specific evals) and the guidance receipt park are in
force until the owner lifts them. Real runs bill the API workspace, never the
allowance; agent work bills the allowance, never the workspace.
