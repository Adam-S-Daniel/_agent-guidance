# 0021 — Actions are pinned through workflow lockfiles, and the lockfile is verified in a required check

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
- **A lockfile repo runs `gh actions-lock --verify` inside a required check**
  that runs on any change under `.github/workflows/` or a local action
  directory (a new workflow not yet onboarded fails `--verify`; a composite's
  inner actions are locked). The tool is installed from its release binary,
  checked against a sha256 written in the workflow, not by
  `gh extension install` on a tag.
- **The check fails on every pin finding; the allow-list holds one category,
  `cooldown-config-ignored`.** That warning (`valid: true`) says the tool could
  not read a `cooldown.exclude` in `dependabot.yml`; it describes Dependabot
  config, not a pin. Fleet repos exclude `Adam-S-Daniel/cms-platform/*` from
  the 7-day cooldown and do not `ignore` it, so the exclude is load-bearing and
  stays. No other category observed above is benign in a correctly locked
  repo: the benign-looking cases (Docker, major-only tag, branch, reusable
  workflow, orphan dependency) produce no finding at all, `sha-as-ref` only
  arises from a ref this form forbids, and `reachability-unknown` is the outage
  hole. The check prints each other finding's category and detail as an
  `::error::`. The reference implementation is `claude-memory-map`'s
  [ci.yml at 96f5266](https://github.com/Adam-S-Daniel/claude-memory-map/blob/96f5266d4a3b15471cbb6ed4755966b1408ab020/.github/workflows/ci.yml),
  step "Verify action lockfile" (copied from it, run under `set -euo pipefail`;
  jq escapes `%`, CR and LF so finding text cannot start a workflow command).
  `ai-usage-dashboard` has no `dependabot.yml` and runs the same step without
  the allow-list:

  ```bash
  rc=0
  out=$(gh actions-lock --verify --no-interactive --json=valid,findings) || rc=$?
  # v0.2.0 exits 0 with a reachability-unknown finding when the API fails; fail on any finding
  # except cooldown-config-ignored, which reports dependabot.yml's cooldown exclude, not a pin.
  if [[ "$rc" -ne 0 ]] || ! jq -e '.valid == true and ([.findings[] | select(.category != "cooldown-config-ignored")] | length) == 0' <<<"$out" >/dev/null; then
    # Escape %, CR and LF so finding text cannot start a new workflow command.
    if jq -e 'type == "object"' <<<"$out" >/dev/null 2>&1; then
      jq -r 'def esc: tostring | gsub("%"; "%25") | gsub("\r"; "%0D") | gsub("\n"; "%0A"); .findings[]? | select(.category != "cooldown-config-ignored") | "::error::actions.lock \(.category | esc): \(.dependency // .workflow // "-" | esc): \(.detail | esc)"' <<<"$out"
    else
      echo '::error::gh actions-lock --verify returned no readable JSON.'
    fi
    if jq -e 'any(.findings[]?; .category == "reachability-unknown")' <<<"$out" >/dev/null 2>&1; then
      echo '::error::reachability-unknown means the GitHub API was unreachable; re-run once it is back.'
    fi
    echo "::error::gh actions-lock --verify failed (exit $rc)."
    exit 1
  fi
  ```

- **An `actions.lock` diff is reviewed like a SHA pin.** It, not the tag in
  the workflow, decides what code runs.
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
- **The guard is a CI step, not the platform.** A repo that adopts the
  lockfile without the verify check has a pin that only *looks* tied to its
  tag. An API outage now fails the check; re-run it once the API is back.
- **Facts every check and agent must allow for:**
  - Lockfile `commit` values read `sha1-<40hex>`, not bare hex.
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
  29,696-byte full build: with this section it is 29,694, and `main` was
  29,632. That is why the tables, recovery, the `cms-platform` consumer rule,
  the `sha1-` prefix, the `could not verify` exit code and the linter results
  live here rather than in the section, and the next
  `base.md` addition has to trim something first.
