# Changelog — Claude Code releases that may affect the fleet

Tracks [Claude Code's release log](https://github.com/anthropics/claude-code/releases)
against the repos named in each entry. Twin file, same structure:
[`agent-codex.CHANGELOG.md`](agent-codex.CHANGELOG.md).

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
[`agent-claude-code.DISCREPANCIES.md`](agent-claude-code.DISCREPANCIES.md). Before
writing a new entry, re-check that file's open discrepancies against the new
window ([process](agent-discrepancy-process.md), last section).

## Entries

### 2026-10-08 — 2.1.293 through 2.1.293

- **Checked:** 2026-10-08T01:20Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.293](https://github.com/anthropics/claude-code/releases/tag/v2.1.293), published 2026-10-07T18:10Z
- **Window:** v2.1.293 (published 2026-10-07T18:10Z), the first stable release after v2.1.292: 1 release, 56 bullets (not individually indexed; the quotes below are copied from the file). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone); the tag page was fetched (HTTP 200) for its publish time and its body matches the file.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` 11e53c9, `adam-agentskills` b552eb2, `adam-agentskills-private` acb07c2, `adamdaniel.ai` 6ff477d, `claude-memory-map` c4e5e1e, `cms-platform` 86ae606, `fastmail-actions` a0522c5, `GHA-bench` 21916084, `jodidaniel.com` e75f43a, `repo-settings` 7b63640, `rss-inator` f9fe3a8, `skills-evals` 9b7f610, `wsl-automation` 20f7246.

#### 1. Claude Haiku 5.5 is now the default Haiku model

- [v2.1.293](https://github.com/anthropics/claude-code/releases/tag/v2.1.293), published 2026-10-07T18:10Z
  > Added Claude Haiku 5.5 (`claude-haiku-5-5`), now the default Haiku model on the Anthropic API — 1M context, $0.10/$0.50 per Mtok ($0.50/$2.50 for prompts over 100K)

**Issues:** [GHA-bench#101](https://github.com/Adam-S-Daniel/GHA-bench/issues/101). `skills-evals` also names the `haiku` tier, but its roster reads vendor defaults from the installed CLI by design (ADR 0002), so no issue.

#### 2. Prompt-audit sweep trigger

- [v2.1.293](https://github.com/anthropics/claude-code/releases/tag/v2.1.293), published 2026-10-07T18:10Z
  > Added Claude Haiku 5.5 (`claude-haiku-5-5`), now the default Haiku model on the Anthropic API — 1M context, $0.10/$0.50 per Mtok ($0.50/$2.50 for prompts over 100K)
- [v2.1.293](https://github.com/anthropics/claude-code/releases/tag/v2.1.293), published 2026-10-07T18:10Z
  > Fixed path-scoped rules and nested CLAUDE.md files not loading when Claude views a file with a single-file cat, head, tail, sed -n or grep command in the Bash tool instead of the Read tool

**Issues:** [_agent-guidance#279](https://github.com/Adam-S-Daniel/_agent-guidance/issues/279) (no label). Sweep fired: https://claude.ai/code/cse_01CYXc6JQZ11XvyXuKdj5qRT.

### 2026-10-07 — 2.1.291 through 2.1.292

- **Checked:** 2026-10-07T01:20Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.292](https://github.com/anthropics/claude-code/releases/tag/v2.1.292), published 2026-10-06T18:59Z
- **Window:** v2.1.291 (published 2026-10-06T03:55Z) and v2.1.292 (published 2026-10-06T18:59Z), the first stable releases after v2.1.290: 2 releases, 93 bullets (not individually indexed: no group was drafted). Publish times are the `datetime` attribute on each tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` sections in `anthropics/claude-code` (shallow clone; tags `v2.1.291` and `v2.1.292` listed by `git ls-remote --tags`); both tag pages were fetched (HTTP 200) for their publish times.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` a655590, `adam-agentskills` 9c35b2e, `adam-agentskills-private` 10b02f6, `adamdaniel.ai` 7fddbde, `claude-memory-map` 08e1701, `cms-platform` 8442e97, `fastmail-actions` 3beea0a, `GHA-bench` 982faa83, `jodidaniel.com` 22b6007, `repo-settings` 1189e72, `rss-inator` 8dce2eb, `skills-evals` 78f5a88, `wsl-automation` a9e16c1.

No group met the name-the-surface bar. A grep of the fleet for `system-reminder`, `NO_PROXY`, `MCP_PROTOCOL`, `OVERLOADED_RETRY`, `claude plugin install` and `permissionMode: auto` found no dependence on a changed behavior: the `skills-bootstrap.sh` hooks mention `claude plugin install` only in a comment saying it is not a substitute for the hook, `skills-evals` passes `NO_PROXY` through as an environment variable unchanged, and `skills-evals`' `arms.py` runs `claude plugin install <bundle>@<market>`, to which the new `--marketplace` flag is additive. The 2.1.292 bullet escaping `<system-reminder>` tags in hook output touches no fleet hook, since none writes that tag. Other bullets cover mod and plugin hook types, `claude plugin validate`/`test`, stdio MCP protocol negotiation, scheduled-task and `/loop` resume, cloud-session and Remote Control fixes, vim mode and prompt-box input, and Claude Tag and Code Review, none of which a fleet repo depends on. No bullet changes models, `/doctor` or instruction-file loading in a way the sweep triggers on (the "instruction file not loaded" line fix is a display fix), so the prompt-audit sweep was not marked (`sweep: none`).

### 2026-10-06 — 2.1.290 through 2.1.290

- **Checked:** 2026-10-06T01:15Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.290](https://github.com/anthropics/claude-code/releases/tag/v2.1.290), published 2026-10-05T23:33Z
- **Window:** v2.1.290 (published 2026-10-05T23:33Z), the first stable release after v2.1.289: 1 release, 190 bullets (not individually indexed: no group was drafted). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.290` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` fe83f32, `adam-agentskills` 9c35b2e, `adam-agentskills-private` 10b02f6, `adamdaniel.ai` b9b91b3, `claude-memory-map` c9497b6, `cms-platform` c6d3125, `fastmail-actions` b268cde, `GHA-bench` 4f165b48, `jodidaniel.com` cc75080, `repo-settings` 6f1e7a6, `rss-inator` 8dce2eb, `skills-evals` a2c129e, `wsl-automation` a9e16c1.

No group met the name-the-surface bar. A grep of the fleet for `blockReadsOutsideWorkingDirectories`, `plansDirectory`, `claude attach`, `claude logs` and `managed-agents-onboard` found no dependence on a changed behavior (the `plansDirectory` hits in `claude-memory-map` and `migrate-claude-memory` concern documentation of the setting, not a backslash path). The bullet on a symlinked `CLAUDE.md`, rule or `AGENTS.md` loading under `permissions.blockReadsOutsideWorkingDirectories` or a `Read` deny rule is a permission fix; no fleet repo symlinks its instruction files or sets that option (fleet `CLAUDE.md` files import `@AGENTS.md`), so it did not mark the prompt-audit sweep (`sweep: none`). Other bullets cover mod and plugin hook types, `claude plugin validate`, scheduled-task resume, `/ultrareview` and Remote Control streaming, none of which a fleet repo depends on.

### 2026-10-04 — 2.1.289 through 2.1.289

- **Checked:** 2026-10-04T16:25Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.289](https://github.com/anthropics/claude-code/releases/tag/v2.1.289), published 2026-10-03T23:07Z
- **Window:** v2.1.289 (published 2026-10-03T23:07Z), the first stable release after v2.1.288: 1 release, 27 bullets (not individually indexed: no group was drafted). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` c298b8b, `adam-agentskills` a70ed64, `adam-agentskills-private` b9f3a1d, `adamdaniel.ai` b827045, `claude-memory-map` 92a7314, `cms-platform` 0c3b80c, `fastmail-actions` 163a13c, `GHA-bench` 68f1671c, `jodidaniel.com` 08354be, `repo-settings` eecc371, `rss-inator` 43578af, `skills-evals` 7f21575, `wsl-automation` 49dbdfa.

No group met the name-the-surface bar. Most bullets concern plugin panes and mods, Bash/Read deny and ask rules under sandbox auto-allow, and a VS Code auth change, none of which a fleet repo depends on (no fleet `settings.json` carries deny rules or sandbox auto-allow). The three `claude plugin validate` fixes do not match how the fleet calls it: `adam-agentskills` CI runs `claude plugin validate . --strict` on a root holding only a marketplace manifest, and `cms-platform`'s `plugin-validate` lane runs it on a root holding only a plugin manifest, so the "folder holds both" and `--json` fixes do not apply to either.

### 2026-10-03 — 2.1.288 through 2.1.288

- **Checked:** 2026-10-03T01:10Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.288](https://github.com/anthropics/claude-code/releases/tag/v2.1.288), published 2026-10-02T20:19Z
- **Window:** v2.1.288 (published 2026-10-02T20:19Z), the first stable release after v2.1.287: 1 release, about 90 bullets (not individually indexed: no group was drafted). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.288` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance b0065eb, `adam-agentskills` 766357e, `adam-agentskills-private` b9f3a1d, `adamdaniel.ai` 3f1070d, `claude-memory-map` 92a7314, `cms-platform` 91392c2, `fastmail-actions` 163a13c, `GHA-bench` 68f1671c, `jodidaniel.com` 08354be, `repo-settings` eecc371, `rss-inator` 43578af, `skills-evals` 7f21575, `wsl-automation` 49dbdfa`.

No group met the name-the-surface bar: a grep of the fleet for `idle_prompt`, `InstructionsLoaded`, `PreToolUse`/`PermissionRequest`, `CLAUDE_CODE_RETRY_WATCHDOG`, `claude project purge`, `claude mcp serve`, `claude plugin test`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `sandbox.credentials` and `blockReadsOutsideWorkingDirectories` found no dependence on a changed behavior (the `InstructionsLoaded` hook fix concerns a hook that is not on any fleet default branch; `_agent-guidance#124` is still open). The background-command time-limit change is already tracked by `Adam-S-Daniel/cms-platform#497`, filed for the 2.1.285 entry.

### 2026-10-02 — 2.1.287 through 2.1.287

- **Checked:** 2026-10-02T01:20Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.287](https://github.com/anthropics/claude-code/releases/tag/v2.1.287), published 2026-10-01T18:00Z
- **Window:** v2.1.287 (published 2026-10-01T18:00Z), the first stable release after v2.1.286: 1 release, about 110 bullets (not individually indexed: no group was drafted). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; the section for v2.1.287 is the top one); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` d4ed931, `adam-agentskills` ca1e575, `adam-agentskills-private` e488539, `adamdaniel.ai` 7a54558, `claude-memory-map` 52c2306, `cms-platform` 5f1bcd3, `fastmail-actions` df3c001, `GHA-bench` 67e67ff8, `jodidaniel.com` c203ff2, `repo-settings` 2596138, `rss-inator` f3ee6b4, `skills-evals` 4368def, `wsl-automation` f05a86a.

No group met the name-the-surface bar: a grep of the fleet for `asyncRewake`, `alwaysLoad`, `availableModels`, `opusplan`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`, `git-subdir`, `--sparse` and the `CLAUDE_CODE_SKIP_*_AUTH` variables found no dependence on a changed behavior. The fix for SessionStart hooks from synced plugins in new cloud sessions concerns claude.ai-synced plugins, not the repo-declared settings the fleet's "no plugins from repo-declared settings" claim covers, so that claim is unchanged. No issue was filed.

### 2026-10-01 — 2.1.286 through 2.1.286

- **Checked:** 2026-10-01T01:20Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.286](https://github.com/anthropics/claude-code/releases/tag/v2.1.286), published 2026-09-30T19:10Z
- **Window:** v2.1.286 (published 2026-09-30T19:10Z), the first stable release after v2.1.285: 1 release, about 100 bullets (not individually indexed: no group was drafted). Publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.286` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time and body, which matched.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` f951789, `adam-agentskills` ca1e575, `adam-agentskills-private` e488539, `adamdaniel.ai` c827165, `claude-memory-map` 52c2306, `cms-platform` fffc5db, `fastmail-actions` df3c001, `GHA-bench` 67e67ff8, `jodidaniel.com` be8eaa5, `repo-settings` 2596138, `rss-inator` f3ee6b4, `skills-evals` 4368def, `wsl-automation` f05a86a.

No group met the name-the-surface bar: a grep of the fleet for `--bare` (only `git init --bare` hits), `--fallback-model`, `verify` skills, npm plugin sources, and `awsAuthRefresh`/`gcpAuthRefresh` (only a settings-key list copied by a sync script, unaffected by the login-browser fix) found no dependence on a changed behavior, so no issue was filed.

### 2026-09-30 — 2.1.285 through 2.1.285

- **Checked:** 2026-09-30T17:08Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.285](https://github.com/anthropics/claude-code/releases/tag/v2.1.285), published 2026-09-29T19:27Z
- **Window:** v2.1.285 (published 2026-09-29T19:27Z), the first stable release after v2.1.284: 1 release, 136 bullets (2.1.285/0-135). Publish times are the `datetime` attribute on the tag page, in UTC.
- **Source text:** the release body is the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.285` listed by `git ls-remote --tags`); quotes are copied from that file. The v2.1.285 tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 13 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` 6655756, `adam-agentskills` 41d223e, `adam-agentskills-private` f5d5d9e, `adamdaniel.ai` 4aa1acf, `claude-memory-map` d86a4da, `cms-platform` 7cbbe4c, `fastmail-actions` 4b092d6, `GHA-bench` a7cb9ec8, `jodidaniel.com` 92441f8, `repo-settings` a5f4781, `rss-inator` f3ee6b4, `skills-evals` cb3b084, `wsl-automation` 0ec6313.

#### 1. Background Bash and PowerShell commands now stop after a time limit

- [v2.1.285](https://github.com/anthropics/claude-code/releases/tag/v2.1.285), published 2026-09-29T19:27Z
  > Changed background Bash and PowerShell commands to stop after a time limit (their `timeout` with `run_in_background`, default 30 min, max 2 h); Claude is notified when one is stopped

**Issues:** [cms-platform#497](https://github.com/Adam-S-Daniel/cms-platform/issues/497)

### 2026-09-29 — 2.1.283 through 2.1.284

- **Checked:** 2026-09-29T01:10Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.284](https://github.com/anthropics/claude-code/releases/tag/v2.1.284), published 2026-09-28T18:02Z
- **Window:** v2.1.283 (published 2026-09-25T21:50Z), the first stable release after v2.1.282, through v2.1.284: 2 releases, 194 bullets. Publish times are the `datetime` attribute on each tag page, in UTC.
- **Source text:** each release body is the matching `CHANGELOG.md` section in `anthropics/claude-code`; quotes are copied from that file. The v2.1.284 and v2.1.283 tag pages were fetched (HTTP 200) for their publish times only; one body (v2.1.284) was spot-checked against its tag page and matched.
- **Repos considered:** `Adam-S-Daniel/_agent-guidance` only (run scoped with `SCOPE=claude-code,_agent-guidance`): reached 542e19a. The other fleet repos were not assessed in this run.

#### 1. `installed_plugins.json` and plugin cache recovery (durable-machine install check)

- [v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283), published 2026-09-25T21:50Z
  > Fixed plugins that declare no version being silently restored at their source's newest commit, not the installed one, when their cached files were missing
- [v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283), published 2026-09-25T21:50Z
  > Fixed user-installed plugins and marketplaces failing to load with "cache-miss" after the home or config directory was moved, for example in bind-mounted devcontainers
- [v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283), published 2026-09-25T21:50Z
  > Fixed `installed_plugins.json` showing no plugins when it holds a record under an invalid plugin id; such a file loads again
- [v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283), published 2026-09-25T21:50Z
  > Fixed `installed_plugins.json` being rewritten, losing records, when it holds a record this version cannot read; `claude plugin` commands now name the record and say how to recover
- [v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283), published 2026-09-25T21:50Z
  > Improved recovery from an `installed_plugins.json` that cannot be read at all: its contents are kept in a file beside it before it is rebuilt, and `claude plugin list` names that file
- [v2.1.284](https://github.com/anthropics/claude-code/releases/tag/v2.1.284), published 2026-09-28T18:02Z
  > Fixed `sparsePaths` plugin marketplaces cloning empty and replacing a working local copy on older git (before 2.39), which failed every refresh with "marketplace.json file is no longer present"

**Issues:** none filed. [_agent-guidance#175](https://github.com/Adam-S-Daniel/_agent-guidance/issues/175) already covers this surface (`agents-md/base.md`, "Durable machine" paragraph: the stale-install check reads `gitCommitSha` from `installed_plugins.json`). These fixes may further change what that file holds on CLI 2.1.283 and later; add them to that issue's "To check" when it is worked.

### 2026-09-25 — 2.1.206 through 2.1.282

- **Checked:** 2026-09-25T16:20Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.282](https://github.com/anthropics/claude-code/releases/tag/v2.1.282), published 2026-09-24T18:38Z
- **Window:** v2.1.206, the latest release on 2026-07-10 (published 2026-07-10T01:45Z; v2.1.207 followed at 2026-07-11T00:52Z, 20:52 ET on 7/10), through v2.1.282: 65 releases, 2,527 bullets.
- **Source text:** each release body matches its `CHANGELOG.md` section in `anthropics/claude-code`; quotes are copied from that file.
- **Repos considered:** `Adam-S-Daniel/_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `claude-memory-map` and `skills-evals`, the five attached to the session that wrote this entry. Other fleet repos, including the `jodidaniel` owner's, were not assessed.

#### 1. Native `AGENTS.md` support

- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > Added AGENTS.md support: in a project with no CLAUDE.md, Claude Code reads AGENTS.md instead; change it under "Project instructions" in `/config`
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Changed AGENTS.md support to also work on Amazon Bedrock, Google Vertex AI, Microsoft Foundry, LLM gateways, and sessions with telemetry disabled

**Issues:** [_agent-guidance#172](https://github.com/Adam-S-Daniel/_agent-guidance/issues/172), [claude-memory-map#47](https://github.com/Adam-S-Daniel/claude-memory-map/issues/47), [skills-evals#191](https://github.com/Adam-S-Daniel/skills-evals/issues/191)

#### 2. SessionStart and Stop hook behavior the fleet's hooks rely on

- [v2.1.214](https://github.com/anthropics/claude-code/releases/tag/v2.1.214), published 2026-07-18T01:20Z
  > Changed SessionStart hooks to report source `"fork"` when a session begins as a fork instead of `"resume"`
- [v2.1.239](https://github.com/anthropics/claude-code/releases/tag/v2.1.239), published 2026-08-21T19:54Z
  > Remote sessions keep sending keep-alives while a long `SessionStart` or `Setup` hook runs, so the container is not idle-reaped mid-hook
- [v2.1.248](https://github.com/anthropics/claude-code/releases/tag/v2.1.248), published 2026-08-27T22:12Z
  > Fixed hooks silently treating a stdout `{…}` object that isn't valid JSON as plain text; it's now reported as a hook error with the parse message
- [v2.1.259](https://github.com/anthropics/claude-code/releases/tag/v2.1.259), published 2026-09-02T22:33Z
  > Fixed blocking Stop hooks causing the turn after a block to lose the model's reasoning from that turn and, on some models, miss the prompt cache
- [v2.1.268](https://github.com/anthropics/claude-code/releases/tag/v2.1.268), published 2026-09-10T20:30Z
  > Improved `--continue` / `--resume`: the conversation appears immediately instead of waiting for SessionStart hooks, and the first message no longer re-reads the whole transcript
- [v2.1.271](https://github.com/anthropics/claude-code/releases/tag/v2.1.271), published 2026-09-14T22:12Z
  > Improved hook feedback: while a SessionStart, UserPromptSubmit, PreToolUse or SessionEnd hook runs, the spinner says so with elapsed time, and Esc cancels a prompt waiting on a SessionStart hook
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > Fixed sessions continued after `/clear` (restart, `--continue`, `--resume`) missing part of their first message when a SessionStart hook printed output, causing a full prompt-cache miss

**Issues:** [_agent-guidance#173](https://github.com/Adam-S-Daniel/_agent-guidance/issues/173), [adam-agentskills#7](https://github.com/Adam-S-Daniel/adam-agentskills/issues/7)

#### 3. `DirectoryAdded` hook for repos attached mid-session

- [v2.1.219](https://github.com/anthropics/claude-code/releases/tag/v2.1.219), published 2026-07-24T17:14Z
  > Added `DirectoryAdded` hook that fires after `/add-dir` or the SDK `register_repo_root` control request registers a new working directory mid-session

**Issues:** [adam-agentskills#8](https://github.com/Adam-S-Daniel/adam-agentskills/issues/8)

#### 4. claude.ai-synced skills: naming, namespace, sync to terminals, trash

- [v2.1.228](https://github.com/anthropics/claude-code/releases/tag/v2.1.228), published 2026-08-11T19:50Z
  > Hardened skills synced from claude.ai: they no longer shadow local commands or MCP prompts, their descriptions are sanitized and labeled, and on your machine their bodies don't run `!` commands or expand `@` files
- [v2.1.269](https://github.com/anthropics/claude-code/releases/tag/v2.1.269), published 2026-09-11T19:17Z
  > Changed skills synced from claude.ai in cloud sessions to be named `anthropic-skills:<name>`, matching Claude Desktop; the bare name still works when nothing else uses it
- [v2.1.271](https://github.com/anthropics/claude-code/releases/tag/v2.1.271), published 2026-09-14T22:12Z
  > Fixed skills synced from claude.ai staying on disk indefinitely after signing out; copies not refreshed within `cleanupPeriodDays` now move to the recoverable trash at the next launch
- [v2.1.273](https://github.com/anthropics/claude-code/releases/tag/v2.1.273), published 2026-09-15T20:23Z
  > Fixed skills synced from claude.ai staying available after your organization turns Skills off; they now move to the recoverable trash
- [v2.1.275](https://github.com/anthropics/claude-code/releases/tag/v2.1.275), published 2026-09-17T22:33Z
  > Added syncing of the skills and plugins enabled on your claude.ai account to terminal sessions signed in with it; opt out with `syncClaudeAiSkills: false` or `syncClaudeAiPlugins: false`
- [v2.1.275](https://github.com/anthropics/claude-code/releases/tag/v2.1.275), published 2026-09-17T22:33Z
  > Improved Write and Edit results for files in the synced account-skills folder: they now say the change is not saved to your account and how to save it
- [v2.1.280](https://github.com/anthropics/claude-code/releases/tag/v2.1.280), published 2026-09-22T16:38Z
  > Fixed skills in `~/.claude/skills/` being moved to `~/.claude/skills/.trash/` when a `manifest.json` in that folder listed their names
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Improved the `/` menu, `/skills`, `/context` and the `/plugin` Installed list to show skills synced from claude.ai by their short name when no other command uses it, not `anthropic-skills:<name>`
- [v2.1.282](https://github.com/anthropics/claude-code/releases/tag/v2.1.282), published 2026-09-24T18:38Z
  > Changed `Skill(anthropic-skills:*)` and `Skill(claude-ai:*)` allow rules to cover only skills synced from claude.ai, not plugins or other skills that merely use such a name
- [v2.1.282](https://github.com/anthropics/claude-code/releases/tag/v2.1.282), published 2026-09-24T18:38Z
  > Changed skill folders, command files and workflow commands in the `anthropic-skills` or `claude-ai` namespace to no longer load; a plugin so named still loads but yields name ties to synced skills

**Issues:** [_agent-guidance#174](https://github.com/Adam-S-Daniel/_agent-guidance/issues/174), [adam-agentskills#9](https://github.com/Adam-S-Daniel/adam-agentskills/issues/9), [skills-evals#196](https://github.com/Adam-S-Daniel/skills-evals/issues/196)

#### 5. `installed_plugins.json` commit recording and update hints

- [v2.1.268](https://github.com/anthropics/claude-code/releases/tag/v2.1.268), published 2026-09-10T20:30Z
  > Added `--json` to `claude plugin install`, `uninstall`, `update`, `enable` and `disable`, and `errorDetails`/`noteDetails` to each row of `claude plugin list --json`
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > Fixed plugins from the official marketplace being recorded without their commit in `installed_plugins.json`, and `installed_plugins.json` keeping the old commit after updating a pinned-commit plugin
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > Improved `claude plugin install` on an already-installed plugin: it now says when the marketplace offers a newer version and names the `claude plugin update` command
- [v2.1.280](https://github.com/anthropics/claude-code/releases/tag/v2.1.280), published 2026-09-22T16:38Z
  > Fixed `installed_plugins.json` keeping the install-time commit after updating a plugin from a GitHub repository or git URL that tracks a branch or tag

**Issues:** [_agent-guidance#175](https://github.com/Adam-S-Daniel/_agent-guidance/issues/175), [adam-agentskills#10](https://github.com/Adam-S-Daniel/adam-agentskills/issues/10), [skills-evals#192](https://github.com/Adam-S-Daniel/skills-evals/issues/192)

#### 6. `claude plugin validate` checks added after the CI pin (2.1.223)

- [v2.1.233](https://github.com/anthropics/claude-code/releases/tag/v2.1.233), published 2026-08-14T22:20Z
  > Improved `claude plugin validate` to check a bare `.claude/skills` directory, reporting SKILL.md files whose frontmatter fails to parse
- [v2.1.259](https://github.com/anthropics/claude-code/releases/tag/v2.1.259), published 2026-09-02T22:33Z
  > Added `--json` to `claude plugin validate` for a machine-readable validation report
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Added MCP server checks to `claude plugin validate`: it reports `.mcp.json` entries that would be silently dropped at load, undeclared `${user_config.*}` references, and insecure URLs
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Fixed `claude plugin validate` reporting `privacyPolicyUrl`, `supportUrl` and other listing metadata keys in plugin.json as unknown fields
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Improved plugin hook-failure errors to name the offending plugin, and added a `claude plugin validate` warning when a shell-form hook leaves `${CLAUDE_PLUGIN_ROOT}` unquoted (it breaks on plugin paths with spaces)

**Issues:** [adam-agentskills#11](https://github.com/Adam-S-Daniel/adam-agentskills/issues/11)

#### 7. Marketplace refresh and plugin install flow

- [v2.1.221](https://github.com/anthropics/claude-code/releases/tag/v2.1.221), published 2026-08-04T00:14Z
  > Changed `/plugin install` to refresh a stale marketplace catalog and retry before reporting a plugin not found
- [v2.1.221](https://github.com/anthropics/claude-code/releases/tag/v2.1.221), published 2026-08-04T00:14Z
  > Changed plugins installed from `/plugin` to activate immediately when safe, instead of always requiring `/reload-plugins`
- [v2.1.232](https://github.com/anthropics/claude-code/releases/tag/v2.1.232), published 2026-08-13T23:29Z
  > `/plugin install plugin@marketplace` now refreshes the marketplace first, so newly published plugins install without a manual marketplace update
- [v2.1.268](https://github.com/anthropics/claude-code/releases/tag/v2.1.268), published 2026-09-10T20:30Z
  > Improved `/plugin`: installing, enabling or disabling a plugin now takes effect when you close the menu; `/reload-plugins` is no longer needed afterwards
- [v2.1.275](https://github.com/anthropics/claude-code/releases/tag/v2.1.275), published 2026-09-17T22:33Z
  > Fixed `claude plugin marketplace update` deleting a GitHub marketplace's local copy when the fetch failed and the marketplace was named after its repository
- [v2.1.280](https://github.com/anthropics/claude-code/releases/tag/v2.1.280), published 2026-09-22T16:38Z
  > Fixed background plugin marketplace auto-update ignoring git credential helpers, so private-repo marketplaces were re-cloned every run or never updated

**Issues:** [adam-agentskills#12](https://github.com/Adam-S-Daniel/adam-agentskills/issues/12), [adam-agentskills-private#25](https://github.com/Adam-S-Daniel/adam-agentskills-private/issues/25)

#### 8. Built-in tooling that overlaps the registry's own: `/skill-doctor`, `claude plugin eval`

- [v2.1.261](https://github.com/anthropics/claude-code/releases/tag/v2.1.261), published 2026-09-04T19:58Z
  > Added `/skill-doctor` to show which loaded skills go unused and what they cost in context, so you can prune them
- [v2.1.269](https://github.com/anthropics/claude-code/releases/tag/v2.1.269), published 2026-09-11T19:17Z
  > Added `claude plugin eval`: run a plugin's eval suite against Claude Code and get scored, reproducible results (JSON + HTML report); see `claude plugin eval --help`

**Issues:** [adam-agentskills#13](https://github.com/Adam-S-Daniel/adam-agentskills/issues/13), [skills-evals#193](https://github.com/Adam-S-Daniel/skills-evals/issues/193)

#### 9. Which instructions subagents and headless runs load

- [v2.1.271](https://github.com/anthropics/claude-code/releases/tag/v2.1.271), published 2026-09-14T22:12Z
  > Added `omitClaudeMd` to agent frontmatter and `--agents` JSON, letting custom and plugin subagents run without user, project and local CLAUDE.md files; managed policy files still load
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > Improved session start-up for SDK and headless (`-p`) use: the first turn no longer waits on the per-directory CLAUDE.md lookup
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Fixed `--setting-sources` (and SDK `settingSources`) not being forwarded to spawned sessions: teammates, `/bg`, `claude agents` sessions and `--worktree --tmux` now start with the parent's restriction

**Issues:** [_agent-guidance#176](https://github.com/Adam-S-Daniel/_agent-guidance/issues/176), [skills-evals#194](https://github.com/Adam-S-Daniel/skills-evals/issues/194)

#### 10. New models vs. the CLI versions the eval workflows pin

- [v2.1.219](https://github.com/anthropics/claude-code/releases/tag/v2.1.219), published 2026-07-24T17:14Z
  > Added Claude Opus 5 (`claude-opus-5`), now the default Opus model — 1M context, fast mode at $10/$50 per Mtok
- [v2.1.223](https://github.com/anthropics/claude-code/releases/tag/v2.1.223), published 2026-08-06T00:52Z
  > Changed auto-compact to keep sessions on unrecognized model IDs within the assumed context window instead of letting them grow past it; set `CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1` to restore the previous behavior
- [v2.1.233](https://github.com/anthropics/claude-code/releases/tag/v2.1.233), published 2026-08-14T22:20Z
  > Improved print mode diagnostics: a `[claude-code:unrecognized_model]` line is written to stderr when a request goes out for a model ID Claude Code doesn't recognize; map it with `modelOverrides` to silence
- [v2.1.257](https://github.com/anthropics/claude-code/releases/tag/v2.1.257), published 2026-09-01T17:53Z
  > Added Claude Fable 5.1 (`claude-fable-5-1`), now the default Fable model — 1M context, $10/$50 per Mtok with $0.25/Mtok cache reads
- [v2.1.280](https://github.com/anthropics/claude-code/releases/tag/v2.1.280), published 2026-09-22T16:38Z
  > Added Claude Opus 5.5 (`claude-opus-5-5`), now the default Opus model — 1M context, $4/$20 per Mtok with $0.20/Mtok cache reads

**Issues:** [skills-evals#195](https://github.com/Adam-S-Daniel/skills-evals/issues/195)

#### 11. Cloud sessions can attach a repo from a different owner

- [v2.1.282](https://github.com/anthropics/claude-code/releases/tag/v2.1.282), published 2026-09-24T18:38Z
  > [Cloud sessions] Added attaching a repository from a different GitHub owner, such as a fork's upstream, to a running cloud session that already has one, including sessions started from Slack

**Issues:** [_agent-guidance#177](https://github.com/Adam-S-Daniel/_agent-guidance/issues/177)

#### 12. Cloud environment network access

- [v2.1.239](https://github.com/anthropics/claude-code/releases/tag/v2.1.239), published 2026-08-21T19:54Z
  > Claude Code on the web: requests from Bash and other tools to non-API anthropic.com hosts (e.g. www, docs) now go through the session's network proxy, so your environment's allowed domains apply
- [v2.1.275](https://github.com/anthropics/claude-code/releases/tag/v2.1.275), published 2026-09-17T22:33Z
  > [Claude Code on the web] Fixed cloud environments with a very long allowed-domains list saving fine and then failing every session start; saving now fails up front and says how much to trim
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > [Claude Code on the web] Fixed a cloud environment saved with Custom network access and no domains silently reverting to Trusted; the dialog now asks for at least one domain

**Issues:** [_agent-guidance#178](https://github.com/Adam-S-Daniel/_agent-guidance/issues/178)

#### 13. Auto-memory files, index limits and project directories

- [v2.1.210](https://github.com/anthropics/claude-code/releases/tag/v2.1.210), published 2026-07-14T23:45Z
  > Memory writes that leave a MEMORY.md index over its read limit now produce an explicit error instead of silent truncation
- [v2.1.211](https://github.com/anthropics/claude-code/releases/tag/v2.1.211), published 2026-07-15T23:02Z
  > Improved the memory index over-limit warning to measure only loaded content, excluding frontmatter and HTML comments
- [v2.1.214](https://github.com/anthropics/claude-code/releases/tag/v2.1.214), published 2026-07-18T01:20Z
  > Added an ISO `modified` timestamp to memory file frontmatter
- [v2.1.214](https://github.com/anthropics/claude-code/releases/tag/v2.1.214), published 2026-07-18T01:20Z
  > Fixed memory frontmatter values being silently truncated at an inline `#` when memory files are saved
- [v2.1.224](https://github.com/anthropics/claude-code/releases/tag/v2.1.224), published 2026-08-07T04:00Z
  > Fixed long (>200 char) project paths resolving to another project's session directory under a shared sanitized prefix; session list, rename, fork, delete and `/resume` no longer cross projects
- [v2.1.228](https://github.com/anthropics/claude-code/releases/tag/v2.1.228), published 2026-08-11T19:50Z
  > Fixed session cleanup deleting contents inside a project's memory folder
- [v2.1.234](https://github.com/anthropics/claude-code/releases/tag/v2.1.234), published 2026-08-17T20:20Z
  > Added the optional `CLAUDE_CODE_PROJECT_DIR_NAME` environment variable: hosts that give each session its own config directory can choose a short name for the per-project transcript directory
- [v2.1.268](https://github.com/anthropics/claude-code/releases/tag/v2.1.268), published 2026-09-10T20:30Z
  > Improved the MEMORY.md truncation warning to say how many lines were cut and where the cut starts
- [v2.1.273](https://github.com/anthropics/claude-code/releases/tag/v2.1.273), published 2026-09-15T20:23Z
  > Fixed `permissions.blockReadsOutsideWorkingDirectories`: a memory directory chosen by a repository's settings is no longer loaded into the prompt, recalled, indexed, or used by memory extraction

**Issues:** [_agent-guidance#179](https://github.com/Adam-S-Daniel/_agent-guidance/issues/179), [adam-agentskills#14](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14), [claude-memory-map#48](https://github.com/Adam-S-Daniel/claude-memory-map/issues/48)

#### 14. Instruction-size warnings

- [v2.1.206](https://github.com/anthropics/claude-code/releases/tag/v2.1.206), published 2026-07-10T01:45Z
  > Added a `/doctor` check that proposes trimming checked-in `CLAUDE.md` files by cutting content Claude could derive from the codebase
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Improved the large CLAUDE.md startup notice to also count instruction files together, so many mid-sized files and @-imports are caught

**Issues:** [_agent-guidance#180](https://github.com/Adam-S-Daniel/_agent-guidance/issues/180)

#### 15. Worktree-isolated subagents can no longer reach the main checkout

- [v2.1.210](https://github.com/anthropics/claude-code/releases/tag/v2.1.210), published 2026-07-14T23:45Z
  > Fixed `isolation: 'worktree'` subagents being able to run git-mutating commands against the main repo checkout instead of their own isolated worktree
- [v2.1.216](https://github.com/anthropics/claude-code/releases/tag/v2.1.216), published 2026-07-20T22:14Z
  > Fixed worktree-isolated subagents redirecting git into the shared checkout via `git -C`, `--git-dir`, or `GIT_DIR`/`GIT_WORK_TREE`
- [v2.1.222](https://github.com/anthropics/claude-code/releases/tag/v2.1.222), published 2026-08-04T22:39Z
  > Fixed worktree-isolated sessions and their subagents being able to run destructive git commands against the main checkout; isolation now applies to file edits and Bash in every session type

**Issues:** [adam-agentskills#15](https://github.com/Adam-S-Daniel/adam-agentskills/issues/15)

#### 16. Claude Code on the web renamed to cloud sessions

- [v2.1.274](https://github.com/anthropics/claude-code/releases/tag/v2.1.274), published 2026-09-17T00:12Z
  > Changed the `/status` GitHub line to read "Cloud sessions", and `/web-setup`, `/ultrareview`, and teleport messages to say "cloud session" instead of "Claude Code on the web"
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > [Claude Code on the web] Changed the admin Claude Code setting labeled "Web" to "Cloud sessions" and removed the redundant read-only Mobile row beneath it

**Issues:** [claude-memory-map#49](https://github.com/Adam-S-Daniel/claude-memory-map/issues/49)
