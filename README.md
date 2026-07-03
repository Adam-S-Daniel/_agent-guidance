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

## Layout

```
agents-md/              # managed AGENTS.md content (base + opt-in sections)
scripts/                # build, sync, drift-report
.github/workflows/      # CI, sync-on-push, nightly drift report
.agents-sync.example.yml
drift-report.md         # generated nightly dashboard
test/run-tests.sh
```
