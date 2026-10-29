# ADR 0010: Run the AI steps of skill and guidance evals in a routine fired by Actions, and measure agent effectiveness on real work

- **Status:** accepted (2026-10-06), on the owner's decisions of the same day
  (see [Decisions (2026-10-06)](#decisions-2026-10-06)). Nothing is built
  yet. Supersedes [ADR 0002](0002-runs-bill-the-api-org-not-the-subscription.md)
  decision 1 **for routine runs only**.
- **Issues:** [#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71)
  (the improvement loop),
  [#122](https://github.com/Adam-S-Daniel/skills-evals/issues/122) (the same
  loop for guidance sections).
- **Decider:** the owner (2026-10-06).

## Context

The owner, 2026-10-06, on the note that guidance evals were "blocked on the paid
CI path":

> "“blocked on the paid CI path for guidance evals”: use Claude routines to
> avoid having to pay API cost"

> "The skills improvement loop should not only use skill-creator. Both skills
> and guidance should be evaluated for how they improve agents’ efficiency and
> effectiveness at doing the work I want them to do. Can the AI steps in occur
> via GitHub actions calling claude routine API triggers?"

**What routines offer** ([Routines](https://code.claude.com/docs/en/routines),
[Trigger a routine via API](https://platform.claude.com/docs/en/api/claude-code/routines-fire),
read 2026-10-06; research preview; requests require
`anthropic-version: 2023-06-01`; the routines page says the endpoint ships
under beta header `experimental-cc-routine-2026-04-01`, which the fire page
says is now optional):

- Triggers: schedule, API, GitHub events. The API trigger is
  `POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire`
  with a per-routine bearer generated only in the web UI, scoped to firing
  that one routine, shown once, regenerable and revocable.
- An optional free-text `text` (at most 65,536 characters) is delivered
  wrapped in a `<routine-fire-payload>` block labeled untrusted; the
  routine's saved prompt must opt in to acting on it. JSON arrives as a
  literal string.
- The response carries `claude_code_session_id` and
  `claude_code_session_url`. There is no polling or status API; a green run
  status means only that the session exited without an infrastructure error.
- Runs "draw down subscription usage the same way interactive sessions do",
  not API billing (metered overage only if usage credits are on). Hourly
  limits: 30 fires per routine (shared with Run now), 100 API fires per
  account, 100 scheduled runs per account.
- Runs push `claude/`-prefixed branches unless the prompt directs otherwise,
  can open PRs, and act as the owner's GitHub identity. No permission-mode
  picker: the session runs shell commands without approval.
- Nested `claude -p` inside a cloud session works: _agent-guidance's
  prompt-audit sweep runs a nested `/doctor prompt-audit` and logs its auth
  probe as `firstParty oauth_token` with "billing basis unverified"
  ([`docs/reference/prompt-audit-runs.md`:24-26](https://github.com/Adam-S-Daniel/_agent-guidance/blob/35f62ec4d9ab7fd8b9d9fe2e3a547c7d5926c3f9/docs/reference/prompt-audit-runs.md#L24-L26),
  [`docs/routines/prompt-audit-sweep.md`:261-268](https://github.com/Adam-S-Daniel/_agent-guidance/blob/35f62ec4d9ab7fd8b9d9fe2e3a547c7d5926c3f9/docs/routines/prompt-audit-sweep.md#L261-L268)).
  How a nested run authenticates inside a routine is not documented.

**What this repo does today, and what blocks the routine path:**

- [ADR 0002](0002-runs-bill-the-api-org-not-the-subscription.md) decision 1
  (its "Decision" section, item 1): arms never run on a subscription
  credential in CI, because a `bypassPermissions` arm running registry
  content must not reach one. Its "Alternatives considered" table (the
  cloud-session-or-routine row) records a routine fired from `eval.yml` as
  permitted but outside the `main`-pinned WIF trust model; decision 4
  (the same section, item 4) makes locally produced results "a local
  exhibit, not badge input".
- `scripts/local_eval_guard.py:23` refuses `CLAUDE_CODE_OAUTH_TOKEN` and
  `CLAUDE_CONFIG_DIR` in the environment; `harness/guidance.py:780-812`
  (`agent_env`) builds an arm's environment from an allowlist (lines
  150-155) that passes `ANTHROPIC_*` and drops every ambient `CLAUDE_*`.
- [ADR 0005](0005-improvement-loop-reuses-skill-creator.md) measures the
  loop with this harness but proposes through skill-creator's description
  loop; its serial trigger-eval addendum (line 306) merged in
  [PR #296](https://github.com/Adam-S-Daniel/skills-evals/pull/296).
- The only guidance fixture, `evals/guidance/_delivery/fixture.yaml`, is a
  delivery canary, "not a behavioral A/B" (lines 1-5). Only `writing-adrs`
  (4 fixtures) and `my-writing-style` (3) have the three fixtures a
  train/held-out split needs.
- #71 item 5: the improvement workflow is not scheduled "until three
  human-reviewed PRs from it have merged".

## Decision

The [weekly routine caller](../../.github/workflows/routine-eval-weekly.yml)
implements the scheduled eval part of [#65](https://github.com/Adam-S-Daniel/skills-evals/issues/65).
It uses [ADR 0008's reviewed list](../../evals/scheduled.yml), refuses a plan
above 30 fires, and dispatches one eval-mode run per fixture through the
[existing fire workflow](../../.github/workflows/routine-eval-fire.yml).
The owner's 2026-10-06 decision, following the [weekly routine PR merge](https://github.com/Adam-S-Daniel/skills-evals/pull/331),
retired the paid [`eval.yml`](../../.github/workflows/eval.yml) Tuesday
schedule; its one-fixture manual dispatch remains. Routine evals use
subscription usage, and Actions separately ingests their results as local
exhibits. The routine path has no API-workflow OIDC exchanges, roster jobs,
badge publishing, or API spending ceiling, and routine results are not badge
input.
The [#71 gate](https://github.com/Adam-S-Daniel/skills-evals/issues/71)
applies to scheduled improvement runs, not these eval runs. Thirty is a
per-run bound, while manual fires and other scheduled runs share the hourly
limit; a rate-limited dispatch fails without retry.

1. **GitHub Actions owns scheduling, bookkeeping and ingestion; a routine
   owns the AI steps.** A workflow builds a JSON task spec (run id, roster
   ref, fixture list, arms, trial count, base sha) and fires one routine via
   the API trigger with the spec as `text`. The routine's saved prompt
   checks out the named sha, runs the AI steps (arms, judge and, for the
   loop, the proposer; see open decision 1), and pushes results to `claude/eval-<run id>`. It
   never writes `persistent/eval-results` or `main`.
2. **Results are untrusted input to Actions.** A workflow on push to
   `claude/eval-*` validates the result files against a schema (run id
   matches a fire it recorded, paths confined, sizes bounded, no executable
   content), then ingests them into `persistent/eval-results`. A missing
   push within a deadline marks the run failed; there is no polling API to
   ask instead.
3. **Measure agent effectiveness on real work, not only triggering.**
   Fixtures are drawn from what the owner's agents actually do (writer, reviewer
   and CI-debug tasks from his sprints), scored on task success plus
   efficiency: turns, usage (tokens), wall time and review rounds. The same
   A/B harness runs skills (with/without the skill) and guidance
   (with/without the section). skill-creator stays one proposer among
   others, not the loop's definition.

## Open decisions for the owner (as proposed)

Kept as they were put to the owner; each is answered under
[Decisions (2026-10-06)](#decisions-2026-10-06).

1. **ADR 0002 decision 1.** A routine's nested arms run on the account's
   subscription credential, which decision 1 forbids for arms. Options:
   supersede it for routine runs; or run arms as the routine's own
   subagents instead of nested `claude -p` (no extra credential in reach,
   but weaker isolation than `agent_env` and a different measurement);
   or keep arms on the API path and move only the judge and proposer.
2. **Usage-terms comfort at scale.** Routines are a documented automation
   surface, but an eval matrix is many runs per week on one personal
   subscription; ADR 0002 read the Consumer Terms for this.
3. **Badge input or local exhibit.** Do routine results feed the badge, or
   stay an exhibit as ADR 0002 decision 4 treats non-`main`-pinned runs?
4. **#71's three-PR gate.** Keep it before any scheduled loop, or replace
   it for routine-fired runs.
5. **Where the fire bearer lives.** A repo or environment secret; its name
   must avoid the gitleaks keyword list (`key`, `token`, `secret`, `auth`
   and the rest) wherever it is serialized beside a value in a generated
   file.

## Decisions (2026-10-06)

The owner answered the five open decisions on 2026-10-06. Three he picked
directly from the options offered:

- Badge or exhibit: "Local exhibit for now (Recommended)".
- #71's gate: "Keep it (Recommended)".
- Usage terms at scale: "Yes, modest scale".

He then asked "What were the motivations behind ADR 0002 decision-1, and are
they applicable in the new Routines setup?" and, on the answer, said "Let’s
go with your recommendations". Those recommendations, now decided:

1. **ADR 0002 decision 1 is superseded for routine runs only.** Its reason
   was that a `bypassPermissions` arm running registry content must not be
   able to read a long-lived subscription credential (in `eval.yml` that
   would be a one-year `setup-token` value in the arm's environment; none
   was ever stored there). A routine run
   holds no such value where an arm can read it (Probe 1 below), so arms may
   run as nested `claude -p` inside a routine, on two conditions:
   - **The condition holds.** The arm cannot read a long-lived subscription
     credential. Probe 1 met it for the environment and the CLI's credential
     file; the session's other token-bearing files are an open risk (below).
     Proposed gate, the author's and pending the owner: the risk must be closed
     before the first scheduled run.
   - **Arms run only trusted content:** the default branches of the
     `agentskills`, `example-site-platform` and `example-site-a` skill registries,
     plus `_agent-guidance` as the guidance source. No pull-request, fork or
     third-party content runs in a routine arm.

   For every other path (`eval.yml` and any Actions runner, a workstation,
   any future runner) ADR 0002 decision 1 holds unchanged.
2. **Usage terms: yes, at modest scale.** Routine-fired evals run on the
   subscription at a modest scale; how many fires a week that means is not
   yet set (open question below).
3. **Local exhibit, not badge input.** Routine results stay a local exhibit,
   as ADR 0002 decision 4 treats every non-`main`-pinned run. They do not
   feed the badge.
4. **#71's three-PR gate stays.** No scheduled improvement loop, routine or
   otherwise, until three human-reviewed PRs from the loop have merged.
   *Lifted on 2026-10-26: see the
   [decision 4 addendum](#decision-4-addendum-2026-10-26).*
5. **The fire bearer is a repository secret** whose name contains none of
   gitleaks' `generic-api-key` keywords (`access`, `auth`, `api`,
   `credential`, `creds`, `key`, `passwd`, `password`, `secret`, `token`).
   Proposed name: `EVAL_ROUTINE_FIRE_BEARER`. The dispatch-workflow PR
   adopts it or records why not.

### Facts this rests on

- Fire API: `POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire`
  with `anthropic-version: 2023-06-01`; the per-routine bearer is generated
  only in the web UI.
- The fire `text` arrives wrapped as untrusted; the saved prompt must opt in
  to acting on it, so the task spec is validated, never obeyed blindly.
- Runs act as the owner's GitHub identity and push only `claude/`-prefixed
  branches by default, so results go to `claude/eval-<run id>` and the
  Actions ingest workflow ([Decision](#decision) 2) validates them and moves them
  to `persistent/eval-results`.

## Probe results

### Probe 1: what a routine session can authenticate with (met)

Routine [`trig_EXAMPLE`](https://claude.ai/code/routines/trig_EXAMPLE),
run [`session_EXAMPLE`](https://claude.ai/code/session_EXAMPLE),
2026-10-06, 40 seconds. Variable and file names only; no value was recorded.

- `claude --version`: 2.1.291. The auth probe reported `oauth_token`,
  `firstParty`.
- **No credential in reach:** neither `CLAUDE_CODE_OAUTH_TOKEN` nor
  `ANTHROPIC_API_KEY` was set, and there was no `~/.claude/.credentials.json`.
- A nested `claude -p` launched under `env -i` with a fresh `HOME` and
  `CLAUDE_CONFIG_DIR` and no credential at all **still authenticated** (it
  answered "ok"). Auth is supplied by the sandbox, most likely its egress
  proxy, not by anything the arm holds.
- `pip download markdown-it-py` and PyYAML both worked, so the harness's
  dependencies install.
- **Present in the session:** `CLAUDE_SESSION_INGRESS_TOKEN_FILE` (the file
  it names exists), `CLAUDE_CODE_MESSAGING_TOKEN`, `GH_TOKEN`,
  `GITHUB_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN` and `ANTHROPIC_BASE_URL`.
  `agent_env` (`harness/guidance.py`) builds an arm's environment from an
  allowlist, so none of these variables reaches an arm except
  `ANTHROPIC_BASE_URL`, which its `ANTHROPIC_*` passthrough keeps (and which
  the nested run plausibly needs).

**Open risk (proposed gate, pending the owner: verify before the first scheduled
run):** `agent_env` strips
variables, not files. A `bypassPermissions` arm can read any path the session
user can, so the file `CLAUDE_SESSION_INGRESS_TOKEN_FILE` names, and any
on-disk copy of the GitHub tokens, may be readable from an arm. Whether it is,
and what that token can do, is not yet known.

### Probe 2: the real harness inside a routine (blocked)

Routine [`trig_EXAMPLE`](https://claude.ai/code/routines/trig_EXAMPLE),
run session `cse_EXAMPLE` (no link recorded), 2026-10-06
14:06Z, 106 seconds. The real harness ran the guidance `_delivery` fixture
(all five arms, N=1, judge off) and the `writing-adrs` bootstrap fixture (with
and without the skill). No repo edit and no workaround was made.

- **Setup works, with one wrinkle.** The pins are `eval.yml`'s
  (`pyyaml==6.0.3 markdown-it-py==4.2.0 tree-sitter==0.26.0
  tree-sitter-bash==0.25.1`). Plain `pip` belongs to `/usr/bin/python3`
  while the harness runs `/usr/local/bin/python3`, so they install with
  `python3 -m pip install --user`.
- **Guidance `_delivery`: exit 2 after 16 seconds.** Every arm (5 of 5)
  errored `nonzero_exit` with "--dangerously-skip-permissions cannot be used
  with root/sudo privileges for security reasons". The steps before the
  agent worked on every arm: the hook installed, the delivery guard reported
  `ok` true and `contaminated` false.
- **`writing-adrs` bootstrap: exit 2 after 1 second**, the same error.
- **Cause:** the routine session runs as uid 0. `IS_SANDBOX` is set in the
  routine's shell, but `agent_env` does not pass it to the arm, so the CLI
  refuses `bypassPermissions` as root.

**Consequence:** the routine path needs a harness change before any real
run. Two options were put to the owner:

- run the arms as a non-root user inside the routine; or
- pass `IS_SANDBOX` through `agent_env`, for routine runs only.

The owner answered on 2026-10-06:

> "Should the harness perhaps use auto approval anyway? It is how I run
> virtually everything anyhow, so arguably only adds to the eval runs’
> validity"

**Resulting direction:** arms run with `--permission-mode auto` instead of
`bypassPermissions` (the routine's CLI, 2.1.291, lists `auto` among the
`--permission-mode` choices). The harness passes
`--permission-mode bypassPermissions` (`harness/run_eval.py:1041`) and still
got the message naming `--dangerously-skip-permissions`, so the CLI refuses
bypass mode as root, whichever flag selects it. Auto mode is expected to be
exempt, and the arm then works the way the owner's own sessions do. **Not yet
shown:** that auto mode runs as root in a routine. A re-run of Probe 2 after the harness change
confirms it (pending). The two options above become fallbacks, used only if
auto mode fails as root.

Consequences of auto mode:

- Permission-classifier denials become part of the measured behavior: an
  arm that gets denied and recovers, or stalls, scores accordingly.
- Results must record the permission mode an arm ran under, so runs under
  different modes are never compared as like-for-like.

The eval runner routine now exists:
[`trig_EXAMPLE`](https://claude.ai/code/routines/trig_EXAMPLE),
fired by API only, with no schedule. The owner creates its fire bearer in the web
UI.

### Probe 3: the loop's launch guard in a routine session (passed)

Routine run [`session_EXAMPLE`](https://claude.ai/code/session_EXAMPLE),
2026-10-06 22:20Z. Variable names only; no value was recorded.

- `local_eval_guard.refused_env_names(os.environ)` returned ten names:
  `ANTHROPIC_BASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_CA_BUNDLE`,
  `AWS_SECRET_ACCESS_KEY`, `CLAUDE_CODE_USE_CCR_V2`,
  `CLOUDSDK_AUTH_ACCESS_TOKEN`, `CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE`,
  `CLOUDSDK_PROXY_ADDRESS`, `CLOUDSDK_PROXY_PORT` and `CLOUDSDK_PROXY_TYPE`.
  So `scripts/propose_skill_edit.py` refuses to start in a routine as it
  stands.
- A nested `claude -p ... --permission-mode auto` run through `env -u` for
  all ten exited 0 and authenticated, and the guard then returned none.
- The owner chose "Probe unset first (Recommended)": the guard is not changed.
  The improvement loop's routine mode launches the loop under an `env -u`
  prefix built at run time from `refused_env_names(os.environ)` (ADR 0005,
  "Routine improve mode addendum").

### Open questions

- Whether an arm can read the ingress token file or an on-disk GitHub token,
  and what each grants (the open risk above).
- Whether arms under `--permission-mode auto` run as root in a routine
  (Probe 2 re-run, pending after the harness change). If not, the fallback
  is a non-root user or `IS_SANDBOX` passed for routine runs only, and that
  choice is still open.
- What "modest scale" is in fires per week.
- Whether routine results agree with API-path results on a paired run (see
  Consequences); until then they are not compared.

## Consequences

- Guidance and loop runs stop competing for API dollars; they compete for
  subscription usage and the hourly fire caps instead.
- The trust model splits: WIF runs remain `main`-pinned; routine runs are
  attested only by the validator, so their numbers are not comparable to
  API-path numbers until a paired run shows they agree.
- The research-preview API can change shape; the firing step sends
  `anthropic-version: 2023-06-01` (and the beta header) and fails closed on an unknown response.
- Real-work fixtures cost more to write and to score than trigger queries,
  and need objective checks first, per DESIGN.md's rule that decidable
  facts never go to a judge.

## Smallest next PRs

1. **Probe, no code change** (done 2026-10-06: Probe 1 met; Probe 2
   blocked on running arms as root): a routine created in the web UI, fired once by
   hand with a toy spec, records `claude --version`, the nested auth probe
   and whether a nested `claude -p` under an `agent_env`-shaped environment
   runs, then pushes `claude/eval-probe-<id>`.
2. A dispatch-only workflow that fires the routine and records the session
   URL, behind the decisions above.
3. The `claude/eval-*` validator and ingester, tested with fakes
   (`scripts/ingest_routine_results.py`, run by
   `.github/workflows/routine-eval-ingest.yml` on the `workflow_run` of
   `routine-eval-results-pushed.yml`; results land under
   `routine-results/<run id>/` on `persistent/eval-results`, apart from
   the badge's `results/`). Not yet built: the fire record decision 2
   matches a run id against, and the missing-push deadline.
4. Two real-work fixtures (one writer, one CI-debug) with objective checks.
5. The improvement loop's routine mode: the routine pushes an accepted
   candidate to `claude/eval-improve-<run id>`, and
   `.github/workflows/routine-improve-gate.yml` validates it and opens a
   draft pull request in agentskills with a GitHub App token, once
   the App and its two secrets exist
   ([ADR 0005](0005-improvement-loop-reuses-skill-creator.md), "Routine
   improve mode addendum"). `routine-eval-fire.yml` fires it with
   `mode: improve`, a `skill` and an optional `holdout`; the routine's
   saved prompt is not yet changed.

## Alternatives considered

- **Stay on the API path** (ADR 0002): keeps the trust model, keeps the
  cost that blocks guidance evals.
- **Routine pushes straight to `persistent/eval-results`:** removes the
  validation step that treats model-written output as untrusted.
- **`setup-token` in `eval.yml`:** rejected by ADR 0002's table; a routine
  at least keeps the credential out of the Actions runner.

## Decision 4 addendum (2026-10-26)

The owner lifted #71's three-PR gate on 2026-10-26, with no human in the loop.
His goal:

> "getting the closed loop completed and working for all aspects of self-improvement"

None of the three
human-reviewed loop PRs had merged; the gate is removed, not met.

What changes:

- `routine-eval-weekly.yml` plans improve fires beside eval fires.
  `scripts/plan_week.py` applies the thresholds design's rules: A
  (eligibility: at least four fixtures with one held out, train headroom, a
  failing check to target, not owned by retirement, not paused), C
  (stopping: a 14-day cooldown after a rejection, a stop after three in a row
  until the skill, a fixture or the roster changes or 90 days pass, at most
  two retries of a failed fire, no attempt after a merged improvement until
  the with arm is re-measured) and D (priority bands with minimum shares,
  each fire reserved at its historical cost).
- Improve outcomes are not recorded by the routine: its saved prompt is
  unchanged (changing it needs the owner's OK). `scripts/improve_outcomes.py`
  derives them from evidence that exists already. The evidence is the fire
  run's title (`run-name`, `Fire eval routine: improve <skill>`). An
  accepted candidate shows as the `claude/eval-improve-<run id>` branch. A
  missing branch twelve hours after the fire counts as a rejection: a
  measured rejection and a failed run look the same, so both count, the
  conservative direction.
- `scripts/allowance_guard.py` charges every improve fire of the last seven
  days its own cost for the whole window. That cost is twelve eval fires at
  their measured mean. The guard used to hold one at an eval fire's
  reservation for two hours (the thresholds design's plumbing gap 2), since
  improve results never reach `routine-results/`. `check --mode improve`
  needs room for that charge.

What does not change: arms still run only the registries' default branches
(decision 1), the per-fire guard still runs before every fire, and the
improve gate (`routine-improve-gate.yml`) still decides what becomes a
registry pull request. How an accepted candidate merges is that gate's
business, not this addendum's.
