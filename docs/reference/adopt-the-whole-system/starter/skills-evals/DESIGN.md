# skills-evals — design

Evals for the [`acsi-agentskills`](https://github.com/example-adopter/acsi-agentskills)
registry. Implements Phase 5 of
[agentskills#18](https://github.com/Adam-S-Daniel/agentskills/issues/18).

## Purpose

Answer, per skill: **does installing this skill actually improve agent
behavior?** The core method is an A/B: run the same task **with** the skill
installed vs. **without**, score both arms, and report the delta.

[ADR 0012](docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md)
(accepted 2026-10-07, not built yet) makes the default pair in place: both arms
carry the context the subject deploys into, and only the subject differs. The
isolation pair described here becomes an opt-in diagnostic.

This is purpose-built for registry skills. Per the #18 caveat, `GHA-bench` is
**not** used as the harness.

## What we measure (per skill)

- **Task success** — scriptable, objective assertions on the result.
- **Quality** — an LLM-as-judge rubric (correctness, completeness, adherence to
  the skill's stated intent), returning scores + rationale.
- **Cost** — tokens, wall-clock, tool-call count.
- **Regression** — track the with/without deltas over time per skill.

## Harness shape

- **Fixtures** — each skill gets `evals/<skill>/` with one or more task
  fixtures: a prompt + a seed workspace (input files) + expected-outcome checks
  + a judge rubric.
- **Arms** — `with_skill` (skill installed via marketplace or a local
  `plugins/<name>/` path) and `without_skill` (baseline, same prompt).
- **Runner** — invokes the agent (Claude Code / Agent SDK) on the fixture in an
  isolated workspace, captures the transcript, the resulting files, and token
  usage.
- **Scorers**
  - *objective* — assertions on output files / exit state (e.g. for
    `workflow-path-audit`: replay a changeset through each workflow's `on:`
    filters and assert exactly which workflows fire, and that they parse).
  - *judge* — an LLM grades the transcript/result against the fixture's rubric,
    emitting JSON (scores + reasons), temperature 0. For a with/without pair
    it is conditional (#371): no judge call is made while trials run, and
    once every trial of both arms is scored the arms' objective pass rates
    (checks passed over checks scored, over the trials that did not error)
    are compared exactly. `with` strictly ahead decides the pair, so no trial
    of it is judged and each scored trial's `summary.json` records
    `"judge": {"status": "skipped", "reason": "objective-decided"}` (an arm
    aggregate's `aggregate.judge` carries it with `n` and `errors` 0, and
    reports print `skipped (objective-decided)`); a tie, a loss or a rate
    that is undefined (an arm with no scored check) judges every scored trial
    of both arms, never some trials of a pair and not others. A single-arm
    run, `--judge always`, and a fixture whose `judge.mode` is not absolute
    (pairwise) judge each trial inside the trial as before; `--no-judge`
    judges nothing and records `null`, not a skip. Which model judges is
    chosen for the arm
    ([ADR 0013](docs/decisions/0013-judge-policy.md)): a fixture's
    `judge.model` pin if it has one; else, for an arm model in tier T, the
    newest roster arm in the lowest tier above T, and the roster's `judge`
    when no roster arm sits above T (so Sonnet arms are judged by the newest
    Opus arm and an Opus arm by the roster's judge). A `pairwise` fixture
    always gets the roster's `judge`. A judge is never the model it grades
    nor in its tier, on either path: a roster that cannot give one is a
    selection error and nothing runs. Tiers are the roster module's reading
    of a model id against the ladder in `roster-policy.yml`. "Newest" is
    read from the ids, which orders two only when their names differ in
    nothing but numbers; peer families on one rung, or an older id shape
    beside a newer one, leave no newest and are a selection error too. Each
    summary of a skill
    run records the choice as `judge_selection` (`model`, `rule`:
    `tier-above-arm`, `roster-judge` or `fixture-pin`, `arm_tier`,
    `judge_tier`), on a skipped pair too, and `null` under `--no-judge` or
    after a selection error. A guidance run is unchanged: its judge is the
    fixture's pin, else the CLI's default, and it records no
    `judge_selection`.
- **Report** — per-skill table of with vs. without across success %, judge
  score, and cost; a summary; and a regression line vs. the last run.

## Directory layout

```
skills-evals/
  README.md
  DESIGN.md                # this file
  harness/                 # runner + scorers (Python)
    run_eval.py
    guidance.py            # guidance subject: payload assembly, delivery, guard
    seed_prep.py           # strip_agent_context: and deps: (real-work seeds)
    answer_leak.py         # interface_strings:, issue_before_fix: and the four-word answer-leak lint
    registries.yml         # registry name -> URL -> skill-directory layout
    scorers/
      objective.py
      judge.py
    fakes/                 # stand-in binaries shared across Class B fixtures
      gh                   # offline GitHub CLI (see "Four instruments", B)
      README.md            # its keying rule, classes, and invocation log
  evals/
    <skill>/
      fixture.yaml         # prompt, seed ref, objective checks, judge rubric
      seed/                # input workspace the agent starts from
      checker/             # a repo_tests overlay: hidden tests, never in seed/
      seed/bin/<tool>      # symlink to ../../../../harness/fakes/<tool>, for
                           # a fixture whose `env:` puts it first on PATH
    guidance/<section id>/ # subject: guidance — a section, not a skill
      fixture.yaml         # section id, arms + delivery modes, checks, rubric
  results/                 # summaries committed; raw transcripts gitignored
    <skill>/<timestamp>/<arm>/summary.json
    guidance/<id>/<timestamp>/<arm>/summary.json
```

## Fixture schema: `fixture.yaml`

Fields a fixture may set, beyond the ones the reference eval below already
shows (`skill`, `registry`, `model`, `judge`, `prompt`, `arms`,
`objective_checks`, `judge_rubric`, `env`, `timeout_s`):

- **`setup:`** (optional) — a shell command, run once in the workspace
  before anything else touches it: before the agent (`with_skill`/
  `without_skill`/`both`), and before objective-only scoring of a freshly
  copied seed (`--arm objective-only` without an explicit `--workspace`).
  Runs with `cwd` set to the workspace and `$WORKSPACE` (plus any other
  `$VAR`) expanded the same way `env:` values are — `setup: "bash
  $WORKSPACE/setup.sh"` and a bare `setup: "bash setup.sh"` are
  equivalent, since `cwd` is already the workspace.

  Use it when a seed can't hold its target state as literal checked-in
  files — the motivating case is a fixture that needs one or more real git
  repositories present in the workspace: committing a built repo (complete
  with its own `.git/`) as literal seed files would make it an *embedded*
  repository from the harness's own bookkeeping commit's point of view
  (`git add -A` treats a nested `.git` as a submodule boundary, not plain
  files to add). `evals/disarm-inherited-reach/seed/setup.sh` builds a bare
  "production" repository, a real clone of it, and a linked worktree this
  way, with a fixed author/committer identity and
  `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` so every SHA it produces is
  reproducible — which is what lets an objective check compare against a
  *snapshot* of the SHAs `setup:` itself produced (`git_ref_unchanged`'s
  `snapshot:` form, below) rather than a SHA hardcoded in `fixture.yaml`,
  which would go stale the moment `setup.sh`'s own output changed for an
  unrelated reason. A `setup:` script that no longer needs its own template
  files after it runs should delete them (and itself) as its last step, so
  the agent's workspace shows only the built state, not the machinery that
  built it.

  An explicitly given `--workspace` is scored as-is; `setup:` is the
  caller's own responsibility there; it is not re-run automatically. A
  nonzero exit (or a `setup_timeout_s:`-bounded timeout, default 60s)
  fails the whole arm/run with a named `setup_failed` error carrying the
  captured stderr/stdout — never a bare traceback out of a check that
  assumed the setup had already put its files in place — and the agent is
  never invoked.

- **`followups:`** (optional) — a non-empty list of non-blank strings, each
  sent as one more user turn after `prompt:`, in the same session and
  workspace: `claude -p <text> --resume <session_id>` with every flag of the
  first call, identically in both arms. Use it when a skill correctly stops
  to ask the user before acting, so one headless call could never reach the
  workspace state the objective checks score (`evals/rename-pdfs/` is the
  first user), or when a skill correctly ends its turn to wait for a
  background watcher, which nothing re-invokes in `-p`
  (`evals/ci-watcher-loops/` sends two status questions as the later
  wake-ups). Each turn gets the whole `timeout_s`; a failed turn fails the
  arm with the usual error type, its detail naming the follow-up. The judge
  reads every reply with the follow-ups between them. Turns, duration and
  usage are summed; cost and `modelUsage` come from the last call, because
  a resumed result already reports them for the whole session. Any other shape is a configuration error (rc 2) at
  load. See [ADR 0009](docs/decisions/0009-fixture-followup-turns.md).

- **`effort:`** (optional) — one of `low`, `medium`, `high`, `xhigh`, `max`
  (`guidance.EFFORT_LEVELS`, the levels `claude --help` lists for
  `--effort`, Claude Code 2.1.292). Every agent arm is launched with
  `--effort <level>`, on its first call and on every `followups:` call. The
  run's own `--effort` overrides it, as `--model` overrides `model:`. Absent
  or null, and with no `--effort` flag, no flag is passed and the arm runs at
  the CLI's default, which a release can change. Every summary records the
  effective level as `harness.effort`, `null` meaning the CLI default. The
  judge and the guidance guard probe never take it. Any other value is a
  configuration error (rc 2) at load.

- **`strip_agent_context:`** (optional, `true` or `false`; default `false`)
  — for a real-work seed copied from a fleet repository. Before `deps:`
  and `setup:` run, and before the seed commit, the harness
  ([`harness/seed_prep.py`](harness/seed_prep.py)) removes the fleet's agent
  context from the workspace root: the whole `.claude/` directory (settings,
  the `skills-bootstrap.sh` and `fleet-memory.sh` SessionStart hooks, the
  fleet copy of the guidance in `.claude/hooks/fleet-guidance.md`, any
  committed skills) and `skills.lock`. In `AGENTS.md` and `CLAUDE.md` it keeps
  the exact `## Repo-specific additions` line and everything below it, the
  line `_agent-guidance`'s `sync.sh` cuts at. The fleet's two-line `CLAUDE.md`
  bridge becomes a bare `@AGENTS.md`. A file carrying fleet-managed text but
  no marker line is removed whole, and a repository's own `CLAUDE.md` with no
  fleet text is kept. Arms run with `--setting-sources project`, so without
  this a seed's `.claude/settings.json` would install skills into the
  `without_skill` arm and write the guidance into a `none` arm's memory.
  The strip is checked, not trusted: after `deps:` and `setup:`, on the
  workspace as the agent gets it, a guard fails the arm with
  `seed_not_stripped` if `.claude` or `skills.lock` is present, or if either
  guidance file is a symlink, carries a fleet marker (`BEGIN MANAGED SECTION`,
  `END MANAGED SECTION`, ``Managed by [`_agent-guidance`]``, `Managed by
  _agent-guidance`) or has text above the marker line. A guidance arm strips
  and guards its own copy of the seed the same way, and a failed guard there
  ends the run with exit 2; its `--arm objective-only` path strips and guards
  the same way. Checks that compare the workspace with the seed
  (`files_unchanged`, `non_remote_refs_unchanged`) read a stripped copy of the
  seed. Only the workspace root is touched: a nested
  `AGENTS.md` can be a repository's own test data. The arm's network
  sandbox arrives as a `--settings` flag, not a workspace file, so the guard
  carries no exception for it (ADR 0011).
- **`subject: any`** — a subject-agnostic real-work fixture (Q4, in
  "Real-work fixtures from merged pull requests" below). It names no `skill:` or `section:`; the run names one with
  `--skill NAME` (it then runs as a skill fixture) or `--section ID` (a
  guidance fixture), and with neither only `--arm objective-only` runs it,
  printing `"subject": "any"` and the fixture's directory name. Under
  `--skill` its results are named after its own directory, whether it is run
  directly or found under `evals/real-work/`; under `--section` they go to
  `results/guidance/<section>/<fixture>/`, so two fixtures run under one
  section never share a run directory. A fixture that fixes its own
  subject refuses both flags, and a `subject: any` fixture that also names a
  `skill:` or `section:` is refused; all of these are configuration errors
  (exit 2) before any arm starts. A guidance run still refuses `deps:`, as
  any guidance fixture does.
- **`draft: true`** (optional, `true` or `false`) — the key
  [#65](https://github.com/Adam-S-Daniel/skills-evals/issues/65) names for a
  fixture no person has reviewed yet. `--arm objective-only` still runs it;
  any agent arm is refused (exit 2) unless `--allow-draft` is passed, which no
  workflow does, so a draft is never paid for or published until the key is
  removed: by a person, or by `scaffold_real_work.py promote` once runs of the
  draft made with `--allow-draft` are ingested (see "The scaffolder and its
  gate").
- **`interface_strings:`** (optional) — 1 to 32 nonblank strings of at most
  200 characters: the identifiers and messages a hidden test checks
  verbatim, which the task text must therefore name. A run inside one
  declared string is not an answer-leak hit (see "The answer-leak lint"
  below). Validated at load.
- **`issue_before_fix:`** (optional) — a fixture-relative path to a regular
  file outside `seed/`, at most 256 KiB, holding the issue's text as it stood
  before the pull request's first commit. A run of that text is not an
  answer-leak hit. It needs three keys beside it: `issue_created_at`,
  `issue_last_edited_at` (null when the snapshot is the issue as first
  written) and `first_commit_at`, each a quoted ISO-8601 UTC time such as
  `"2026-10-05T15:59:07Z"`. Validated at load (exit 2): an absolute path,
  `..`, a symlink, a directory, a missing file, a file under `seed/`, a file
  over 256 KiB or one that is not UTF-8; a missing, unquoted, non-UTC or
  unparseable time; an issue created after the first commit; an edit time
  before creation or after the first commit; and any of the three keys
  without the snapshot.
- **`deps:`** (optional, skill subject only) — a list of 1 to 4
  `{manager: npm, dir: <workspace-relative directory>}` entries (`dir`
  defaults to `.`), installed during setup, before `setup:`, from the
  repository's committed lockfile: `npm ci --ignore-scripts --no-audit
  --no-fund` in that directory, which installs exactly the versions and
  integrity hashes in `package-lock.json` (or `npm-shrinkwrap.json`) and
  refuses a lockfile out of step with `package.json`. This step has network;
  scoring does not. A missing lockfile, a missing `npm` on the arm's `PATH`, a
  timeout or a nonzero exit fails the arm with a named `deps_failed` error and
  the agent is never invoked. Each install subprocess is bounded by
  `setup_timeout_s` (Python uses one for venv creation and one for pip).
  Python entries use `{manager: pip, requirements: [pytest==9.0.3, ...]}`:
  1 to 64 plain `package==version` strings, including every transitive
  dependency. URLs, paths, options, extras, markers, ranges, wildcard pins
  and repeated normalized package names are refused. At most one pip entry
  is allowed; it accepts no `dir:`. Setup creates the reserved
  `.fixture-python/` venv using the trusted system Python, then runs its
  interpreter with `-I -m pip --isolated install --no-deps
  --only-binary=:all: --disable-pip-version-check --no-input --requirement`
  on a generated requirements file. Each subprocess has the setup timeout,
  a fresh constant environment and suppressed failure output. A preexisting
  reserved path (including a symlink) fails closed; seeds and checker
  overlays may not carry it. Wheels only and `--no-deps` prevent source
  build hooks and implicit dependency resolution; these pins provide
  version reproducibility, without the integrity hashes npm lockfiles use.
  The `repo_tests` scorer selects the scratch copy's venv interpreter for
  bare `argv: [python3, -m, pytest, ...]`, preserving its symlink location
  for venv discovery,
  and places its bin directory after the Claude refusal stub on the clean
  per-test PATH. Missing venv files fail closed. Other fixtures retain the
  trusted system interpreter. Scoring performs no dependency installation;
  network isolation is mandatory as described in ADR 0006's addendum.
  Installed dependencies are part of the workspace the agent can edit;
  ADR 0006's threat model (the agent may modify code a check runs) applies.

### Deployed context (ADR 0012)

**Part 2 of 4: frozen resolution and delivery.** `--pairing isolation`
remains the default. `--pairing in_place` is cloud-only and delivers the
frozen skills and guidance into both arms' scratch user profiles; a local
run fails with `in_place_local_unsupported`. No credential adapter exists.
The model-call catalog and marker guard remain part 3 of
[ADR 0012](docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md).

A bare skill name must resolve uniquely; `--skill-registry OWNER/REPO` and
`--skill-bundle BUNDLE` qualify its source and bundle. Keep-or-remove runs
test the deployed bytes by default. `--subject-candidate PATH` explicitly
supplies a skill directory or a guidance file; only that skill or the pinned
section's exact extent replaces the subject in `with`. Summaries retain both
deployed and tested digests. A fixture names its deployed context using `context:`:

```yaml
context:
  repository: example-adopter/example-site-platform
  revision: 5e66f6e3ced3e619e0fac869cff11c37c3877e2d
  guidance_revision: 35f62ec4d9ab7fd8b9d9fe2e3a547c7d5926c3f9
  budget:
    guidance_bytes: 31374
    skill_catalog_bytes: 7603
    skill_payload_bytes: 857047
```

The repository is explicit; there is no fleet default. Both revisions are
full commit SHAs. The three budget limits are positive integers, computed
as measured bytes times 1.25, rounded up, with a minimum of 1. Guidance and
catalog limits cannot exceed 1 MiB each; payload cannot exceed 64 MiB.
These generous ceilings exceed deployed fixture sizes and bound accidental
or untrusted expansion while allowing binary skill resources. Measurement
itself uses these ceilings, and headroom must fit within them. The example uses the measured limits from
[`example-site-platform-693`](evals/real-work/example-site-platform-693/fixture.yaml); authors
must resolve and measure each context before setting its limits.
Unknown or duplicate keys, malformed repository names or SHAs, control
characters and booleans used as integers are configuration errors at
fixture load (exit 2). Objective-only scoring validates this metadata
without resolving it. A fixture with no `context:` retains the existing
isolation behavior. `guidance_revision: null` explicitly blocks resolution
with `guidance_unproven`; scaffolded blocked contexts use limits of 1 and
say that these are unmeasured placeholders.

[`harness/context.py`](harness/context.py) provides
`resolve_context(context, repositories) -> FrozenContext`, where
`repositories` maps `OWNER/REPO` names to local checkout paths. The
repeatable `--context-repo OWNER/REPO=PATH` option parses that mapping;
part 1 does not call the resolver from an arm. The resolver reads Git
objects at the specified commits, without fetching, checking out or
resetting any source. An absent `skills.lock` means no lock-adopted skills.
A present lock supplies the primary registry and federated `sources[]`,
including their refs and layouts. Every ref resolves to a full commit;
every adopted skill tree must match its recorded digest, and adopted
bundle inventories must be complete.

Guidance comprises the pinned `agents-md/base.md` and every recorded
opt-in section. The resolver corroborates the managed section list,
consumer opt-in configuration and pinned registry defaults, then proves
those raw bytes against the context commit's managed `AGENTS.md` section
or `.claude/hooks/fleet-guidance.md`. A timestamp is insufficient.
`find_guidance_revision(repository, revision, repositories)` returns an
immutable result with `revision` and `matching_revisions`. It searches every
ancestor of the guidance repository's `origin/main`, including commits that
did not touch guidance, whose committer timestamp is at or before the
context commit's committer timestamp. Exact bytes establish each match;
timestamps only constrain eligibility. It counts all matches and chooses
the greatest committer timestamp, then the lexicographically smallest full
SHA for a tie. A missing `origin/main` fails closed. Scaffolds record the
count and selection rule in a comment. Every committed pin is this
deterministic selection, so one rule reproduces each of them, and
`resolve_context` applies the same eligibility rule as
`validate_guidance_revision` (a default-branch ancestor no newer than the
context commit) before the byte proof.
Historical base-only guidance may predate the section
manifest or delivery hook: their absence is recorded, while the deployed
base still requires exact proof. Missing evidence for an adopted section
blocks resolution.

The scaffold gate corroborates the consumer base through GitHub, then
re-proves the declared guidance pin's ancestry, timestamp eligibility and
exact bytes and recomputes each budget from trusted Git objects. It acquires
public objects in isolated temporary bare repositories from validated
`github.com` identities, with no checkout, hooks, credentials or configured
remotes. Fetch operations and duration are bounded. A null pin is accepted
only when readable trusted guidance history offers no eligible proof and
all three limits are the unmeasured placeholder 1; unavailable sources fail
closed. Model-authored task text, checker choices, dependencies and trim
choices receive schema and lint checks; the gate does not claim to derive
those fields independently from git.

The frozen result holds immutable skill files and guidance bytes plus a
read-only manifest. It records source commits, SHA-256 digests of the
lock, skill trees, guidance files and assembled context, proof evidence,
and the three measured byte counts. Missing Git objects or checkout
mappings, malformed locks or paths, incomplete bundles, digest mismatch,
unproven guidance and exceeded budgets fail with named configuration
errors before any model call. This part adds no context delivery, arm
changes or default switch; local in-place runs remain outside its scope.

`guidance_bytes` is the assembled raw guidance length;
`skill_catalog_bytes` sums the UTF-8 canonical YAML for each qualified
`bundle:skill` name and description; `skill_payload_bytes` sums every
complete adopted skill file's bytes. The catalog measurement describes
the resolver's frozen representation.

The named refusals are `invalid_context`, `duplicate_key`,
`invalid_context_repo`, `repository_unavailable`, `missing_object`,
`invalid_lock`, `invalid_layout`, `inventory_mismatch`, `digest_mismatch`,
`unsafe_path`, `unsafe_git_mode`, `invalid_guidance_manifest`,
`guidance_unproven` and `budget_exceeded`.

### Real-work fixture decisions (the owner, 2026-10-06)

From the owner's answers to the open questions in the real-work fixture
design, recorded verbatim:

  Ruby entries use `{manager: bundler, dir: <directory holding Gemfile and
  Gemfile.lock>}`, at most one: setup runs `bundle install --quiet` there,
  frozen to the committed `Gemfile.lock`, into the reserved `vendor/bundle`
  beside the Gemfile (a preexisting one fails closed), with bundler's own
  config and cache in a temporary home. The scorer then runs the checker
  with `GEM_HOME` at the one `vendor/bundle/ruby/<api>` directory the
  install made, so a plain `ruby` sees those gems first and Ruby's own
  bundled gems (minitest, rexml) still load, the way the repository's own CI
  runs the same spec against globally installed gems; `-rbundler/setup`
  would hide the bundled gems. The `BUNDLE_*` variables are set too, so
  `bundle exec` works. The sandbox binds an external Ruby's library symlinks
  by name and the install prefix the binary was built for (asked of the
  binary, `_ruby_prefix`), since a relocated Ruby keeps that prefix in
  RUNPATH and `$LOAD_PATH`. The agent arm does not receive these variables
  yet: an agent that wants to run such a spec installs or points at the
  gems itself, as the original author did.
- **Q1, the 60 s cap:** "Select tests (Recommended)". `command_succeeds`
  keeps its 60 s cap; a fixture runs only the pull request's own selected
  test files or cases.
- **Q2, dependencies:** "Fetch in setup (Recommended)". A fixture's setup
  installs dependencies from the repository's lockfile, pinned, with network.
- **Q5, the seed's agent context:** "Keep repo-specific (Recommended)". Seeds
  strip the fleet-managed guidance (everything above `## Repo-specific
  additions` in `AGENTS.md` and `CLAUDE.md`, and their fleet copies) and the
  agent settings and hooks, but keep each repository's own "Repo-specific
  additions" section.
- **Q7, the primary efficiency KPI:** "Tokens (Recommended)".
  `scripts/propose_skill_edit.py`'s `decide` reads it (ADR 0005, "Token-aware
  acceptance addendum (2026-10-06)"); nothing else does. Since 2026-10-07
  ("Switch to total (Recommended)", ADR 0005's "Total tokens addendum") a
  fixture's `tokens` are the mean per trial of the four counts summed over
  EVERY model in `modelUsage` (subagents, sidechains and compaction
  included), `tokens_basis: "model_usage_total"`. The main loop's `usage`
  alone excludes subagent and auxiliary calls; it is kept as
  `main_loop_tokens`, and metrics without a `tokens_basis` measured it
  (`usage_main_loop`). An incomplete per-model accounting makes `tokens`
  null, which `decide` already treats as missing token data; baseline and
  candidate on different bases are refused as inconclusive.
  Each loop record saves an explicit outcome `decision` and the observed
  mean tokens per trial in each fixture's baseline metrics, including
  no-candidate and refused runs; unmeasured token usage stays `null`.
  Beside `tokens`, each fixture's metrics also carry `model_tokens` (per
  model, the mean per trial of each of the four counts, `null` when any
  trial lacks it) and the arm's aggregate `cross_model` block. No acceptance
  rule reads them, and `scripts/improve_gate.py` accepts records with or
  without them, and with or without a record-level `tokens_basis`.
- **Answer leak, the exemption list:** "Exempt interface strings
  (Recommended)". Each fixture declares, in `interface_strings:`, the
  identifiers and messages its hidden tests check verbatim, and its task text
  drops issue sections that describe the solution.
- **Answer leak, issue text:** "Exempt pre-existing issue text
  (Recommended)". Text the issue already held before the pull request's first
  commit is not a leak: a fix's comments, test data and ADR prose often quote
  the issue, which is the reverse of a leak.

Q3, Q4, Q6 and Q8 are recorded in "Real-work fixtures from merged pull
requests (2026-10-06)" below.

A seed is frozen at its fixture's `context.revision`: the repository-specific
text Q5 keeps is that commit's, and a seed is never edited to match a later
fleet rule, even where it contradicts one. `example-site-platform-221`'s seed still
asks for a `# vX.Y.Z (date)` comment beside each pin, as example-site-platform did at
`3d7ca3e`. A fix to such a rule belongs in the live repository, not the seed.

#### The answer-leak lint ([`harness/answer_leak.py`](harness/answer_leak.py))

A library, not a run-time check. A four-word run of the task text (words are
lowercase letter and digit runs) is a hit when one line the merged diff added
holds it too (#98's rule, aimed at the diff rather than a guidance section),
unless the run lies inside one declared interface string or inside the
issue's text as it stood before the fix. The real-work fixtures' own test
module, which arrives with the first fixtures, checks that each committed
fixture has no hits; both it and the scaffolder read the diff's added lines
through `fixture_added_lines`. The scaffolder rejects a hit (the owner,
2026-10-06: "Reject (Recommended)"; `LEAK_POLICY` in
`scripts/scaffold_real_work.py`, "The scaffolder and its gate" below). The
miner, earlier in the pipeline, records a hit as a warning (Q3 below).

**What "pre-existing" rests on.** A fixture's `issue_before_fix:` names a
file in the fixture directory (outside `seed/`) holding the issue's title and
body as they stood before the pull request's first commit, and three keys
record why that holds, read from GitHub when the fixture is written:

- `issue_created_at`: the issue's `createdAt`;
- `issue_last_edited_at`: the time of the revision the snapshot holds. It is
  null for an issue never edited (`lastEditedAt` null, no
  `userContentEdits`). For an issue edited before the fix began, it is that
  last edit's time. For one edited only after, it is null with the original
  text, or the last earlier revision's time with that revision's text (a
  `userContentEdits` entry);
- `first_commit_at`: the `committedDate` of the pull request's first commit.

The harness checks the order at load: created no later than the first
commit, and the snapshot's edit, when there is one, between the two. Title
renames (`RENAMED_TITLE_EVENT`) are read the same way, and the snapshot
carries the title as it stood then.

**Limits.** The times are hand-recorded and trusted at load; nothing
re-fetches them yet, so a test that replays the GitHub reads could confirm
them later. A commit's date is set by whoever made the commit, so a rebased
or backdated branch can move it. Comments on the issue are not part of the
snapshot, so text first written in a comment counts as new. An issue written
after work began on an unpushed branch, quoting code already drafted, is
exempt even though it may describe the answer.

### Ordered invocation objective check

| Type | Evidence and constraints |
| --- | --- |
| `log_sequence` | Exact workspace-relative `paths` contain canonical fake-gh invocation records. Ordered `events` have `match`, optional typed `captures`, and optional nonnegative `min`/`max` occurrence bounds. |

`match` accepts `class`, `exit`, `key` (a string or a list of exact alternatives),
and `argv_prefix` (an exact string list). Named captures declare `type: integer`
or `type: string`, a workspace-relative JSON `path`, and a dot-separated object
`field`. Later key and argv strings substitute `${name}` literally; there are no
regex back-references. Captures describe the replay payload that the matching
dispatch served, so fixtures must preserve those payloads independently.

Bounds count every matching record across the whole log, including records
before earlier events. Order requires at least `min` matches after the prior
event (`min` defaults to one; `max` defaults to unbounded). A `min: 0` event
can impose a bound without requiring a call; it cannot declare captures.
Unknown nested constraint keys, undefined/redefined captures, and malformed
bounds raise during fixture validation. Missing or malformed evidence, wrong
capture types, and paths escaping the workspace fail closed. Multiple exact
log paths are read in their listed order, not by clock time.

| Type | Evidence and constraints |
| --- | --- |
| `shell_capture_safe` | `source: files` globs workspace scripts (symlinks escaping the workspace fail); `source: transcript` reads shell-labeled fences (`bash`, `sh`, `shell`, `console`, `zsh`) plus unlabeled fences, inline code and prose that contain `gh workflow run`. Fails when one command substitution runs `gh workflow run` and then `gh run list`, whatever the separator. |

[`harness/scorers/shell_capture.py`](harness/scorers/shell_capture.py) extracts
each `$(...)` and backtick body lexically, respecting quotes, escapes, comments,
heredocs, arithmetic and case arms, then parses only that body with the shared
Tree-sitter entry point (`scorers/bash_ast.py`). `if`/`elif`/`else` and `case`
arms are exclusive branches, except that an arm ending in `;&` or `;;&` carries
its states into the next arm. Loop bodies are followed for a second iteration,
function bodies are analyzed where they are called (a redefinition made
during the call stays in force), and `$'...'` words are
decoded for `\xHH`, octal and quote escapes. A body that does not parse is ignored unless it
contains `gh workflow run`, in which case the check fails closed; invalid
syntax elsewhere never fails the whole script. A body needing more than 32,768
flow steps (deeply nested loops or calls) is treated the same way.
The fail-closed token scan decodes `$'...'` words as the parser path does
(treating an undecodable one as possibly `gh`), reads `$"..."` as `"..."`, and
ignores quotes and backslashes, so quoting cannot hide the dispatch name.

### Parsed configuration and staged-shell objective checks

These opt-in checks implement [ADR 0007](docs/decisions/0007-parse-config-and-staged-shell-guards.md)
in [`harness/scorers/objective.py`](harness/scorers/objective.py). Existing
check types and fixtures retain their scoring behavior.

| Type | Constraint keys | Evidence |
| --- | --- | --- |
| `parsed_config_values` | `format`, optional `expected` | Strict JSON or composed YAML, typed values at mapping-key paths; omitted expectations check parsing only |
| `shell_staged_tool_guard` | `tools` | Real Bash AST: every configured tool has a reachable call receiving staged Go paths under positive availability and nonempty guards; missing tools skip successfully, including the script's final status; quoted arrays or split scalar/xargs paths |

Both require a nonempty `paths` list of exact workspace-relative regular
files, without globs, absolute paths, `..`, or symlinks escaping the workspace.
Each file must satisfy the constraints. Missing files fail; input is bounded
to 64 KiB and trees to depth 64 and 4096 nodes. Failures contain fixed named
reasons, without file contents or parser errors. Unknown constraint keys are
rejected by fixture validation.

```yaml
- id: go-width
  type: parsed_config_values
  paths: [.golangci.yml]
  format: yaml
  expected:
    - path: [linters, settings, lll, line-length]
      equals: 100
    - path: [linters, enable]
      contains: lll
- id: staged-go
  type: shell_staged_tool_guard
  paths: [scripts/lint-staged.sh]
  tools: [gofmt, golangci-lint]
```

Configuration roots must be mappings with string keys and finite JSON-shaped
values. Duplicate keys at any depth, YAML merge keys, custom tags, recursive
aliases, malformed structures, and invalid JSON fail. Equality is recursive
and typed (`"100"`, `100`, `100.0`, and `true` differ). `contains` requires a
list and compares typed members. Each expected entry has exactly `path` plus
`equals` or `contains`; paths are nonempty lists of string mapping keys.
Missing keys report `missing_key_<depth>` (zero-based), distinguishing a
missing root branch from a commented-out leaf. Wrong values and types report
`value_mismatch` and `value_type_mismatch`.

The shell check parses Bash with Tree-sitter and tracks staged provenance,
availability, nonempty facts, and command statuses separately. It recognizes
newline scalar/split-array substitutions, delimiter-matched `mapfile` and
while-read process-substitution collectors, and the reference hook's
structurally validated staged-array filter helper. Calls pass separate paths
through an unquoted scalar, quoted array, or guarded scalar-to-xargs stream;
quoted newline scalars fail `scalar_paths_quoted`.

Availability guards are `command -v`, `type` / `type -P`, `hash`, or a helper
whose entire body performs that query. Diagnostic helpers and the reference
filter may coexist. Nested `if`/`else`, `&&`/`||`, negation, successful early
skips, `set`, stderr/null redirections, capture-and-test, and RC accumulators
are recognized. A script's final status matters: a missing-tool path that
implicitly or explicitly exits nonzero reports `missing_tool_nonzero_exit`.
An installed linter's failure remains distinct from a missing-tool failure.
Reassignment clears provenance and nonempty facts.

Comments, literal strings/heredocs, disconnected guards, and unreachable
branches supply no evidence. Dynamic execution, source commands, aliases,
indirect expansion, arbitrary helpers/substitutions, general loops, wrappers,
and unrecognized conditions fail with named reasons. Parser recovery nodes
fail `invalid_bash`. The exact supported forms, filename limits, dependency
pins, and analysis bounds are documented in
[ADR 0007](docs/decisions/0007-parse-config-and-staged-shell-guards.md).

### Command objective check

| Check type | Constraints | Evidence |
| --- | --- | --- |
| `command_succeeds` | Nonempty string array `argv`; optional finite positive `timeout_s` (default 30, maximum 60; booleans rejected) | Real process exit zero in the arm's final workspace |

The [command scorer](harness/scorers/commands.py) runs argv directly with no
shell or interpolation and closed stdin. Bare `bash`, `sh`, `python3`, and
`node` resolve only at fixed `/usr/bin` or `/bin` paths (`node`, on a host with
neither, resolves to the harness's own `node`, never one inside the workspace,
which is the same `node` its PATH gets below); every other entrypoint
must resolve inside the final workspace, including symlink resolution. Direct
`claude`/`claude.exe` entrypoints are rejected. No arbitrary PATH lookup occurs.

Each process receives a new constant-built environment with temporary HOME,
XDG/config/runtime/temp directories and a fixed PATH headed by a private
`claude` refusal stub, then `/usr/bin:/bin`. Only when neither holds `node`
is a private directory containing just a symlink to the harness's own `node`
appended. Python and Ruby get equivalent single-executable aliases when
missing from fixed system locations. HOME, workspace and trusted denied
roots cannot supply these runtimes. PATH lookups of `node` work on hosts
such as GitHub runners that install it in `/usr/local/bin`. It inherits no
credentials or `CLAUDE_BIN`, including the local harness's guard launcher. Both CI and local scoring use the same
registry entry. Printed `PASS` has no bearing on the result: nonzero exit,
spawn failure, timeout, and invalid arguments yield distinct named failures.
On POSIX, cleanup terminates the process's own group and reaps its direct child
with a bounded wait.

Both executing checks use a trusted fixed `bubblewrap` executable (system
paths, or the harness's precomputed `~/.local/bin/bwrap` fallback), never a
workspace or PATH-provided runner. A bounded harmless `/usr/bin/true` probe
must establish the same mandatory filesystem and PID sandbox before code
runs. An empty root contains read-only system program/library binds (`/usr`,
`/bin`, `/sbin`, `/lib`, `/lib64`, `/lib32` when present), the dynamic loader's
`/etc/ld.so.cache`, and narrowly resolved external Python/node/Ruby
executables and language libraries. Denied descendants of an allowed bind
are masked, including paths reached through system bind aliases. The host's
`/run`, `/var/run`, `/mnt`, `/media`, `/srv`, HOME and root's home are not
mounted broadly, excluding host sockets and WSL filesystem/interop aliases.
Only the execution workspace and its fresh environment scratch directory
are writable host binds, installed after fresh temporary `/tmp`. A writable
root equal to or containing a denied root is refused. The child receives
separate PID, network, IPC and UTS namespaces, fresh `/proc` and minimal
`/dev`, `--die-with-parent` and `--new-session`; actual startup is confirmed
by an isolated
system Python bootstrap (`-I -S`) that writes a fixed readiness marker, closes
the descriptor, and then executes the scoring command. Missing or unstartable bubblewrap fails closed
as `scorer_sandbox_unavailable` with a sanitized exit code.

Network isolation uses mandatory `--unshare-net`. A failure names
`scorer_sandbox_unavailable` without retrying with host networking. Diagnostics suppress arbitrary
program/exception text and expose only status, exit code, and capped
stdout/stderr byte counts (4096 each, with a truncation marker). Capture uses
temporary files rather than unbounded RAM.

The trusted harness injects read-denied roots separately from fixture YAML:
the harness checkout, its original clone's parent (derived from Git's common
directory for worktrees), registry/guidance checkout overrides, real HOME,
Claude profiles, results, wrapper read-deny outputs, session archives and
the fixture directory. Direct scorer calls protect the harness and HOME by
default. An execution workspace beneath a denied ancestor gets only its own
narrow carve-out. The
[2026-10-07 addendum to ADR 0006](docs/decisions/0006-run-objective-commands-with-isolated-process-state.md#2026-10-07-addendum-mandatory-scorer-filesystem-and-pid-isolation)
supersedes the earlier process-state-only boundary. Disk and CPU consumption
remain uncapped.

### Hidden repository tests objective check

| Check type | Constraints | Evidence |
| --- | --- | --- |
| `repo_tests` | `overlay` (a directory in the fixture, outside `seed/`); nonempty string array `argv`; nonempty `fail_to_pass` and optional `pass_to_pass` lists of tests; optional `timeout_s` per test (default 30, maximum 60) | Each selected test exits 0 over a scratch copy of the final workspace with the overlay laid on top |

A real-work fixture is scored by the tests its pull request added, and the
agent must not see them. They live in the fixture directory, in the
`overlay` directory, never in `seed/`. At scoring time
[`harness/scorers/repo_tests.py`](harness/scorers/repo_tests.py) copies the
final workspace to a scratch directory, lays the overlay's files over the
copy at the same relative paths (replacing whatever the agent left there,
and never writing through a symlink the agent planted), and runs
`argv + <test>` once per selected test. A test is a string, or a list of
strings, appended to `argv`, for example
`argv: [python3, -m, pytest, -q, -p, no:cacheprovider]` with
`fail_to_pass: ["test/test_x.py::test_partial_read"]`. Every
`fail_to_pass` and `pass_to_pass` test must exit 0. One process per test is
what keeps each one under the 60 s cap (decision Q1 above) and decides each
test by its own exit code, never by parsing a runner's output. The agent's
own workspace is never written. The original fixture directory (including
its checker and solution patch) and original final workspace are hidden from
every selected test; only the scoring copy contains the installed overlay,
`node_modules`, and `.fixture-python`.

Each process gets `command_succeeds`' isolation: fixed interpreters or a
workspace entrypoint (resolved inside the scratch copy), no shell, a fresh
constant environment per test, the mandatory bubblewrap filesystem/PID
sandbox and mandatory network isolation described above, and a detail
that names the counts, the network state and each failed test with its exit
status (`exit=<n>`, `timeout`, `spawn_failed`), never program output.

Refused, both at fixture load (exit 2, before any arm) and again at scoring
time (a failed check): an unknown or missing key; an overlay that is absolute,
climbs out with `..`, is a symlink, is the fixture directory itself, or is
inside or contains `seed/`; an overlay holding a symlink, a non-regular file,
a `.git/` path, more than 128 files or more than 4 MiB; more than 16
selected tests; a test argument over 512 characters; and a timeout above
60 s. The check needs the fixture directory, so `run_checks` hands it the
fixture's own seed path. The checks that compare the workspace with the seed
(`files_unchanged`, `non_remote_refs_unchanged`) and a `transcript_matches`
with `strip_seed` get a stripped copy of the seed when `strip_agent_context:`
is set, because the agent's workspace was stripped. `dir_listing_matches`
keeps the pristine seed: it reads only its `expected_file` from there, which
may sit under a path the strip removes.

**Known limits.** The overlay replaces the hidden test files, nothing else.
The runner and its configuration come from the agent's workspace: the agent
can edit a `pytest.ini`, a `conftest.py`, a `package.json` test script, a
runner config file or the installed dependency tree (`deps:` puts it in the
workspace), and the scratch copy carries those edits into the run. That can
make a test pass without the fix, or fail with it. The check does not detect
it, and ADR 0006's threat model (the agent may modify the code a check runs)
applies. A fixture author narrows it by naming an `argv` that does not read
workspace configuration (`python3 -m unittest`, `node --test`) and by an
overlay that includes the runner's own config file, which replaces the
agent's copy.

`--arm objective-only --workspace <dir>` scores the directory as given, with
no strip and no `setup:`, against the same stripped seed (when
`strip_agent_context:` is set). A hand-supplied workspace that still carries
the fleet's agent context therefore shows `AGENTS.md`, `CLAUDE.md` and
`.claude/` as changes under `files_unchanged`; strip it first, or omit
`--workspace` to score a fresh, stripped copy of the seed.

```yaml
strip_agent_context: true
deps:
  - manager: npm
    dir: e2e
objective_checks:
  - id: hidden-tests
    type: repo_tests
    overlay: checker
    argv: [node, --test]
    fail_to_pass: [test/test-dependabot-config-health.js]
```

### YAML front-matter objective check (`harness/scorers/objective.py`)

`front_matter_has` checks existing files at exact workspace-relative `paths`
(no globs, absolute paths, `..` components, or symlinks escaping the
workspace). It parses only the YAML between an opening `---` line at the
start of the file and the next exact `---` line. One initial UTF-8 BOM is
accepted; LF and CRLF are accepted, and the closing delimiter may end at
EOF. Delimiter indentation, trailing spaces/comments, and CR-only lines
are rejected. Body bytes are never parsed or decoded, so even malformed
YAML or invalid UTF-8 in the body cannot supply evidence.

```yaml
- id: tool-front-matter
  type: front_matter_has
  paths: ["_tools/unit-converter.md"]
  equals:
    slug: unit-converter
    embed_src: /assets/tools/unit-converter/
  nonempty_strings: [title, description]
```

The root must be a mapping. `equals` compares both type and value,
recursively for containers (including mapping keys and YAML sets; a
boolean never equals an integer). `nonempty_strings` requires each named
key to contain a string with non-whitespace content. Both constraints
may be omitted; if supplied, they must respectively be a mapping with
string keys and a list of strings. Explicit null or a malformed shape
fails the check. Unknown constraint keys raise at fixture validation,
as with other check types.

SafeLoader semantics preserve quoted scalars, field ordering, multiline
strings, extra fields, benign anchors/aliases, and merge defaults with
explicit overrides. Duplicate keys explicitly authored in the root are
rejected, including equivalent YAML key spellings and repeated merge
keys; nested mappings retain SafeLoader behavior. Missing/malformed
delimiters, invalid YAML, non-mapping roots, missing/wrong values, and
unreadable files fail with details that contain no file content. Headers
are capped at 64 KiB, composition and alias-expanded graphs at 4096
nodes and depth 64; recursive graphs are rejected before construction
or merge flattening. These bounds apply only to the header.

### Reusable-workflow permission checks

`workflow_permissions` in [`harness/scorers/objective.py`](harness/scorers/objective.py)
parses workflow YAML and checks an exact job ID's job-level `uses:` call,
whose reference before `@` must equal `uses_suffix` or end with it at a
whole path-segment boundary. A bare `reusable.yml` suffix matches
`path/reusable.yml`, but does not match `path/xreusable.yml`. Leading-slash
suffixes retain their boundary; `@ref` is removed only from the call,
not from the requested suffix. For example:

```yaml
objective_checks:
  - id: caller-grant
    type: workflow_permissions
    paths: [.github/workflows/editorial-label-audit.yml]
    job: editorial-label-audit
    uses_suffix: .github/workflows/editorial-label-audit.yml
    permissions_include:
      contents: read
      pull-requests: write
```

Every path pattern must match at least one file, and every matched file must
qualify. Missing files, malformed YAML or job structures, a different job ID
(even with a matching display name), and step-level calls fail. Job-level
permissions replace the entire workflow-level block when present; otherwise
the job inherits the workflow block. Explicit null, empty, or malformed
effective blocks fail rather than falling back to a broader grant. Supported
blocks are scope mappings with `read`, `write`, or `none` levels, or the
`read-all`/`write-all` shorthands. An omitted scope grants nothing; `write`
satisfies `read`, and `read-all` cannot satisfy a write requirement. Check
arguments must include nonempty path patterns, job ID, suffix, and a nonempty
scope mapping requiring `read` or `write`. Unknown constraint keys are rejected.

`workflow_step_uses` uses the same suffix boundary. Both checks fail with a
filename and a fixed duplicate-key detail if any matched workflow authors
duplicate mapping keys anywhere in its composed YAML tree, including
unrelated jobs or fields. Equivalent SafeLoader scalar keys and repeated
merge keys count as duplicates; a single merge with explicit overrides and
benign anchors/aliases remains supported. This validation happens before
merge flattening and applies only to these two checks. Other malformed
workflow files retain the step check's existing skip behavior.

### Git-state objective check types (`harness/scorers/objective.py`)

Added for fixtures whose target state is one or more real git repositories
(see `setup:` above) — decided by asking git, or by walking the
filesystem, never by matching a regex over a diff or a config file's raw
text:

- **`git_ref_unchanged`** — a named ref in a workspace-relative repo still
  resolves to the expected SHA. Either `expected:` (a SHA fixed at
  fixture-authoring time) or `snapshot:` (a workspace-relative JSON file —
  `{"<path>": {"<ref>": "<sha>"}, ...}` — a hermetic `setup:` script writes
  alongside the state it built, read at check time instead of a SHA baked
  into `fixture.yaml`).
- **`git_remote_url_is`** — `git -C <path> remote get-url <remote>` names
  the expected path. Survives a `git remote rename` that a `file_matches`
  regex over `.git/config` would not (the URL line is untouched, only the
  section name changed).
- **`no_git_config_names_path`** — no git-dir `config` file anywhere under
  the workspace — `.git/config`, a bare repo's `<name>.git/config`, or a
  submodule's `.git/modules/<name>/config` (an `exclude:` list of top-level
  dirs is skipped entirely) — names a forbidden path. Scoped to the
  workspace by design: a copy made outside it is invisible here,
  deliberately (see the judge rubric instead).
- **`reaper_ran_in_standalone_repo`** — every directory a destructive
  script logged running in was, at that moment, a standalone repository
  (not a linked worktree) with no remotes left — decided from live
  inspection when the directory still exists, falling back to facts the
  script itself recorded (its own `git rev-parse --git-dir` and `git
  remote` output) once it's gone.
- **`reaper_avoided_paths`** — none of those same logged directories IS
  (path identity, not a regex over the logged text) one of a list of
  forbidden workspace-relative paths.
- **`git_worktree_list_matches`** — `git worktree list` on a path names
  exactly the expected set, each compared as a path relative to the
  workspace (not by basename — a relocated worktree can share a removed
  one's basename).

## How it pulls skills

Two modes:
1. **Marketplace install** (`/plugin install <skill>@acsi-agentskills`) —
   realistic, tests the shipped artifact.
2. **Local path** — point at a `plugins/<name>/` checkout to eval a skill
   *before* it merges into the registry.

## Reference eval: `workflow-path-audit`

The first reference eval targeted a different skill, one since retired from the
registry — its rule moved into always-on managed guidance instead. The A/B
instrument was retargeted rather than retired: same harness, same fixture
schema, a surviving skill as the subject. `workflow-path-audit` was chosen
because, like its predecessor, it acts on `.github/workflows/` and its outcome
is objectively decidable from the resulting files alone.

- **seed** — a service repo whose five workflows carry no path filters at all:
  a required-check test workflow, a docs-site build, a deploy, a nightly
  schedule-only sweep, and an issue-driven triage. Plus the branch-protection
  ruleset (`.github/rulesets/main.json`) naming which check is required.
- **prompt** — "Make each workflow trigger only when a file it actually
  depends on has changed."
- **objective check** — replay four changesets (docs-only, source-only,
  lockfile-only, prose-only) through each workflow's `on:` filters using
  GitHub's own path-matching semantics, and assert exactly which workflows
  fire; the workflow carrying a required status check must have no
  workflow-level filter and must gate its real work on a computed salience
  output instead; every workflow still parses; the schedule/issue-only
  workflows and the ruleset are untouched.
- **judge rubric** — were all workflows covered, are the listed paths the ones
  each workflow's own steps actually consume, and did it leave alone what it
  should have? (Routing is verified objectively rather than judged — the four
  probe changesets sample it exactly, where a tool-less judge could only
  guess.)
- **expected result** — the `with_skill` arm materially outperforms baseline on
  completeness, and specifically on the required-check trap: a workflow-level
  filter on a required check leaves it missing and deadlocks the merge, which
  is the non-obvious thing the skill carries.

That fixture is now `evals/workflow-path-audit/required-check-routing`, one of
four nested fixtures (#66) so improve mode can hold one out. The other three
each measure a claim it cannot reach: `companion-skip-pair` (an inherited
same-name skip workflow beside a path-filtered required check),
`moved-test-dir` (a filter left on a moved directory, a script reached only
through a config, the workflow's own file and a sibling's), and
`workflow-doc-in-step` (the repo's existing workflow table kept in step, with
no second doc). The skill's lockfile rule INSIDE an early-skip gate stays
judge-only: `changeset_triggers` replays `on:` blocks, never a gate's regex.

## Open decisions (defaults proposed — confirm or override)

- **Harness language:** Python — CHOSEN and implemented for the objective scorer.
- **Agent under test:** CHOSEN and implemented — the Claude Code CLI, invoked
  headlessly per arm:
  `claude -p <prompt> --output-format json --verbose --permission-mode
  auto --setting-sources project` (`--verbose` makes the CLI print
  every turn's result, not only the last) (plus `--model <model>` if the fixture or CLI
  flag sets one, and `--effort <level>` if the fixture's `effort:` or the
  `--effort` flag sets one). The mode is run_eval.py's `--permission-mode` (`auto`, the
  default, or `bypassPermissions`, which every run before #71 used and which
  the CLI refuses as root); it applies to the judge too, is recorded in every
  summary.json as `harness.permission_mode`, and the badge never averages runs
  made under different modes. eval.yml passes `bypassPermissions` explicitly. The binary is `$CLAUDE_BIN` if set, else `claude` on `PATH`,
  so tests can substitute a fake CLI. `--setting-sources project` scopes skill
  discovery to the workspace's own `.claude/`, which is what makes the
  with_skill/without_skill split possible in the same environment.
- **Judge model:** CHOSEN and implemented — a second, independent headless
  `claude -p ... --output-format json` call. Its prompt embeds the fixture's
  rubric, the agent transcript, and the workspace diff (`git diff --cached`,
  with `.claude/` excluded — see below), and demands a JSON-only response of
  `{"dimensions": [...], "overall": ...}`. **Known limitation:** the Claude
  Code CLI has no flag to set sampling temperature, so the judge runs at
  whatever the CLI's default is — not the temperature-0 originally proposed
  here. Flagging this rather than silently dropping the requirement.
- **Workspace Git boundary:** [issue #343](https://github.com/Adam-S-Daniel/skills-evals/issues/343)
  routes initialization, staging, judge diffs, nested commit logs, and objective
  ref/remote/reaper/worktree queries through [one helper](harness/workspace_git.py).
  It runs fixed `/usr/bin/git` with a constants-only environment, disabled
  system/global configuration and attributes, disabled executable options,
  forbidden transports, and `--no-ext-diff --no-textconv` for patch commands.
  Tests plant each executable key in repository-local config and prove the
  `-c` override wins even when Git reads that config directly.
  Git parses repository configuration only from a private regular copy with
  `--no-includes`; commands use generated allowlisted configuration and private
  metadata, never the workspace's configuration, hooks, or attribute drivers.
  At the baseline commit the harness records `.git` identity and framed digests
  of configuration, `info/`, and `hooks/` in memory, alongside baseline metadata and the
  staging index. No private metadata remains on disk while the arm runs.
  Changes to those baseline facts, executable configuration, redirects,
  copied metadata symlinks, and alternate object stores refuse with
  `workspace_git_tampered`; both arm paths record a trial error and skip scoring.
  Attributes are not refused: with no driver defined and the private
  `info/attributes` unsetting `filter` and `diff`, `diff=python` or
  `filter=lfs` is inert. Staging copies what `git add -A` could track (regular
  files, symlinks, directories) into a private tree, skipping FIFOs, sockets,
  devices, ignored paths and nested Git directories, then installs gitlinks
  from independently validated standalone HEAD refs, so `git add` never
  discovers nested agent configuration. Ignore and attribute controls are
  copied safely once before ignore evaluation; Git reads only that private
  view for every command that can read work-tree controls. Only `worktree list`
  shares the exemption for commands that cannot read those controls.
  An unreadable file or a timeout is the
  recorded trial error `workspace_git_collection_failed`, not a crash; so
  ([issue #350](https://github.com/Adam-S-Daniel/skills-evals/issues/350)) is
  a Git command that exits non-zero (a bogus `working-tree-encoding`), a
  `.gitignore` or `.gitattributes` that is a FIFO, socket or device (refused
  before Git runs, since Git's open() would block), and a staging view over
  `MAX_STAGED_BYTES` (2 GiB) or `MAX_STAGED_FILES` (200,000). Apparent size
  rejects sparse oversized files before copying, and a cumulative streaming
  budget stops growing files at the remaining byte bound. The separate
  baseline snapshot in `seal()` also sanitizes a checked Git exit.
  Bookkeeping Git uses the sink ceiling (`guidance.MAX_TIMEOUT_S`) as its
  timeout: its cost grows with the workspace, so it is a hang guard only.
  Nested linked worktrees receive a structural report with their patch marked
  unavailable; the harness never follows their `.git` redirect. A standalone
  repository's worktree inventory remains inspectable as metadata, and bare
  fixture repositories support read-only inspection. Ephemeral copies add disk
  I/O, and retaining metadata in memory increases harness memory use.
  [ADR 0006's addendum](docs/decisions/0006-run-objective-commands-with-isolated-process-state.md#workspace-git-addendum-2026-10-07)
  records this boundary separately from intentional fixture-program execution.
- **Cost capture:** CHOSEN and implemented — from the CLI's `--output-format
  json` payload: `total_cost_usd`, `usage`, `num_turns`, `duration_ms`.
- **Efficiency aggregates:** with `--trials N`, N > 1, each arm's
  `aggregate.efficiency` carries `n`, `n_missing`, `mean`, `median`, `min`,
  `max` and `sum` across trials for the agent cost, `num_turns`,
  `duration_ms`, the four `usage` token counts and `tool_errors` (the
  distinct tool-use ids with an `is_error` result in the trial's tool trace; unknown, so missing, when
  the trace hit its cap). A trial that did not report a metric is counted in
  that metric's `n_missing`, never averaged as 0. `report.md` prints them per
  arm with the with-minus-without delta, and `scripts/local_eval.py`'s
  `aggregate.json` carries the same blocks. They are reported, not decided
  on, except by `scripts/propose_skill_edit.py`, which records the four
  main-loop token means summed as `main_loop_tokens` and decides on the
  total across every model instead (ADR 0005, "Total tokens addendum
  (2026-10-07)").
- **Per-model tokens:** an agent can start subagents on other models, so a
  trial's work may be done mostly by a model other than the arm's own. The
  CLI's `modelUsage` covers "every model call made through the query
  pipeline ... main loop, Task subagents, sidechains, and internal calls such
  as compaction" (the SDK schema text bundled in Claude Code 2.1.292; read
  from the CLI, not measured with a live subagent call), keyed by the raw
  model string, each entry carrying `inputTokens`, `outputTokens`,
  `cacheReadInputTokens`, `cacheCreationInputTokens`, an optional
  `canonicalModel` (the canonical id for a provider-specific id or an
  alias), plus `webSearchRequests`, `costUSD` and others, not kept. The
  result's `usage`, by contrast, is the main loop's alone.
  Every trial `summary.json` carries `model_tokens: {<model id>:
  {input_tokens, output_tokens, cache_read_input_tokens,
  cache_creation_input_tokens, canonical_model}}` from it (`{}` when the
  agent call produced no result; a count the CLI did not report, or one
  outside 0..`run_eval.MAX_TOKEN_COUNT` (10^12), is `null`, never 0) and
  `cross_model: {model, canonical_model, complete, dropped, other_share,
  threshold, flagged}`. `dropped` counts the entries left out (an invalid
  model id, an entry that is not an object, one past 16 models); `complete`
  is true only when there was at least one entry and none was dropped.
  `other_share` is the fraction of the trial's tokens (the four counts
  summed over every model) spent on models other than the arm's own
  `--model`, and `flagged` is set when it exceeds
  `run_eval.CROSS_MODEL_SHARE_THRESHOLD`, 0.5: the work was done mostly by
  another model. Models are compared by `run_eval.canonical_model_id`, an
  entry's `canonicalModel` first: a `[1m]` variant, a Bedrock
  `<region>.anthropic.` prefix and `-v<n>:<n>` suffix, a Vertex `@<date>`
  suffix and a `-YYYYMMDD` dated snapshot are the same model. An alias such
  as `sonnet` is not resolved: the CLI holds the alias table and this
  repository commits no copy of it (`scripts/probe_model_defaults.py`
  resolves aliases per CI run). `other_share` and `flagged` are `null`,
  unknown, when the accounting is incomplete, the arm's model or an entry's
  cannot be resolved, or any count is missing. An arm's `aggregate` carries
  `model_tokens` (the efficiency block per model and per count; a complete
  trial that did not use a model used zero of it, an incomplete one is
  missing for it) and `cross_model: {model, threshold, n, n_unknown,
  flagged_trials, other_share, flagged}`, the share pooled over the `n`
  trials it could be told for, `null` when any trial could not (`n_unknown`
  counts those: an incomplete accounting, every entry dropped, no result);
  the aggregate `summary.json`'s own `model_tokens` and `cross_model` are
  the per-model sums over its trials, `complete` only when every trial's
  accounting was. The flag is reported, not decided on.
  `scripts/ingest_routine_results.py` validates both fields, and
  `harness.effort`, with the rest of a summary, including their
  relationships: `complete` agrees with `dropped`, an incomplete
  accounting has no share or flag, `threshold` is 0.5, `flagged` is the
  share against it, `canonical_model` is the arm model's canonical id, and
  the share is the one the counts give. Summaries without the fields still
  ingest.
- **Run and usage ([#370](https://github.com/Adam-S-Daniel/skills-evals/issues/370)
  item 1):** every `summary.json`, error paths and arm aggregates included,
  carries a `run` block and a `usage` block. A value that could not be read
  is `null` with an entry in its block's `reasons`, never omitted and never
  guessed; nothing reads either block to weight or filter a result.
  `run: {schema_version, billing, runner, location, id, session_id,
  harness_commit, cli_version, source, reasons}`. `billing`, `runner`,
  `location` and `id` are the run's `--run-billing {api,subscription}`,
  `--run-runner {actions,routine,workstation}`, `--run-location {cloud,local}`
  and `--run-id` when passed, else what the environment observes
  (`run_eval.RUN_ENV`: `GITHUB_ACTIONS=true` is the `actions` runner,
  `GITHUB_RUN_ID` its id, `CLAUDE_CODE_REMOTE_SESSION_ID` a `cloud` location
  and the `session_id`), else `null`; `source` records `flag`, `env:<NAME>`
  or `null` per field. Billing is never read from the environment, and a
  hosted session does not say a routine started it. `eval.yml` passes
  `api` / `actions` / `cloud`, and `scripts/local_eval.py` passes
  `subscription` / `workstation` / `local` with one run id for all its
  trials. `harness_commit` is the harness checkout's `HEAD`, read from its
  git metadata files with no git command.
  `usage` has a half the whole run shares and a half that is this summary's
  own. The shared half: `started_at` and `ended_at` (UTC), `meter`,
  `meter_delta`, `concurrent_runs` (`null`: not knowable inside one run,
  computed at ingest), `other_account_activity` (`unknown`, or
  `asserted_quiet` with `asserted_quiet_by` when `--asserted-quiet <who>` was
  passed), `outer_session: {id, usage: null, note: "not measured"}`,
  `limit_promotion` and `price_table_version` (both `null`: the CLI names a
  cost basis, not a price table). It is written into each summary as it
  stood at that moment, and when the run ends every summary the run wrote is
  rewritten with the final one, so all of a run's summaries agree; a run that
  is killed leaves `ended_at: null` with its reason.
  A meter snapshot is the CLI's own `rate_limit_event`: with `--verbose`
  a call's message array carries one (CLI 2.1.296, measured 2026-10-10), so
  reading the account meter adds no model call. The agent, the judge
  (`scorers/judge.py`) and the guidance guard (`run_canary.run_leg`) are all
  made with `--verbose`, and each hands the events it printed to the run's
  telemetry, so every call the harness makes is read as it starts. Each
  snapshot is stored once, in the summary of the trial whose call printed
  it, under `usage.measured.meter: {snapshots, omitted}`: the first and the
  last event of each of that trial's agent, judge and guard CLI calls, each as `{at,
  at_source, call: {role, index}, info}` with `info` the event's
  `rate_limit_info` verbatim (whatever windows it names), `at` the timestamp
  of the message before it (`cli_message`), else the call's start
  (`call_start`: only an event before the call's first timestamped message
  has none), and `call` naming the entry of the same trial's `calls`. A
  trial keeps at most `run_eval.METER_MAX_SNAPSHOTS` (12), its first six and
  last six, and `omitted` counts the rest; an arm aggregate lists none. A
  run's meter series is therefore the union of its trial summaries. The
  run-level `meter` is `{snapshot_count, unreadable_events, not_sampled}`:
  how many snapshots the run recorded, how many events were not kept for
  their shape or size, and why the end of the run is not sampled (no call
  the harness makes prints a meter event after its last call has started,
  and no call is added to read the meter). Summaries written before the
  judge and the guard were made `--verbose` also name them there; the
  ingester still accepts those. The meter is read when a call (agent, judge
  or guard) starts and at no other time: the one real call observed (two API
  requests) printed one event, as it began. That is one observation, and a
  longer call may print more; the first and last of each call are kept
  either way. So there is no snapshot at run end, and the run's last
  snapshot is the start of its last call. `meter_delta` is the run's first
  snapshot against its last, per window both name, whatever a trial's own
  list dropped. It therefore leaves out the call that printed the last
  snapshot (its own spend) and every call after it, and any call before the
  first snapshot. Conditional judging (#371) makes a judged pair's judge
  calls all come after its last agent call, but each is read as it starts,
  so the delta now leaves out only the last judge call of the pair (the last
  trial's judge under `--judge always`, a single arm). Against the stand-in
  CLI, one fixture, two trials, a tie: 1 call from the last snapshot on, both
  by default and under `--judge always` (5 and 2 before the judge was read).
  A real judge call and a real guard call, each made through the harness's
  own code (`judge._run_judge_cli`, `run_canary.run_leg`, CLI 2.1.310,
  model `haiku`, 2026-10-26), each printed one event naming the
  `five_hour` and `seven_day` windows, and returned their answer unchanged. Such a held judge call is added to the
  summary of the trial it judges after that summary was written
  (`_RunTelemetry.amend`), which also adds the snapshots its events make to
  that summary's own (the same first-and-last rule and bounds), so that
  trial's `usage.measured.started_at` to `ended_at` spans the trials that ran
  in between and trial windows of a judged pair overlap;
  `meter_delta.uncovered: {calls_before_first_snapshot,
  calls_from_last_snapshot}` counts both, `report.md`'s meter line repeats
  the second, and a run of one agent call and no other has no delta at all. Each window
  is `{from, to, delta, window_reset}`, at most 16 of them, `delta`
  `null` when the window's reset time changed between them. It is
  account-wide, so it is this run's cost only if nothing else used the
  account, and it is labeled, never corrected. A run billed `api` has
  `meter: null`, `meter_delta: null` and `measured.meter: null` with the
  reason.
  `usage.measured` is `{scope, started_at, ended_at, calls, meter, agent,
  judge, guard, reasons}`. For one trial (`scope: trial`), `calls` lists each CLI
  call with its role, start, end, wall-clock `duration_ms` and outcome, and
  each role has `{calls, duration_ms, num_turns, cost_usd, cost_basis,
  models, cache_creation, reasons}`: `models` per model id with the four
  token counts and `cost_usd` from `modelUsage`, `cost_usd` the CLI's own
  list-price figure, and `cache_creation` the 5-minute and 1-hour cache-write
  split, which the CLI reports for the main loop only. `models` is whole or
  `null`: a result with no `modelUsage`, or with an entry that could not be
  read, leaves it `null` and never the models that happened to be readable.
  The judge's and the guard's results are read like the agent's: the judge's
  sink (`judge.collecting_models`) and the guard's (`run_canary.
  collecting_results`) keep `modelUsage`, `num_turns`, `usage` and
  `total_cost_usd` of each result, raw, whichever the result has. A figure a
  result lacks is `null` with the reason "the <role>'s CLI result does not
  carry it"; a call that ended without a result says so. A role with no call
  records zeros. An arm aggregate (`scope: arm`)
  sums its trials role by role, a `null` in any trial making that sum
  `null`, `models` included, and lists no calls. A trial with more than
  `run_eval.USAGE_MAX_CALLS` (32) calls keeps the role totals and
  `calls: null`. Calls queued by a trial that wrote no summary are dropped
  when the next trial starts, not given to it.
  The counts above bound how many entries a summary lists, not how large a
  snapshot's verbatim `info` is once indented, so `usage.measured` is also
  held to `run_eval.USAGE_MAX_BYTES` (32 KiB) as written: past it the trial
  keeps its first and last snapshot, then none (`omitted` counts them), then
  no call list. `meter_delta` is computed from the run's own record and is
  unaffected. `scripts/ingest_routine_results.py` accepts both blocks
  (together or not at all, so earlier summaries still ingest) and checks
  every value, not only every key: times are real and in order, a `source`
  is the flag or an environment variable the harness reads for that field
  with the value it implies, a role's `calls` matches the calls listed, a
  window's `delta` is its change, and a field the harness only ever writes
  as `null` must be `null`. The one free-form value is a snapshot's `info`,
  which is bounded. With every snapshot and call slot taken the two blocks
  measured 26,347 bytes pretty-printed, against the ingester's 64 KiB cap on
  a summary, which is unchanged. Over a both-arms, three-trial run (eight
  summaries, one event per call) they measured 38,840 bytes in all, 4,192
  to 5,076 each.
- **What's committed:** fixtures + summarized reports; raw transcripts
  gitignored.
- **Passive fleet-guidance counters:** every attempted real-work
  `subject: any` agent trial retains `guidance_violations` in its
  `summary.json`, whether selected with `--skill` or `--section`, including
  agent, scorer, and pre-agent refusals. Objective-only runs launch no agent
  trial and do not produce these counters. The detector is
  [`harness/guidance_violations.py`](harness/guidance_violations.py); it never
  executes candidate files. Schema version 1 carries `rules`, keyed by
  `unpinned_actions`, `event_input_in_run`, and `push_without_verification`.
  Each entry contains the stable `section_id`, `status` (`known` or
  `unknown`), `count` (integer or null), and `observed_count` (the observed
  lower bound when the total is unknown). No candidate text, command,
  path, commit, or branch is copied into this block.

  The section ids come from the registry's
  [`agents-md/eval-coverage.yml`](https://github.com/example-adopter/_agent-guidance/blob/main/agents-md/eval-coverage.yml):
  `pinning-github-actions`, `data-exposure-in-ci`, and
  `git-push-does-not-mean-commit-exists`, respectively. YAML is parsed with
  PyYAML nodes: workflow job-level reusable `uses`, workflow step `uses`
  and `run`, and composite-action step `uses` and `run`. Remote actions
  require a full 40-character hexadecimal SHA, or the lockfile form (ADR
  0021 in `_agent-guidance`): a step ref is also credited when
  `.github/workflows/actions.lock` keys it by the exact `uses` string with
  a `sha1-<40 lowercase hex>` commit and a `ref` equal to the tag after
  `@`, and that workflow's `workflows` list names it. A tag inside a local
  composite action is credited only when every workflow that reaches the
  composite through `$/<dir>` steps, directly or through other composites
  (cycles included), lists it; a reaching workflow absent from the
  lockfile's `workflows` map locks nothing, and a composite no workflow
  reaches gets no credit. Job-level reusable workflow refs never take
  lockfile credit. An unreadable, invalid or symlinked lockfile makes
  `unpinned_actions` unknown; the interpolation counter, which does not
  read it, stays known. Local `./`, `$/` and `docker://`
  references are exempt, and this account's `example-site-platform/.github/actions/`
  and `example-site-platform/.github/workflows/` references accept
  `vMAJOR.MINOR.PATCH` release tags. The interpolation
  counter counts direct `${{ inputs.* }}` and `${{ github.event.* }}`
  expression tokens in parsed `run` scalars; composed expressions and
  other exposure routes are outside its scope. Per-file occurrence
  multisets are compared against the prepared workspace immediately before
  the agent runs. Untouched inherited occurrences, comments, unrelated
  `uses`/`run` data keys, and harmless changes around an existing
  interpolation do not count; additional copies count separately.

  Bash tool calls are parsed with the existing Tree-sitter parser. A
  successful standalone literal `git push [-u|--set-upstream] REMOTE
  SHA:BRANCH` requires a later successful standalone
  `git merge-base --is-ancestor SHA REMOTE/BRANCH` for that same SHA and ref.
  The equivalent `refs/remotes/REMOTE/BRANCH` spelling also matches; remote
  and branch names remain case-sensitive. Abbreviated SHAs, revision
  expressions, unsupported check options, opaque executables, and unfamiliar
  Git subcommands stay unknown because the trace cannot establish what they
  verified.
  A successful literal branch push with no later successful check for that
  ref counts as an omission. A branch push followed by a candidate check
  has unknown status because this trace cannot bind the branch to its SHA.
  Failed pushes do not count. Success requires a uniquely paired subsequent
  tool result with `is_error: false`; shell compound commands, variable
  assignments (including assignment-only calls), background execution, wrappers,
  global git options, dynamic arguments, default pushes, subagent scope,
  or missing results are unknown. Unquoted glob, brace, and tilde expansion
  syntax is checked in Bash AST argument fragments before quote removal;
  quoted and escaped characters remain literal. `Task` and `Agent` delegation
  makes the counter unknown, even with no parent push or with visible child
  calls: the stored trace cannot prove complete child execution evidence,
  and a delegation result's prose does not supply that proof.
  Comments and heredoc data are not executed
  commands. Missing, capped, clipped, or redacted evidence is unknown, never
  a clean zero. Any uncertainty makes the push counter's `observed_count`
  zero: an uncertain call or omitted event could verify any observed push,
  so no minimum omission is proven. The trace marks changed input/output text with
  `input_incomplete`/`output_incomplete`. YAML parse failures, aliases,
  duplicate keys, symlinks, unreadable files, and bounds (1 MiB per file,
  2,000 matching files, 20,000 traversal entries or YAML nodes) likewise
  make the YAML totals unknown. A push count is known only when the trace
  explicitly has `complete: true` and `omitted_events: 0`; older traces
  without completeness remain unknown. Git metadata, profiles, and `node_modules`
  are excluded from traversal.

  Arm aggregates carry `aggregate.guidance_violations`, per rule `section_id`,
  `n`, `n_missing`, and `sum` (null when no total is known). Errored trials
  remain in these denominators when they supplied evidence. Single-trial,
  multi-trial, and guidance reports render the totals and unknown trial
  counts. These counters do not change objective scores, improvement gates,
  or graduation. Closing keywords from `git-practices` and model attribution
  are deferred: a closing keyword may be authorized, and an ordinary product
  mention does not establish attribution. This is a scoped measurement of
  observable rules, not a claim of complete fleet-guidance compliance.
- **Tool-call trace (#89):** `transcripts/raw.json` keeps only the CLI's
  result objects, so beside it the harness writes
  `transcripts/tool_trace.json` (`schema_version` 1): one event per tool call
  (`name`, an input summary such as a Bash command, `subagent`) and per tool
  result (`is_error`, `output_chars`, the head of the output), tagged with the
  CLI call (`call` 0 is the prompt, then each follow-up). Inputs are cut to 200
  characters, outputs to 300, and one trial's events to 64 KiB; events past
  the cap are counted in `omitted_events`. `calls` counts every attempted
  CLI invocation, including attempts with no usable tool evidence; those
  attempts retain their position in later events' `call` indices. `complete`
  is true only when every attempted call supplied a valid verbose array
  containing a result and the event cap omitted nothing. A timeout,
  undecodable output, invalid array shape, or nonverbose result object makes
  it false for the whole trial, even if a later call supplies valid evidence.
  Earlier observed events remain available on every error return. A follow-up
  blocked by a missing session id is not an attempted call and does not
  change the prior evidence's completeness. Every string is redacted whole
  before it is cut (`cli_json.redact`; a cut first could split a secret so
  no pattern matches the kept part, and a string over 1 MiB is not kept at
  all): the values of credential-named variables in the arm's environment,
  credential shapes (`gh*_`, `github_pat_`, `glpat-`, `sk-`, `sk_`/`rk_`
  `live`/`test`, `AIza`, `AKIA`, `xox*-`, `xapp-`, JWTs, any `BEGIN ...
  PRIVATE KEY` block including PGP, `Authorization:` values, URL userinfo,
  `curl -u user:pass`, `.netrc` `login`/`password`, `NAME=value` or
  `Name: value` where the name says token, secret, password, api-key,
  `-key`/`_key`, PAT, credential, auth or cookie), email addresses (to
  `<email>`) and absolute paths (the same patterns `failed_run_detail`
  applies). Residual risk: a secret with no recognizable name or shape, such
  as a PEM body without its BEGIN line or a base64-encoded token, is not
  detected; heuristics for those would mangle ordinary output. The file is
  gitignored like `raw.json` and never reaches `persistent/eval-results`, but eval.yml uploads
  it in the run's workflow artifact, which is public on this repository. That
  artifact is an allowlist (#289): `summary.json`, `report.md` and
  `tool_trace.json`; the unredacted `raw.json` stays on the runner. No
  objective scorer reads it; the passive fleet counter above does.
  `run_agent` returns it under `tool_trace` after any CLI attempt, including
  calls without tools, beside the unchanged `transcript` and `raw`. Tool-error
  metrics treat explicit incomplete traces as unknown; legacy traces retain
  their previous interpretation.

### Skill install path (corrected)

Claude Code auto-loads a skill from `.claude/skills/<name>/` only when
`SKILL.md` sits directly at that path. In the `acsi-agentskills` registry,
each skill ships as part of a *plugin*, with the actual skill content nested
one level deeper:

```
plugins/<plugin>/.claude-plugin/plugin.json
plugins/<plugin>/skills/<skill>/SKILL.md   <- this is what gets installed
```

The registry has shipped (and, mid-migration, may still contain a mix of)
two layouts for `<plugin>`:

- **Legacy, one skill per plugin:** `<plugin> == <skill>` — a plugin dir
  named after its single skill, e.g. `plugins/writing-adrs/skills/writing-adrs/`.
- **Bundle, many skills per plugin:** `<plugin>` is a bundle name distinct
  from any skill it contains, e.g. `plugins/acsi/skills/workflow-path-audit/`
  alongside other skills under that same `acsi` bundle.

Because the plugin/bundle directory name can't be assumed to equal the skill
name — and because example-site-platform's flat `skills/<skill>/` and example-site-a's
`.claude/skills/<skill>/` shapes need the same treatment (issue #63) —
resolution is not a glob hardcoded in `run_agent` any more. Each registry
gets a `layout` glob in [`harness/registries.yml`](harness/registries.yml)
(`plugins/*/skills/*/SKILL.md` for acsi-agentskills, `skills/*/SKILL.md` for
example-site-platform, `.claude/skills/*/SKILL.md` for example-site-a), and `run_agent`
substitutes the skill name for the placeholder segment immediately before
`SKILL.md`. It globs for the `SKILL.md` FILE itself, not the containing
directory, and takes that file's parent — so a skill directory that exists
but has no `SKILL.md` inside it (a stub left by a rename, a bundle
mid-migration) fails closed as `skill_not_found` rather than "installing"
whatever's actually in there. Matches are sorted and the first is used, so
resolution is deterministic even if a skill name were ever (mistakenly)
present under more than one plugin/bundle. The `with_skill` arm then copies
that resolved directory to `<workspace>/.claude/skills/<skill>/` — copying
the outer plugin/bundle directory instead would silently produce a workspace
where the skill never loads. `run_agent` fails loudly, naming the glob
pattern searched, if nothing matches.

## Scaling to the registry (2026-08-30)

One eval exists; the other ~30 registry skills have none. This section is the
method for closing that gap without a big-bang project: classify each skill to
the right instrument, mine fixtures from the incident record instead of
inventing tasks, and let process gates accrue coverage where the churn is.
The scale target is deliberately small — validation-gated skill iteration
(WikiSkill, arXiv:2608.27454) ran on 10–40-task validation splits, so per
skill here a handful of fixtures is in-spec, not a compromise.

### Four instruments, one harness

Not every skill takes the same eval, and some take none. Classify first:

- **A. Workspace transforms** — correctness is decidable from the resulting
  files alone. The `workflow-path-audit` shape applies unchanged: seed +
  objective checks + thin judge. Candidates: none open.
  (`github-actions-sha-pinning` was also Class A; it has already shipped —
  see "Backfill order" below.) `rename-pdfs` graduated out of this list:
  covered by `evals/rename-pdfs/` (issue #82). `post-failure-comment`
  graduated out of this list: covered by `evals/post-failure-comment/`
  (issue #86). `writing-adrs` graduated out of this list: covered by
  `evals/writing-adrs/` (issue #80). `review-bash-ci-reliability` graduated
  out of this list: covered by `evals/review-bash-ci-reliability/` (issue #74).
  `pdf-ocr-audit` graduated out of this list: covered by
  [`evals/pdf-ocr-audit/`](evals/pdf-ocr-audit/)
  ([issue #83](https://github.com/Adam-S-Daniel/skills-evals/issues/83)).
  The consumer-bump half of `platform-release-and-bump` is covered by
  [`evals/platform-release-and-bump/`](evals/platform-release-and-bump/)
  ([issue #93](https://github.com/Adam-S-Daniel/skills-evals/issues/93)):
  the pinned platform verifier checks the release refs and caller parity, while
  the offline GitHub replay records whether a write was attempted. The live
  release and deployment steps are outside this fixture.
  `admin-config-render` graduated out of this list: covered by
  [`evals/admin-config-render/`](evals/admin-config-render/)
  ([issue #87](https://github.com/Adam-S-Daniel/skills-evals/issues/87)).
  It scores a real render: three `command_succeeds` checks run a seed script
  that drives the vendored Ruby renderer and parses its output. The scorer's
  fixed `PATH` needs `ruby` at `/usr/bin` or `/bin`, and a missing Ruby fails
  the checks rather than skipping them.
  `browser-testing` is covered by
  [`evals/browser-testing/`](evals/browser-testing/)
  ([issue #92](https://github.com/Adam-S-Daniel/skills-evals/issues/92)).
  Frozen AST commands check spec conventions; actual browser correctness is
  judged and remains unmeasured by this fixture.
  `code-quality` graduated out of this list: covered by
  [`evals/code-quality/`](evals/code-quality/)
  ([issue #88](https://github.com/Adam-S-Daniel/skills-evals/issues/88)).
  Its hook checks pair ADR 0007's Bash AST check with a `command_succeeds`
  probe kept inline in `fixture.yaml`, out of the agent's sight, that runs
  the hook in a scratch Git repository with fake Go tools; the probe needs
  `bash`, `git`, `tar` and `mktemp` at `/usr/bin` or `/bin`, not `node`.
- **B. Diagnosis/triage** — correctness = reaching a recorded root cause.
  The hermetic trick is a fake `gh` on the seed workspace's `PATH` serving
  canned JSON captured from the real incident (the same substitution move as
  `$CLAUDE_BIN`/`test/fake-claude`, applied to the tool the skill consults).
  The verdict is scored objectively against the postmortem; the judge grades
  reasoning quality only. `cms-stuck-pr-triage` graduated out of this list:
  covered by `evals/cms-stuck-pr-triage/` (issue #84), which is also where
  the shared `harness/fakes/gh` every other Class B fixture reuses came
  from. `debug-github-workflows` has four covered slices. The first is
  [`evals/debug-github-workflows/wrong-branch/`](evals/debug-github-workflows/wrong-branch/)
  (issue [#76](https://github.com/Adam-S-Daniel/skills-evals/issues/76)). Main
  invokes a missing test module while an unmerged branch already fixes its
  workflow. The investigation-only prompt asks the agent to explain the cause
  and next step without editing files. A fixture-owned setup builds an offline
  origin with fetchable main and fix refs. Seven checks require an actual log
  read, a comparison read through gh or through a git diff, show, or patch log
  of the fix branch (git commands are recorded under `.git/`), exact
  branch/action/error tokens in the reply, and preservation of
  project/instrument files. The independent judge assesses
  whether those tokens express the correct cause and merge direction; token
  coverage alone is not semantic correctness. No A/B calibration or measured
  improvement is claimed. Three more slices cover the skill's other failure
  patterns, each with a test module under `test/issues/test_issue_76_*.py`:
  [`swallowed-failure/`](evals/debug-github-workflows/swallowed-failure/)
  (a green run whose `mapfile < <(gh ...)` swallowed a failed listing; two
  `command_succeeds` probes run the final script against a scripted gh, one
  where the listing works and one where it fails),
  [`git-identity/`](evals/debug-github-workflows/git-identity/) (a bare exit
  128 from a test script that commits with no git identity; probes run the
  final suite with git forbidden to guess an identity, and with a broken
  changelog to show the tests still run) and
  [`secret-name/`](evals/debug-github-workflows/secret-name/) (a workflow
  reads a secret the repository does not have, and gh's own hint points at
  `github.token`; checks require the secret listing and the existing name).
  None of them has an A/B calibration yet.
  [`consumer-repo-provisioning`](https://github.com/example-adopter/skills-evals/blob/main/evals/consumer-repo-provisioning/fixture.yaml)
  now has its first Class B fixture:
  a consumer startup failure with a required Actions secret missing and the
  secret listing inaccessible ([issue #91](https://github.com/Adam-S-Daniel/skills-evals/issues/91)).
  The extra-permission and variable-misconfiguration scenarios remain open.
  [`ci-watcher-loops`](https://github.com/example-adopter/skills-evals/blob/main/evals/ci-watcher-loops/fixture.yaml)
  now covers offline dispatch, returned-run-ID polling, the final conclusion,
  poll bounds, and restraint ([issue #89](https://github.com/Adam-S-Daniel/skills-evals/issues/89)),
  with two follow-up status turns so a background watch can report later.
  Candidates: `editorial-label-audit`, `skills-doctor`.
- **C. Judgment/style** — the judge carries the load; keep the few decidable
  bits objective (banned buzzwords absent, required sections present), and
  prefer pairwise preference against committed reference samples over
  absolute rubric scores. Expect noise; run more trials. Candidates:
  `acsi-writing-style`. `finding-unknowns` now has one Class C fixture at
  [`evals/finding-unknowns/`](evals/finding-unknowns/) for a pre-build
  unknowns pass ([issue #78](https://github.com/Adam-S-Daniel/skills-evals/issues/78));
  during-build and post-build behavior remains uncovered. The pairwise schema
  and scorer exist, but [`harness/run_eval.py`](harness/run_eval.py) currently
  rejects a judged pairwise run before either arm with
  `judge_mode_unsupported` (exit 2). Its comment attributes the missing
  runner wiring to [issue #97](https://github.com/Adam-S-Daniel/skills-evals/issues/97),
  which is closed; this implementation gap remains.

  A Class C fixture says so in its `judge:` block: `mode: pairwise` plus
  `references:` ({name, path} entries, relative to the fixture dir — a path
  that climbs out of it is refused, because a yardstick from elsewhere on
  the machine is neither reviewable nor reproducible). The judge is shown
  the writing under test beside those references, blind: every draft is
  normalised to the same line shape (a hard-wrapped reference beside an
  unwrapped reply is separable without reading a word), fenced with a
  per-call nonce so nothing inside a draft can pose as the prompt, and
  shuffled systematically per trial so no draft keeps a slot. The score IS
  the rank, 1 = best; `weights:` is an absolute-mode idea and is rejected
  here rather than half-honoured. `timeout_s:` (default 120) bounds the
  call, and a timeout is recorded as a judge error, never as a score.
  `harness/scorers/judge.py` documents the returned shape.
- **D. Wrong instrument entirely** — record the decision in the
  non-coverage table below instead of leaving a silent gap.

Reference-heavy skills (`aws-bootstrap`, `preview-environments`, and
`consumer-repo-provisioning`'s tables) fail by going stale, not by teaching a
bad procedure. Their instrument is a **freshness lint in the registry's own
CI** — every file, workflow, and secret name a SKILL.md cites still exists
where it points — not an A/B rollout here.

### Fixtures are mined, not invented

The fleet's incident record is a pre-scored task set: every dated incident in
the fleet guidance, every root-cause writeup in example-site-platform's
`docs/VERSION-HISTORY.md`, every postmortem issue. Per fixture:

1. **Seed** — reconstruct the minimal pre-incident workspace. Scrub it:
   `example.com`/`example.net` only, no real addresses — this repo is public
   and fixtures are committed.
2. **Prompt** — what the operator actually asked at the time.
3. **Objective check** — the recorded root cause or fix shape.

The expected A/B delta comes free: a real agent already missed this once, so
the ceiling-effect risk is pre-tested, and `with_skill` catching what the
baseline plausibly misses is exactly the delta the skill exists to buy.

### Real-work fixtures from merged pull requests (2026-10-06)

Merged fleet pull requests that carry their own tests are the second mined
source: the task is what was asked, the checker is the PR's tests, red on
its base and green on its merge. Part of
[#65](https://github.com/Adam-S-Daniel/skills-evals/issues/65) and
[#98](https://github.com/Adam-S-Daniel/skills-evals/issues/98).
`scripts/mine_real_work.py` is the read-only first stage:

- **mine** enumerates `_agent-guidance`'s `repos.yml` `cron_coverage.fleet`
  under every owner in `sync.yml`'s `SYNC_OWNERS` (a name no owner resolves
  is an error), lists merged pull requests, drops bot and `on-hold` ones and
  writes `candidates.json` to a path outside this repo. It reads GitHub
  through REST only (`gh api`): the eval routine runs it in a Claude Code
  cloud session, which refuses every GraphQL request with HTTP 403, and
  `gh repo view`, `gh pr list` and `gh pr diff` all use GraphQL (s27,
  2026-10-06, the first scaffold-mode run). REST has no
  `closingIssuesReferences`, so `closing_issues` is the body's closing
  keywords read as GitHub reads them, each number confirmed as an issue of
  the same repository, for a pull request into the default branch; over
  the 40-PR-per-repo window it matched GraphQL on all 132 candidates, and an
  issue linked only by hand in the sidebar is what it cannot see;
- **prepare** builds the red (base plus the merge's tests) and green trees
  with `git archive`, and runs nothing;
- **admit** reads JUnit XML from both runs. FAIL_TO_PASS is what fails red
  and passes green. A test that fails on the merge too, or fails red on a
  missing package or the network, is environmental and excluded
  ([_agent-guidance#82](https://github.com/Adam-S-Daniel/_agent-guidance/pull/82)'s pattern); a red failure on a name the fix invented
  (`is not a function`, `ImportError`, `AttributeError`) rejects the
  candidate unless the task text names it ([cms-platform#430](https://github.com/Adam-S-Daniel/cms-platform/pull/430)'s pattern). Both
  runs must finish under 60 s.

The owner's answers (2026-10-06) to the questions this stage depends on,
recorded verbatim. Q1, Q2, Q5 and Q7 are recorded in "Real-work fixture
decisions (the owner, 2026-10-06)".

- **Q3, sources:** "Public repos + PR bodies (Recommended)". Only public
  fleet repos are mined and the PR body is the task text; no private repos,
  no mail. Each candidate carries an `answer_leak` flag (the body quotes a
  line the pull request added), a warning for review, not a rejection.
- **Q4, one fixture for many subjects:** "Subject-agnostic (Recommended)".
  The skill or guidance subject is named at run time, so a candidate records
  no subject.
- **Q6, where the scaffolder runs:** "Routine + own gate (Recommended)". Its
  model call runs in the ADR 0010 routine, and scaffold PRs get their own
  review gate, separate from #71's three improvement PRs.
  How it is fired: "New fire-workflow input (Recommended)".
  `routine-eval-fire.yml` takes `mode: scaffold` and a `candidate` miner key
  (`OWNER__REPO__PR`), validates both before the bearer is in any step's
  env, and sends only `mode`, `run_id` and `candidate`.
- **Q8, training cutoffs:** "Keep, report apart (Recommended)". Each
  candidate records its merge date, so pre- and post-cutoff fixtures are
  reported separately rather than excluded.

### Lessons from real sessions (2026-10-26, ADR 0015)

The third mined source is the owner's own Claude Code sessions, and it is mined
only on his own computer ([ADR 0015](docs/decisions/0015-mine-real-sessions-only-on-the-owners-own-computer.md)).
`scripts/mine_sessions.py` keeps real-work sessions, turns corrections,
redirections, stated rules, interruptions, repeated tool failures, red CI
after a push and unfinished endings into short redacted **lesson records**,
and routes them into **proposal files**: `agent-check` first when a git hook
could enforce the lesson, else `skill-edit`, `new-skill`, `guidance-edit`,
`memory-note` or `fixture-candidate`. A fixture candidate is a concrete
failure tied to a skill or guidance section, and the input a fixture writer
on that machine expands (`mine_sessions.py locate <session hash>` finds the
transcript). Records and proposals land on acsi-agentskills-private's
`persistent/lessons` branch; that repo's `lessons/README.md` holds both
schemas. Tests: `test/issues/test_issue_session_miner.py`, invented
transcripts only.

Daily run, from WSL, after a dry run first (`PRIVACY_DENYLIST_FILE` is a file
outside every repo; `WIN_HOME` is the Windows profile as a WSL path, which
also locates the desktop app's data; without it the script asks `cmd.exe`):

```bash
export PRIVACY_DENYLIST_FILE="$HOME/.config/privacy-denylist.txt"
export WIN_HOME=/mnt/c/Users/<user>
bash ~/repos/skills-evals/scripts/run_session_miner.sh --dry-run
bash ~/repos/skills-evals/scripts/run_session_miner.sh
```

Scheduled like the usage census (`evals/usage/CENSUS.md`, Option B or C), for
example in WSL cron:

```cron
17 7 * * * PRIVACY_DENYLIST_FILE="$HOME/.config/privacy-denylist.txt" WIN_HOME=/mnt/c/Users/<user> bash "$HOME/repos/skills-evals/scripts/run_session_miner.sh" >> "$HOME/.cache/session-miner.log" 2>&1
```

The script clones skills-evals `main` fresh, so the local checkout's branch
does not matter; it pushes with the private clone's remote and the git
credentials WSL already has, and moves its state file
(`~/.local/state/session-miner/state.json`) into place only after the push is
verified. `--file-issues` is off until the owner has rated a sample of records.

### The scaffolder and its gate

Q6 puts the scaffolder's model call in the ADR 0010 routine and gives
scaffold pull requests their own gate. The model writes only what needs
judgment: the task text (the issue minus the sections that describe the
fix, and minus its dates and version numbers),
`interface_strings:` and the checker selection (`argv`,
`fail_to_pass`, `pass_to_pass`, `deps:`), as a JSON spec. A date or version
number stays only where dropping it would very significantly disadvantage
the agent relative to the one that did the original work (the owner,
2026-10-08: "Omit dates and version numbers from eval prompts unless doing
so will very significantly disadvantage the agent relative to the agent
that performed the work on which the eval is based"). Everything else is
deterministic, in [`scripts/scaffold_real_work.py`](scripts/scaffold_real_work.py):

- **build** turns one miner candidate and the spec into
  `evals/real-work/<repo name>-<pr>/` (fixture ids are at most 72 characters)
  in the shape of the first three fixtures: `seed/` is the base tree from
  `git archive`, with the agent context stripped (`seed_prep.strip_agent_context`), the evaluated paths
  removed (`.claude`, `skills.lock`, `agents-md`, `skills`,
  `.claude-plugin`), and every symlink, every file over 100 KiB but the
  lockfile, `AGENTS.md`, the checker's own files, and regular `.py` source
  through 1 MiB, and every e2e spec but the checker's own
  trimmed; `checker/` is the merge commit's copy of each selected test
  file; `solution.patch` is the rest of the diff, leaving out what the seed
  no longer holds. Retaining bounded Python source preserves its fix patch;
  the static check and gate enforce the same 1 MiB source cap, with the
  fixture file-count, total-size, and patch caps unchanged.
  `issue-before-fix.txt` and its three times come from one
  `gh api graphql` read, picking the body revision and title from before
  the first commit by the rules above. It is the one GraphQL read left on
  the routine's path, because REST has no body revisions
  (`userContentEdits`); where GraphQL is refused (a Claude Code cloud
  session) `build` stops and names the gap rather than use the issue's
  current body. The routine gets it from the fire payload instead
  (**snapshot** below). The fixture is `draft: true` and
  `subject: any`, and its header says the prompt was written by a model.
  It reads the clone with `git archive`, `git cat-file` and `git diff` and
  never writes it.
- **check** validates with the harness's own code: the fixture loads as
  `run_eval.py` loads it, the seed passes `seed_prep`'s guard, the size
  caps hold, and the answer-leak lint finds no hit. `--run` also scores the
  seed (every `fail_to_pass` test fails, every `pass_to_pass` test passes)
  and the seed with `solution.patch` (all pass), each within 60 s (Q1).
- **gate** is the review gate. The routine never opens a pull request: it
  pushes `claude/scaffold-<fixture id>`, and `routine-scaffold-pushed.yml`
  (no permissions, runs nothing) hands off to `routine-scaffold-gate.yml`,
  which runs the default branch's copy on `workflow_run`, the same split as
  the results ingest. Its `validate` job (`contents: read`) reads the branch
  through git plumbing only and rejects it unless it only adds regular files
  under the one `evals/real-work/<fixture id>/` its name names (an
  executable bit only under `seed/` and `checker/`), within the caps, as a
  draft with no `setup:` or `env:` that passes `check` without `--run`,
  and whose `checker/` files are the merge commit's own, compared with
  trusted Git bytes. Nothing from the branch runs in the gate. Its
  `pull-request` job then has the roster App open the pull request ready,
  labeled `eval-scaffold`, with auto-merge on (the owner, 2026-10-26: no human in
  the loop; ADR 0003's 2026-10-26 addendum), after a `disarm` job has turned
  auto-merge off for every push. Without the App token it opens a draft and
  arms nothing. It never approves or pushes. The label and the repository setting that lets Actions open
  pull requests come from repo-settings (the owner, 2026-10-06: "Yes, via
  repo-settings (Recommended)"), and the routine is fired in scaffold mode
  by a new `routine-eval-fire.yml` input (the owner, 2026-10-06: "New
  fire-workflow input (Recommended)"); both land in their own PRs.
- **snapshot** is the issue snapshot read on its own. A Claude Code cloud
  session, where the routine runs, refuses GitHub GraphQL, and body
  revisions have no REST read, so `routine-eval-fire.yml` computes the
  snapshot on Actions with its read-only token before the fire (the pull
  request and its closing issue through REST, as the miner reads them; the
  revisions through GraphQL) and sends it
  as the payload's `issue_snapshot` (null for no closing issue), and the
  routine passes it to `build --issue-snapshot`, which validates it
  strictly instead of reading GitHub (the owner, 2026-10-06: "Fire workflow
  precomputes (Recommended)"). The gate recomputes it with the same code,
  for the pull request the fixture's header names (which must be its id's),
  and rejects the branch unless `issue-before-fix.txt` is the same bytes and
  the three times are equal, so the routine cannot alter the issue text.
  Both the fire and the gate pin the repository to the fleet before any
  read: its owner in `SYNC_OWNERS` and its name in `cron_coverage.fleet`,
  from `_agent-guidance`'s default branch as the miner reads them, and its
  GitHub `full_name` equal to the name given, so a renamed repository's
  redirect is refused too (the owner, 2026-10-06: "Pin to fleet owners
  (Recommended)"). A pull request that closes several issues is refused at
  the fire (the owner, 2026-10-06: "Keep refusing (Recommended)"). If the chosen
  revision is deleted, or the issue passes the 100 revisions one query
  reads, between the fire and the gate, the gate rejects the branch: fail
  closed (the owner, 2026-10-06: "Yes, fail closed (Recommended)"). A payload
  over the fire API's 65,536-character `text` cap is refused, never
  truncated; the snapshot is JSON-encoded twice inside it, so an issue
  whose body is at or near GitHub's own 65,536-character limit cannot be
  scaffolded this way.

Nothing outside the fixture's directory needs an edit: ci.yml and
`scripts/dev_setup.sh` install each fixture's own `deps:`
(`scaffold_real_work.py deps-dirs`), and `test` scores it red and green on
the pull request, which the App opened so that its `pull_request` run
starts with no approval. `CHECKER_BLOBS` in
`test/issues/test_issue_real_work_fixtures.py` keeps the hand-checked blob
ids of the first five fixtures; the gate checks every later one. A merged
fixture is `draft: true` until `scaffold_real_work.py promote`, run daily by
`routine-fixture-supply.yml`, finds two ingested runs of its committed
content with both arms and no errored arm, and opens an auto-merging pull
request that sets `draft: false` and adds it to `evals/scheduled.yml`. The
same workflow mines the fleet daily and fires scaffold mode for up to three
new candidates (`scaffold_real_work.py pick`). The scaffold branches' head ref,
`claude/scaffold-`, is in the miner's bot list, so a merged scaffold is never
mined back.

The first slice of
[#98](https://github.com/Adam-S-Daniel/skills-evals/issues/98) is an offline
authoring aid in
[`scripts/scaffold_real_work.py`](scripts/scaffold_real_work.py), without a
model, GitHub, or issue writes:

```text
python3 scripts/scaffold_real_work.py guidance [section-id] \
  --guidance PATH [--dest evals/guidance]
```

`--guidance` is required. With no id, the command selects every `status: gap`
row in
[`agents-md/eval-coverage.yml`](https://github.com/example-adopter/_agent-guidance/blob/main/agents-md/eval-coverage.yml);
with an id, it requires that exact row to be a GAP. It validates all ids and
statuses, reads each Markdown heading's exact extent with
[`harness/guidance.py`](harness/guidance.py), selects dated entries from
[`docs/guidance-impact.md`](https://github.com/example-adopter/_agent-guidance/blob/main/docs/guidance-impact.md)
by exact id, and preflights the full batch before writing. `_agent-guidance`
is read-only. The destination cannot overlap that checkout or pass through
symlinks. Existing target directories are preserved and counted as skipped.

Each new `evals/guidance/<id>/` contains `fixture.yaml` with explicit TODO
prompt and rationale fields and a comment to choose the instrument, rebuild
the seed, and author nonempty objective checks; it also has `section.md`,
matching `guidance-impact.md`, a minimal `seed/README.md` TODO, and
`tracking.json` (schema version 1) with the
section provenance, lexical incident dates, proposed title and branch, issue
links, marker, and reviewer instructions. The arms name `section` and `none`
and have empty checks by design, so objective-only refuses to report a score
until an author supplies real checks. Draft fixtures remain blocked from agent
arms by the harness. This is tracking data only: it does not create a branch,
pull request, or issue.

[`test/issues/test_issue_98.py`](test/issues/test_issue_98.py) is discovered
by [`test/run_tests.py`](test/run_tests.py)'s `test_issue_*.py` loader. The
required `test` job in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
runs that loader.

### Harness-wide rules (promoted from the first fixture)

The `workflow-path-audit` fixture learned these the hard way; they are policy
for every fixture, not folklore in one file's comments:

- **Arms follow the roster, and the judge follows the arm.** A fixture pins
  neither model without a stated reason; the judge is one tier above the arm
  it grades ([ADR 0013](docs/decisions/0013-judge-policy.md), which
  supersedes "arms on a pinned mid-tier model, judge pinned strong"). A
  ceiling-effect arm is still signal-free.
- **Anything a script can decide is never left to the judge**, and the
  rubric caps a dimension when a decidable fact fails (the judge once scored
  a 9 on an arm the objective column failed).
- **Correctness outweighs guardrails** in judge weights — equal-weighted
  restraint quietly rewards the do-nothing arm.
- **3–8 small fixtures per skill beat one big one.** Coverage definition:
  every claim in the skill's body has at least one fixture that would fail
  without it.
- **N≥3 trials per arm before believing a delta**, and reports carry the
  trial count. The CLI has no temperature flag (see Open decisions), so
  trials are the mitigation.
- **Hermetic, always** — no network, no wall-clock; canned payloads and fake
  binaries.
  A fixture puts a fake binary in front of the real one with an `env:`
  block (`PATH: "$WORKSPACE/bin:$PATH"` — `${WORKSPACE}` reads the same;
  `$WORKSPACE` expands to the arm's temp workspace), and reads what the
  agent did off the log the fake writes — `file_matches` over the log,
  `transcript_matches` over the final reply.
  `windows-elevation-from-wsl` is the first fixture in that shape;
  `cms-stuck-pr-triage` is the second, and its `gh` is shared from
  `harness/fakes/`.
- **A check whose evidence is a log says so** (`require_present: true` on
  `file_matches`). A `must_not_match` over a file that does not exist
  PASSES, so "the agent attempted no write" is otherwise indistinguishable
  from "the agent never ran the tool", and deleting the log becomes a way
  to score restraint.

### Coverage accrues by process, not by project

- **Graduation gate:** a skill enters the registry with at least one fixture
  here, and the graduation PR's definition of done includes a green
  `with_skill` run.
- **Touch gate:** a PR that edits an existing SKILL.md either runs that
  skill's eval or adds its first fixture.

Both gates belong in the registry's own contributor guidance (acsi-agentskills'
`AGENTS.md` repo-specific additions and the skill-creator flow); this file is
the reference they point at.

Backfill order, by usage × decidability × incident material:
`cms-stuck-pr-triage` (builds the fake-`gh` machinery every Class B eval
reuses), `debug-github-workflows` (four slices have shipped, above), then `acsi-writing-style` as the
Class C pilot. (`github-actions-sha-pinning` — fully decidable, including the
example-site-platform tag carve-out — has shipped: `evals/github-actions-sha-pinning/`.
`review-bash-ci-reliability`, which headed this list because the incident
record practically is its fixture set, has shipped too:
`evals/review-bash-ci-reliability/`.)

### Deliberate non-coverage

A row here is a decision with a reason; an absent eval without a row is a
gap. (Mirrors the fleet convention that "deliberately out" and "not adopted
yet" must stay distinguishable.)

| Skill | Decision | Reason |
|---|---|---|
| `test-canary` | no A/B | internal canary; no propagation arm loads it, so its delivery probe is not built and no open issue tracks it (closed [#17](https://github.com/Adam-S-Daniel/skills-evals/issues/17) built the acsi-agentskills arms only) |
| `wj-next-break` | skip | wall-clock/calendar-bound; low value to freeze |
| `launch-top-level-claude-session` (renamed from `launch-wsl-claude-session` on 2026-09-25, [acsi-agentskills PR 27](https://github.com/Adam-S-Daniel/adam-agentskills/pull/27)), `sync-skills`, `sync-cc-settings-between-wsl-and-windows`, `migrate-claude-memory`, `compare-pdfpairs`, `ocr-pdfs` | defer | machine-bound (WSL/WPF/browser surfaces); faking the surface costs more than the churn justifies today |
| `windows-elevation-from-wsl` | Class B, covered | the one machine-bound skill whose surface is cheap to fake: `evals/windows-elevation-from-wsl/seed/bin/powershell.exe` answers reads, denies writes, refuses dodges, and logs; the fixture's `env:` block puts it on the arm's `PATH` |
| `fastmail` bundle | defer | credentialed live service; a fixture may not carry real accounts, and a faked Fastmail is a harness project of its own |
| `aws-bootstrap`, `preview-environments` | freshness lint | staleness is the failure mode, not procedure quality |

### Budget

The owner's 2026-10-06 decision, after the merged
[weekly routine PR](https://github.com/Adam-S-Daniel/skills-evals/pull/331),
retired `eval.yml`'s Tuesday schedule. The scheduled path is the weekly
routine: it plans the reviewed ready list in
[`evals/scheduled.yml`](evals/scheduled.yml) and fires eval-mode routine runs
through Actions. Routine runs use subscription usage; Actions separately
validates and ingests their results as local exhibits. They do not use the
manual API workflow's OIDC exchanges, roster jobs, badge publishing, or API
workspace spending ceiling. Routine results are not badge input; see
[ADR 0010](docs/decisions/0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md).

Manual dispatch of [`eval.yml`](.github/workflows/eval.yml) still runs one
fixture through the API-backed workflow. The following credentials, roster,
and publication guarantees describe that manual path only. [ADR 0008](docs/decisions/0008-run-ready-fixtures-on-the-weekly-schedule.md)
records the reviewed fixture list and its manual API workflow behavior. Two
eval legs run concurrently at most, with separate credential exchanges and
success artifacts; serialized publishers build each fixture's badge against
accumulated history. Roster jobs run once per manual workflow run.

One manual API dispatch selects one fixture. At the workflow's estimate of
$0.30–0.90 per skill fixture, that is its estimated cost; actual usage varies,
and the API workspace spend limit is the hard ceiling for this path. Adding a
fixture to the weekly routine's reviewed list is a subscription-usage
decision, not an API-spend estimate. Automated budget enforcement is the
weekly allowance guard: [`evals/budget.yml`](evals/budget.yml) holds the owner's
30% cap, enforced in the run, before each fire and in the weekly plan
([docs/how-it-works.md](docs/how-it-works.md#the-weekly-allowance-guard)).
Rotation, monthly sweeps, model products and trial changes remain deferred
under [#68](https://github.com/Adam-S-Daniel/skills-evals/issues/68).

### `claude plugin eval` (assessed 2026-08-30)

The CLI's native eval harness was assessed against this design. It has
first-class with/without-baseline arms and a stable `aggregate-result.json`
report, but: it is early-access and gated for this account (probing prints
"currently in early access"); the graders assessed then were regex / tool-use
/ file-exists / LLM-judge / baseline. That assessment did not establish a way
to run `scorers/objective.py`'s changeset replays as a grader; substituting
the assessed graders would force decidable
facts back onto regex or the judge, the exact anti-pattern the rules above
forbid; and its case layout is per-plugin where this harness is centralized.

Decision: **monitor, don't wrap.** Re-evaluate when it is both un-gated for
this account and has grown a run-a-script grader; until then this harness
stays the system of record. If `results/` is ever restructured, mirror its
report schema to keep a future migration cheap.

**Re-read 2026-10-04 ([#193](https://github.com/Adam-S-Daniel/skills-evals/issues/193)),
from `claude plugin eval --help` on Claude Code 2.1.289, run with a throwaway
`HOME` and no login.** Only the help text was read; no eval was run, so
everything below is "read in `--help`", not measured.

- *Early access:* the help no longer says so. Whether this account is still
  gated is **not verified**; probing it needs a login this note does not use
  (measured below: it is not gated).
- *Graders:* the help names LLM and baseline graders as the paid ones (with
  "free graders" beside them), a `with-only` marker that includes
  `tool_used: Skill`, and a `scaffold_script` that runs author-supplied bash
  as you, off unless `--scaffold` is passed. It does not name a grader that
  runs a script and scores its result. This does not establish whether custom
  code graders are supported; the `scaffold_script` is described as setup,
  not scoring.
- *Layout:* still per plugin: cases live in `<eval dir>/**/case.yaml` (or
  `prompt.md` plus `graders/*.md`) under the plugin, results in
  `<plugin>/<dir>/results/`, with `--eval-dir` and a manifest
  `experimental.evals` to move it. This harness keeps its fixtures centralized
  under `evals/<skill>/`.
- *New since 2026-08-30, as read:* an `init` subcommand that authors a suite
  by interview; with/without-baseline ablation on by default whenever a plugin
  resolves; an OS sandbox around shell tools and MCP mocks (`--mocks`);
  `--runs`, `--concurrency` (1 to 8), `--max-cost-usd`; `--json` output and an
  HTML `--report`, which the CLI also **publishes to claude.ai by default when
  the account supports it** (`--no-publish` opts out). This harness
  publishes its results to its own `persistent/eval-results` branch, so a
  wrapper would have to decide that default deliberately.
- *Not assessed:* `/skill-doctor`, the other tool in the same issue.

**Decision unchanged: monitor, don't wrap.** The criterion that would flip it
was a run-a-script grader, and `--help` does not show one; the layout is still
per plugin; and the gate is unverified either way. The help also describes
plugin targets, and says nothing about whether a `subject: guidance` fixture
(an `AGENTS.md` section, not a plugin) could run under it. Re-read `--help` on
the next CLI bump, and run it once if a grader that scores a script appears.

**Measured 2026-10-04 ([#232](https://github.com/Adam-S-Daniel/skills-evals/issues/232#issuecomment-5977656310)),
one real run on Claude Code 2.1.289** with the operator's own login (API-key
environment variables unset) and `--runs 1 --no-publish --max-cost-usd 1
--trust-plugin`, against a scratch `git archive` copy of the
`acsi-coding-anywhere` plugin that has no `.git` and so no push path.

- *Gate:* the account is **not gated**. The command ran end to end with no
  early-access message.
- *Graders:* the binary's case validation lists `type:` as exactly
  `regex | tool_order | tool_used | file_exists | llm | baseline`. There is
  still **no script or exit-code grader**, so the flip criterion above is not
  met.
- *Output:* `result.json` with `schemaVersion` 1, per-arm graders (each with
  `scored` and `withOnly` flags) and `aggregates.delta` / `meanDelta`, plus a
  local HTML report.
- *Cost:* **$0.175** for one hand-written with/without case (about $0.10 with
  the plugin, $0.08 without; 43 s in all).
- *Result:* Δ = 0. The case was an easy one (a CI step piping `npm test`
  through `tee` and `tail` before `./deploy.sh`, asked to be reviewed for CI
  reliability problems), graded by a `pipefail|PIPESTATUS` regex plus a
  with-only `tool_used: Skill`. Both arms scored 1.0 because the baseline
  already knows `pipefail`; a lift needs cases a baseline fails.

**Decision updated:** the gate is verified ungated, so that half of the flip
criterion is met; the other half (a grader that runs a script and scores its
result) is not. **Monitor, don't wrap** stays for behavior scoring: this
harness remains the system of record, and the layout and `subject: guidance`
gaps above are unchanged. One narrow use is worth allowing, **local only**: a
cheap trigger check (did the skill fire?) using `claude plugin eval` with a
`tool_used: Skill` grader and `--no-publish`. It is run by hand on a durable
machine, never in CI, and its output is not written to `results/` or
`persistent/eval-results`. Part of #232.

## Model roster (2026-09-04, #67; redesigned 2026-09-13, #147)

The harness's model choices were literals: an arm pinned in each fixture, a
judge beside it, a preflight model in `eval.yml`. Nothing in the repo noticed
when a model shipped or retired, and the first symptom would have been a run
against a model that no longer exists.

**The roster the harness RUNS ON is `evals/roster.yml`, committed on `main`.**
That is [ADR 0001](docs/decisions/0001-roster-trusted-on-main.md), and it is
the whole shape of the feature: `main` is ruleset-protected and pull-request
only, so an arm, the judge, the preflight model or a `catalogue_seen` entry
cannot appear there or vanish from there without a reviewed commit.
`run_eval.select_models` reads that file and nothing else for the roster rung
of its precedence (`--model` > the fixture's own pin > `evals/roster.yml` >
error). Since [ADR 0013](docs/decisions/0013-judge-policy.md) the judge it
picks from that file also depends on the tier ladder in
`evals/roster-policy.yml`, committed and pull-request-only like the roster.

**`harness/roster.py` computes a PROPOSAL, not the running set.** It is still
**a pure function over files** — already-parsed documents and a frozen `now`
in, a roster dict out; no network, no clock, no environment — which is what
makes the whole policy testable at the granularity of one threshold. The
single network call in the feature is `scripts/refresh_models.py`; the usage
side is `scripts/model_usage_census.py`, which runs on a durable machine (a CI
runner has no transcripts), scheduled by the owner via
`scripts/publish_usage_census.sh` (see `evals/usage/CENSUS.md`; it used to ride
on the Tier-3 account-store Routine, retired 2026-09-28, see
`evals/propagation/ROUTINE.md`, now HISTORY). Its output carries a `proposal` block —
`{status: "same"|"differs", changes: [...]}` — computed against the committed
file, with every seat change carrying its numerator, its denominator and the
share they make, in words.

**What each store is trusted for**, stated once because the previous design's
defects all came from leaving it unstated:

| Store | Trusted for | Written by |
| --- | --- | --- |
| `evals/roster.yml` (on `main`) | the running set, and the observation history (`catalogue_seen`) | a human, through a reviewed pull request |
| the Models API response | availability, within the run that fetched it | Anthropic |
| `usage/latest.json` (on `persistent/eval-results`) | **nothing.** It can shape a proposal and nothing else | a job on another machine |
| `roster/latest.json` (on `persistent/eval-results`) | nothing. An exhibit for the explorer, read by no decision | this workflow |

**The proposal flow.** When the computed roster differs from the committed one,
`eval.yml` renders it with `scripts/render_roster_yaml.py` and admits the
rendered file against the committed-roster contract. A valid proposal is pushed
as one commit on the bot-owned branch `roster/proposal` (recreated from `main`
every run — never a shared branch), with one tracking issue carrying the
rendered summary and compare link. An invalid proposal instead leaves the
branch and compare link untouched and creates or updates a “needs review”
tracking issue with its admission failures. Under `roster_mode: proposal` a
human opens the pull request for a valid proposal and merges it after CI;
under `roster_mode: auto` the roster App opens it and arms auto-merge on a
clean probe ([ADR 0003](docs/decisions/0003-roster-merges-automatically.md)).
Nothing in CI writes `evals/roster.yml` directly. When the computed roster
matches, the tracking issue is closed.

**Which roster the eval runs on** ([ADR 0004](docs/decisions/0004-eval-runs-on-the-roster-its-run-merged.md),
2026-09-30). The committed one, unless this run armed its own proposal and
it merged within the run's bounded wait (the `roster-wait` job, up to 30
minutes): then the `eval` job takes `evals/roster.yml`, and only that file,
from the verified merge commit, while its code stays at the run's own
commit. Either way the run summary says which roster it used and why.

**Who is an arm (2026-09-22, the owner's decision).** Two rules, the second
subordinate to the first. **(1) Usage seats:** every available model at or
above `arm_enter_usage_pct` of rankable, attributable census turns over
`arm_enter_window_weeks` is an arm, with its share in its reason. **(2)
Newest per QUALIFYING tier:** in a tier rule 1 already seated somebody in,
the newest available model past the cooling-off (`cooling_off_days`, 0 since
2026-09-27 — see below) is an arm too, and its reason
says so in words, naming the qualifying share it rides on. A tier no model of
which clears the entry bar gets no arm from rule 2, however new its newest
model is; that model is listed under `excluded` saying exactly that.

Rule 2 used to read "newest in its tier" across every tier on the ladder.
That seated the newest haiku and the newest fable on a census showing the
fleet ran 6.3% and 3.0% of its turns on them — a four-arm roster, at four
arms' worth of spend per fixture, two of whose arms measured tiers nobody
uses. The ladder decides *capability order*; it was never evidence that a
tier is worth measuring, and the census already is.

**The no-census fallback is deliberately NOT restricted.** With no usable
usage there is no usage-qualified tier at all, and a roster must not be
empty — so wherever the enter window carries no usable evidence (any of
`_census_verdict`'s eight verdicts, or a fresh census whose enter window
alone fails one of the ranked-usage floors) rule 2 reverts to newest per tier
across every tier. When the census itself is unusable, each fallback reason
names its degradation. A usable census whose enter window alone fails a
ranked-usage floor gets the bare newest-per-tier reason. A usable census
that simply names no model at the entry bar is a different thing: that is
evidence, and it says no tier qualifies, so the only seats are previous arms
held over the exit bar — and with none, `main()` refuses to publish a roster
with no arms (rc 3) and the committed one stands. Rule 2 added no threshold
of its own: it reads rule 1's entry bar, and every number stays in
`evals/roster-policy.yml`.

**Vendor defaults override both rules where they resolve (2026-09-27, the owner's
decision, #202, ADR 0002).** Evals are valued going forward only, so the
roster follows the vendor's default version of each family rather than the
fleet's usage of individual versions. `scripts/probe_model_defaults.py` asks
the Claude Code CLI CI has just installed at the npm latest, with no
credential and a scrubbed environment, which model each family alias on the
ladder resolves to — the `model` of the first `system/init` event, after
which the CLI's process group is killed (#203). The CLI makes unauthenticated
TLS connections of its own around that event; no credential exists in the
probe's environment, so nothing can be billed or leaked, and alias resolution
is built into the binary rather than fetched, so it does not depend on the
network. A word
the CLI echoes back is not an alias and is skipped. `roster.py` takes the
reported id when it is an available model (a dated snapshot collapsed onto its
alias stands for it) of the alias's own family. In such a tier usage has
one job left — putting the tier on the roster (a model clearing the entry bar,
another previous arm in it that is still seated this run, or the no-census
fallback) — and the seat goes to the default with no cooling-off. The
default's own previous seat is not one of those: with none of them, a default
that is a previous arm gets rule 3's exit check (held on a stale or thin
census, held at or above the exit bar over the exit window, else retired),
measured on its TIER's combined share — every model of its family, the same
numerators over the same denominator — so a tier between the exit and entry
bars is never left with no arm by a version change (#203 round 2). "Its tier"
is its family: a default governs the models whose family word is its alias,
and a peer family in the same rung keeps rules 1 to 3. A
superseded model gets no usage seat; a superseded previous arm is retired once
`superseded_exit_weeks` complete ISO weeks have passed since the default's
`created_at` and its share over the most recent such weeks is under the exit
bar (a stale or too-thin census holds it, and the exit-window rule no longer
applies to it; the shipped buffer has been 0 since 2026-10-25, so it retires at once). A model newer than the default earns no new seat, and a
previous arm newer than it gets rule 3's exit check instead of retiring on
sight — held under its dated id too (#203 probe round 5, R5-3): a previous
arm published under `<base>-YYYYMMDD` is still a previous arm once `<base>`
appears in the catalogue. A failed probe freezes its family for that run
(#203 probe round 1): a family whose alias the probe recorded an error for,
answered with a model of the wrong tier or family (#203 probe round 3), or
answered with a model with no `created_at` to start a predecessor's buffer
from (`no-created-at`, #203 probe round 5, R5-1) — or every family the
probe did not skip, when the document is unreadable, junk, answered for
no ladder alias, or is the workflow's stand-in for a probe that exited
non-zero (`probe-exited`) — keeps every previous arm's seat and retires
nothing but a model gone from the Models API. While it still holds a seat
the Models API lists it gets no new seat at all (#203 probe round 3); only
a family that would otherwise vanish is seated, by the usage entry bar or,
with no usable enter window (no fresh census, or one whose enter window is
under the ranked-usage floors), as the newest in its tier as with no probe;
with a usable one and nothing clearing the bar it gets no seat, the same
outcome a clean run gives. It is
named in the published `defaults_failed`, on the summary's first lines and
in a `::warning::` that keeps the tracking issue open (the proposal step
runs unless the workflow is cancelled; its `gh issue` writes warn rather
than fail, its `git push` does not). An answer this run's catalogue does
not otherwise match (not available, an ambiguous snapshot) is not a
probe failure, but it FREEZES the family exactly the same way (#203 probe
round 7, R7-1 — the governing guarantee, first stated in round 5 as R5-2:
a single run whose probe answer is a failure or a mismatch changes no seat
a clean run would not, now structural rather than resting on a guessed
"effective default"); only the wording differs, and `defaults_mismatched`
says "does not match this run's catalogue", loudly whether it holds a
listed seat or seats by usage — its own fixed `::warning::` (#203 probe
round 8, R8-1) and, on "same", the tracking issue kept open exactly as a
failure keeps it, with a title that says "probe failed" only when a probe
genuinely failed, "did not match this run's catalogue" for a mismatch, or
both when both classes are present in the same run. Falling back to
rules 1 and 2 instead flipped seats on one bad week. The freeze is per run; nothing is
carried to the next (a carried-defaults block with an expiry briefly stood
here and is gone). With no defaults document at all the roster is
byte-for-byte the one computed without it.
The committed roster keeps no `defaults` block; the published one records
the CLI version, `probed_at`, and the resolved and unresolved aliases.
The preflight keeps its cooling-off, and the judge rule is unchanged.

**The cooling-off is 0 and the harness is unpinned (2026-09-27, the owner's
decision, #202, ADR 0002's update).** `cooling_off_days: 0` makes every model
with a `created_at` "past" it, so rule 2 and the preflight pick take the
newest model at once; the reasons say "no cooling-off applies" rather than
"past the 0-day cooling-off", and the machinery stays so a positive value
restores it. The CI install always takes the npm latest (#203: never a CLI
already on the runner, a plain `MAJOR.MINOR.PATCH` only, and a `claude` on
PATH reporting any other version fails the step), still ahead of any
credential; since a pin no longer names the
version, each run records it — the step summary, and in every arm's
`summary.json` `harness.version` beside `models_used` (the agent result's
`modelUsage` keys) and `judge_models_used` (the judge's), all three present on
error paths too, and one `- Harness:` line in `report.md`. The propagation
record carries each arm's `harness_version` and init-event `model`.

Five properties are load-bearing and should survive any rework:

1. **No model id in the machinery.** Tier comes from the family word in a
   model's own id, matched against a ladder in `evals/roster-policy.yml`. A
   model that ships after this was written needs no edit anywhere.
   `evals/roster.yml` is the one DATA file admitted to that guard, for the
   same reason a fixture's own `model:` pin is.
2. **Every entry carries its reason in words**, and every degradation says
   which degradation it was — absent census, stale census, future-dated
   census, census present but empty over the window. A roster that falls back
   silently is indistinguishable from one that did not need to. A proposed
   change carries, in addition, the numerator and denominator its share was
   taken over: a percentage with no counts behind it is unfalsifiable from
   the outside.
3. **No evidence is not evidence.** Nothing proposes retiring an arm except
   leaving the Models API or measurably falling under the exit bar. A missing
   or stale census proposes nothing.
4. **Two previous-roster states, not three.** The published `previous_state`
   is `compared` or `none` (nothing to compare against). The third,
   `unavailable`, is gone: a committed roster that is present and unreadable
   is a defect in this repository rather than a fact about an unprotected
   branch, so `compute_roster` raises `TrustedRosterUnreadable`, `main()`
   exits 5, and nothing is published at all.
5. **A since-retired model counts by catalogue HISTORY, not id shape.** The
   roster carries `catalogue_seen`: every model id the Models API has been
   observed to list, with the date it was last seen. A model that has left
   the API but that this harness has actually observed before still counts in
   the usage denominator — real work that happened does not stop counting
   just because the model is gone. An earlier approach inferred this from the
   id's SHAPE instead; that was withdrawn because shape cannot distinguish a
   since-retired real model from a plausibly-named proxy alias, and it missed
   the pre-#67 legacy id shape entirely.

   An entry's `last_seen` is refreshed to today whenever the Models API
   actually returns that id, and the entry is dropped once `last_seen` is
   older than `catalogue_seen_max_age_days`. **That is the only way an entry
   leaves.** Both the exemptions that used to sit beside it, and both length
   caps, are deleted — see below.

   **A refresh is only a PROPOSAL once the committed date is stale.** The
   COMMITTED `last_seen` (in `evals/roster.yml`) only needs re-reviewing at
   the cadence the ageing window actually cares about, not on every run: a
   refresh is routine — recorded, not proposed — while the committed date is
   younger than half of `catalogue_seen_max_age_days`, and becomes material
   once it is at least that old. Proposing on every observation made the
   weekly tracking issue nothing but date churn (#200); the half-window cut
   still refreshes the committed history at least every ~90 days, which
   keeps a departed model's eviction clock (above) from running down more
   than half its allowance before a human last confirmed the date.

   **Ageing out is not a repair.** It ends a plant's future effect, but it
   does not undo a retirement the plant already caused: a model whose
   measured share fabricated usage pushed under the exit bar is proposed
   for retirement, and once that proposal is merged the model is no longer
   a previous arm, so the exit bar no longer applies to it and a dozen
   turns a week never re-seats it. It returns by clearing the ENTRY bar, by
   being the newest in a tier that some model of ITS OWN clears the entry
   bar in, or by hand.

   **Migration.** `evals/roster.yml` was seeded by hand with an empty
   `catalogue_seen`, because the only history that existed lived on
   `persistent/eval-results` and that branch is not trusted to supply one. So the first
   run after ADR 0001 landed behaves exactly like a genuine first run — the
   same migration `catalogue_seen`'s own introduction made — and a model
   retired before this harness observes it directly is unattributable until
   a run sees it.

**What was deleted, and why it is named here rather than left beside the
redesign.** Every one of these existed to approximate a trusted history over
an untrusted `previous.json`, and each is deleted with the measurement that
shows it now decides nothing (ADR 0001, decision 4):

- **The anchored denominator, the retirement veto keyed on it, and the
  count-only notice beside it.** They measured how much of a window's
  denominator rested on the previous roster; with that roster reviewed and
  committed, the fraction measures how much of the usage belongs to models
  this repository has a merged record of, and a veto on it refuses precisely
  the honest case.
- **The fold-relation question the ageing rule asked, and BOTH ageing
  exemptions.** They existed to disbelieve a `last_seen` date, which a
  trusted record does not need.
- **Both 500-entry length caps, the 10,000-entry carry ceiling, the tiering
  they evicted by, and `_clean_previous_arms`' never-evict clause and
  two-list return.** They bounded an unbounded public input; that input is
  bounded by review now, and every remaining effect was on honest data.

ADR 0001's decision 4 names each of them by identifier. Nothing else in the
tree does, deliberately: a name that no longer resolves is a name a reader
will go looking for.

**What stays**, and it is the shorter list: schema validation of every
untrusted field with a named one-line skip, the census freshness window and
its degradation reasons, the `judge.is_arm` refusal (now also a lint over the
committed file, run by `ci.yml`), and a SIZE BOUND on the census document —
`CENSUS_MAX_KEYS` and `CENSUS_MAX_BYTES` — so the one input still written by
another machine cannot exhaust the runner.

Thresholds, and the reasoning behind the numbers, belong in an ADR —
[#73](https://github.com/Adam-S-Daniel/skills-evals/issues/73). See the
README's "Model roster" section for the precedence rule and the census's
public-output contract.
## Guidance subject (2026-09-05, skills-evals#97)

**What it measures.** A skill is loaded when it is invoked; a guidance section
is loaded in every session of every fleet repo. The question that decides
whether a section stays is therefore not "does it teach the behavior" but
"does the full guidance WITH it beat the full guidance WITHOUT it", by enough
to pay for its bytes. So a `subject: guidance` fixture names a `section:` (an
id from `_agent-guidance`'s `agents-md/eval-coverage.yml`, stable across
heading rewordings) and its arms carry a delivery `mode:`. Five exist:
`none` (the control: no guidance, but a DECOY token of its own — see the
guard below), `stub` (`agents-md/stub.md`, what a
repo carries inline), `section` (the section's extent — its `##` heading
through the line before the next `##`, `###` children included — with its
file's intro prepended), `full` (the whole delivered corpus: `base.md`, plus
the section's own file when it is an opt-in `sections/*.md`), and
`full-minus-section` (that corpus with the extent removed). The default pair
`section`/`none` asks whether the section teaches; the declared
`ablation: [full, full-minus-section]` pair asks what it is worth in situ,
including any lost-in-the-middle effect of a 56 KB file. [ADR 0012](docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md)
(not built yet) makes the in-place pair the default. Extents are located
with a real markdown parse (`markdown-it-py`, pinned exact) using the same
arithmetic as `_agent-guidance`'s own `scripts/check-guidance-coverage.js`, so
the manifest's `bytes` column and the delivered payload can never disagree; a
regex would end an extent on the `## ` inside one of `base.md`'s fenced
blocks.

**The delivery path.** The fleet does not import its guidance from a repo
file — `fleet-memory.sh` writes a marked block into user memory
(`$CLAUDE_CONFIG_DIR/CLAUDE.md`, default `~/.claude/CLAUDE.md`), read once per
session. An eval of the guidance therefore delivers the same way: a fresh
scratch dir per arm, `CLAUDE_CONFIG_DIR` pointing at it, and the *real* hook
run with `FLEET_GUIDANCE_PAYLOAD` — running the real hook rather than
imitating it is the point, since a harness that reimplements the delivery path
measures the imitation. Guidance arms invoke the CLI with
`--setting-sources user,project`; skill arms keep `project` and are otherwise
untouched. Whether the CLI honours `CLAUDE_CONFIG_DIR` for *memory*
specifically has not been measured against a live CLI yet, so `--delivery
project` exists as the documented fallback (same hook, pointed at the
workspace, read as project memory) and every summary records which was used —
`delivery: user` or `delivery: project`. Either way the guard, not this
choice, decides whether an arm counts. The agent's environment is an
allowlist, not the ambient one: `PATH`, `HOME`, `TMPDIR`,
`CLAUDE_CONFIG_DIR`, every `ANTHROPIC_*` variable, and the fixture's own
`env:` — `harness/propagation/arms.py` measured 16 vs 35 loaded skills between
a scrubbed and an ambient environment, and an arm that inherits the operator's
own settings is not measuring the guidance.

Every CLI session the harness starts — skill and guidance arms, the judge,
the canary and guard probes — also gets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
SET last at the spawn (`guidance.CLI_FORCED_ENV`, applied by
`guidance.cli_child_env`), never merely inherited, so neither the ambient
environment nor a fixture's `env:` can turn it back on. Skill arms and the
judge keep the real `HOME` on a workstation, because the interactive login
lives there (ADR 0002, decision 4); with auto-memory on, a local trial wrote
fixture-derived notes into the operator's own
`~/.claude/projects/<workspace>/memory/` (observed 2026-10-05).

**Isolation is by flags, because the login pins HOME.** Measured on CLI
2.1.289 with no credential copied: a scratch `HOME`, a scratch
`CLAUDE_CONFIG_DIR` and `--bare` each answer "Not logged in". With the real
HOME, `--setting-sources project` alone still loaded the account's claude.ai
MCP connectors (mail, drive, GitHub) and wrote a transcript under
`~/.claude/projects/`. So:

| Spawn | Flags beyond its own |
|---|---|
| arm (`run_eval.run_agent`) | `--setting-sources project` (guidance: `user,project`), `--settings <sandbox, read-deny and agent-config-deny JSON>`, `--disallowedTools WebFetch,WebSearch` and `--no-chrome` (`run_eval.arm_isolation_flags`, every turn), `--strict-mcp-config`; `--no-session-persistence` only with no `followups:` |
| judge (`judge._run_judge_cli`), proposal (`propose_skill_edit`), eval.yml preflight | `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence` |
| canary/guard leg (`run_canary.run_leg`) | `--strict-mcp-config`; `--no-session-persistence` unless the leg has its own scratch `CLAUDE_CONFIG_DIR` |
| anything through local_eval's guard launcher | `--strict-mcp-config`, and `--setting-sources project` when argv names none |

A follow-up turn `--resume`s, and a non-persisted session cannot be resumed
("No conversation found"), so a multi-turn arm persists; when it ends, the
`~/.claude/projects/<munged workspace>` directory it created (the CLI's name:
every character outside `[A-Za-z0-9]` becomes `-`) is moved to
`$XDG_STATE_HOME/skills-evals/sessions/`. A directory that existed before the
arm, one past the CLI's 200-character truncation, and one inside a guidance
arm's own scratch are never touched. The judge's empty setting source means
the CI judge no longer loads this checkout's `CLAUDE.md`/`AGENTS.md` or the
fleet-memory SessionStart hook: that is the isolation, not a regression.
What none of this stops: managed settings, the CLI's bundled skills and
writes to `~/.claude.json`. An arm's own reads of the real HOME are fenced
in separately (below).

**Agent arms have no route to GitHub** ([ADR 0011](docs/decisions/0011-sandbox-agent-arm-network.md)).
A real-work seed is a public repository's pre-fix tree, and the merged fix is
one `git clone` away; the empty `GH_TOKEN`/`GITHUB_TOKEN` stop nothing a
public repository serves. Every agent arm, skill or guidance, and every
follow-up turn, runs with `--settings` carrying `run_eval.arm_sandbox_settings()`:
Claude Code's sandbox on, `failIfUnavailable` (a CLI that cannot start it
exits 1 and the arm fails with `sandbox_unavailable`, never runs with a
network), `allowUnsandboxedCommands: false` (no `dangerouslyDisableSandbox`
retry), `strictAllowlist` with no allowed domains, and `deniedDomains` naming
`github.com`, `githubusercontent.com`, `codeload.github.com`, the fleet's
deployed sites (`example-site-a`, `example-site-b`, which serve built fixes)
and `cdn.jsdelivr.net`. The sandbox covers Bash and its children only, so
`--disallowedTools WebFetch,WebSearch` removes the two web tools. Nothing is
written into the workspace: `seed_guard` still refuses any `.claude/`, and
no scoring check sees a harness file. The judge, the guard and canary probes,
`deps:`/`setup:` and objective commands are not sandboxed this way.

**Agent arms cannot read the answer key on disk either** (ADR 0011's reads
addendum). The same `--settings` denies reads of this checkout (a worktree
resolves, through `git rev-parse --git-common-dir`, to its main clone), the
directory holding that clone and its siblings (`~/repos` on a workstation,
where example-site-platform `main` holds the merged fixes), every registry and
guidance checkout the run was given (`run_eval.run_checkouts`), the run's
results directory, every `--read-deny` directory (a wrapper's whole output
tree: local_eval's earlier trials, propose_skill_edit's baseline run and
patch) and the harness's session archive, the harness's other
directories under TMPDIR (other arms' workspaces and scratch profiles, and
`workspace_git`'s private `trusted-git-` metadata copies), and
the real HOME and Claude Code profiles (`~/.claude`, an inherited `CLAUDE_CONFIG_DIR`):
`sandbox.filesystem.denyRead` for Bash and its children, and the same paths
as `Read(//<abs>/**)` deny rules for the Read tool. For commands,
`allowRead` re-opens only git's global configuration (so `git commit` keeps
its identity) and PATH directories under HOME with a `bin`'s sibling `lib`
(toolchains such as `~/.local/bin`), never one at or around a checkout. For
the Read tool, a skill arm's HOME and profile are denied structurally around
its own `~/.claude/projects/<munged workspace>`, where the CLI saves large
tool outputs the agent reads back: complement patterns deny every other
name at each level, sessions created after the settings included; a
symlink there leading out to a system or PATH directory (a GitHub runner's
`~/.ghcup`) is left out of those patterns rather than denied, and one that
also leads to a denied path fails the arm with `read_rules_unsafe`. A workspace under a denied path (TMPDIR inside
HOME, say) fails the arm with `workspace_read_denied` before the CLI starts.

On Linux the read fences also cover alternate mounts of the root or HOME
filesystem, discovered from `/proc/self/mountinfo` by device number,
filesystem type, mount root and the longest HOME mount prefix. WSL drive
mounts and root/HOME aliases under `/mnt` deny `/mnt` whole, closing WSLg's
second view of Linux and Windows-side sibling clones. Other aliases, including
custom drive mountpoints, are denied at their mount roots. Canonical self-binds
such as `/tmp` on `/tmp` and HOME on itself stay available; malformed or
unreadable Linux mount metadata refuses the arm with `read_rules_unsafe`.
Other platforms add no mount fences.

Every alias is denied **whole**: one `denyRead` entry and the
`Read(//<alias>)`/`Read(//<alias>/**)` pair, with no rule and no `allowRead`
carve-out inside it. A carve-out per Windows PATH directory made a skill
arm's `--settings` 60,680 bytes on a WSL workstation, against the 64 KiB cap.
Instead the arm never needs a Windows-side executable: `run_agent` drops
every PATH entry under an alias (spelled there, or reached through a
symlink) from the arm's environment, a skill arm's and a guidance arm's
`env_override` alike (`path_without_aliases`), and builds the settings from
that PATH. An arm whose `bash` or `git` is found only under an alias fails
with `toolchain_under_alias` rather than reopen it. The Linux carve-outs
under HOME refuse any directory that is, holds or lies inside a denied path
other than HOME itself: a checkout or the directory holding the clone, the
results, the archive, a profile, an alias. On this WSL workstation a skill
arm's settings are now 12,695 bytes.

The named temporary-store inventory under `TMPDIR` is explicit in
[`HARNESS_TEMP_PREFIXES`](harness/run_eval.py). Both existing stores (`sandbox.filesystem.denyRead`)
and later allocations (`Read` prefix rules) are denied, preserving only the
arm's own workspace. The inventory includes explicit parent directories:
`dest`, an output parent, or a fake CLI's `LOG_DIR` can itself be `TMPDIR`.

| Denied prefix | Allocator |
|---|---|
| `workspace-` | [harness/run_eval.py](harness/run_eval.py) materialization and [scripts/local_eval.py](scripts/local_eval.py) |
| `skills-evals-` | [harness/run_eval.py](harness/run_eval.py) guidance arms (`ARM_WORKSPACE_PREFIX`) |
| `guidance-bridge-canary-` | [harness/run_canary.py](harness/run_canary.py) |
| `propagation-` | [harness/run_propagation.py](harness/run_propagation.py), including `propagation-selftest-` |
| `scoring-seed-`, `deps-python-`, `deps-bundler-`, `deps-cache-` | [harness/seed_prep.py](harness/seed_prep.py) |
| `objective-repo-tests-` | [harness/scorers/repo_tests.py](harness/scorers/repo_tests.py) |
| `objective-command-` | [harness/scorers/commands.py](harness/scorers/commands.py) |
| `local-eval-guard-` | [scripts/local_eval.py](scripts/local_eval.py) |
| `sink-mutation-` | Reserved existing sink-mutation scratch prefix |
| `trusted-git-` | [harness/workspace_git.py](harness/workspace_git.py) private metadata, allocated with `mkdir` |
| `scaffold-`, `scaffold-context-`, `.scaffold-` | [scripts/scaffold_real_work.py](scripts/scaffold_real_work.py) candidate, context Git stores, and destination staging |
| `claude-probe-home-` | [scripts/probe_model_defaults.py](scripts/probe_model_defaults.py) isolated CLI profile |
| `skill-edit-guard-`, `propose-skill-edit-` | [scripts/propose_skill_edit.py](scripts/propose_skill_edit.py); [scripts/propose_guidance_edit.py](scripts/propose_guidance_edit.py) under `propose-skill-edit-guidance-` |
| `scoring-guidance-`, `scoring-skill-` | [harness/run_eval.py](harness/run_eval.py) objective-only scoring copies |
| `mine-real-work-` | [scripts/mine_real_work.py](scripts/mine_real_work.py) atomic output staging file |
| `.gh-label-`, `.gh-timeline-` | [harness/fakes/gh](harness/fakes/gh) atomic local state files |
| `usage-census-` | [scripts/publish_usage_census.sh](scripts/publish_usage_census.sh) scratch repositories |

The only tempfile allocations without a prefix in this inventory are
`TemporaryFile` handles, which have no named store an arm can open, and
[`run_eval._archive_session_dir`](harness/run_eval.py)'s dynamic session-name prefix beneath the
already-denied session archive. A Python AST regression inventories named
allocations, including the extensionless fake CLI and explicit parents;
a Bash AST regression checks the census allocation. The prefix coverage
regression checks command denies for existing stores and file-tool denies
for future names.

Alias classification wins when deduplication finds that a profile or HOME
also names an alias: that mountpoint remains denied whole, with no profile
exception or HOME carve-out. PATH's physical alias check resolves each
original spelling before interpreting `..` after a symlink. Mount-table
read and decode errors refuse the arm as `read_rules_unsafe`.

The mount table and PATH are parameters (`mountinfo=`, `path_env=`),
defaulting to `host_mountinfo()` and `os.environ` only at the outermost
call. `SKILLS_EVALS_MOUNTINFO` names a mountinfo-format file that stands in
for `/proc/self/mountinfo` (an empty one names no alias): every test that
runs an arm sets it and an explicit PATH, so no result depends on the host's
mounts or PATH. The shared [test/arm_test_env.py](test/arm_test_env.py) module fixture covers the
legacy runner and remaining arm-running issue modules; install helpers also
set both locally, so a poisoned parent inside a test cannot override them.
Tests measuring particular mounts keep their own mountinfo fixtures, and
explicit child environment mappings carry the fixture too. See the
[reads addendum and live evidence](docs/decisions/0011-sandbox-agent-arm-network.md#addendum-reads-2026-10-07)
and [mount fixture regressions](test/issues/test_issue_arm_read_isolation.py).

A live WSL probe with CLI 2.1.293 read an answer-key patch through WSLg
using both Bash and Read before these fences. With the whole-alias deny,
Bash got `No such file or directory` and Read refused it; the arm's PATH
held no `/mnt` entry, and workspace, Python, Node and git still worked.
Unix sockets were blocked in both runs by the existing
`allowAllUnixSockets: false`, `allowUnixSockets: []` settings: Linux seccomp
refused `AF_UNIX` socket creation with `EPERM` before connection, closing
Docker and WSL interop. No additional `/run` fence was needed.

**Nothing else outside the sandbox runs for an arm** (ADR 0011's hardening
addendum). Hooks run unconfined, so the agent may not write its workspace's
`.claude/` (settings, hooks, skills, agents, commands) or the profile it
loads as user settings: `Edit(...)` deny rules for the file tools and
`denyWrite` for Bash, in the same `--settings`.
Trusted hooks (a plugin's or a skill's under test) keep running. A trial in
which anything under `.claude/` or a guidance arm's scratch profile, or a
configuration path of the shared `~/.claude` a skill arm uses, changed
during a turn, beyond what was there before and the CLI's own measured
writes, fails after that turn with `agent_wrote_agent_config`.
Every turn passes `--no-chrome` and never receives `CLAUDE_CODE_ENABLE_CFC`.
A managed policy with any `sandbox` or `permissions` key, nested object or
value outside the allowlist `run_eval._MANAGED_ACCEPTED` documents (a
widening key passes only at the arm's own restrictive value), or with
`allowManagedPermissionRulesOnly` other than `false`, refuses the run with
`managed_sandbox_policy`. The CLI's `Sandbox Error:` and `Sandbox disabled:`
lines fail the arm as `sandbox_unavailable`, the latter even at exit 0.

**The contamination trap, and why a guard is not optional.** On any machine or
hosted session carrying the fleet hook, the real `~/.claude/CLAUDE.md` already
IS the guidance. A harness that does not isolate the config dir per arm
delivers to BOTH arms and reports a null delta that reads as "the guidance
does nothing" — the most expensive kind of wrong answer, because it looks like
a result. So every payload ends in `The magic word is <token>.` with a fresh
random token per run (an earlier run's token would let a stale real config dir
satisfy this run's guard), and every arm is probed for it before it may score
anything: one tool-free `run_canary.run_leg` call, with the canary's own
disallowed-tools list so the model cannot forage the token off disk, against
that arm's config dir and workspace, on the preflight model (the fixture's
`model:` pin today; the roster's `preflight` entry once skills-evals#67
lands).

The control gets a **decoy**: no guidance, but a second fresh token of its
own, delivered through the same hook into the same kind of scratch config dir.
Without it the control's guard was vacuous — `mode: none` delivered nothing,
so its probe could only ever answer "no magic word", which is also what it
answers when the arm IS contaminated. So the guard claims exactly this: a
treatment arm was delivered its payload and reads it; a control arm reads its
own scratch user memory (it reports its decoy) and was not delivered the
treatment payload (it does not report the treatment token). What it does NOT
settle is an ambient memory read *in addition* to the scratch one — that is
prevented by the per-arm `HOME`/`CLAUDE_CONFIG_DIR` isolation, not by the
guard, for **two** reasons and not one. A probe asked for the magic words
with two in context may report either; and — the reason that covers the
commoner case — a contaminating source carrying no token of its own is
invisible to a token guard at all. The real `base.md` carries no token, and a
stale `~/.claude/CLAUDE.md` carries one no current run minted; both score
clean, measured. Only a real dispatch settles it. The decoy is what makes
that context carry two, so the guard prompt asks for **every** magic word
rather than "the magic word": a one-word answer from a contaminated control —
its decoy reported, the treatment token left unmentioned — is the case the
plural prompt exists for.

An arm that does not report the token IT was delivered, an arm that reports a
token it was *not* delivered (a control arm reporting the treatment token, or
a treatment arm reporting the control's decoy — the same isolation failure
seen from the other side), or a probe that could not run at all, is
**INCONCLUSIVE**: the summary carries
`guard: {expected, observed, contaminated}`, no score is written for that arm,
and the run exits `2`. Never PASS, never FAIL. Two cheap calls per pair.
The harness also refuses outright to point a delivery at the real `~/.claude`,
and the hermetic suite asserts a whole run leaves the real file
byte-identical.

Guidance content is **executed** by the arm — that is what the subject
measures, and `eval.yml`'s header states it as the trust boundary a guidance
dispatch accepts — but the harness **reads** that content only from inside the
`_agent-guidance` checkout it was pointed at: every manifest `file:` is
resolved with its symlinks followed and refused if it lands outside the
checkout root. The two are different boundaries and the second is not implied
by the first: a manifest row naming `../OUTSIDE_SECRET.md` was read,
delivered, and written verbatim into
`results/guidance/<key>/<ts>/<arm>/transcripts/raw.json`, which `main` pushes
to the public `persistent/eval-results` branch — so a row could publish any file the
runner can read.

### Section fixtures, verdicts and removal (2026-10-26, skills-evals#124)

**Layout.** A section's behavioral fixtures live at
`evals/guidance/<section id>/<name>/fixture.yaml`, with `subject: guidance`,
`section: <id>` and the section id as their parent directory. A directory
whose name starts with `_` (`_delivery`) is not a behavioral fixture and no
gate reads it. Each declares four arms, `with_guidance_section` (`section`),
`without_guidance` (`none`), `with_guidance_full` (`full`) and
`with_guidance_full_minus_section` (`full-minus-section`), plus
`ablation: [full, full-minus-section]`. `eval.yml`'s `--arm both` therefore
runs both pairs in one fire, at four agent runs per fire, so the agent gets
180 s and the guard 60 s to fit the 45-minute job;
`run_eval.py <fixture> --ablation` still runs the in-situ pair alone, as arms
`ablation_full` and `ablation_full_minus_section`. Results go to
`results/guidance/<section id>/<ts>/<arm>/summary.json`, beside the section's
other fixtures, so each guidance summary records `fixture_name`,
`fixture_digest` (the git-staged digest), `guidance_digests` (`section`: the
sha256 of the extent, `rest`: of the corpus without it, from
`guidance.content_digests`) and `judge_conditional`. A fixture's badge key is
its path without `evals/`; `make_badge.py` reads only run dirs whose summary
names that fixture, on the in-situ pair.

**The verdict.** `harness/gates.py` `guidance_verdict` reuses the skill
machinery (run-level pooling, looks at 5, 10 and 20 runs, the same
classes) twice per section: the in-situ pair (`full` vs
`full-minus-section`) and the alone pair (`section` vs `none`), classified by
each arm's `mode`, never its name. A run pools when its `section` digest is
the section's extent on main and its fixture digest matches; the `rest`
digest is the pair key, so runs beside different neighbours never share a
cell. The in-situ verdict decides. The one change: a section whose in-situ
verdict is `retire` but which shows `benefit` alone in any cell gets
**`shorten-or-merge`**, since the rest of the guidance covers what it teaches.
That verdict never removes anything. The thresholds are `gate-policy.yml`'s
`guidance:` block, which is separate from `skills:`.

**Removal.** `scripts/retire_skill.py --target guidance --guidance PATH`
acts on at most one section per evaluation. `shorten-or-merge` always
becomes a skills-evals issue. `retire` becomes an issue in `proposal` mode
(the committed `guidance.retirement_mode`), and in `auto` mode a branch
`retire-guidance/<id>` in the `_agent-guidance` checkout that auto-merges.
That branch removes the extent from `base.md` (or `git rm`s a `sections/`
file when this was its only `##`) and re-copies `base.md` to
`.claude/hooks/fleet-guidance.md`. It also deletes the manifest row, runs
`check-guidance-coverage.js --write-bytes`, adds a typed `remove` entry to
`docs/guidance-impact.md` whose `Eval:` line gives the pooled run count
(`n=`) and the fixtures, and rebuilds `AGENTS.md`, split on the
`## Repo-specific additions` heading line. A dry run lists every step.
Auto mode is not wired: it needs a write credential for `_agent-guidance` and
the same refusals as the skill path.

## Out of scope

- `GHA-bench` as the harness (#18 caveat) — this is a dedicated harness.
