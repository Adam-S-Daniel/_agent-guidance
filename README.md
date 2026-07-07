# _agent-guidance

The **guidance layer** of Adam Daniel's agent setup: shared `AGENTS.md` content
and the sync machinery that propagates it into every repo in the account, with a
nightly drift dashboard.

This repo deliberately does **not** carry skills. Reusable skills live in the
canonical registry, [Adam-S-Daniel/agentskills](https://github.com/Adam-S-Daniel/agentskills)
(private/sensitive ones in `agentskills-private`) — consume them from there via
the plugin marketplace or that repo's `setup.sh`. The two-layer split (skills vs
guidance) is documented in the registry's
[`STRATEGY.md`](https://github.com/Adam-S-Daniel/agentskills/blob/main/STRATEGY.md).
The skills that used to live here (`debug-github-workflows`,
`review-bash-ci-reliability`) were promoted into the registry.

## How it works

- `agents-md/base.md` + `agents-md/sections/<name>.md` — the managed guidance
  content, composed per-repo by `scripts/build-agents-md.sh`.
- Consumer repos opt into sections by dropping a `.agents-sync.yml` in their root
  (see `.agents-sync.example.yml`).
- `scripts/sync.sh` (CI: `sync.yml`) scans the account's repos, rebuilds each
  repo's managed `AGENTS.md` portion, preserves anything under
  `## Repo-specific additions`, and opens/updates a PR when content changed.
- `scripts/drift-report.sh` (CI: nightly `drift-report.yml`) writes
  `drift-report.md`, a dashboard of which repos are missing or out of date.
  This tracks **AGENTS.md drift** — it is the guidance layer working as
  designed, not the skill-copy drift that the strategy consolidation removed.
- `scripts/sync.sh` / `scripts/drift-report.sh` consult the central
  `repos.yml` registry for repos excluded from sync entirely and for
  `default_sections` applied to repos with no `.agents-sync.yml` of their own.

## Required secrets

The sync workflow scans repos across **two GitHub accounts** —
`Adam-S-Daniel` and the `jodidaniel` org (see `SYNC_OWNERS` in `sync.yml`) —
so whatever token(s) back the sync need `contents:write` and
`pull-requests:write` on **all target repos in both accounts**. `sync.sh` and
`drift-report.sh` resolve a **per-owner token** at the top of each owner's
loop iteration: for owner `$ORG` they look for `GH_TOKEN_<OWNER>`, where
`<OWNER>` is `$ORG` uppercased with `-` and `.` mapped to `_` (e.g.
`Adam-S-Daniel` → `GH_TOKEN_ADAM_S_DANIEL`, `jodidaniel` →
`GH_TOKEN_JODIDANIEL`). If that's set it's used for the whole iteration; if
not, they fall back to the plain `GH_TOKEN` captured before the loop started
(restored on every iteration, so one owner's per-owner token never leaks into
another's). There are three ways to supply these, in order of preference:

### a) Recommended: GitHub App (no rotation, ever)

Create one GitHub App (any name, e.g. `agents-md-sync`) with permissions
`Contents: read & write`, `Pull requests: read & write`, and
`Metadata: read`, then install it on **both** the `Adam-S-Daniel` account and
the `jodidaniel` org. Store the app ID as a repository variable (`APP_ID`)
and the private key as a repository secret, then mint short-lived
installation tokens per owner at workflow runtime with
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)
(pin it to a full commit SHA when adopting this). Installation tokens live
1 hour and are minted fresh on every run, so nothing ever expires and there
is no PAT to rotate.

This mode is **documented but not yet wired up** — the workflows currently
run in PAT mode (below). Adopting it means replacing the `secrets.*`
references in `sync.yml`/`drift-report.yml` with `create-github-app-token`
steps that populate `GH_TOKEN_ADAM_S_DANIEL` / `GH_TOKEN_JODIDANIEL`.

### b) Two fine-grained PATs (single-owner scope each)

A fine-grained PAT is scoped to a single GitHub resource owner, which maps
cleanly onto the per-owner token resolution above. Create one fine-grained
PAT per account with `contents:write` + `pull-requests:write` on all target
repos, and add them as repository secrets:

- `AGENTS_SYNC_READWRITE_TOKEN_ADAM_S_DANIEL`
- `AGENTS_SYNC_READWRITE_TOKEN_JODIDANIEL`

Optionally add a read-only pair for the nightly drift report (`contents:read`
+ `pull-requests:read`):

- `AGENTS_SYNC_READONLY_TOKEN_ADAM_S_DANIEL`
- `AGENTS_SYNC_READONLY_TOKEN_JODIDANIEL`

GitHub has no API to create or regenerate a PAT, so regeneration on expiry is
manual by design — GitHub emails the token owner before it expires. After
regenerating, update the secret with:

```
gh secret set <NAME> --repo Adam-S-Daniel/_agent-guidance
```

### c) One classic PAT with `repo` scope (shared, coarser-grained)

Classic PATs aren't resource-owner-scoped, so a single token can cover repos
across both accounts (as long as the token's owner has access to both). Add
it as `AGENTS_SYNC_READWRITE_TOKEN` (and, for the drift report,
`AGENTS_SYNC_READONLY_TOKEN` with read-only scopes). This is the shared
fallback used by any owner that has no per-owner token of its own — the two
modes can be mixed, e.g. a per-owner PAT for one account and the shared
classic PAT covering the other.

Add secrets under Settings → Secrets and variables → Actions → New
repository secret on this repo. If **no** token resolves for an owner (no
per-owner secret and no shared `AGENTS_SYNC_READWRITE_TOKEN`), `sync.yml`'s
"Verify sync token is configured" step emits a `::warning::` naming that
owner and continues — sync then fails per-repo for that owner's repos, which
the run summary surfaces. It only hard-fails the whole job if *no* token is
configured anywhere (not even the shared fallback), since in that case `gh`
would otherwise fail opaquely (exit code 4) partway through the run.

The nightly drift report only reads repo contents, so it can run with the
default `github.token` GitHub Actions provides automatically (covering
whichever account owns this repo) when no read-only tokens are configured at
all. Private repos in an account with no matching token (per-owner or
shared) will simply show up as fetch failures in the report — a workable
degraded mode rather than a hard failure.

## Layout

```
agents-md/              # managed AGENTS.md content (base + opt-in sections)
scripts/                # build, sync, drift-report
.github/workflows/      # CI, sync-on-push, nightly drift report
.agents-sync.example.yml
repos.yml               # central exclusion list + default sections
drift-report.md         # generated nightly dashboard
test/run-tests.sh
```
