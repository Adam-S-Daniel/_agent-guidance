# Network allowlists

Read this when you edit either egress allowlist in `docs/reference/`. Nothing
loads these files: they are reference copies, each with a `.CHANGELOG.md`
sidecar.

## The two allowlists

- [`network-allowlist-claude-environments.txt`](network-allowlist-claude-environments.txt):
  in force for the Claude Code cloud environment `My Whitelist`. The dialog at
  claude.ai/code is authoritative; this file is a reference copy.
- [`network-allowlist-github-runners.txt`](network-allowlist-github-runners.txt):
  proposed for CI. It is unenforceable on a standard runner (roadmap #821,
  closed).

## Changing one

Add an entry to the matching sidecar, [`network-allowlist-claude-environments.CHANGELOG.md`](network-allowlist-claude-environments.CHANGELOG.md)
or [`network-allowlist-github-runners.CHANGELOG.md`](network-allowlist-github-runners.CHANGELOG.md),
in the same change. An allowlist line is a bare domain with no room for a
comment, so the sidecar is the only place the reason can live. Each sidecar's
header gives the entry format.

## Traps

- `*.example.com` does **not** match the apex `example.com`. List both.
- The "also include default list..." checkbox in the environment dialog adds
  about 200 domains.
