# 2026-11-13: ADR 0024's gh-actions-lock experiments, re-run on v0.4.1

Evidence for [ADR 0024](../decisions/0024-actions-pin-through-lockfiles.md),
whose two tables were first measured on gh-actions-lock v0.2.0
([2026-10-20 evidence](2026-10-20-stale-lockfile.md), kept as it was). This
file holds the v0.4.1 re-run asked for in
[_agent-guidance#531](https://github.com/Adam-S-Daniel/_agent-guidance/issues/531),
under [ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md).

## Setup

- **Binary.** Installed by
  [verify-actions-lock](https://github.com/Adam-S-Daniel/verify-actions-lock)'s
  `scripts/install.sh`, unmodified, from a fresh shallow clone at `88d8fda`.
  The script's own `sha256sum -c` passed on
  `39deb2fe714dbf74b073d8245fc7f3d50cd0eee18d5c0900e9e849ae933ae17e`
  (`linux-amd64`); `gh actions-lock --version` reads `v0.4.1`.
  `RUNNER_TEMP`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME`
  pointed into a temporary directory, so the extension and its run logs
  lived there; `gh` authenticated through `GH_TOKEN` for each process.
- **Host.** The owner's workstation (WSL), where GitHub GraphQL is reachable.
  Two earlier attempts in cloud sessions stopped because their proxy refuses
  GraphQL, which both `gh release download` and the extension's tag
  resolution need.
- **Repositories.** One fresh `git init` per case with no remote
  (`git remote -v` empty, checked before every run).
- **Method, as in 2026-10-20.** Each case is locked with
  `gh actions-lock --no-narrow --no-interactive`, then checked with
  `gh actions-lock --verify --no-interactive --json=valid,findings`. Exit codes
  below are the extension's own, captured unpiped.
- **Only v0.4.1 was run.** No other version was downloaded, so the v0.4.0 side
  of the nested-pin case is not reproduced (see that section).

Fixed commits used: `actions/checkout` `v7.0.1` →
`3d3c42e5aac5ba805825da76410c181273ba90b1`, `v7.0.0` →
`9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`.

## Case table

| Case | Workflow `uses:` / setup | Lock run | Lockfile | `--verify` |
|---|---|---|---|---|
| Docker URL step | `docker://alpine:3.20` | exit 0, prints `container image alpine:3.20 is not pinned by digest` | `ci.yml: []` | exit 0, `valid: true`, `container-image` (warning) |
| Docker-container action | `actions/hello-world-docker-action@v2` | exit 0 | entry `ref: 'v2'`, commit `0b406c0…`; base image not covered | exit 0, `valid: true`, `container-image` (warning) |
| Major-only tag | `actions/hello-world-docker-action@v1` (tags: `v1`, `v2` only) | exit 0 | entry `ref: 'v1'`, commit `9a36e3c…` | exit 0, `valid: true`, `container-image` (warning, from the Docker action, not the tag) |
| Branch ref | `actions/checkout@main` | exit 0 | entry `ref: 'main'`, commit `f548e57…` | exit 0, `valid: true`, no findings |
| Bare SHA ref | `actions/checkout@3d3c42e…` | exit 0 | entry keyed `actions/checkout@3d3c42e…`, `ref: 'v7.0.1'` | exit 0, `valid: true`, `sha-as-ref` (warning) |
| Entry the workflow no longer uses | lock `@v7.0.0`, then edit the workflow to `@v7.0.1` | exit 0 | unchanged (`v7.0.0`) | exit 1, `valid: false`, `ref-changed` + `stale` (errors) |
| Dependency listed under no workflow | lock `@v7.0.1`, then append a `actions/checkout@v7.0.0` dependency no workflow lists | exit 0 | extra entry present | exit 0, `valid: true`, no findings |
| Workflow with no entry | lock `ci.yml`, then add `second.yml` | exit 0 | `second.yml` absent | exit 1, `valid: false`, `not-pinned` (error) |
| `./` local action, plain run | composite `./.github/actions/x` using `actions/github-script@v7.0.1` | **exit 1**, `local-action`; "Nothing was written"; suggests `--migrate-local-actions` or `--ignore-local-actions` | none; `./` left as is | exit 1, `valid: false`, `not-pinned` + `local-action` (errors) |
| `./` with `--no-migrate-local-actions` | same | exit 0 ("Cannot follow 1 workspace-local action"); the flag is accepted but no longer in `--help` | `ci.yml: []` | exit 1, `valid: false`, `local-action` (error) |
| `./` with `--migrate-local-actions` | same | exit 0, "Migrated 1 local action to `$/…`" | `uses:` rewritten to `$/.github/actions/x`; inner `actions/github-script@v7.0.1` locked | exit 0, `valid: true`, no findings |
| `$/` written by hand | `$/.github/actions/x` | exit 0 | inner `actions/github-script@v7.0.1` locked (`60a0d83…`) | exit 0, `valid: true`, no findings |
| Cross-repo reusable, branch | `actions/reusable-workflows/.github/workflows/check-dist.yml@main` | exit 0 | `ci.yml: []` | exit 0, `valid: true`, no findings |
| Cross-repo reusable, SHA | same `@e9d8b5a26aba5d8c9e1d44aa475ac98d0256ae29` | exit 0 | `ci.yml: []` | exit 0, `valid: true`, no findings |
| Cross-repo reusable, tag (`cms-platform`) | `Adam-S-Daniel/cms-platform/.github/workflows/scheduled-run-health.yml@v0.1.163` | exit 0 | `ci.yml: []` | exit 0, `valid: true`, no findings |
| Same-repo reusable | caller `./.github/workflows/called.yml`; `called.yml` uses `actions/checkout@v7.0.1` | exit 0 | caller `[]`; `called.yml` onboarded with checkout locked; `./` not rewritten | exit 0, `valid: true`, no findings |
| API unreachable | lock `@v7.0.1`, verify with `HTTPS_PROXY=http://127.0.0.1:9` | exit 0 (before the proxy) | — | exit 1, `valid: false`, `reachability-unknown` (**error**, `"retryable": true`), after "retrying in 1s, attempt 2/3" |
| Pin disagreeing with its tag | lockfile commit for `@v7.0.1` hand-edited to v7.0.0's `9c091bb…` | — | — | exit 1, `valid: false`, `tag-commit-mismatch` (error), remediation `--relock` |
| Pin not in history | lockfile commit hand-edited to `0123456789abcdef0123456789abcdef01234567` | — | — | exit 1, `valid: false`, `unreachable-pin` (error), remediation `--accept-moved` |

`actions/reusable-workflows` has no tags, so the cross-repo tag case rests on
the `cms-platform` row, as the v0.2.0 table's did.

## Recovery

A fresh repository per cell: lock `actions/checkout@v7.0.1`, hand-edit its
commit, run the command, then `--verify`.

| Command | Mismatched pin (`9c091bb…`, in history) | Unreachable pin (`0123456…`) |
|---|---|---|
| `gh actions-lock --no-narrow --no-interactive` | exit 1, `tag-commit-mismatch`, nothing written; verify exit 1 | exit 1, `unreachable-pin`, nothing written; verify exit 1 |
| `… --relock` | exit 0, commit now `3d3c42e…`; verify exit 0 | exit 1, `unreachable-pin`, nothing written; verify exit 1 |
| `… --accept-moved` | exit 0, `3d3c42e…`; verify exit 0 | exit 0, `3d3c42e…`; verify exit 0 |
| `… --relock --accept-moved` | exit 0, `3d3c42e…`; verify exit 0 | exit 0, `3d3c42e…`; verify exit 0 |

Same outcomes as v0.2.0. What changed is the label on the mismatched pin: it
is `tag-commit-mismatch`, and the tool now suggests `--relock` for it and
`--accept-moved` for an unreachable pin.

## Nested pin across a composite bump (github/gh-actions-lock#257)

[github/gh-actions-lock#257](https://github.com/github/gh-actions-lock/issues/257):
a v0.4.0-era regeneration of `DeterminateSystems/update-flake-lock` `v28` →
`v29` dropped the one child pin that did not change across the bump,
`DamianReeves/write-file-action@6929a9a6d1807689191dcc8bbe62b54d70a32b42`, and
`--verify` then failed with "Lockfile doesn't match locked manifest".

On v0.4.1:

1. `gh actions-lock --no-narrow --no-interactive` on
   `DeterminateSystems/update-flake-lock@v28`: exit 0, "Pinned 1 action (+ 5
   transitive)"; the parent entry (`834c491…`) lists five `uses:` children,
   `damianreeves/write-file-action@6929a9a…` among them, and each has its own
   `dependencies:` entry.
2. The workflow is edited to `@v29` and the same command re-run: exit 0,
   "Pinned 1 action (+ 5 transitive)"; the `v28` entries are gone, the parent
   (`da03c0f…`) again lists five children, and
   `damianreeves/write-file-action@6929a9a…` is still both in its `uses:` and
   in `dependencies:`. The other four children moved to new commits.
3. `--verify`: exit 0, `valid: true`, no findings.

**Not reproduced: v0.4.0 dropping the pin.** Only the v0.4.1 binary that
verify-actions-lock's `install.sh` pins was installed; no other version was
downloaded. The regeneration here is `gh actions-lock` itself, not
Dependabot's updater, which is where #257 was reported.

## Where v0.4.1 departs from a rule ADR 0024 states

Recorded here and in the pull request; the ADR's rules and `base.md` were not
edited.

- **`./` is no longer migrated by default.** The ADR (and `base.md`'s
  "Pinning GitHub Actions") say the default run rewrites `./` to `$/`. On
  v0.4.1 the default run fails on a `./` local action and writes nothing;
  `--migrate-local-actions` does the rewrite.
- **A bare SHA no longer fails the shared check.** The ADR says a bare SHA
  "trips `sha-as-ref`, which fails the check". On v0.4.1 `sha-as-ref` is a
  warning with `valid: true` and exit 0, and the check (verify-actions-lock
  v1.1.0 onward) passes warnings.
- **`unreachable-pin` no longer fires for a pin that merely disagrees with
  its tag.** The ADR's facts list says it does; on v0.4.1 an older commit in
  the tag's history is `tag-commit-mismatch`, and `unreachable-pin` means the
  commit is not in the history. The recovery rule (`--accept-moved` fixes
  both) still holds.
