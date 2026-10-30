<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->
<!-- Mode: stub -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## Fleet guidance is delivered once per session — not by this file

The account's full guidance — incidents, fleet policy, machine layout, the
traps that cost real outages — is installed into **user memory**
(`~/.claude/CLAUDE.md`) by the `fleet-memory` SessionStart hook, so it is
loaded **once per session** no matter how many repos are attached
([inlined per repo, it took 332.3k tokens](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/2026-08-29-guidance-inlined-in-every-repo.md)).

**Check the session-start verdict before you rely on it.** The hook prints one
line:

- `fleet-guidance: installed (v<id>, <n> bytes)` or `fleet-guidance: current` —
  the full guidance is in context. Use it.
- `fleet-guidance: DEGRADED — <reason>` — it is **not** in context. You have
  only what is below. Read `agents-md/base.md` in the `_agent-guidance`
  checkout (or on GitHub) before non-trivial work, and say in your reply that
  you were running degraded.
- `fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)` — also not in context,
  but by the machine owner's deliberate choice, not a fault. User memory is
  GLOBAL on a durable machine, so the guidance would otherwise load in every
  unrelated project on that box; `FLEET_GUIDANCE_SKIP` opts out and removes any
  block an earlier session installed. Read `agents-md/base.md` the same way you
  would when degraded — just don't report it as a problem or try to "fix" it.

No verdict at all means the hook never ran — treat that as DEGRADED.

## Codex reads the same block, from `~/.codex/AGENTS.md`

The hook writes the same block there whenever `~/.codex` exists. Setup for
local Codex and Codex Cloud, and how to verify delivery:
[`docs/codex-cloud.md`](https://github.com/example-adopter/_agent-guidance/blob/main/docs/codex-cloud.md).

## The floor: rules that hold even when the guidance did not load

These are the ones with teeth. They are restated here, deliberately, because a
session that lost the guidance must not also lose these.

- **Branch protection is real.** Fleet repos are PR-only on their default
  branch; a direct push is rejected (GH013), even from the repo's own
  workflows. Never design a bot that pushes to a protected default branch.
- **Every `uses:` is pinned through `.github/workflows/actions.lock`: an
  exact tag, locked, checked by a required `gh actions-lock --verify`
  failing on any pin finding.** A repo not yet migrated keeps the legacy
  form, a full 40-character commit SHA with no trailing version comment,
  until its migration PR; never mix the two in one repo. Cross-repo
  reusable workflows take a full SHA either way. The one carve-out is a ref
  into this account's own `example-site-platform`, which stays on its release tag.
- **Never commit secrets or `.env` files, and never print personal data to a
  CI log** — logs, artifacts and git history on a public repo are public.
- **A successful `git push` does not mean your commit exists.** A refused
  pre-commit hook still lets the push report success. Verify with
  `git merge-base --is-ancestor <sha> origin/<branch>` — it is the only check
  that names both the commit and the ref.
- **"The watch finished" is not "CI passed."** Read the parsed conclusions;
  never infer pass/fail from a watch command's exit code.
- **A GitHub 404 means "not authorized", not "not there."** Never report a
  repo, PR or branch as gone on a 404 alone.
- **The fleet spans TWO owners** — `example-adopter` and `example-adopter-two`. A query
  scoped to one returns a plausible, complete-shaped, wrong answer.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`), except in the three
  example-site-platform-managed repos (`example-site-platform`, `example-site-a`,
  `example-site-b`), which squash; do not amend published commits or
  force-push shared branches.
- **Keep this file under 32 KiB.** Codex truncates project instructions at
  that byte silently; the sync warns and the drift report flags
  `codex-truncated`.

<!-- END MANAGED SECTION -->
## Repo-specific additions

<!-- Add your repo-specific agent guidance below this line -->

- **Evals programme handoff.** Read [`HANDOFF.md`](HANDOFF.md) first: the whole
  systematic-evals effort (skills and fleet guidance, six epics), where it
  stands, what is parked on the owner and why, and how to resume it. Keep the
  live operational state on a status-board issue of your own, linked from
  `HANDOFF.md`'s "Current state".

- **Running tests in a fresh or cloud session.** Run `bash scripts/dev_setup.sh`
  first: it installs what `ci.yml`'s `test` job installs (the pip pins, the fixture
  `npm ci` directories and the fixtures' Ruby gems via `bundle install`), and `test/issues/test_issue_dev_setup.py`
  keeps the two in step. Then run the module that covers your change,
  `python3 -m pytest test/issues/<file>.py -q -n 2`, and report its count
  beside its exit code. `python3 -m unittest test.issues.<name>` cannot import
  (`test/` is not a package). The full suite,
  `python3 test/run_tests.py --jobs auto`, did not finish inside a 10-minute
  foreground command limit on 2 cores (2026-10-10), so start it in the
  background with its output in a log and poll, or let CI's `test` be the
  record. A cloud container has no `bwrap`, so tests of workspace-executing
  scorers fail closed there. Wrap any run in
  `unshare --user --map-current-user --pid --fork --mount-proc --`.

- **Workflow triggers and salient paths.** `test` (ci.yml) is the one
  required check. Keep this table in step when a trigger or filter moves.

  | Workflow | Triggers | Salient-path handling |
  |---|---|---|
  | `ci.yml` | `pull_request`, `push` to main, dispatch | `pull_request` unfiltered (required check); step `salient` gates the suite on `SALIENT_PATHS`, which must equal `on.push.paths` (`CiDispatchTests`) and includes `.github/workflows/actions.lock`, `.github/workflows/**` and `.github/actions/**`. The suite runs as a `suite` matrix of shards (`run_tests.py --shard K/N`, split by `test/shard.py`); the required `test` job passes only when every shard succeeded (`CiShardTests`) |
  | `agent-checks.yml` | `pull_request`, `push` to main/master, dispatch | none: the `pull_request` trigger is unfiltered and the job always runs `.githooks/agent-checks` over the change's commits (the fleet default check, _agent-guidance ADR 0021) |
  | `propagation.yml` | `pull_request`, `push` to main, schedule, dispatch | deliberately unfiltered: it probes an unpinned upstream registry (its header says why). `coverage` is informational and not required: absent optional registry access makes its census unresolved without testing a PR, so PR/push/dispatch record a warning while schedule fails. Only `coverage-publish` on schedule writes `persistent/eval-results`; the optional private checkout uses a token minted from the read-only registry GitHub App (variable `REGISTRY_READ_APP_CLIENT_ID`, secret `REGISTRY_READ_APP_PEM`) in that checkout step alone. |
  | `dependabot-auto-merge.yml` | `pull_request`, schedule, dispatch | acts only on Dependabot PRs whose changes stay inside the manifest-path allowlist |
  | `eval.yml` | dispatch only | no push or PR trigger; the owner's 2026-10-06 decision retired its Tuesday schedule because the [weekly routine](https://github.com/Adam-S-Daniel/skills-evals/pull/331) replaces it |
  | `roster-refresh.yml` | schedule, dispatch | no push or PR trigger, so no path filter; dispatches eval.yml with roster_only=true weekly so the roster follows current use |
  | `scheduled-run-health.yml` | schedule, dispatch | no push or PR trigger, so no path filter |
  | `routine-eval-fire.yml` | dispatch only | none needed; its `run-name` carries the mode and target (fixture, candidate, skill or section), which `scripts/improve_outcomes.py` and the allowance guard read; `guidance-improve` mode takes a `section` and runs `scripts/guidance_improve_gate.py fire-check` and `scripts/plan_week.py guidance-headroom` (refuses a section under 0.10 train headroom) before the bearer is in any env |
  | `routine-eval-weekly.yml` | schedule, dispatch | no push or PR trigger; dispatch (no inputs) reruns the sweep when GitHub skips the schedule, as on 2026-10-27; replaces `eval.yml`'s Tuesday schedule per the owner's 2026-10-06 decision; `scripts/plan_week.py` plans eval and improve fires by band within the allowance (ADR 0010 decision 4 addendum, 2026-10-26) and dispatches them one at a time through `routine-eval-fire.yml`, at most three in flight; the top eligible guidance section is one `guidance-improve` fire (`section`, `trials=3`); the plan job uploads the value-ranked improve candidates (skills and guidance sections) as the `improve-ranking` artifact |
  | `routine-eval-results-pushed.yml` | `push` to `claude/eval-*` | `paths: eval-results/**` (the routine's pinned result root); holds no permissions and runs nothing from the branch |
  | `routine-eval-ingest.yml` | `workflow_run` of the above, dispatch | runs the default branch's copy only; `scripts/ingest_routine_results.py` validates the branch and writes `persistent/eval-results` `routine-results/<run id>/` (ADR 0010 decision 2) |
  | `routine-improve-pushed.yml` | `push` to `claude/eval-improve-*` | `paths: eval-improve/**` (the one directory an improve branch adds to; disjoint from `eval-results/**`, which is what keeps it off the results ingest); holds no permissions and runs nothing from the branch |
  | `routine-improve-gate.yml` | `workflow_run` of the above, dispatch | runs the default branch's copy only; `scripts/improve_gate.py` validates the branch without running it, builds the registry commits with no credential (`prepare`), then a PR in acsi-agentskills is opened with a GitHub App token, minted only when secrets `EVAL_IMPROVE_APP_CLIENT_ID` and `EVAL_IMPROVE_APP_PEM` both exist; `evals/improve-policy.yml` `improve_merge_mode: auto` makes it ready with auto-merge (merge commit), `draft` (or a plugin that runs code) a draft (ADR 0005, routine improve mode and automatic merge addenda) |
  | `routine-guidance-improve-pushed.yml` | `push` to `claude/eval-guidance-improve-*` | `paths: eval-guidance-improve/**` (the one directory a guidance improve branch adds to; disjoint from `eval-results/**` and `eval-improve/**`, which keeps it off the results ingest and the skill gate); holds no permissions and runs nothing from the branch |
  | `routine-guidance-improve-gate.yml` | `workflow_run` of the above, dispatch | runs the default branch's copy only; `scripts/guidance_improve_gate.py` validates the branch without running it, builds the _agent-guidance commit with no credential (`prepare`, inside repo-settings ADR 0014's allowlist), then mints the eval-improve App token for `_agent-guidance` only under `continue-on-error` and opens the PR from `eval-guidance-improve/<section>`: ready with auto-merge (merge commit, `--match-head-commit`) in `improve_merge_mode: auto`, a draft otherwise; no token or a failed mint opens nothing and succeeds (ADR 0005, routine guidance improve mode addendum) |
  | `routine-scaffold-pushed.yml` | `push` to `claude/scaffold-*` | deliberately no path filter: every push must reach the gate, whose first job turns off auto-merge on the branch's open PR (an armed PR follows the branch); holds no permissions and runs nothing from the branch |
  | `routine-scaffold-gate.yml` | `workflow_run` of the above, dispatch | runs the default branch's copy only; `disarm` turns auto-merge off, `scripts/scaffold_real_work.py gate` validates the branch without running it (checker files must be the merge commit's own), then the roster App opens the PR ready, labeled `eval-scaffold`, with auto-merge on (merge commit, `--match-head-commit`); no App token opens a draft and arms nothing (ADR 0003's 2026-10-26 addendum) |
  | `routine-draft-skill.yml` | daily schedule, dispatch | no push or PR trigger, so no path filter; picks one `new-skill` lesson from acsi-agentskills-private's `lessons/` on `persistent/lessons` (read-only registry App token), skipping drafted subjects and existing skills, re-admitting subjects whose hook proposal was closed as not planned, and dispatches draft mode through `routine-eval-fire.yml` when one fire fits the weekly allowance |
  | `routine-draft-skill-pushed.yml` | `push` to `claude/draft-skill-*` | `paths: draft-skill/**, evals/**` (the two directories a draft branch adds to; no other routine signal matches the branch); holds no permissions and runs nothing from the branch |
  | `routine-draft-skill-gate.yml` | `workflow_run` of the above, dispatch | runs the default branch's copy only; `scripts/draft_new_skill.py validate` judges the branch without running it; hook-first opens a `Hook proposal: <subject>` issue (job token, `issues: write`); accepted opens a skill PR in acsi-agentskills (App token, as the improve gate) and then a fixtures PR here (job token, `contents: write`); both drafts, or in `improve_merge_mode: auto` (`improve_gate.py merge-mode`) both ready with auto-merge, the skill's first |
  | `routine-fixture-supply.yml` | daily schedule, dispatch | no push or PR trigger, so no path filter; mines merged fleet PRs, `scaffold_real_work.py pick` fires up to 3 new candidates in scaffold mode through `routine-eval-fire.yml` (within the allowance guard), and `scaffold_real_work.py promote` opens an auto-merging PR (roster App) that moves drafts with 2 clean ingested runs onto `evals/scheduled.yml` |
  | `skill-coverage.yml` | daily schedule, dispatch | no push or PR trigger; reads all four registries for a redacted census, publishes on schedule or explicit non-dry dispatch, and proposes up to three public GAP drafts; its `retirement` job runs on schedule only, computes the #70 verdicts from `persistent/eval-results` and acts on a `retire` one: in `retirement_mode: auto` (committed) a ready, `agent-authored`, auto-merging removal PR in acsi-agentskills, written with the improve gate's GitHub App token (`EVAL_IMPROVE_APP_CLIENT_ID`/`EVAL_IMPROVE_APP_PEM`, minted under `continue-on-error` in the step before the one that uses it, passed as `REGISTRY_TOKEN`), with full registry history (`fetch-depth: 0`); with no token, or for a plugin that runs code, it files or updates a retirement issue instead (`issues: write`, `--fallback-to-proposal`); it opens nothing while an `eval-improve/`, `draft-skill/` or `retire-skill/` PR is open there; then does the same for one guidance section (`--target guidance`, #124), opening the removal PR in `_agent-guidance` with an eval-improve App token minted for that repo alone, or filing the issue when that token is absent |

### Action pins

Workflows name third-party actions by release tag; [`.github/workflows/actions.lock`](.github/workflows/actions.lock) locks each tag to an exact commit, and the runner runs the locked commit even when it disagrees with the tag. The `test` job's "Verify action lockfile" step is the shared action [`Adam-S-Daniel/verify-actions-lock`](https://github.com/Adam-S-Daniel/verify-actions-lock); it runs `gh actions-lock --verify` only when a PR touches workflow dependencies (`.github/workflows/**`, `.github/actions/**`, an `action.yml`/`action.yaml`) and fails on any finding except `cooldown-config-ignored` (dependabot.yml's cooldown `exclude`, which the tool ignores). It is early feedback, not the only guard: the runner rejects forged pins, and GitHub's Require lockfile policy is on. Write the exact tag by hand, regenerate with `gh actions-lock --no-narrow`, and commit both files; review an `actions.lock` diff as you would a SHA pin. A stale or moved pin is fixed with `gh actions-lock --accept-moved`; review the diff. A `reachability-unknown` failure means the GitHub API was unreachable: re-run. Cross-repo reusable workflows (`jobs.<id>.uses`) are not locked, so they take a full commit SHA (`example-adopter/example-site-platform` refs excepted); a test enforces it. Fleet rule: [ADR 0024](https://github.com/example-adopter/_agent-guidance/blob/main/docs/decisions/0024-actions-pin-through-lockfiles.md).
