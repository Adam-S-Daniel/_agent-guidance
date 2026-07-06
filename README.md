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

## Required secret

The sync workflow needs write access across the org to push branches and
open PRs. Create a fine-grained personal access token with `contents:write`
and `pull-requests:write` scopes on all target repos, then add it as a
repository secret named `ORG_JODIDANIEL_READWRITE_CONTENTS_PRS` (Settings →
Secrets and variables → Actions → New repository secret) on this repo.
Without it, `scripts/sync.sh` runs with an empty `GH_TOKEN` and `gh` fails
opaquely (exit code 4) partway through the run — the workflow now checks
for this up front and fails with a clear message instead.

The nightly drift report only reads repo contents, so it can run with the
default `github.token` GitHub Actions provides automatically. For full
coverage of private repos across the org, optionally add a fine-grained
PAT with read-only `contents` and `pull-requests` scopes as a repository
secret named `ORG_JODIDANIEL_READONLY_CONTENTS_PRS`; `drift-report.yml`
falls back to `github.token` when this secret isn't set, so private repos
will simply show up as fetch failures in the report until it's added —
a workable degraded mode rather than a hard failure.

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
