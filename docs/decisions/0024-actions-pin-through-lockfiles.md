# 0024 — Actions are pinned through workflow lockfiles, and the lockfile is verified in a required check

**Status:** Accepted (2026-10-20). Amends [0007](0007-sha-pins-carry-no-version-comment.md):
its no-comment rule still governs the legacy SHA form, but a SHA is no longer
the only accepted pin.

## Context

Until now every `uses:` in the fleet was a full 40-character SHA with no
version comment (0007). That is safe but unreadable: the version a pin stands
for costs a lookup, and 0007 accepted that price because the alternative, a
comment, lied.

GitHub workflow lockfiles reached public preview on 2026-10-14. A repo commits
`.github/workflows/actions.lock`; workflows name a tag, and the lockfile records
the exact commit (plus owner and repository IDs) each tag resolved to. The
runner then downloads the locked commit, not whatever the tag points at today.
That gives readable `uses:` lines and an immutable pin together, and
`sha_pinning_required: true` accepts a locked tag (verified live on
`ai-usage-dashboard`, where the setting is on and CI on tag refs is green).

The pilot on `ai-usage-dashboard`
([PR #11](https://github.com/Adam-S-Daniel/ai-usage-dashboard/pull/11)) and the
experiments below were run with `gh-actions-lock` v0.2.0 on 2026-10-20. Two
results drive this decision ([evidence](../evidence/2026-10-20-stale-lockfile.md)):

1. **The runner trusts the lockfile over the tag.** A lockfile pointing
   `actions/checkout@v7.0.1` at v7.0.0's commit ran that commit with no error.
   Only `gh actions-lock --verify` caught it.
2. **`--verify` passes when it cannot check.** It exits 0, with
   `"valid": true` and `reachability-unknown` findings, when GitHub's API
   fails.

### What the tool and the runner do, tested

Each case was locked with `gh actions-lock --no-narrow` and then checked with
`gh actions-lock --verify --no-interactive --json=valid,findings` (exit code,
`valid`, finding categories).

| Case | Lockfile | `--verify` |
|---|---|---|
| `docker://alpine:3.20` step | no entry | exit 0, valid, no findings |
| Docker-container action (`actions/hello-world-docker-action@v2`) | action repo commit only; the image is not locked | exit 0, no findings |
| Major-only tag (`…@v1`, no `vX.Y.Z` exists) | entry with `ref: 'v1'` | exit 0, no findings |
| Branch ref (`actions/checkout@main`) | entry with `ref: 'main'` | exit 0, no findings |
| Bare SHA ref in a lockfile repo | entry | exit 0, `sha-as-ref` (warning) |
| Workflow listing an entry it no longer uses | — | exit 1, `stale` + `ref-changed` (errors) |
| Dependency listed under no workflow | — | exit 0, no findings (ignored) |
| Workflow with no lockfile entry | — | exit 1, `not-pinned` |
| `./` local action with `--no-migrate-local-actions` | workflow onboarded with no entries | exit 1, `local-action` ("not supported") |
| Same, written `$/` (the tool's default rewrite) | the composite's inner actions locked transitively | exit 0, no findings |
| Cross-repo reusable workflow, by tag, branch or SHA (`jobs.<id>.uses`) | workflow onboarded as `[]`; no entry | exit 0, no findings |
| Same-repo reusable workflow, `./.github/workflows/<x>.yml` | left as `./` (not rewritten); caller `[]`, the called workflow onboarded with its own actions locked | exit 0, no findings |
| `Adam-S-Daniel/cms-platform/.github/workflows/scheduled-run-health.yml@v0.1.163` | `[]`; no entry | exit 0, no findings |
| API unreachable | — | exit 0, `reachability-unknown` (warning) |
| Pin disagreeing with its tag | — | exit 1, `unreachable-pin` |

Linters, on a workflow with a `$/` action step and a `$/` workflow call:

- **actionlint 1.7.7 and 1.7.12 reject both.** The step: `specifying action "$/.github/actions/x" in invalid format because ref is missing` `[action]`; the call: `reusable workflow call "$/.github/workflows/c.yml" at "uses" is not following the format "owner/repo/path/to/workflow.yml@ref" nor "./path/to/workflow.yml"` `[workflow-call]`. No upstream fix was found.
- **zizmor 1.31.1 accepts them** (`--offline`: no findings).

`cms-platform` runs actionlint as a required lane, and 41 of its 48 local refs are `./.cms-platform/.github/actions/*`, into a checkout made at runtime: `$/` cannot express that path and `--verify` rejects the `./` form as `local-action`.

At runtime ([run 39359501093](https://github.com/Adam-S-Daniel/ai-usage-dashboard/actions/runs/39359501093),
a temporary probe reverted the next commit):

- **The reusable-workflow job ran the ref's live commit**
  (`Uses: actions/reusable-workflows/.github/workflows/check-dist.yml@refs/heads/main (d468c63…)`)
  with no "Using locked action versions" line, and the called workflow's own
  steps ran on the SHAs it pins itself. The caller's lockfile governs neither.
  (The job then failed at `npm ci` for lack of a package lockfile, which is
  unrelated.)
- **The `$/` composite job used the lockfile**: `$/` resolved to the repository
  at the run's commit, and the composite's inner `actions/github-script@v7.0.1`
  downloaded its locked SHA.

Recovery, tested on a hand-edited lockfile (a commit reachable but not the
tag's, and a commit that does not exist):

| Command | Mismatched pin | Unreachable pin |
|---|---|---|
| `gh actions-lock` (plain fix run) | still fails | still fails |
| `--relock` | fixed | **still fails** |
| `--accept-moved` | fixed | fixed |
| `--relock --accept-moved` | fixed | fixed |

## Decision

Two pin forms are accepted while repos migrate; the lockfile form is the
target.

- **A repo that lints with actionlint and has local actions, or has `./` refs
  into a runtime checkout, stays in legacy form.** `cms-platform` is the named
  case, until actionlint accepts `$/` and gh-actions-lock can lock or skip
  runtime-checkout local refs. A repo with no local actions is not blocked by
  actionlint: a same-repo `./` workflow call stays `./`, which it accepts. The
  carve-out below governs refs *into* `cms-platform` from any repo, which stay
  on its release tag; `cms-platform`'s own third-party pins stay on full SHAs
  while it is legacy.

- **Lockfile form.** The repo commits `.github/workflows/actions.lock` with
  every workflow onboarded. Write each `uses:` by hand on the action's most
  exact tag, `vX.Y.Z` when one exists, otherwise the most specific tag it has
  (`v1`), and a branch only when it has no tag. Then run
  `gh actions-lock --no-narrow`, which keeps the ref as written and locks it.
  Local actions are written `$/<path>`: `--verify` rejects `./`, and the
  default run rewrites `./` to `$/` (so do not pass
  `--no-migrate-local-actions`). Never a bare SHA in this form: it trips
  `sha-as-ref`, which fails the check below.
- **Legacy form,** until a repo migrates: a full 40-character SHA with no
  trailing comment, exactly as 0007 decided.
- **Cross-repo reusable workflows take a full SHA in both forms.** No
  lockfile locks them; the caller runs the ref's live commit. Neither
  `--verify` nor `sha_pinning_required` flags a tag or branch there, so only
  review or a repo's own check catches one; the pilot's is a Pester test that
  parses every workflow and requires `@<40hex>` on each `jobs.<id>.uses` that
  is not local or `cms-platform`. The `cms-platform` release-tag carve-out is
  the one exception, as before. A same-repo `./.github/workflows/<x>.yml` call
  stays `./`. A workflow whose jobs are all reusable-workflow calls still
  needs its `[]` entry, which the tool writes.
- **A lockfile repo runs the shared action
  [`Adam-S-Daniel/verify-actions-lock`](https://github.com/Adam-S-Daniel/verify-actions-lock)
  in a job that is a required check and runs on every pull request.** The
  action decides for itself whether to verify: on a pull request it runs
  `gh actions-lock --verify` if and only if the change touches
  `.github/workflows/**`, `.github/actions/**` or an `action.yml`/`action.yaml`
  (a new workflow not yet onboarded fails `--verify`; a composite's inner
  actions are locked), and runs whenever it cannot tell; on other events it
  skips, so the required check always reports. It installs gh-actions-lock
  from the release binary checked against a sha256 written in the action, not
  by `gh extension install` on a tag. It is referenced by release tag
  (`@v1.0.0`) and locked in each repo's `actions.lock` like any action: a bare
  SHA ref would trip `sha-as-ref`.
- **The repo also turns on GitHub's Require lockfile policy** (Settings →
  Actions → Policies) once its migration PR merges. The policy is GitHub's
  runtime enforcement point: it blocks a targeted workflow that is not
  onboarded, which covers a deleted or unreadable lockfile that the runner
  would otherwise ignore (github/gh-actions-lock#125). The Actions policies
  REST API documents no rule type for it as of 2026-10-20, so repo-settings
  cannot declare it yet; it is set in the UI until then.
- **The check fails when the tool fails or reports an error; warnings stay
  warnings** (as of verify-actions-lock v1.1.0, which runs gh-actions-lock
  v0.3.0). It fails when `--verify` exits non-zero, reports `valid` other than
  `true`, or reports any finding whose severity is not `warning`. Warnings —
  v0.3.0's `container-image` (images it cannot lock), `ref-moved` and
  fresh-tag notices — are printed as `::warning::` and do not fail. Every
  finding is printed with `%`, CR and LF escaped so its text cannot start a
  workflow command. All of this lives in the shared action's
  `scripts/check.sh`. Before v0.3.0 the check failed on every finding but
  `cooldown-config-ignored` (a Dependabot-config warning v0.2.0 raised in every
  repo with a cooldown `exclude`), because v0.2.0 exited 0 on unverifiable pins;
  the five wave-1 repos ran that logic inline before the shared action existed
  (for example claude-memory-map's
  [ci.yml at 96f5266](https://github.com/Adam-S-Daniel/claude-memory-map/blob/96f5266d4a3b15471cbb6ed4755966b1408ab020/.github/workflows/ci.yml)).
- **A GitHub API outage fails the check** (decided 2026-10-20). From v0.3.0
  the tool does this itself: a pin GitHub did not answer for is an error with
  `"retryable": true`. Re-run once the API is back; it is cheap because the
  check only verifies when workflow dependencies change (and on `schedule`).
- **An `actions.lock` diff is reviewed like a SHA pin.** It, not the tag in
  the workflow, decides what code runs.
- **Anything that treats a workflow pin change as special treats
  `.github/workflows/actions.lock` the same way.** Dependabot rewrites it in
  its own action-bump PRs, so a Dependabot auto-merge path allowlist that
  accepts `.github/workflows/*.yml` must also accept that exact path, or every
  action bump stops auto-merging. A gate that holds changes to its own
  workflow for owner approval (adam-agentskills' `plugin_runtime_gate.py`)
  must also gate the lockfile, which now chooses the commit its checkout
  runs. A salient-path diff runs with `--no-renames`, so moving a workflow out
  of `.github/workflows/` still runs the verify step.
- **A repo that references a `cms-platform` composite action stays in legacy
  form until the platform bump regenerates its `actions.lock`.** A composite
  action ref gets a lockfile entry, so a bump that only rewrites the
  `@v0.1.x` tag leaves a `ref-changed`/`stale` lockfile and fails the check. A
  repo that calls only `cms-platform` reusable workflows is unaffected: those
  refs get no entry.

Unchanged: the 7-day wait after a third-party release, annotated-tag
dereferencing, the `cms-platform` release-tag carve-out, and
`sha_pinning_required: true`.

## Consequences

- **Readable pins without a lying label.** The tag in `uses:` is checked
  against the lockfile on every relevant PR, so unlike 0007's comment it cannot
  drift silently.
- **Three layers, each with its own job.** The runner rejects forged pins (a
  commit outside the ref's history, a changed repo identity) but accepts any
  locked commit in the tag's history, so a downgrade within a tag runs. The
  Require lockfile policy blocks un-onboarded workflows at run time. The PR
  check gives early feedback and enforces the stricter rule that each locked
  commit is what its tag points at now. Review of the `actions.lock` diff is
  still the control GitHub names ("A lockfile is only a security control if
  someone reads the diff").
- **Why the check is ours and not GitHub's.** GitHub's 2026 Actions security
  roadmap aims for "fewer per-workflow configurations, and more centralized,
  enforceable policy", and its enforcement guide treats the CI check as
  author feedback beside the policy; it promises "ready-to-copy
  implementations" in the gh-actions-lock repository, none of which existed on
  2026-10-20. No community action was worth adopting (one installs unpinned
  and trusts the exit code; another uses a different lockfile format). The
  shared action is a thin wrapper meant to be replaced by an official one.
- **Upstream status: fixed and released in gh-actions-lock
  [v0.3.0](https://github.com/github/gh-actions-lock/releases/tag/v0.3.0)
  (2026-10-22).** Four issues filed from this pilot drew same-day fixes:
  [#199](https://github.com/github/gh-actions-lock/issues/199) →
  [#207](https://github.com/github/gh-actions-lock/pull/207) and
  [#209](https://github.com/github/gh-actions-lock/pull/209) (an unverifiable
  pin is an error, exit 1, `valid: false`, `"retryable": true`);
  [#201](https://github.com/github/gh-actions-lock/issues/201) →
  [#204](https://github.com/github/gh-actions-lock/pull/204) (`--verify` no
  longer reports `cooldown-config-ignored`);
  [#202](https://github.com/github/gh-actions-lock/issues/202) →
  [#205](https://github.com/github/gh-actions-lock/pull/205) and
  [#208](https://github.com/github/gh-actions-lock/pull/208) (an exact tag
  locked to an older commit in its history is a `tag-commit-mismatch` error,
  fixed with `--relock`); and
  [#200](https://github.com/github/gh-actions-lock/issues/200) →
  [#233](https://github.com/github/gh-actions-lock/pull/233), an official
  sample workflow. The shared action adopted v0.3.0 in v1.1.0, inside the
  7-day wait on the owner's decision after a review of the v0.2.0..v0.3.0
  diff and a verified build attestation (release workflow at the `v0.3.0`
  tag); every lockfile repo passed v0.3.0 `--verify` with no findings first.
  It keeps two differences from the official sample: a checksum-verified
  install and verifying only when workflow dependencies change (plus on
  `schedule`, which the sample does weekly). Whether those justify keeping
  the action is tracked in
  [verify-actions-lock#2](https://github.com/Adam-S-Daniel/verify-actions-lock/issues/2).
- **Facts every check and agent must allow for:**
  - Lockfile `commit` values read `sha1-<40hex>`, not bare hex.
  - Lockfile keys lowercase the owner/repo part
    (`adam-s-daniel/verify-actions-lock@v1.0.0`); a check that looks a `uses:`
    up compares the part before the last `@` case-insensitively and the ref
    exactly.
  - Docker images (`docker://`, a Docker action's image) are not locked.
  - Dependabot regenerates the matching lockfile entry in its own version-update
    PR, but does not onboard a new workflow (GitHub's lockfile docs).
  - "Pinned commit not in upstream history" (`unreachable-pin`) also fires when
    a pin merely disagrees with its tag.
- **Recovery:** `gh actions-lock --accept-moved`. It re-resolves only the
  flagged pins to the tag's current commit and fixes both a mismatched and an
  unreachable pin. `--relock` alone leaves an unreachable pin failing and
  advances every moved ref in the repo. GitHub's docs suggest
  `--relock --accept-moved`, which behaved the same in these tests but is
  broader. Always review the lockfile diff.
- **The verify tool is pinned by digest, and the digest moves by hand.** It is
  a preview extension (v0.2.0); a new release means a new sha256 in every lockfile
  repo's workflow, in the spirit of
  [0008](0008-downloaded-binaries-are-pinned-and-digest-checked.md).
- **Two forms in the tree for a while.** Repo-local checks that assert 40-hex
  `uses:` (this repo's `test/run-tests.sh` and
  `test/test-dependabot-config-health.js` among them) stay until that repo
  migrates its workflows; migration and its test change land together.
- **`base.md` is at its budget.** The binding limit is `test/run-tests.sh`'s
  29,696-byte full build: with this section it is 29,695, and `main` was
  29,632. That is why the tables, recovery, the `cms-platform` consumer rule,
  the `sha1-` prefix, the `could not verify` exit code and the linter results
  live here rather than in the section, and the next
  `base.md` addition has to trim something first.
