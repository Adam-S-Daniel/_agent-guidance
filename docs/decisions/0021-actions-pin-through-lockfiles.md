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
([PR #11](https://github.com/Adam-S-Daniel/ai-usage-dashboard/pull/11)) found two
things the documentation does not say
([evidence](../evidence/2026-10-20-stale-lockfile.md)):

1. **The runner trusts the lockfile over the tag.** A lockfile pointing
   `actions/checkout@v7.0.1` at v7.0.0's commit ran that commit with no error.
   The runner rejects an unreachable commit, not a mismatched one. Only
   `gh actions-lock --verify` caught it.
2. **`--verify` passes when it cannot check.** v0.2.0 exits 0, with
   `"valid": true` and `reachability-unknown` findings, when GitHub's API
   fails.

## Decision

Two pin forms are accepted while repos migrate; the lockfile form is the
target.

- **Lockfile form.** The repo commits `.github/workflows/actions.lock` with
  every workflow onboarded, and every non-local `uses:` names an exact release
  tag (`vX.Y.Z`). Generate with
  `gh actions-lock --no-narrow --no-migrate-local-actions`: by default the tool
  narrows a partial tag and rewrites `./` local actions to `$/`, and on first
  pin it rewrites a bare SHA to a tag.
- **Legacy form,** until a repo migrates: a full 40-character SHA with no
  trailing comment, exactly as 0007 decided.
- **A lockfile repo runs `gh actions-lock --verify` inside a required check**
  that runs on any change under `.github/workflows/` (a new workflow that is not
  onboarded fails `--verify`, so the PR that adds it must be checked). The tool
  is installed from its release binary, checked against a sha256 written in the
  workflow, not by `gh extension install` on a tag. The check fails on **any**
  finding, not on the exit code alone. The reference implementation is
  `ai-usage-dashboard`'s
  [ci.yml at 405217a](https://github.com/Adam-S-Daniel/ai-usage-dashboard/blob/405217aaaf0a212aae8a036a2598f133694f6dfe/.github/workflows/ci.yml),
  step "Verify action lockfile":

  ```bash
  out=$(gh actions-lock --verify --no-interactive --json=valid,findings) || rc=$?
  jq -e '.valid == true and (.findings | length) == 0' <<<"$out"
  ```

- **An `actions.lock` diff is reviewed like a SHA pin.** It, not the tag in
  the workflow, decides what code runs.

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
  - Docker images (`docker://`, a Docker action's image) get no lockfile
    entry.
  - Dependabot regenerates the matching lockfile entry in its own version-update
    PR, but does not onboard a new workflow.
  - "Pinned commit not in upstream history" also fires when a pin merely
    disagrees with its tag.
- **Recovery** (always review the lockfile diff): a ref that legitimately moved
  takes `gh actions-lock --relock`; a locked commit no longer reachable upstream
  takes `gh actions-lock --relock --accept-moved` (`--relock` alone leaves
  unreachable pins as errors).
- **The verify tool is pinned by digest, and the digest moves by hand.** It is
  a preview extension (v0.2.0); a new release means a new sha256 in every lockfile
  repo's workflow, in the spirit of
  [0008](0008-downloaded-binaries-are-pinned-and-digest-checked.md).
- **Two forms in the tree for a while.** Repo-local checks that assert 40-hex
  `uses:` (this repo's `test/run-tests.sh` and
  `test/test-dependabot-config-health.js` among them) stay until that repo
  migrates its workflows; migration and its test change land together.
- **`base.md` is at its budget.** The binding limit is `test/run-tests.sh`'s
  29,696-byte full build: with this section it is 29,672, and `main` was
  29,632. That is why the evidence link, recovery commands and tool facts live
  here rather than in the section, and the next `base.md` addition has to trim
  something first.
