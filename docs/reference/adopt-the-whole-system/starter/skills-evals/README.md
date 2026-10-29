# skills-evals

[![skill eval: workflow-path-audit](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fexample-adopter%2Fskills-evals%2Fpersistent%2Feval-results%2Fbadges%2Fworkflow-path-audit%2Frequired-check-routing.json)](https://github.com/example-adopter/skills-evals/actions/workflows/eval.yml)

Evals for the [`acsi-agentskills`](https://github.com/example-adopter/acsi-agentskills)
registry: for each skill, measure agent quality **with vs. without** the skill
installed, so "this skill helps" is a number instead of an assertion.

Implements Phase 5 of
[agentskills#18](https://github.com/Adam-S-Daniel/agentskills/issues/18).
Full method and rationale: [`DESIGN.md`](DESIGN.md). Deliberately a dedicated
harness — `GHA-bench` is not used for this.

New here? Start with [`docs/how-it-works.md`](docs/how-it-works.md): a
plain-English guide to what runs, where the results are, what each fixture
measures, what is left and what it costs.

## Layout

```
DESIGN.md                  # eval method, harness shape, open decisions
harness/
  run_eval.py              # runner: loads a fixture, runs both arms, scores, reports
  run_canary.py            # runner: probes the guidance-bridge canary against the real CLI
  guidance.py              # guidance subject: payload assembly per delivery mode,
                           # delivery through the real fleet-memory hook, the guard
  run_propagation.py       # runner: Tier-2 propagation arms
  roster.py                # model roster: availability + usage -> arms/judge/preflight
  timeweeks.py             # ISO-week arithmetic shared by the roster and the census
  skill_usage.py           # usage/weekly-local.json: its schema, and per-skill weight for the planner
  scorers/
    objective.py           # scriptable assertions on the output workspace
    judge.py               # LLM-as-judge rubric scoring
  propagation/
    init_probe.py          # the primitive: read a session's loaded skill set, free
    arms.py                # one arm per delivery channel, each with a control leg
  fakes/                   # stand-in binaries shared across Class B fixtures
    gh                     # offline GitHub CLI: replays recorded responses,
                           # refuses every write, logs each invocation
    README.md              # its keying rule, its classes, its invocation log
  registries.yml           # registry name -> URL -> skill-directory layout
evals/
  workflow-path-audit/     # the reference A/B eval: a skill dir of nested fixtures
    required-check-routing/  # the original fixture; the scheduled run's default
      fixture.yaml         # prompt, arms, objective checks, judge rubric
      seed/                # workspace the agent starts from (unfiltered workflows)
    companion-skip-pair/   # an inherited same-name skip workflow beside a required check
    moved-test-dir/        # a filter left pointing at a directory the tests moved out of
    workflow-doc-in-step/  # the repo's own workflow table kept in step with the filters
  github-actions-sha-pinning/  # A/B eval, Class A: SHA-pinning + the example-site-platform tag carve-out
    fixture.yaml           # prompt, PINS.md-bound objective checks, judge rubric
    seed/                  # a repo with third-party actions + a example-site-platform ref pinned by tag
  post-failure-comment/    # A/B eval, Class A: wire CI failures into the platform's composite
    fixture.yaml           # prompt, structural workflow-step checks, judge rubric
    seed/                  # two Playwright workflows + a vendored example-site-platform action contract
  windows-elevation-from-wsl/  # A/B eval, Class B: a fake powershell.exe on PATH
    fixture.yaml           # prompt, env (PATH), arms, log/transcript checks, rubric
    seed/                  # the WSL-side checkout, with bin/powershell.exe + pwsh.exe
  disarm-inherited-reach/  # A/B eval, Class A: disarm a clone's inherited reach into production
    fixture.yaml           # prompt, setup: hook, git-state objective checks, rubric
    seed/                  # setup.sh builds a bare prod repo, a clone and a worktree
  writing-adrs/            # A/B eval, Class A (format half): four fixtures
    bootstrap/             # no docs/decisions/ yet — bootstrap the folder
    existing-convention/   # docs/decisions/ already has a house format
    supersede/             # replace an accepted decision and update its pointers
    why-no-comment-trail/  # "why did we do X?": record the relayed reason as an ADR
    trigger-eval-set.json  # reviewed should/should-not-trigger queries for
                           # propose_skill_edit.py --trigger-eval-set
  review-bash-ci-reliability/  # A/B eval, Class A: bash CI-reliability findings, four fixtures
    release-pipeline/      # six findings and two decoys in a release pipeline's shell
      fixture.yaml         # prompt, objective checks (file_matches over the seed scripts), rubric
      seed/                # a release pipeline with the findings baked in
    drift-report-capture/  # diff and grep captures abort a drift report under set -e
    test-runner-assertions/  # a test runner's helpers abort the run on a failing assertion
    empty-repo-discovery/  # a failed gh listing reads as "nothing to audit" and goes green
      checker/             # hidden probe the repo_tests checks lay over the workspace
  ci-watcher-loops/        # Class B: dispatch, captured run id and bounded timeline polling
    fixture.yaml           # ordered-log, parsed shell capture, conclusion and restraint checks
    seed/                  # consumer site and shared fake gh with call-count payloads
  debug-github-workflows/  # Class B: four diagnosis fixtures
    wrong-branch/          # main invokes an obsolete test module; an unmerged branch has the fix
      fixture.yaml         # replay-read, affirmative-reply and file-restraint checks
      seed/                # Python service, shared gh symlink and scrubbed run/branch responses
    swallowed-failure/     # a green run whose process substitution hid a failed gh listing
    git-identity/          # a bare exit 128 from a test script that commits without a git identity
    secret-name/           # a workflow reads a secret name the repository does not have
  ci-job-performance/      # Class B: speed up a slow CI job from recorded timings
    browser-cache/         # a Playwright browser cache that slows the firefox leg it was meant to speed up
      fixture.yaml         # gh-log timing reads, parsed-workflow probes per matrix leg, file guards, rubric
      seed/                # an e2e browser-matrix workflow, shared gh symlink, eight recorded runs
  embeddable-tool-pages/   # A/B eval, Class A: publish a tool and preserve site wiring
    fixture.yaml           # prompt, parsed front matter, asset digest, preservation checks, rubric
    seed/                  # a wired Tools collection, an existing tool, and a standalone app
  admin-config-render/     # A/B eval, Class A: a consumer site's /admin config will not render
    fixture.yaml           # prompt, command_succeeds renders, kit/verifier/identity digests, rubric
    seed/                  # a consumer site plus a vendored example-site-platform kit and its Ruby render checks
  browser-testing/         # A/B eval, Class A: add an admin-read draft-preview spec
    fixture.yaml           # fixed AST command checks and parser/verifier integrity checks
    seed/                  # pinned platform slice and pure-fs parser dependencies
  code-quality/            # A/B eval, Class A: add Go to the staged lint hook and configs
    fixture.yaml           # prompt, parsed config + Bash AST + hidden hook probe, preservation checks, rubric
    seed/                  # platform lint configs, the vendored lint-staged.sh, a small Go module
  vendor-release-impact-issues/  # A/B eval, Class A: draft GitHub issues for a
                           # vendor's release notes' effect on a repo
    issue-hygiene/         # quote fencing, publish times, backlinks, title placeholders
      fixture.yaml         # prompt, objective checks (quote/link/title hygiene), rubric
      seed/                # ExampleCLI 4.1.0-4.3.0 release notes + a small consumer repo
    closed-duplicate/      # a finding already tracked in a CLOSED issue: search, link, don't refile
      fixture.yaml         # gh-log search check, link/duplicate checks, instrument guard, rubric
      seed/                # 4.2.0 notes, a consumer repo, shared gh symlink and issue payloads
    stamps-skip-quotes/    # publish-time stamps go on the agent's prose, never in quotes or titles
      fixture.yaml         # verbatim-quote, unstamped-quote/title and prose-stamp checks, rubric
      seed/                # 4.1.0-4.3.0 notes whose bullets name versions, a consumer repo
    closing-keywords/      # a tracking PR body and commit message free of closing keywords
      fixture.yaml         # PR-file keyword/quote bans, issue quote, tracking-row checks, rubric
      seed/                # 4.4.0 conventional-changelog notes ("fixes #88"), a consumer repo
  rename-pdfs/             # A/B eval, Class A: rename a folder of PDFs by content
    fixture.yaml           # prompt, objective checks (listing + content digests), rubric
    seed/inbox/            # six committed PDFs built by ../make_pdfs.py
  pdf-ocr-audit/           # A/B eval, Class A: audit synthetic PDFs for OCR needs
    fixture.yaml           # prompt, objective report checks + source immutability, rubric
    seed/archive/          # eight committed PDFs; generator and answer key live outside seed/
  platform-release-and-bump/ # A/B eval, Class A+B: local platform consumer bump
    fixture.yaml           # tagged verifier exit, structural refs, caller set, replay restraint
    seed/                  # older consumer pins and fixed platform release machinery
  skills-doctor/           # A/B eval, Class B: diagnose what a session is
    bucketed-account-store/ # actually being delivered, when the account store
      fixture.yaml         # is bucketed per account and its copy of a skill is
      seed/                # the stale one. No fake binary: the consulted
                           # surface IS a filesystem, so the captured tree is
                           # the instrument
  cms-stuck-pr-triage/     # A/B eval, Class B: diagnose a stuck publish loop
    fixture.yaml           # prompt, env (PATH + replay dir), checks, rubric
    seed/                  # the site checkout: bin/gh is a symlink to
                           # harness/fakes/gh, and .gh/replay/ holds its
                           # recorded responses
  editorial-label-audit/  # A/B eval, Class B: diagnose and repair persistent editorial labels
    fixture.yaml           # prompt, permissions and label-transaction checks, rubric
    seed/                  # thin daily caller, vendored audit, shared gh payloads
  consumer-repo-provisioning/ # A/B eval, Class B: diagnose missing consumer credentials
    fixture.yaml           # missing Actions secret diagnosis and PAT permission cells
    seed/                  # shared fake gh, startup-failure metadata, denied secret listing
  github-actions-repo-settings/  # A/B eval, Class A+B: onboard and diagnose settings
    drift-diagnosis/
      fixture.yaml         # read-only two-repo diagnosis and objective checks
      seed/                # declared baseline, shared gh, and canned API responses
    onboarding/
      fixture.yaml         # draft: append a repo with inherited fleet defaults
      seed/                # baseline, shared fake gh, canned API responses, README
  disarm-inherited-reach/  # A/B eval: severing an inherited git remote before it can reach prod
    fixture.yaml           # prompt, setup: builds prod.git/checkout/scratch-wt, git-state checks
    seed/                  # repo-content/ + setup.sh (builds prod.git, checkout/, scratch-wt/)
  guidance-bridge-canary/  # behavioral canary for the CLAUDE.md -> @AGENTS.md import
    fixture.yaml           # prompt, disallowed tools, per-layout magic tokens
    layouts/               # bridge / no-bridge / fence / agents-only probe workspaces
  real-work/               # subject: any, draft: real pull requests replayed (decision Q4)
    <repo>-<pr>/           # example-site-platform-693, example-site-platform-221, _agent-guidance-196, example-site-platform-761, example-site-platform-760
      fixture.yaml         # the issue as the prompt, a repo_tests check, interface_strings
      seed/                # the base tree: agent context, subject paths and other specs trimmed
      checker/             # the pull request's own test file, hidden from the agent
      solution.patch       # the pull request's fix, read only by the fixture tests
      issue-before-fix.txt # the issue as it stood before the fix: its text is no leak
  guidance/                # guidance-subject fixtures (subject: guidance)
    _delivery/             # the delivery canary: one arm per mode, no seed
      fixture.yaml         # section id, five arms, per-arm transcript checks
  propagation/             # skill-delivery probes (issue #17)
    fixture.yaml           # arms, bundle, collision skill, staleness budget
    ROUTINE.md             # HISTORY: the retired Tier-3 scheduled session, and why it was session-bound
  usage/
    CENSUS.md              # the usage census: contract, and how to schedule it
  roster-policy.yml        # model roster thresholds + the capability ladder
  acsi-writing-style/      # Class C pilot (issue #81): three writing fixtures,
    README.md              # one dir each (recruiter-reply/, proposal-bio/,
    <fixture>/             # self-appraisal-opening/), every one a runnable
      fixture.yaml         # eval dir of its own — judge.mode: pairwise
      seed/                # the material the writing is drawn from
      references/          # the committed drafts the judge ranks against
  finding-unknowns/        # Class C: repo-grounded questions before building
    fixture.yaml           # three path citations, source restraint, pairwise rubric
    seed/                  # small export service, test, and architecture decision
    references/            # hand-written strong and shallow replies, outside seed
  orchestrator-scenarios/ # five synthetic, offline orchestration decision fixtures
scripts/
  board_feed.py            # immutable git trees -> the board's dash documents
  make_badge.py            # shields.io endpoint badge, averaged over the
                           # --window newest run summaries (default 5)
  refresh_models.py        # GET /v1/models -> availability document (the one network call)
  model_usage_census.py    # local transcripts and hook logs -> weekly model, skill and hook counts
  publish_usage_census.sh  # runs the census on the owner's machine: counts to persistent/eval-results, private events to acsi-agentskills-private
  local_eval.py            # guarded local runner: N trials of one skill fixture under the owner's /login,
                           # results outside every repo, a "local exhibit, not badge input"
  local_eval_guard.py      # the credential rules local_eval.py and its launch-time guard launcher share
badges/                    # badge JSON, generated by make_badge.py; the copy
                           # shields.io reads is on the `persistent/eval-results` branch
results/                   # run summaries, committed (raw transcripts are
                           # gitignored); the weekly eval.yml run appends here
test/
  run_tests.py             # eval harness's test suite (hermetic, no real `claude`);
                           # also discovers and runs test/issues/test_issue_*.py
  issues/                  # one module per issue, so two fixture PRs never
                           # collide at the bottom of run_tests.py
  test_propagation.py      # propagation probes' mutation suite (hermetic)
  fake-claude              # stand-in CLI used by the eval tests
  fake-claude-init         # stand-in CLI for the propagation probes (a simulator)
```

## Skill coverage

The [daily skill coverage workflow](.github/workflows/skill-coverage.yml) reads
all four [registry layouts](harness/registries.yml) and publishes a redacted
[latest census](https://github.com/example-adopter/skills-evals/blob/persistent/eval-results/coverage/latest.json)
and [coverage badge data](https://github.com/example-adopter/skills-evals/blob/persistent/eval-results/badges/coverage.json)
on `persistent/eval-results`. A missing private registry read credential fails
the census before publication. Dispatch defaults to a dry run that uploads the
redacted census artifact without writing to GitHub.

Each scheduled run proposes up to three public GAPs as draft fixture PRs and
tracking sub-issues under [the coverage backfill](https://github.com/Adam-S-Daniel/skills-evals/issues/62).
These are TODO skeletons, not evaluated fixtures; drafts remain GAPs in the
census until an incident, measured context limits, objective checks, and
red/green evidence have been reviewed. A backlog above three gaps can take
multiple daily runs. The [registry-side graduation and touch gate](https://github.com/Adam-S-Daniel/adam-agentskills/issues/66)
is a separate dependency before [automatic coverage](https://github.com/Adam-S-Daniel/skills-evals/issues/60)
can be considered complete.

## Board snapshot

[`scripts/board_feed.py`](scripts/board_feed.py) reads fixture inventory from
`origin/main` and summaries and reports from `origin/persistent/eval-results`
through git plumbing, without checking either tree out or running an agent.
Fetch those refs first, then write the board's four `dash` documents:

```bash
python3 scripts/board_feed.py --out board.json
```

Use `--repo DIR`, `--main-ref REF`, and `--results-ref REF` for another local
repository or snapshot. The only written file is `--out`; its parent must exist.
The main and results refs resolve once to immutable commits, and their source
links use those commits. Guidance manifest links use the supplied manifest commit
when available. The canonical board script link intentionally follows `main`.
`meta.asOf` is the later input commit's committer timestamp, so identical
input trees and commits produce identical JSON.

Results cover `results/`, `eval-results/`, and `routine-results/`, including
run-ID wrappers used by routine ingestion. The newest timestamp per fixture
wins; equal timestamps use the source path as a stable tiebreak. `runs` counts
distinct source/timestamp runs for that fixture. Reports remain plain text.
Aggregate summaries take precedence over trial children, including when the
aggregate is malformed. Trial-only runs use the mean of each metric when all
trials report it. Token totals add input, output, and cache creation/read when
present; aggregate token means require compatible observation counts.

Unknown or malformed measurements remain `null`. Inventory `total` counts
named-skill fixtures plus `subject: any` fixtures on main, excluding
`seed/`, `references/`, guidance fixtures, and the separately run
[propagation probe](evals/propagation/fixture.yaml) and
[guidance-bridge canary](evals/guidance-bridge-canary/fixture.yaml).
Probe exclusion requires their exact paths and names without a conflicting
subject, skill, or section; invalid inventory identities keep counts unknown.
`dirs` counts distinct named skills plus distinct fixture directories containing
`subject: any` fixtures.
`minFixtures` is the literal proposal policy from
[`scripts/propose_skill_edit.py`](scripts/propose_skill_edit.py). `triggerSet`
links to the validated
[`evals/writing-adrs/trigger-eval-set.json`](evals/writing-adrs/trigger-eval-set.json).
Guidance coverage comes from the immutable
[`_agent-guidance` manifest](https://github.com/example-adopter/_agent-guidance/blob/main/agents-md/eval-coverage.yml)
supplied with `--guidance-repo` and `--guidance-ref`: `sections` counts its
validated rows, and `gap` counts sections without a linked fixture on main,
including sections marked skipped. Missing or malformed inputs make their
affected values unknown. Notes expose only non-identifying error types; error
details and free-form notes are omitted. The trend preserves each flat
workflow-path-audit run separately, including missing arms, rather than joining
measurements from different runs.

## Running

Objective-only (no agent invocation — scores a workspace as-is; it scores one
fixture, so name a nested one or pass `--fixture NAME`):

```bash
python3 harness/run_eval.py evals/workflow-path-audit/required-check-routing --arm objective-only
```

Full A/B run (spawns the Claude Code CLI headlessly for each arm, scores with
the objective checks and the LLM judge, writes `results/<skill>/<timestamp>/`):

```bash
python3 harness/run_eval.py evals/workflow-path-audit --arm both \
  --registry ../acsi-agentskills
```

Useful variations:

```bash
# Only the with_skill or without_skill arm:
python3 harness/run_eval.py evals/workflow-path-audit --arm with_skill --registry ../acsi-agentskills

# Skip the LLM judge (objective checks + cost/turns only):
python3 harness/run_eval.py evals/workflow-path-audit --arm both --no-judge

# Point at a different agent binary or output root (a sibling ../acsi-agentskills
# checkout resolves with no --registry flag at all — see below):
CLAUDE_BIN=/path/to/claude \
  python3 harness/run_eval.py evals/workflow-path-audit --arm both --results-dir /tmp/eval-out
```

Local exhibit under your own `/login` (ADR
[0002](docs/decisions/0002-runs-bill-the-api-org-not-the-subscription.md),
decision 4), N trials of one skill fixture with no API dollars:

```bash
env -u ANTHROPIC_API_KEY python3 scripts/local_eval.py evals/workflow-path-audit \
  --results-dir ~/evals-local/workflow-path-audit --trials 3
```

`scripts/local_eval.py` takes a flat fixture, a nested one (`evals/<skill>/<name>`)
or a skill directory of nested ones (`--fixture NAME` picks one). It refuses
(exit 2, nothing run) when the environment carries any provider-selection or
credential variable (`ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, `CLAUDE_CONFIG_DIR`,
`AWS_*`, `GOOGLE_*`, `GCLOUD_*`, `CLOUDSDK_*`, `AZURE_*`, `CLAUDE_CODE_OAUTH_TOKEN`,
or a name containing `API_KEY`, `AUTH_TOKEN`, `ACCESS_KEY`, `SECRET` or
`BEARER`), when a proxy URL embeds `user:pass@`, when a settings file the
judge or an arm would load (user, checkout, managed, fixture seed, registry
root) names `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport` or such an
`env` variable or cannot be read, when
`--results-dir` resolves inside any git repository or is not empty, and when
the skill under test is already visible to an empty workspace (a user-level
copy would contaminate the `without_skill` arm), and when a selected fixture
has a check that parses Bash (`shell_staged_tool_guard`, `shell_capture_safe`) and this python cannot
import `tree_sitter`/`tree_sitter_bash`: install the CI pins
(`python3 -m pip install tree-sitter==0.26.0 tree-sitter-bash==0.25.1`, in a
venv if you like) first. `run_eval.py` itself never scores a missing parser as
a failed check either: the trial is a `scorer_unavailable` error, listed in
`trial_errors` and left out of every check's pass rate. Every child it starts sees
only an allow-listed environment (no `XDG_*`): PATH, HOME, LANG, LANGUAGE,
`LC_*`, TERM, TMPDIR, TZ, USER, LOGNAME, SHELL, `HTTP_PROXY`, `HTTPS_PROXY`,
`NO_PROXY` and their lowercase forms, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`,
`SSL_CERT_DIR`, `CLAUDE_BIN` and the two registry locators, plus
`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, set rather than inherited, so a trial's
auto-memory never lands in your real `~/.claude/projects/`. The login keeps
HOME real, so arms and the judge are isolated by flags instead
(`--strict-mcp-config`, `--no-session-persistence` where no `--resume`
follows, an empty setting source for the judge; DESIGN.md has the table), and
a multi-turn arm's transcript is moved to
`~/.local/state/skills-evals/sessions/` when it ends. Every CLI launch
(version call, probe, arms, judge) goes through a temporary guard launcher that
re-checks the settings in the directory the CLI is about to start in, so a
symlinked or `setup:`-written `.claude/settings.json` is refused at launch (exit
2, no further trial). It writes `aggregate.json` and
`manifest.json`, stamped "local — not badge input", marks every kept
`summary.json` `"local_exhibit": true` and drops a `LOCAL_EXHIBIT` marker in the
results dir and each trial (`scripts/make_badge.py` refuses both),
never pushes and never calls `gh`. Its module docstring lists the flags and the
limits.

A fixture's `registry:` field names which registry its skill lives in (by
URL); [`harness/registries.yml`](harness/registries.yml) maps each registry
named there to a layout glob. `--registry NAME=PATH` (repeatable) points a
name at a local checkout; a bare `--registry PATH` (no `=`, legacy) is taken
as the `acsi-agentskills` entry. `$SKILLS_EVALS_REGISTRIES` (same
`NAME=PATH,NAME=PATH` shape; a bare entry there is likewise taken as
`acsi-agentskills`) merges with `--registry` **by name** — a flag naming one
registry does not suppress an env entry naming another, and a flag wins only
where both name the same one. `$AGENTSKILLS_DIR` (name kept unchanged since
the `agentskills` -> `acsi-agentskills` rename) covers the `acsi-agentskills`
entry specifically (its older, single-registry override), and any name still
unresolved after all of the above falls back to a sibling checkout
`../<name>` next to this repo — so `acsi-agentskills`, `example-site-platform`, and
`example-site-a` checkouts next to `skills-evals/` resolve with no flags at
all. (The harness's older `~/repos/agentskills` last-resort default is gone;
a sibling `../acsi-agentskills` checkout is the default now, which is what
the invocations above rely on.) An unknown registry name, an empty `PATH`, or
an override that resolves to a nonexistent directory aborts the run before
any arm starts — including `--arm objective-only` — rather than failing
partway through or silently ignoring the bad value. Within a registry, the
`with_skill` arm resolves the skill dir by globbing that registry's layout
with the skill name substituted for its second-to-last path segment — the
`*` immediately before `SKILL.md` (the first sorted match wins), which is
what lets `plugins/*/skills/<skill>` (acsi-agentskills' own
mix of the legacy `plugins/<skill>/skills/<skill>/` shape and the bundled
`plugins/<bundle>/skills/<skill>/` shape), `skills/<skill>` (example-site-platform),
and `.claude/skills/<skill>` (example-site-a) all resolve through the same
code path. It then copies that resolved directory (the one containing
`SKILL.md`) into the workspace's `.claude/skills/<skill>/`.

For YAML metadata at the start of Markdown files, use `front_matter_has`
with exact `paths`, an `equals` mapping, and a `nonempty_strings` list.
It parses front matter and ignores the body; see the
[scorer contract and example](DESIGN.md#yaml-front-matter-objective-check-harnessscorersobjectivepy).

### The `judge:` block

A fixture's `judge:` block picks the instrument and pins its model:

| Key | Meaning |
| --- | --- |
| `model` | the judge's model, pinned strong (DESIGN.md's harness-wide rule) |
| `timeout_s` | seconds before the judge call is abandoned (default 120); a timeout is recorded as a judge error, never as a score |
| `weights` | absolute mode only: dimension name -> weight, used to recompute `overall` as a weighted mean. Rejected in pairwise mode rather than half-honoured |
| `mode` | `absolute` (default) or `pairwise` |
| `references` | pairwise only: `{name, path}` entries, paths relative to the fixture dir and refused if they climb out of it |

`mode: absolute` is every fixture before #81: the judge sees the rubric, the
transcript and the workspace diff, and returns per-dimension scores.
`mode: pairwise` (Class C, DESIGN.md) shows the judge the writing under test
together with the fixture's committed reference samples — blind, fenced, and
shuffled per trial — and the score IS the rank, 1 = best. An unknown mode is
an error rather than a silent fall back to absolute.

> [!NOTE]
> `run_eval.py` does not read `judge.mode` yet: it still calls
> `judge.score()` with the arguments it knew before #81, so it cannot score
> a pairwise fixture — and rather than scoring one with the absolute judge,
> it now refuses. A fixture whose `judge.mode` is anything but `absolute`
> exits 2 with a named `judge_mode_unsupported` error, written to
> `report.md` and `summary.json`, before any arm runs; `--no-judge` runs it
> with the objective column as the only score.
> `harness/scorers/judge.py`'s `score_fixture()` is the seam that fixes it;
> moving `_run_arm` onto it (plus a trial loop and a rank column in
> `_render_report`) is
> [#97](https://github.com/Adam-S-Daniel/skills-evals/issues/97).

## Guidance subject

A fixture with `subject: guidance` measures the fleet guidance itself rather
than a skill. It names a `section:` — an id from
[`agents-md/eval-coverage.yml`](https://github.com/example-adopter/_agent-guidance/blob/main/agents-md/eval-coverage.yml)
in the `_agent-guidance` checkout (`--guidance PATH`, else
`$AGENT_GUIDANCE_DIR`, else the sibling `../_agent-guidance`) — and each arm
carries a delivery `mode:`.

**Five modes.** `none` delivers no guidance — the control, which does get a
decoy token of its own (see the guard below). `stub` delivers
`agents-md/stub.md`, what a repo carries inline. `section` delivers that
section's extent — its `##` heading through the line before the next `##`,
`###` children included — with its file's intro prepended. `full` delivers the
whole corpus (`base.md`, plus the section's own file when it is an opt-in
`sections/*.md`). `full-minus-section` delivers that corpus with the extent
removed. The default pair is `section` / `none` — "does this section teach the
behavior". A fixture may also declare `ablation: [full, full-minus-section]`,
run with `--ablation`: the marginal value of the section *in situ* inside a
56 KB always-on file, which is the question that decides whether it keeps
paying for its bytes. Extents come from a real markdown parse
(`markdown-it-py`, pinned exact), never a regex — `base.md` has fenced blocks,
and a `## ` inside one is not a heading.

**Delivery is the production path.** Each arm gets a fresh scratch dir,
`CLAUDE_CONFIG_DIR` pointing at it, and the *real*
[`fleet-memory.sh`](https://github.com/example-adopter/_agent-guidance/blob/main/.claude/hooks/fleet-memory.sh)
run with `FLEET_GUIDANCE_PAYLOAD`, so the marked block lands in
`<scratch>/CLAUDE.md` exactly as a real session gets it. Guidance arms invoke
the CLI with `--setting-sources user,project`; skill arms keep `project`.
`--delivery project` is the documented fallback for a CLI that does not read
memory from `CLAUDE_CONFIG_DIR` — same hook, pointed at the workspace — and
whichever was used is recorded as `delivery:` in every summary.

**The contamination trap, and the guard.** On any machine or hosted session
carrying the fleet hook, the real `~/.claude/CLAUDE.md` already *is* the
guidance. A harness that does not isolate the config dir per arm delivers the
guidance to both arms and reports a null delta that reads as "the guidance
does nothing" — a quiet, plausible, wrong number. So every payload carries a
trailing `The magic word is <token>.` with a fresh token per run, and every
arm is probed for it before it is allowed to score anything: a tool-free
`run_canary.run_leg` call against that arm's own config dir. The control's
token is a **decoy** — a second fresh token, delivered to it and to nothing
else — so that its guard asks a question with a wrong answer. Without it,
`mode: none` delivered nothing and the control's probe could only ever answer
"no magic word", which is also what it answers when the arm is contaminated.

The guard therefore claims exactly this: a treatment arm was delivered its
payload and reads it; a control arm reads its own scratch user memory and was
not delivered the treatment payload. An ambient memory read *in addition* to
the scratch one is prevented by the per-arm `HOME`/`CLAUDE_CONFIG_DIR`
isolation rather than by the guard, for two reasons: a probe asked for the
magic words with two in context may report either, and a contaminating source
carrying no token of its own — the real `base.md`, say — is invisible to a
token guard at all. Only a real dispatch settles it.
The decoy is what makes a contaminated control's context carry *two* magic
words, which is why the guard prompt asks for **every** one of them rather
than "the magic word": a one-word answer — the decoy reported, the treatment
token left unmentioned — is the case the plural prompt exists for. An
arm that does not report the token it was delivered, an arm that reports a
token it was *not* delivered (a control arm reporting the treatment token, or
a treatment arm reporting the control's decoy), or a probe that could not run
at all, makes the arm **INCONCLUSIVE** — the summary carries
`guard: {expected, observed, contaminated}`, no score is written for that arm,
and the run exits `2`. Never PASS, never FAIL.

Results land under `results/guidance/<id>/<timestamp>/<arm>/summary.json`
(with `subject`, `section`, `mode`, `bytes`, `delivery`, `guard`), and
`scripts/make_badge.py guidance/<id>` builds a badge from a
`with_guidance`/`without_guidance` pair.

```bash
# The delivery canary — one arm per mode, against a sibling _agent-guidance:
python3 harness/run_eval.py evals/guidance/_delivery --arm both

# One section, the default section/none pair, with an explicit checkout:
python3 harness/run_eval.py evals/guidance/<id> --arm both \
  --guidance ../_agent-guidance
```

`evals/guidance/_delivery` is the scheduled behavioral canary for the
fleet-memory delivery path — the probe
[_agent-guidance#17](https://github.com/Adam-S-Daniel/_agent-guidance/issues/17)
item 3 asked for and that the guidance-bridge canary below no longer covers,
since the payload no longer travels through the `CLAUDE.md → @AGENTS.md`
bridge. Keep the bridge canary for the repo-stub path.

Any fixture can be given a real run on `main` by dispatching **Real eval**
with its `fixture` input (default
`evals/workflow-path-audit/required-check-routing`); the value is
validated against the committed fixture set before any credential is minted.
Set `roster_only` to refresh the model roster and file its proposal without
the eval: the WIF preflight, the eval and the badge are skipped, so the only
model-side call is the Models API read. A roster_only run publishes nothing to
`persistent/eval-results`; its only outputs are the `roster/proposal` branch and the
tracking issue, whose text says that no eval ran.

## Guidance-bridge canary

The fleet's agent guidance lives in each repo's `AGENTS.md`; `CLAUDE.md`
carries just an `@AGENTS.md` import line that Claude Code's memory loader
expands. That loader's import behavior has changed upstream more than once,
so a repo can silently lose all its guidance while its CLAUDE.md still looks
correct — only a behavioral probe, actually asking an agent whether guidance
made it into context, proves the bridge still works. Implements
[skills-evals#5](https://github.com/Adam-S-Daniel/skills-evals/issues/5),
item 3 of
[Adam-S-Daniel/_agent-guidance#17](https://github.com/Adam-S-Daniel/_agent-guidance/issues/17).

Run it:

```bash
python3 harness/run_canary.py evals/guidance-bridge-canary
```

Add `--subagent` to also probe subagent memory passing (a Task-launched
subagent asked the same question against the bridge layout):

```bash
python3 harness/run_canary.py evals/guidance-bridge-canary --subagent
```

This needs real API access — like a full eval run, it is **not** part of the
hermetic test suite (`test/run_tests.py` exercises this runner against
`test/fake-claude` only) and is **not** run in CI. Run it on demand, or wire
it into a schedule. Each run's report records `claude --version`, so a
regression can be tied to a specific CLI release.

### What a failure means

- **`bridge` failed** (magic word expected but absent) — likely a CLI import
  regression: check anthropics/claude-code#7768, #18371, #18518, #24987,
  #29525 for the historical pattern, and the CLI changelog for the version
  recorded in the report. Could also be fixture rot — check
  `layouts/*/CLAUDE.md` and the tokens in `fixture.yaml` haven't drifted.
- **`no-bridge` or `fence` failed** (magic word visible but shouldn't be) —
  most likely the probe's tool controls broke (foraging leaked the token).
  Native AGENTS.md support does **not** explain it: Claude Code 2.1.277
  reads `AGENTS.md` natively only "in a project with no CLAUDE.md" (its
  release note, quoted in
  [skills-evals#191](https://github.com/Adam-S-Daniel/skills-evals/issues/191)),
  and both of these layouts have a `CLAUDE.md` (a link in `no-bridge`, a
  fenced import in `fence`), so they stay invisible even where that support
  exists. An earlier version of this section said native support
  ([anthropics/claude-code#6235](https://github.com/anthropics/claude-code/issues/6235))
  would surface here; with a `CLAUDE.md` present it could not.
- **`agents-only` failed** (magic word expected but absent) — the one leg
  with `AGENTS.md` and no `CLAUDE.md` at all, so the only leg where native
  AGENTS.md support can show up. On a CLI at or past 2.1.277 it expects
  `visible`; a failure there means native loading regressed or never worked
  for this layout. On an older CLI it is **expected to fail**, so read the
  `claude --version` recorded in the report before calling it a regression.
  When it passes, the fleet's `CLAUDE.md` bridge is no longer the only way
  Claude Code gets the guidance — a signal to revisit the bridge pattern, not
  a fleet failure. Whether 2.1.277's native loading behaves as its release
  note says in this layout has **not been measured**: the leg has not been run
  against a real CLI yet (see below).
- **`bridge-subagent` failed** — subagent memory passing regressed.

**Owed: a live run on a CLI at or past 2.1.277.** The `agents-only` layout and
this reading are hermetic only (against `test/fake-claude`, whose
`canary_loader` mode models native AGENTS.md reading as the release note
describes it, which is an assumption, not a measurement). A real run
(`python3 harness/run_canary.py evals/guidance-bridge-canary`) costs API
spend and has not been done; its report records `claude --version`, which is
what ties a result to a CLI release.

## Propagation probes

Every eval above asks whether a skill *helps*. These ask something more basic
and, historically, more often wrong: **did the skill arrive at all?** Three of
the fleet's four known skill-delivery incidents were invisible until a human
went looking. Implements
[skills-evals#17](https://github.com/Adam-S-Daniel/skills-evals/issues/17).

The primitive is the `{"type":"system","subtype":"init"}` event that
`claude -p … --output-format stream-json --verbose` emits, which carries the
session's loaded skill set and its resolved plugins. The CLI computes it
locally, **before its first API call** — so with `ANTHROPIC_BASE_URL` pointed at
a black hole and the child killed the moment the event arrives, the whole
assertion layer is credential-free, network-free and costs **$0.00**. That is
what lets these run on every pull request *and* again on a daily schedule
(`.github/workflows/propagation.yml`) rather than behind `eval.yml`'s OIDC.

```bash
# All five arms against a local acsi-agentskills checkout (~15s, no credential):
python3 harness/run_propagation.py evals/propagation --registry ~/repos/acsi-agentskills

# One arm, plus the live self-test that proves the assertions can still fail:
python3 harness/run_propagation.py evals/propagation --arm plugin-marketplace \
  --registry ~/repos/acsi-agentskills --self-test
```

| Arm | Asserts |
| --- | --- |
| `clean-room` | none of the lock's skills, and no `acsi:` namespace, in an empty scratch surface |
| `project-mirror` | a repo-committed `.claude/skills/` mirror loads, exactly the locked set |
| `plugin-marketplace` | `claude plugin install` delivers all 9 as `acsi:*`, and the resolved tree's content digests match `skills.lock` |
| `bootstrap-hook` | the SessionStart hook installs 9/9 on an ephemeral surface — **and declines with a `skipped — durable session` verdict that names the marketplace install as authoritative, writing nothing, on a durable one** (an interpolated diagnostic clause in the middle is allowed and ignored) |
| `collision-guard` | a repo-owned copy wins: the hook says it skipped, and the file really was not written |

Every arm probes twice — a control leg before the channel delivers anything,
then the arm leg — and asserts **exact set equality** on the difference against
`skills.lock`. Every leg also records a guard block (HOME isolation, the
environment allowlist, bootstrap surface variables, account-store absence, a
non-empty built-in floor, the init event's stream index). **Any guard that does
not hold makes the arm INCONCLUSIVE (exit 2), never PASS and never FAIL** — an
arm expecting to find nothing is the one most able to pass on a dead CLI.

Two things worth knowing before editing any of it:

- **An environment allowlist, not a scratch `HOME`.** Measured: same scratch
  HOME, `env -i PATH HOME TMPDIR` loads 16 skills; inheriting the ambient
  environment loads 35, because the CLI re-syncs 17 account-store skills into
  it. Unsetting `CLAUDE_CODE_SYNC_SKILLS` alone does not stop it.
- **The init event's position is not contractual.** Same CLI build: index 0
  under a scrubbed environment, index 4 under the ambient one. Select on
  `(type, subtype)`; selecting on `type == "system"` alone picks
  `commands_changed`, which has no `skills` key.

### Tier 3 — the account store (retired 2026-09-28)

The claude.ai account store lands at `~/.claude/skills/synced/`, which exists
only on a signed-in surface, so CI cannot see it. This repo used to run a
daily Tier-3 audit of that store on a claude.ai Routine, and gate
`propagation.yml` on the audit's freshness. The owner deleted the Routine on
2026-09-28, the same day the claude.ai ZIP-upload channel it audited was
retired ([adam-agentskills#23](https://github.com/Adam-S-Daniel/adam-agentskills/issues/23)):
once nothing repopulates the account store from that channel, there is
nothing left for an audit of it to check. `harness/run_account_audit.py`,
`harness/run_account_drift_issue.py`, `harness/propagation/account_store.py`
and `.github/workflows/account-store-drift.yml` were removed in the same
change, and `propagation.yml`'s freshness gate went with them. What the audit
was, what it found, and the incidents that shaped its design are recorded in
[`evals/propagation/ROUTINE.md`](evals/propagation/ROUTINE.md), now marked
HISTORY.

## Tests

Two hermetic suites — no real `claude` binary is ever invoked, no network, no
wall-clock dependence:

```bash
python3 test/run_tests.py          # the eval harness (ci.yml)
python3 test/test_propagation.py   # the propagation probes (propagation.yml)
```

The propagation suite is a **mutation** suite: every assertion has a test that
breaks exactly one clause and requires the specific verdict and exit code that
must result, plus `test_unmutated_run_passes_every_arm`, which catches the
probe that fails on everything. Its stand-in CLI (`test/fake-claude-init`) is a
simulator — it really resolves skills from `$HOME/.claude/skills`, the
project's `.claude/skills` and `installed_plugins.json` — so the arms exercise
their real code path rather than a canned answer.

## Model roster

Which models does this harness run against today? Until #67 the answer was a
literal in each fixture — an arm pinned here, a judge there, a preflight in the
workflow — and **nothing noticed when a model shipped or retired.**

**The answer is [`evals/roster.yml`](evals/roster.yml), committed on `main`.**
`main` is ruleset-protected and pull-request-only, so an arm, the judge, the
preflight model or a `catalogue_seen` entry cannot appear there or vanish from
there without a reviewed commit. That is
[ADR 0001](docs/decisions/0001-roster-trusted-on-main.md), and it is what
replaced reading the roster off the `persistent/eval-results` branch — where one line
decided which models every unpinned fixture ran against, and fourteen review
rounds on [PR #129](https://github.com/Adam-S-Daniel/skills-evals/pull/129)
could not make a local check over it safe
([#147](https://github.com/Adam-S-Daniel/skills-evals/issues/147)).

`harness/roster.py` still computes what the roster SHOULD be, from two inputs:

- **availability** — `scripts/refresh_models.py` reads `GET /v1/models` (the
  only authenticated network call in the feature) using the WIF-derived bearer
  `eval.yml` already mints;
- **usage** — `scripts/model_usage_census.py` counts what this account actually
  ran, per model per ISO week, from the local Claude Code transcripts.

**Who becomes an arm** (the owner's decision, 2026-09-22). Two rules, and the
second is subordinate to the first:

1. **Usage seats.** Every available model at or above the entry bar — 10% of
   rankable, attributable census turns over the trailing enter window (2 weeks) — is an arm,
   with its share in its reason.
2. **Newest per *qualifying* tier.** In a tier rule 1 already seated somebody
   in, the newest available model past the cooling-off is an arm too,
   and its reason says so in words, naming the qualifying share it rides on.
   **A tier no model of which clears the entry bar gets no arm at all**,
   however new its newest model is; that model is listed under `excluded`
   with exactly that reason.

Rule 2 used to read "newest in its tier" across every tier on the ladder,
which seated the newest haiku and the newest fable on a census showing the
fleet ran 6.3% and 3.0% of its turns on them — four arms' worth of spend per
fixture to measure two tiers nobody uses. On the 2026-09-22 census (sonnet-5
45.4%, opus-5 45.2%) the roster is two arms; ship an opus 5.1 and the opus
tier, which qualifies, seats it beside opus-5 for three; ship a newer haiku or
fable and nothing changes. Under that census the judge became
`claude-fable-5-1` from `claude-fable-5`: the unchanged judge rule selects the
strongest available model that is not an arm, so excluding `claude-fable-5-1`
from the arm set changed the selection. **The no-census fallback is untouched**:
with no usable usage there is no qualifying tier and a roster must not be
empty, so the rule reverts to newest-per-tier across *all* tiers. When the
census itself is unusable, each fallback reason names the degradation. When
the census is usable but only its enter window fails a ranked-usage floor, the
reason is the bare newest-per-tier sentence. No new threshold was added — rule
2 reads rule 1's entry bar, and the numbers all stay in
`evals/roster-policy.yml`.

**A tier with a known vendor default follows the vendor** (the owner's decision,
2026-09-27, [#202](https://github.com/Adam-S-Daniel/skills-evals/issues/202),
[ADR 0002](docs/decisions/0002-roster-follows-vendor-defaults.md)).
`scripts/probe_model_defaults.py` asks the Claude Code CLI the workflow has
just installed at the npm latest which model each family alias on the tier
ladder (`opus`, `sonnet`, `haiku`, `fable`, …) resolves to: it starts
`claude -p --model <alias> --output-format stream-json --verbose` with **no
credential** (a scrubbed environment: PATH, a fresh temporary HOME, LANG=C,
and `DISABLE_AUTOUPDATER=1`/`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`),
reads the `model` of the first `system/init` event and kills the CLI's process
group ([#203](https://github.com/Adam-S-Daniel/skills-evals/pull/203)). The
CLI does open unauthenticated TLS connections of its own around that event,
but no credential exists in the probe's environment, so nothing can be billed
or leaked; and the alias table is built into the binary, so resolution does
not depend on the network (the same ids come back with networking removed). A word the
CLI echoes back unchanged (`mythos`) is not an alias and is skipped. The
roster takes the reported id when it is one of this run's available models (a
dated snapshot the catalogue collapses onto its alias stands for that alias,
and so does a dated `<base>-YYYYMMDD` the catalogue does not list while it
lists `<base>`) of the alias's own family. In a tier whose default resolved, usage only
decides whether the tier is on the roster (a model in it clears the entry bar,
another previous arm in it is still seated this run, or there is no usable
census): the seat goes to the default **at once, with no cooling-off**, and
rules 1 and 2 do not apply there. The default's own previous seat does not
keep its tier on the roster: once nothing else does, a seated default gets
the ordinary exit check (2% over the 2-week exit window) and can retire — measured on the
TIER's combined share, every model of the default's family in it, not its
own, so a tier sitting between the 2% exit and 10% entry bars keeps an arm
while the fleet moves from one version to the next (#203 round 2). A default
governs the models of its own family word only: a peer family in the same
rung (`[fable, mythos]`, a different access programme) keeps rules 1 and 2. A
model the default supersedes earns no seat from its usage; a previous arm it
supersedes retires on the first roster run that sees it superseded, whatever
the census says (`superseded_exit_weeks` is 0 since the owner's 2026-10-25
decision; a positive value would keep its seat until that many complete ISO
weeks have passed since its successor's `created_at` **and** its share over
those weeks is under the 2% exit bar, with a stale or too-thin census holding
it) — a previous arm is recognized under its DATED id too (#203 probe round 5, R5-3): a
previous arm published under `<base>-YYYYMMDD` is still a previous arm once
`<base>` appears in the catalogue. A model newer than the default earns no
new seat; one that is already a previous arm gets the ordinary exit check
rather than retiring on sight. **A failed probe freezes its family for that
run** (#203 probe round 1): a family whose alias the probe recorded an
error for, answered with a model of the wrong tier or family (nonsensical
about its own alias, #203 probe round 3), or answered with a model with no
`created_at` to start a predecessor's buffer from (`no-created-at`, #203
probe round 5, R5-1), is frozen — or every family the probe did not skip,
when the document is unreadable, junk, answered for no ladder alias, or is
the workflow's `{"probe_exit": "nonzero"}` stand-in for a probe script that
exited with an error (`probe-exited`). A default the probe **answered** but
this run's catalogue does not otherwise match (not available — an undated
id the catalogue lists only as exactly one dated `<id>-YYYYMMDD` does
resolve to it — an ambiguous snapshot) **freezes its family exactly like a
failure** (#203 probe round 7, R7-1 — the GOVERNING GUARANTEE, first stated
in round 5 as R5-2: a single run whose probe answer is a failure or a
mismatch changes no seat a clean run would not, made structural in round 7
rather than resting on a guessed "effective default"), only the summary
and reason wording says "the CLI's default `<id>` for `<alias>` does not
match this run's catalogue (<class>)" rather than "vendor default ...
unknown this run", so a human can tell the two apart. A frozen family keeps
every previous arm's seat until it leaves the Models API and retires none —
including a previous arm whose LISTED FORM switches between dated and
undated from one run to the next, freeze or no freeze: it is still that
same arm, held (or, for a seated default's tier, still counted as seated)
under whichever spelling this run's catalogue lists (#203 probe round 9).
While it still holds
a seat the Models API lists it gets **no new seat at all** (#203 probe
round 3); only a family that would otherwise vanish from the roster is
seated — by the usage entry bar, or, with no usable enter window (no fresh
census, or one whose enter window is under the ranked-usage floors), as the
newest in its tier exactly as with no probe; with a usable one and nothing
clearing the bar it gets no seat, the same outcome a clean run gives. Falling back to the usage rules
instead let one bad week retire a seated default or seat a preview that
the next clean week then undid, and a usage seat granted during a freeze
was one the next clean week retired. The freeze is **per run —
nothing is carried to the next** (the owner's decision): the probe is not
the eval (the eval authenticates; the probe must not), so a probe failure
does not stop the eval, and the next run's probe decides afresh. It is loud:
the published roster carries `defaults_failed` (`{alias: class}`, plus
`defaults_document_failed` for a whole-document failure) or, for a catalogue
mismatch, `defaults_mismatched` (`{alias: {id, class}}`), the step summary
names the frozen families on its first lines, and the proposal step emits its
own fixed `::warning::` for each class present and keeps the tracking issue
open even when nothing else changed — the issue's title and first line say
"probe failed", "a vendor default did not match this run's catalogue", or
both, by whichever class or classes are present, so a mismatch-only run
never claims the probe failed; that step runs unless the workflow is
cancelled, so it fires even when the eval failed, and a failed `gh issue`
write in it is a fixed `::warning::`, never a failed job; it pushes nothing
itself — `roster-pr` publishes `roster/proposal` with the roster App's token,
and a failed publish there is a fixed `::warning::` too, which the tracking
issue reports as a proposal that was not pushed. Its issue says whether
an eval runs this dispatch and on which roster it will run; since ADR 0004
`roster-pr` writes it before the eval, so the eval's own outcome is in the
run summary. With no `--defaults` document at all nothing is frozen and the
roster is the same one the pre-#202 code computes, with one exception: a
previous arm whose listed spelling has switched between dated and undated
since the previous run is still recognised as that arm, rather than reading
as no longer returned and dropping its seat. The committed `evals/roster.yml` keeps no
`defaults` block; the published roster's `defaults` (source
`claude-code-cli <version>`, `probed_at`, resolved and unresolved aliases)
is there for the reviewer. The preflight pick applies the cooling-off either
way.

**The cooling-off is 0 days** (the owner's decision of 2026-09-27, #202):
`cooling_off_days: 0` in `evals/roster-policy.yml`, so rule 2 (in a tier
with no resolved vendor default, e.g. haiku) and the preflight pick take the
newest model at once, and their reasons say "no cooling-off applies". The
knob and the code that applies it are kept; a positive value restores it.

**But what it computes is a PROPOSAL.** When it differs from the committed
file, the weekly run renders the proposed `evals/roster.yml`
(`scripts/render_roster_yaml.py`) and checks it against the committed-roster
contract. A valid proposal is published as one commit on the bot-owned branch
`roster/proposal` — by the `roster-pr` job, with the roster App's token,
parented on live `main`; if `main` changed `evals/roster.yml` after the run
rendered its proposal, nothing is published and the next run re-proposes
([ADR 0003, round 7](docs/decisions/0003-roster-merges-automatically.md#round-7-the-app-publishes-the-proposal-branch))
— with one tracking issue carrying the rendered summary —
every seat's reason in words, with the numerator and denominator its share was
taken over — and a `main...roster/proposal` compare link. An invalid proposal
does not update the branch or compare link; its tracking issue says it needs
review and lists the admission failures. The paid eval runs on the committed
roster, unless this run's own proposal merged in time (below,
[ADR 0004](docs/decisions/0004-eval-runs-on-the-roster-its-run-merged.md)) —
except on a `roster_only` dispatch, where no eval runs and nothing is
published to `persistent/eval-results`. Nothing in CI writes
`evals/roster.yml` directly — only a merged pull request does. When the
computed roster matches the committed one, that issue (and, under
`roster_mode: auto`, any open pull request) is closed.

**Who merges the pull request depends on `roster_mode`** in
`evals/roster-policy.yml` ([ADR 0003](docs/decisions/0003-roster-merges-automatically.md)).
`roster_mode: proposal` (the flow above, unabridged) leaves the pull request
for a human to open and merge after CI. `roster_mode: auto` — the shipped
setting — instead opens the pull request itself (or, when one is already
open, closes and reopens it so `test` runs on the new head) and sets it to
merge automatically once `test` passes. All of that is done with a dedicated
GitHub App's installation token (the repository variable
`ROSTER_APP_CLIENT_ID` and secret `ROSTER_APP_PRIVATE_KEY`, minted only in the
`roster-pr` job), never `GITHUB_TOKEN`: a pull request `github-actions[bot]`
opens has its `pull_request` run held for approval, and a dispatched `test`
does not count for the required check
([ADR 0003, round 6](docs/decisions/0003-roster-merges-automatically.md#round-6-the-roster-pr-is-the-apps)).
The same App token publishes `roster/proposal` itself, under either
`roster_mode`. If that token is unavailable, nothing is published, opened or
armed, and the tracking issue carries the rendered `evals/roster.yml` for a
human to apply. It only does
this on a run whose vendor-default probe was clean (no `defaults_failed`,
no `defaults_mismatched`) — a dirty probe or a rejected proposal keeps the
human-merge flow exactly, and says why in the tracking issue. Any `gh`
failure along the way is a fixed warning, never a failed job, and degrades
that run to the human-merge flow too. A dedicated `disarm` job
(B1, adversarial round 4 on #209) turns off any already-open pull request's
auto-merge FIRST, before anything else touches it. `roster-pr` then re-arms
it, but only for a proposal ITS OWN independent checks admit; on every run
that does not (re-)enable auto-merge — a dirty probe, `roster_mode:
proposal`, a rejected proposal, or a failed `gh` call partway through — the
pull request's auto-merge is turned off again (or stays off).

**The eval runs on the roster its own run merged**
([ADR 0004](docs/decisions/0004-eval-runs-on-the-roster-its-run-merged.md),
the owner's decision of 2026-09-30). The jobs run roster, `disarm`, `roster-pr`,
`roster-wait`, `eval`, `publish`, in that order. When `roster-pr` armed this
run's pull request, `roster-wait` waits for it to merge, polling read-only
for up to 30 minutes, and verifies the merge: the merged head is the commit
it armed, the pull request touches exactly `evals/roster.yml`, that file at
the merge commit is this run's proposal byte for byte, and the merge commit
is on `main`. The `eval` job then re-verifies the merge commit locally and
writes that one file over its own checkout, whose code stays at the run's
commit. In every other case (proposal mode, a dirty probe, a rejected or
unchanged proposal, no App token, an arm that did not complete, a failed
`test`, a merge not seen within the wait, a merge that does not verify, any
API error) the eval runs on the committed roster, and the run summary says
which roster it used and why. A merge that lands after the last poll is
still used if it verifies. `roster-wait` ends with a step that runs whatever
happened, turns off auto-merge on any roster pull request still open, and
re-reads it: **the eval and `publish` (the one other job holding `contents:
write`, which matters because `--match-head-commit` is checked only when
auto-merge is enabled) do not run unless that step confirmed no roster pull
request is left armed.** It checks both any open roster pull request and the
one `roster-pr` armed. If it cannot confirm that, it fails `roster-wait` (a
red run), and the run skips both and says so in a warning and in its
summary; off `main`, where `roster-wait` is skipped, the eval runs as
before. A pull
request whose wait ran out stays open with auto-merge off; the next run
re-arms it. A `roster_only` dispatch has no wait and no eval. Between arming and the merge (which waits on a green
`test`, possibly for days), any other write-access actor that pushes
`roster/proposal` retargets the armed pull request; that is outside what this
workflow controls. The known instance is
`.github/workflows/dependabot-auto-merge.yml`'s `auto-merge` job, which holds
`contents: write` and keeps the default persisted checkout credential
(pre-existing, unchanged here). A ruleset restricting creation, update and
deletion of `refs/heads/roster/proposal` to the roster App and repository
admins closes it: since round 7 the App is the only thing this workflow
pushes that branch with, so the ruleset (added in repo-settings) can leave
`github-actions` out, and the gap is closed once that ruleset is live. Set
`roster_mode: proposal` to switch back; nothing else needs to change.

Thresholds and the capability ladder live in
[`evals/roster-policy.yml`](evals/roster-policy.yml) — no model id appears in
the roster code, in the policy file, or in either script; a model's tier comes
from the family word in its own id. `evals/roster.yml` is the one data file
admitted to that guard, exactly as a fixture's own `model:` pin is. The
rationale and the numbers' provenance belong in an ADR:
[#73](https://github.com/Adam-S-Daniel/skills-evals/issues/73). `DESIGN.md`
covers where the roster sits in the harness, and what each store is trusted
for.

The computed roster is also published to the `persistent/eval-results` branch as
`roster/latest.json`, recomputed by each real run. **That copy is an exhibit**
— it is what the explorer renders, and it is read by no decision.

**Precedence for the model a run uses:** `--model` > the fixture's `model:` pin
> `evals/roster.yml` > **error**. That last rung is not a fallback: an unpinned
fixture with no usable roster is a runner-level error naming the roster path,
because falling through to the CLI's own default publishes a badge for a model
nobody chose and makes every week-over-week comparison a comparison against a
different model. `--roster` and `$EVAL_ROSTER` remain as overrides for tests
and local runs; `eval.yml` sets neither. The skill fixtures keep their pins
— "deliberately one tier below the ceiling" is a per-fixture calibration the
roster cannot express.

**The census's public-output contract.** Its output is committed to a public
branch, so it emits `{model_id: {iso_week: count}}` and nothing else: no
project names, no paths, no prompt or reply text, no session ids, no timestamp
finer than a week — and no key that is not model-id-shaped, because
`message.model` is whatever the routing layer wrote there and has been observed
carrying an ARN, a cloud project path and free prose. Everything else is
counted under `other`. `test_census_emits_only_model_week_counts_and_leaks
_nothing` is the guard, and it stays. The census runs on a durable machine, not
in CI (a runner has no transcripts): the owner schedules
`scripts/publish_usage_census.sh` there, which publishes `usage/latest.json` to
`persistent/eval-results` — [`evals/usage/CENSUS.md`](evals/usage/CENSUS.md) has the
contract and the exact prompt and cron line. (It used to ride on the Tier-3
account-store Routine, retired 2026-09-28; see "Tier 3 — the account store"
above.)
`harness/roster.py`'s `_census_verdict` recognizes eight distinct ways there
is no usable evidence — present but unreadable, absent, future-dated, stale,
published but empty over the window, published but holding no usage the
tier ladder can rank or attribute, and holding some but under either the
absolute or the relative rankable-usage floor — and in every one the roster
falls back to newest-per-tier **across every tier** and says, in every arm's
reason, which of those it was. (So does a fresh census whose four-week enter
window alone fails one of those floors: there is no share there to qualify a
tier with.) **It remains the one input written by another machine**, so it
is the one input with a size bound on it (`CENSUS_MAX_KEYS`,
`CENSUS_MAX_BYTES`): past either, the run refuses with a named error rather
than letting an untrusted document decide how much work it does.

**What moved since last time.** The published roster carries `previous_state`
— `compared` (the committed roster was read and diffed against) or `none`
(nothing to compare against: a genuine first run, or a committed roster naming
neither an arm nor an observed model) — plus
`added_since_last`/`retired_since_last`, the arms that changed, and the
`proposal` block itself. The two states are not interchangeable: reporting "no
change since the last run" on a first run is a claim about a comparison nobody
made. A third state, `unavailable`, used to sit beside them for "a previous
roster was published but could not be read"; it is gone, because a committed
roster that is present and unreadable is a defect in this repository rather
than a fact about an unprotected branch — the run exits 5 and publishes
nothing.

## Coverage census

The [coverage job](https://github.com/example-adopter/skills-evals/blob/main/.github/workflows/propagation.yml)
reads each configured registry and the committed fixtures daily. Its badge
shows `N covered · M skipped · K gap · date`. A skill is covered when a fixture
names it; that count does not assert the fixture is runnable or that paired
context was delivered. The report also counts draft fixtures, declarations of
`context:`, missing context, and unproven guidance revisions. Context delivery
is not yet verified by the harness.

Scheduled runs publish a complete census to `persistent/eval-results` as
[latest JSON](https://raw.githubusercontent.com/example-adopter/skills-evals/persistent/eval-results/coverage/latest.json),
[timestamped JSON](https://github.com/example-adopter/skills-evals/tree/persistent/eval-results/coverage),
and the [markdown summary](https://raw.githubusercontent.com/example-adopter/skills-evals/persistent/eval-results/coverage/latest.md),
with the [Shields endpoint](https://raw.githubusercontent.com/example-adopter/skills-evals/persistent/eval-results/badges/coverage.json).
A GAP remains visible in
those files and does not block publication. If any registry is missing or
empty, the job uploads an unresolved report and fails the scheduled run;
it publishes no fleet totals or numeric badge. Pull requests, pushes, and manual dispatches get
the same safe report and a warning when a checkout is unavailable. The private
registry is checked out only on schedule with the optional
read-only GitHub App (repository variable `REGISTRY_READ_APP_CLIENT_ID`, secret `REGISTRY_READ_APP_PEM`); its skill names, bundle names,
skip reasons, and paths never appear in the public report.

[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fexample-adopter%2Fskills-evals%2Fpersistent%2Feval-results%2Fbadges%2Fcoverage.json)](https://raw.githubusercontent.com/example-adopter/skills-evals/persistent/eval-results/coverage/latest.json)

## Quality badge (real weekly run)

`.github/workflows/eval.yml` runs the full `workflow-path-audit` A/B eval every
Tuesday 07:00 UTC (and on manual dispatch) against the live Claude Code CLI and
Anthropic API, commits the run's summaries and report under `results/`, and
regenerates `badges/workflow-path-audit.json` with `scripts/make_badge.py` — a
[shields.io endpoint badge](https://shields.io/badges/endpoint) whose message
carries each arm's objective-check score and the run date. Green means the
with-skill arm strictly beat baseline, yellow tied or mixed signals, red
worse, grey missing data.

The badge averages the **5 newest runs** (`--window N`), not just the latest.
The baseline arm has been observed swinging several checks between runs, so a
one-run badge reports scheduling luck rather than a measurement; the message
appends `n=N` whenever it averaged more than one. Runs where either arm is
missing or errored drop out of the window instead of blanking the badge, and
`--window 1` reproduces the old single-run badge exactly.

Trust model: the badge reflects exactly what a scheduled or
maintainer-dispatched run of this repo's **committed** fixtures
produced — the workflow never runs on pull requests (it holds an API key and
runs the agent with `bypassPermissions`, so it must never see untrusted
fixture content; fixtures here — and the three checked-out registries
(acsi-agentskills, example-site-platform, example-site-a) whose skill content the
with-skill arm executes — are trusted because only maintainers can push to
any of the four repos involved, directly or through an automated lane such
as example-site-platform's Decap CMS publish loop or dependabot auto-merge; see
`.github/workflows/eval.yml`'s security header), and the badge JSON is
served raw from the default branch, so it can only change via a commit to
this repo.

**Which Claude Code, and which models.** The CLI is **not pinned, and always
the latest** (the owner's decisions of 2026-09-27,
[#202](https://github.com/Adam-S-Daniel/skills-evals/issues/202), and
2026-09-28, [#203](https://github.com/Adam-S-Daniel/skills-evals/pull/203)):
the workflow's "Install Claude Code CLI" step asks npm for the latest
`@anthropic-ai/claude-code` version, refuses anything that is not a plain
`MAJOR.MINOR.PATCH`, installs exactly that version — never reusing one already
on the runner — and fails if the `claude` on PATH then reports a different
version (another install shadowing it). It runs before the OIDC token
exchange, so no credential exists while it installs, and records the version
in the job's step summary. `propagation.yml` does the same. Every arm's `summary.json` then records what
actually ran:

| Field | What it holds |
| --- | --- |
| `harness` | `{"name": "claude-code", "version": ..., "permission_mode": ..., "effort": ...}` — `effort` is the `--effort` level the agent arms ran at, `null` for the CLI default; `version` is the first line of `claude --version`, read once per run, reduced to version characters and capped at 64; `null` (with a warning) if it could not be read. Present on error paths too |
| `models_used` | sorted keys of the agent result's `modelUsage` — the model id(s) that served the arm; `[]` when the agent call never produced a result |
| `judge_models_used` | the same, from the judge's CLI result(s); `[]` when no judge ran |
| `model_tokens` | per model id in the agent result's `modelUsage` (subagents' models included), its `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` and `canonical_model`; `{}` when the agent call never produced a result |
| `cross_model` | `{"model", "canonical_model", "complete", "dropped", "other_share", "threshold", "flagged"}` — whether that accounting is complete, and the share of its tokens spent on models other than the arm's own `--model`, flagged above 0.5; `null` when unknown (see DESIGN.md, "Per-model tokens") |

| `run` | how the run was paid for and where it ran: `billing` (`api`, `subscription`), `runner` (`actions`, `routine`, `workstation`), `location` (`cloud`, `local`), `id`, `session_id`, `harness_commit`, `cli_version`. Each of the first five comes from `--run-billing`, `--run-runner`, `--run-location` or `--run-id` when passed, else from what the environment observes, and `source` says which (`flag`, `env:<NAME>`, `null`). A field that could not be read is `null` with an entry in `reasons` (see DESIGN.md, "Run and usage") |
| `usage` | what the run cost: its start and end, how far the account meter moved over the run (`meter_delta`, labeled `"confounded": true, "scope": "account-wide"`, with `meter` counting the snapshots behind it), what else was running (`concurrent_runs`, `other_account_activity`, `outer_session`), and under `measured` this summary's own calls with tokens and cost split into `agent`, `judge` and `guard`, plus the meter snapshots those calls printed (`measured.meter`, each stored once, in its trial's summary) |

`report.md` carries one `- Run:` line (billing, runner, location, id, start
and end), one `- Account meter` line (which says how many calls at the end of the run it does not
cover), and one `- Harness:` line naming the version and each arm's
models. The propagation probe's `--json` run record carries, per arm,
`harness_version` and `model` from that arm's init event.

## Status

- [x] Design (`DESIGN.md`)
- [x] Fixture schema + first fixture (`workflow-path-audit`)
- [x] Objective scorer (real, tested against the seed)
- [x] Agent invocation (both arms)
- [x] LLM-as-judge scorer
- [x] Report generation
- [x] Guidance-bridge canary (`harness/run_canary.py`)
- [x] Weekly real run + quality badge (`.github/workflows/eval.yml`, `scripts/make_badge.py`)
- [x] Propagation probes, Tier 2 (`harness/run_propagation.py`, `.github/workflows/propagation.yml`)
- [x] Propagation probes, Tier 3 (built, ran daily via a Routine bound to an
  authorized session after freshly-minted ones were refused the push —
  [#20](https://github.com/Adam-S-Daniel/skills-evals/issues/20) — then
  **retired 2026-09-28** along with the claude.ai ZIP-upload channel it
  audited; see `evals/propagation/ROUTINE.md`, now HISTORY)
- [ ] Regression tracking (compare a run against the previous one)
