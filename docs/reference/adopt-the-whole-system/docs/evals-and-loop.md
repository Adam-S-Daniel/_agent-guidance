# Evals and the loop

## What skills-evals holds

[`skills-evals`](../starter/skills-evals/README.md) measures skills and
guidance sections and acts on the numbers:

- `harness/` runs an eval: `run_eval.py` loads a fixture, runs the agent
  with and without the subject (the skill, or a guidance section), scores
  each arm with scripted checks (`scorers/objective.py`) and a judge model
  (`scorers/judge.py`), and writes a summary.
- `evals/` holds the fixtures and the policy files: `budget.yml` (the
  allowance cap), `scheduled.yml` (which fixtures run on the weekly plan),
  `roster.yml` (which models), `gate-policy.yml` (retirement),
  `improve-policy.yml` (whether accepted improvements merge on their own).
- `scripts/` holds the planner (`plan_week.py`), the allowance guard, the
  gates, ingest, and the improve, draft, scaffold and retire lanes.
- `.github/workflows/` holds the workflows that fire the eval runner routine
  and turn what it pushes into pull requests.
- `dashboard/` is the optional project dashboard.

The deepest account is its
[`docs/how-it-works.md`](../starter/skills-evals/docs/how-it-works.md);
[`DESIGN.md`](../starter/skills-evals/DESIGN.md) has the method.

## Fixtures

A fixture is a directory with a `fixture.yaml` (the prompt, the arms, the
objective checks, the judge rubric) and a `seed/` the agent starts from. The
kit ships the fixtures that test the machinery: `evals/guidance/_delivery`
(guidance delivery), `evals/guidance-bridge-canary`, `evals/propagation`,
the orchestrator scenarios, and test-only fixtures under `test/fixtures/`;
and the fixtures of the core skills and guidance sections (`evals/<skill>/`,
`evals/guidance/<section>/`). `evals/scheduled.yml` starts with a row for
each core item's fixture, so the weekly plan has something to run;
`bootstrap/add-content.sh` adds each optional item's fixtures and rows with
it ([choosing optional content](skills.md#choosing-optional-content)). Add
the rest as you write them: write a fixture for your
next skill under `evals/<skill>/<name>/`, run it locally
(`python3 harness/run_eval.py evals/<skill>/<name> --arm both --registry ../acsi-agentskills`, see the
[README's "Running"](../starter/skills-evals/README.md#running)), and add it
to `scheduled.yml` in a pull request. An improve fire needs four fixtures
for its skill.

The loop also writes fixtures: the daily fixture supply mines your merged
pull requests into real-work fixtures (the tree before the fix, the pull
request's own tests as a hidden checker, the closing issue as the prompt),
and a draft is promoted onto the schedule after two clean ingested runs.

## The eval runner and its lanes

Evals run inside a Claude routine on your plan, not on API dollars
([ADR 0010](../starter/skills-evals/docs/decisions/0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md)).
`routine-eval-fire.yml` is the only workflow that fires it: it validates
the dispatch, runs the allowance guard, and POSTs to the routine's API
trigger with the bearer and trigger id from the `routine-fire` environment
([routines](routines.md#the-eval-runner)). The routine does the AI work in
its own sandbox and pushes the result to a branch of `skills-evals`; one
no-permission workflow per lane listens for it, and a gate workflow that
runs only the default branch's copy validates the branch without running
it and acts:

| Lane | Branch | The gate |
|---|---|---|
| eval results | `claude/eval-*` (files under `eval-results/`) | `routine-eval-ingest.yml` writes `routine-results/<run id>/` to `persistent/eval-results` |
| improve | `claude/eval-improve-*` (`eval-improve/`) | `routine-improve-gate.yml` opens a pull request in `acsi-agentskills` from `eval-improve/<skill>` with the `eval-improve` App |
| guidance improve | `claude/eval-guidance-improve-*` (`eval-guidance-improve/`) | `routine-guidance-improve-gate.yml` opens one in `_agent-guidance` |
| scaffold | `claude/scaffold-*` | `routine-scaffold-gate.yml` opens a fixtures pull request here with the `roster` App |
| draft skill | `claude/draft-skill-*` (`draft-skill/`, `evals/`) | `routine-draft-skill-gate.yml` opens a skill pull request in `acsi-agentskills` and a fixtures one here, or a hook-proposal issue |

An improve branch also matches `claude/eval-*`; the two lanes' path filters
are disjoint, so only one fires. `skills-evals`' `AGENTS.md` has the full
workflow table.

## Workload identity federation

Optional. `eval.yml`, the dispatch-only workflow that runs one fixture on
GitHub's runners instead of in the routine, pays with API credit: each leg
trades the runner's GitHub OIDC token for a short-lived Anthropic access
token (an RFC 7523 jwt-bearer exchange), so no API key is stored anywhere.
Skip this section if you run evals only through the routine.

1. In the Anthropic Console, create a workspace for these runs and give it
   a spend limit: that limit, not the workflow, is the hard ceiling.
2. Create a service account with access to that workspace.
3. Create a workload identity federation rule for that service account
   that trusts GitHub's issuer, `https://token.actions.githubusercontent.com`,
   with audience `https://api.anthropic.com` and subject
   `repo:<you>/skills-evals:ref:refs/heads/main`, so only the default
   branch gets a token. The Console's own federation docs have the current
   screens.
4. Put the four ids in `fleet.env`: `ANTHROPIC_FEDERATION_RULE_ID`
   (`fdrl_...`), `ANTHROPIC_ORGANIZATION_ID` (a uuid),
   `ANTHROPIC_SERVICE_ACCOUNT_ID` (`svac_...`) and `ANTHROPIC_WORKSPACE_ID`
   (`wrkspc_...`). They are ids, not secrets, but they name your account,
   so they live in repository variables rather than in the workflow file.
5. Run `bootstrap/create-environments.sh`: it sets them as `skills-evals`
   repository variables. `bootstrap/verify.sh --only federation` checks them.

Left unset, nothing fails on a schedule: the weekly `roster-refresh.yml`
dispatch (`roster_only`) skips the exchange with a notice and publishes no
roster. An eval leg does need the token, so a dispatched eval fails naming
the first unset variable. With any of the four set, the workflow checks
each value's shape before the exchange and fails with the variable's name
when one is missing or malformed. A failed exchange logs its HTTP status
and the error type only, never the response body.

## The budget

`evals/budget.yml` caps all automated evals at 30% of the weekly allowance,
one run at 0.03 plus a 0.005 overrun, and stops a run when the account
meter reads 80%. Three layers enforce it: the in-run stop in the harness,
the pre-fire check (`scripts/allowance_guard.py`, which refuses a fire that
would not fit and refuses when the GitHub API cannot be read), and the
weekly plan, which spends only the room left.
[What it costs](before-you-start.md#what-it-costs) turns these into calendar
time.

Expect improve fires to be refused at first. The guard reserves an improve fire
at 12 times the average cost of an ingested run, and on day one, with no run
ingested, each run counts at the 3.5-point cap (0.03 plus the 0.005 overrun),
so that reserve is 42 allowance points against the 30-point weekly cap. It
clears once the ingested runs average at most 2.5 points, which takes a few
weekly eval runs; until then a refused improve fire is expected, not a fault.

## The loop

- **The weekly plan** (`routine-eval-weekly.yml`, Tuesdays 07:00 UTC):
  `plan_week.py` computes the retirement verdicts, then plans eval fires
  and improve fires by band (missing evidence first, then cells near a
  decision, then improvements ranked by expected value per allowance point,
  then re-checks) within the room left, and dispatches them one at a time.
- **Improve:** an improve fire runs the skill-creator loop on one skill
  against a fixed holdout; an accepted candidate becomes a pull request.
  `improve-policy.yml`'s `improve_merge_mode: auto` makes it ready with
  auto-merge; `draft` leaves it for you.
- **Retire:** every day `skill-coverage.yml` computes each skill's verdict;
  a skill with no benefit on every fixture and model at two fixed looks
  gets a removal pull request (`retirement_mode: auto`), or an issue. The
  core items listed in `evals/retirement-exempt.yml` are never retired: their
  verdict reads `retire-exempt` and nothing is opened.
- **New skills:** `routine-draft-skill.yml` picks one new-skill lesson a day
  from `acsi-agentskills-private`'s `persistent/lessons`, hooks first.
- **New fixtures:** `routine-fixture-supply.yml`, daily.

These pull requests merge by auto-merge once required checks pass, with no
human review, within the limits `acsi-agentskills`'
[ADR 0021](../starter/agent-skills/docs/decisions/0021-automated-skill-changes-merge-without-review.md)
writes down. Set `improve_merge_mode: draft` and `retirement_mode: proposal` (in
`evals/gate-policy.yml`) if you want to review each one first.

## Where results land

`persistent/eval-results` in `skills-evals` holds every ingested run, the
verdicts and the coverage census; it is never deleted (a deletion-only
ruleset guards it). The weekly plan's ranking is the run's
`improve-ranking` artifact.
