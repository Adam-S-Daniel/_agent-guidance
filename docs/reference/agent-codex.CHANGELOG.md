# Changelog — Codex releases that may affect the fleet

Tracks [Codex's release log](https://github.com/openai/codex/releases)
against the repos named in each entry. Twin file, same structure:
[`agent-claude-code.CHANGELOG.md`](agent-claude-code.CHANGELOG.md).

## How to add an entry

Add each new entry at the top of **Entries**, in the same structure as the one
below it:

1. Heading `### YYYY-MM-DD — <first version> through <last version>`, starting
   at the first release after the previous entry's last version.
2. **Checked** (UTC time you read the release log), **Latest version in the
   change log**, **Window**, **Source text**, **Repos considered**.
3. One `####` group per change, or set of related changes, that may affect a
   repo: a list item per change linking its release page, with the exact quote
   beneath it as a `>` line; then **Issues** you opened. Search for duplicates
   first, open one issue per affected repo, and link each back to this file.

## Entries

### 2026-09-25 — 0.144.1 through 0.157.0

- **Checked:** 2026-09-25T16:20Z, [release log](https://github.com/openai/codex/releases)
- **Latest version in the change log:** [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0)
- **Window:** 0.144.1, the latest release on 2026-07-10 (published 2026-07-09T23:02Z; 0.144.2 followed on 2026-07-13), through 0.157.0: 29 stable releases. Pre-releases (`-alpha`) excluded; 0.149.1 has no notes.
- **Source text:** the GitHub release pages; for 0.152.0 onward, cross-checked against OpenAI's changelog feed, which mirrors the release bodies.
- **Repos considered:** `Adam-S-Daniel/_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `claude-memory-map` and `skills-evals`, the five attached to the session that wrote this entry. Other fleet repos, including the `jodidaniel` owner's, were not assessed.

#### 1. Project trust now gates project `AGENTS.md` and workspace helpers

- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0)
  > Require explicit trust for unfamiliar local projects and enforce managed authentication restrictions before credentials are used. (#36960, #37132)
- [0.150.0](https://github.com/openai/codex/releases/tag/rust-v0.150.0)
  > Untrusted projects no longer supply project-level `AGENTS.md` instructions, and managed deny-read rules remain enforced after permission changes. (#39837, #40004)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0)
  > Startup avoids running workspace-controlled helpers before trust is established, and the macOS sandbox blocks terminal input injection. (#42324, #42590)

**Issues:** PENDING

#### 2. Background server on by default; instruction refresh

- [0.148.0](https://github.com/openai/codex/releases/tag/rust-v0.148.0)
  > Model switches and settings updates no longer leave stale instructions behind or change an active turn midstream. (#37260, #38785)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0)
  > Windows sessions can now share a background Codex server, with daemon lifecycle commands and managed updates. (#42405, #42392)
- [0.156.0](https://github.com/openai/codex/releases/tag/rust-v0.156.0)
  > Update the local background server through `/daemon`, or bypass it with `--no-daemon`. (#45854, #46088)
- [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0)
  > Enabled automatic background-server startup for eligible interactive sessions, with recovery choices when server settings are incompatible. (#47179, #47318)

**Issues:** PENDING

#### 3. Plugins, marketplaces and skill catalogs

- [0.146.0](https://github.com/openai/codex/releases/tag/rust-v0.146.0)
  > Support Agent Plugins manifests, workspace plugin publishing, and additional plugin marketplaces for Amazon Bedrock and Claude Code. (#35105, #35254, #34931, #34979)
- [0.146.0](https://github.com/openai/codex/releases/tag/rust-v0.146.0)
  > Retain more available skills under tight context budgets and warn when skill catalogs must be truncated. (#34732, #34738, #34997)
- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0)
  > Install portable Agent Plugins and search across local, personal, workspace, and remote plugin catalogs. (#36544, #36409, #36919, #36796)
- [0.151.0](https://github.com/openai/codex/releases/tag/rust-v0.151.0)
  > Plugin catalogs now combine per-repository configuration and report invalid project marketplaces without hiding valid plugins. (#41208)
- [0.153.0](https://github.com/openai/codex/releases/tag/rust-v0.153.0)
  > The plugin CLI can list, install, and remove plugins from remote marketplaces. (#42150)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0)
  > Existing sessions pick up newly installed plugin tools and refresh skills and hooks after external plugin upgrades or rollbacks. (#42284, #42593, #42990)

**Issues:** PENDING

#### 4. `/import` of Claude Code settings and project-scoped memories

- [0.145.0](https://github.com/openai/codex/releases/tag/rust-v0.145.0)
  > Expanded `/import` to migrate Cursor and Claude Code settings, MCP servers, plugins, sessions, commands, and project-scoped memories. (#31672, #33411, #33426, #33444)
- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0)
  > Import Cursor-managed skills and synchronize changes to imported Claude and Cursor conversations without creating duplicates. (#36361, #36356, #35623)
- [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0)
  > Made `/import` available in remote sessions and local background-server sessions. (#47317)

**Issues:** PENDING
