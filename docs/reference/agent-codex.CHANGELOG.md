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
   change log** (with its publish time), **Window**, **Source text**, **Repos
   considered**.
3. One `####` group per change, or set of related changes, that may affect a
   repo: a list item per change linking its release page and giving the
   release's GitHub publish time (UTC, `YYYY-MM-DDTHH:MMZ`), with the exact
   quote beneath it as a `>` line; then **Issues** you opened, written to
   [`agent-changelog-issues.md`](agent-changelog-issues.md).

The full procedure, including the traps the first entry hit, is
[`agent-changelog-routine.md`](agent-changelog-routine.md).

Behavior found to contradict an entry goes in
[`agent-codex.DISCREPANCIES.md`](agent-codex.DISCREPANCIES.md). Before
writing a new entry, re-check that file's open discrepancies against the new
window ([process](agent-discrepancy-process.md), last section).

## Entries

### 2026-10-01 — 0.159.3 through 0.159.3

- **Checked:** 2026-10-01T01:20Z, [release log](https://github.com/openai/codex/releases)
- **Latest version in the change log:** [0.159.3](https://github.com/openai/codex/releases/tag/rust-v0.159.3), published 2026-09-30T22:57Z
- **Window:** 0.159.3 (published 2026-09-30T22:57Z), the first stable release after 0.159.2: 1 stable release, 1 bullet (0.159.3/0: optional account security setup reminders for ChatGPT-signed-in local sessions). Pre-releases (`-alpha`) excluded. Publish time read from the tag page's `datetime` attribute, UTC.
- **Source text:** the GitHub release list pages 1-2 and the tag page; the pages walked included `rust-v0.158.0`, an earlier entry's version, and `rust-v0.159.2`, the previous entry's last version.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` f951789, `adam-agentskills` ca1e575, `adam-agentskills-private` e488539, `adamdaniel.ai` c827165, `claude-memory-map` 52c2306, `cms-platform` fffc5db, `fastmail-actions` df3c001, `GHA-bench` 67e67ff8, `jodidaniel.com` be8eaa5, `repo-settings` 2596138, `rss-inator` f3ee6b4, `skills-evals` 4368def, `wsl-automation` f05a86a.

No group met the name-the-surface bar: the one bullet is a ChatGPT sign-in UI reminder, and no fleet repo depends on it, so no issue was filed.

### 2026-09-30 — 0.159.0 through 0.159.2

- **Checked:** 2026-09-30T17:08Z, [release log](https://github.com/openai/codex/releases)
- **Latest version in the change log:** [0.159.2](https://github.com/openai/codex/releases/tag/rust-v0.159.2), published 2026-09-29T23:57Z
- **Window:** 0.159.0 (published 2026-09-29T08:05Z) through 0.159.2: 3 stable releases, 16 bullets indexed (0.159.0/0-13, 0.159.1/0, 0.159.2/0; the release-note highlights, not the trailing per-PR "Changelog" lists). Pre-releases (`-alpha`) excluded. Publish times read from each tag page's `datetime` attribute, UTC: 0.159.1 2026-09-29T20:32Z, 0.159.2 2026-09-29T23:57Z.
- **Source text:** the GitHub release list pages 1-2 and each release's tag page; the pages walked included `rust-v0.158.0`, the previous entry's last version.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` 6655756, `adam-agentskills` 41d223e, `adam-agentskills-private` f5d5d9e, `adamdaniel.ai` 4aa1acf, `claude-memory-map` d86a4da, `cms-platform` 7cbbe4c, `fastmail-actions` 4b092d6, `GHA-bench` a7cb9ec8, `jodidaniel.com` 92441f8, `repo-settings` a5f4781, `rss-inator` f3ee6b4, `skills-evals` cb3b084, `wsl-automation` 0ec6313.

No group met the name-the-surface bar: a grep of the fleet for `prompt_suggestions`, `plugin-creator`, `instant_interrupt`, GPT-6 model names and `.aws` sandbox paths found no Codex dependency on any of them, so no issue was filed.

### 2026-09-28 — 0.157.1 through 0.158.0

- **Checked:** 2026-09-28T21:26Z, [release log](https://github.com/openai/codex/releases) (`DRY_RUN`, `SCOPE=codex`)
- **Latest version in the change log:** [0.158.0](https://github.com/openai/codex/releases/tag/rust-v0.158.0), published 2026-09-28T05:07Z
- **Window:** 0.157.1 (published 2026-09-26T01:02Z; no notes) through 0.158.0: 2 stable releases, 11 bullets indexed (0.157.1/none, 0.158.0/0-10). Pre-releases (`-alpha`) excluded. Publish times read from each tag page's `datetime` attribute, UTC.
- **Source text:** the GitHub release list pages 1-2 and each release's tag page; the pages walked included `rust-v0.157.0`, the previous entry's last version.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (default-branch head read): `_agent-guidance` 85e3d8d, `adam-agentskills` 9a754d1, `adam-agentskills-private` 3bb77f4, `adamdaniel.ai` 61893ff, `claude-memory-map` 62c5bd4, `cms-platform` 21810a6, `fastmail-actions` ae5e1ca, `GHA-bench` 8b1c7c2, `jodidaniel.com` 3e61a3a, `repo-settings` 523215d, `rss-inator` a10cfd9, `skills-evals` 18e5839, `wsl-automation` ce73630.

No groups: a keyword grep over all 13 repos found the fleet's Codex dependencies to be the `AGENTS.md` chain, `codex debug prompt-input`, the SessionStart hook and its trust flag (`register-codex-hook.sh`), and the plugin-compatibility notes in `adam-agentskills`. None of the 0.158.0 bullets (copy-on-select, MCP OAuth client secrets, exec-server bearer tokens, transparent image backgrounds, terminal input approval, sandbox and Mermaid fixes, command completion events) touches one, so no issue is filed. 0.157.1's notes say only that release highlights could not be determined.

### 2026-09-25 — 0.144.1 through 0.157.0

- **Checked:** 2026-09-25T16:20Z, [release log](https://github.com/openai/codex/releases)
- **Latest version in the change log:** [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0), published 2026-09-25T02:31Z
- **Window:** 0.144.1, the latest release on 2026-07-10 (published 2026-07-09T23:02Z; 0.144.2 followed on 2026-07-13), through 0.157.0: 29 stable releases. Pre-releases (`-alpha`) excluded; 0.149.1 has no notes.
- **Source text:** the GitHub release pages; for 0.152.0 onward, cross-checked against OpenAI's changelog feed, which mirrors the release bodies.
- **Repos considered:** `Adam-S-Daniel/_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `claude-memory-map` and `skills-evals`, the five attached to the session that wrote this entry. Other fleet repos, including the `jodidaniel` owner's, were not assessed.

#### 1. Project trust now gates project `AGENTS.md` and workspace helpers

- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0), published 2026-08-07T01:41Z
  > Require explicit trust for unfamiliar local projects and enforce managed authentication restrictions before credentials are used. (#36960, #37132)
- [0.150.0](https://github.com/openai/codex/releases/tag/rust-v0.150.0), published 2026-08-26T19:37Z
  > Untrusted projects no longer supply project-level `AGENTS.md` instructions, and managed deny-read rules remain enforced after permission changes. (#39837, #40004)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0), published 2026-09-09T22:35Z
  > Startup avoids running workspace-controlled helpers before trust is established, and the macOS sandbox blocks terminal input injection. (#42324, #42590)

**Issues:** [_agent-guidance#181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181)

#### 2. Background server on by default; instruction refresh

- [0.148.0](https://github.com/openai/codex/releases/tag/rust-v0.148.0), published 2026-08-18T22:26Z
  > Model switches and settings updates no longer leave stale instructions behind or change an active turn midstream. (#37260, #38785)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0), published 2026-09-09T22:35Z
  > Windows sessions can now share a background Codex server, with daemon lifecycle commands and managed updates. (#42405, #42392)
- [0.156.0](https://github.com/openai/codex/releases/tag/rust-v0.156.0), published 2026-09-22T19:51Z
  > Update the local background server through `/daemon`, or bypass it with `--no-daemon`. (#45854, #46088)
- [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0), published 2026-09-25T02:31Z
  > Enabled automatic background-server startup for eligible interactive sessions, with recovery choices when server settings are incompatible. (#47179, #47318)

**Issues:** [_agent-guidance#182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182)

#### 3. Plugins, marketplaces and skill catalogs

- [0.146.0](https://github.com/openai/codex/releases/tag/rust-v0.146.0), published 2026-07-29T01:42Z
  > Support Agent Plugins manifests, workspace plugin publishing, and additional plugin marketplaces for Amazon Bedrock and Claude Code. (#35105, #35254, #34931, #34979)
- [0.146.0](https://github.com/openai/codex/releases/tag/rust-v0.146.0), published 2026-07-29T01:42Z
  > Retain more available skills under tight context budgets and warn when skill catalogs must be truncated. (#34732, #34738, #34997)
- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0), published 2026-08-07T01:41Z
  > Install portable Agent Plugins and search across local, personal, workspace, and remote plugin catalogs. (#36544, #36409, #36919, #36796)
- [0.151.0](https://github.com/openai/codex/releases/tag/rust-v0.151.0), published 2026-08-29T09:55Z
  > Plugin catalogs now combine per-repository configuration and report invalid project marketplaces without hiding valid plugins. (#41208)
- [0.153.0](https://github.com/openai/codex/releases/tag/rust-v0.153.0), published 2026-09-03T01:37Z
  > The plugin CLI can list, install, and remove plugins from remote marketplaces. (#42150)
- [0.154.0](https://github.com/openai/codex/releases/tag/rust-v0.154.0), published 2026-09-09T22:35Z
  > Existing sessions pick up newly installed plugin tools and refresh skills and hooks after external plugin upgrades or rollbacks. (#42284, #42593, #42990)

**Issues:** [adam-agentskills#16](https://github.com/Adam-S-Daniel/adam-agentskills/issues/16)

#### 4. `/import` of Claude Code settings and project-scoped memories

- [0.145.0](https://github.com/openai/codex/releases/tag/rust-v0.145.0), published 2026-07-21T18:21Z
  > Expanded `/import` to migrate Cursor and Claude Code settings, MCP servers, plugins, sessions, commands, and project-scoped memories. (#31672, #33411, #33426, #33444)
- [0.147.0](https://github.com/openai/codex/releases/tag/rust-v0.147.0), published 2026-08-07T01:41Z
  > Import Cursor-managed skills and synchronize changes to imported Claude and Cursor conversations without creating duplicates. (#36361, #36356, #35623)
- [0.157.0](https://github.com/openai/codex/releases/tag/rust-v0.157.0), published 2026-09-25T02:31Z
  > Made `/import` available in remote sessions and local background-server sessions. (#47317)

**Issues:** [_agent-guidance#183](https://github.com/Adam-S-Daniel/_agent-guidance/issues/183)
