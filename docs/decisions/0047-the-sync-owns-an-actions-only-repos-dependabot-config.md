# 0047 — The sync owns an actions-only repo's dependabot.yml

**Status:** Accepted (2026-11-20). Builds on
[`docs/reference/dependabot-grouping.md`](../reference/dependabot-grouping.md).

## Context

Most fleet repos carry a hand-written `.github/dependabot.yml` that covers only
`github-actions`, with no `groups:`, so every action bump and every security
update opens its own PR. Nothing synced that file, so rolling grouping out
meant editing two dozen repos by hand. The same file must also keep Dependabot
away from the synced `.githooks/dated-phrasing/package-lock.json`, which the
sync overwrites: a PR against one copy is churn.

## Decision

`scripts/sync.sh` delivers `templates/dependabot/dependabot.yml`, behind
`dependabot_config.deliver` in `repos.yml`, the way it delivers the auto-merge
workflow. It writes the file when the repo has none, when the file starts with
the managed header (overwrite on drift), or when every `updates[].package-ecosystem`
is `github-actions` (the first migration). It never writes to cms-platform or
its callers, to a repo whose file lists any other ecosystem, or to a repo with
no `.githooks/dated-phrasing/package.json` (Dependabot errors on a directory
with no manifest); each case logs why.

A repo with another ecosystem hand-maintains its file and must carry the same
`actions` and `actions-security` groups and the `dated-phrasing` `ignore` entry
itself. _agent-guidance's own file does likewise.

## Consequences

- Actions-only repos get grouped version and security PRs with no per-repo edit.
- The delivered file is overwritten on drift, so an actions-only repo cannot
  tune it; adding any other ecosystem opts the repo out of delivery entirely.
- Multi-ecosystem repos can drift from the template unnoticed.
- A repo gets the file only after the agent-checks lane has delivered the
  dated-phrasing manifest, which may take a second sync.
