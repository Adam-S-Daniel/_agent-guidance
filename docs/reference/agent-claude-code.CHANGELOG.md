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

### 2026-10-28 — 2.1.312 through 2.1.312

- **Checked:** 2026-10-28T01:15Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.312](https://github.com/anthropics/claude-code/releases/tag/v2.1.312), published 2026-10-27T20:24Z
- **Window:** v2.1.312 (published 2026-10-27T20:24Z), the first stable release after v2.1.311: 1 release, 39 bullets (only the two in group 1 are indexed). The publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.312` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`; this run did not re-read their heads and relied on the 2026-10-22 reach list, so none is newly assessed beyond a fleet grep. Last reached: `_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `adamdaniel.ai`, `ai-usage-dashboard`, `claude-memory-map`, `cms-platform`, `fastmail-actions`, `GHA-bench` (archived), `jodidaniel.com`, `repo-settings`, `rss-inator`, `skills-evals`, `wsl-automation` (local checkouts present this session).

#### 1. Prompt-audit sweep trigger

- [v2.1.312](https://github.com/anthropics/claude-code/releases/tag/v2.1.312), published 2026-10-27T20:24Z (bullet 2.1.312/3)
  > Fixed Claude overwriting a very large CLAUDE.md, rules file or @-included file in a subdirectory after seeing only its start; Write now asks Claude to read it all or edit in place
- [v2.1.312](https://github.com/anthropics/claude-code/releases/tag/v2.1.312), published 2026-10-27T20:24Z (bullet 2.1.312/4)
  > Fixed per-directory and `paths:` skills not being found after a Bash `cd` deeper into a git repository

**Issues:** [_agent-guidance#358](https://github.com/Adam-S-Daniel/_agent-guidance/issues/358) (no label). Sweep fired: https://claude.ai/code/cse_01SphsQALvzaFjKqVqwYqpxp.

No other group met the name-the-surface bar. The remaining bullets (VS Code, Claude Tag, Code Review, cloud-session, Remote Control, OpenTelemetry, effort-indicator, vim-mode and hook-concurrency changes) remove failures or adjust display without changing a documented command, flag or setting the fleet uses. Codex: no stable release after 0.164.0 (page 1 holds only 0.165.0 and 0.166.0 alphas above it).

### 2026-10-27 — 2.1.311 through 2.1.311

- **Checked:** 2026-10-27T01:15Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.311](https://github.com/anthropics/claude-code/releases/tag/v2.1.311), published 2026-10-26T21:51Z
- **Window:** v2.1.311 (published 2026-10-26T21:51Z), the first stable release after v2.1.310: 1 release, 92 bullets (only the one in group 1 is indexed). The publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.311` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`; this run did not re-read their heads and relied on the 2026-10-22 reach list, so none is newly assessed beyond a fleet grep. Last reached: `_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `adamdaniel.ai`, `ai-usage-dashboard`, `claude-memory-map`, `cms-platform`, `fastmail-actions`, `GHA-bench` (archived), `jodidaniel.com`, `repo-settings`, `rss-inator`, `skills-evals`, `wsl-automation` (local checkouts present this session).

#### 1. Prompt-audit sweep trigger

- [v2.1.311](https://github.com/anthropics/claude-code/releases/tag/v2.1.311), published 2026-10-26T21:51Z (bullet 2.1.311/7)
  > Fixed Claude seeing stale MEMORY.md and CLAUDE.md content right after automatic compaction when those files were edited earlier in the session

**Issues:** [_agent-guidance#345](https://github.com/Adam-S-Daniel/_agent-guidance/issues/345) (no label). Sweep fired: https://claude.ai/code/cse_01UzAgQJGNAcMNs6W4eQB3Fp.

No other group met the name-the-surface bar. A fleet grep for `DISABLE_COMPACT`, `SubagentStop`, `CLAUDE_CODE_SYNC_SKILLS`, `CLAUDE_CODE_MODEL_CATALOG`, `ultrathink`, `CLAUDE_CODE_RETRY_WATCHDOG` and `self-hosted-runner` found no fleet-repo file using them (hits were only in the vendor checkouts `claude-code` and `codex`, and `adam-agentskills` `setup.sh` for `syncClaudeAiSkills`, which the bullet about reusing a recent claude.ai skills list does not contradict). One candidate surface exists but cannot take an issue: the bullet about model names and IDs dropping the `[1m]` suffix may touch `GHA-bench` `combine_results.py` `_context` and `runner.py`, which key `modelUsage` by the requested model id with a `[1m]` fallback, but `GHA-bench` is archived, so no issue was filed; the suffix is still accepted and the fallback chain ends at the same 1M default for the models involved. The remaining bullets (VS Code, Claude Tag, Code Review, cloud-session, screen-reader and MCP fixes) remove failures without changing a documented command, flag or setting. Codex: no stable release after 0.164.0 (page 1 holds only 0.165.0 and 0.164.0 alphas above it).

### 2026-10-24 — 2.1.310 through 2.1.310

- **Checked:** 2026-10-24T01:12Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.310](https://github.com/anthropics/claude-code/releases/tag/v2.1.310), published 2026-10-23T23:39Z
- **Window:** v2.1.310 (published 2026-10-23T23:39Z), the first stable release after v2.1.309: 1 release, 26 bullets (not individually indexed: no group was drafted). The publish time is the `datetime` attribute on the tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` section in `anthropics/claude-code` (shallow clone; tag `v2.1.310` listed by `git ls-remote --tags`); the tag page was fetched (HTTP 200) for its publish time.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`; this run did not re-read their heads and relied on the 2026-10-22 reach list, so none is newly assessed beyond a fleet grep. Last reached: `_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `adamdaniel.ai`, `ai-usage-dashboard`, `claude-memory-map`, `cms-platform`, `fastmail-actions`, `GHA-bench` (archived), `jodidaniel.com`, `repo-settings`, `rss-inator`, `skills-evals`, `wsl-automation` (local checkouts present this session).

No group met the name-the-surface bar, and no bullet changes models, `/doctor` or instruction-file loading, so the prompt-audit sweep is not triggered. A grep of the fleet for `cleanupPeriodDays`, `PreToolUse` and `PermissionRequest` found only `adam-agentskills` ADR 0010, which cites `cleanupPeriodDays` for the earlier trash behavior (the 2.1.310 fix, about background cleanup deleting recent files at very large values, changes nothing it documents), and no fleet hook registered on either Windows-affected event.

### 2026-10-23 — 2.1.308 through 2.1.309

- **Checked:** 2026-10-23T01:10Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.309](https://github.com/anthropics/claude-code/releases/tag/v2.1.309), published 2026-10-22T22:18Z
- **Window:** v2.1.308 (published 2026-10-22T04:34Z, "Bug fixes and reliability improvements") and v2.1.309 (published 2026-10-22T22:18Z), the first stable releases after v2.1.307: 2 releases, 82 bullets (1 + 81; only the two in group 1 are indexed). Publish times are the `datetime` attribute on each tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` sections in `anthropics/claude-code` (shallow clone; tags `v2.1.308` and `v2.1.309` listed by `git ls-remote --tags`); both tag pages were fetched (HTTP 200) for their publish times.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`; this run did not re-read their heads and relied on the 2026-10-22 reach list below, so none is newly assessed beyond a fleet grep. Last reached: `_agent-guidance`, `adam-agentskills`, `adam-agentskills-private`, `adamdaniel.ai`, `ai-usage-dashboard`, `claude-memory-map`, `cms-platform`, `fastmail-actions`, `GHA-bench` (archived), `jodidaniel.com`, `repo-settings`, `rss-inator`, `skills-evals`, `wsl-automation` (local checkouts present this session).

#### 1. Prompt-audit sweep trigger

- [v2.1.309](https://github.com/anthropics/claude-code/releases/tag/v2.1.309), published 2026-10-22T22:18Z
  > Fixed sessions compacting repeatedly after reading files under a very large nested CLAUDE.md; a nested CLAUDE.md over the size limit now loads only its start
- [v2.1.309](https://github.com/anthropics/claude-code/releases/tag/v2.1.309), published 2026-10-22T22:18Z
  > Fixed a skill in an `--add-dir` folder being unreachable when another skill already held its name; it is now offered as `<folder>:<name>`

**Issues:** [_agent-guidance#317](https://github.com/Adam-S-Daniel/_agent-guidance/issues/317) (no label). Sweep fired: https://claude.ai/code/cse_01Du2rKYDkUecMsqz1Anp17Q.

No other group met the name-the-surface bar. A fleet grep for `ultrareview`, `--mcp-config`, `synced/manifest.json`, `"shell": "powershell"`, `BEDROCK_BASE_URL` and `git replace` found only `GHA-bench` `runner.py` passing an empty `--mcp-config` (unaffected by the fix about the config file being modified by sandboxed commands) and documentation of the `synced` store in `adam-agentskills` and `skills-evals`, which the 2.1.309 fix about synced plugins being removed after a disk read error does not contradict. The sandbox, permission-check, worktree, VS Code, cloud-session and Claude Tag bullets remove failures without changing a documented command, flag or setting. Codex: no stable release after 0.163.0 (pages 1 to 3 walked: page 1 holds only 0.163.0 and 0.164.0 alphas, 0.163.0 is on page 2, 0.162.0 and 0.162.1 on page 3).

### 2026-10-22 — 2.1.306 through 2.1.307

- **Checked:** 2026-10-22T01:08Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.307](https://github.com/anthropics/claude-code/releases/tag/v2.1.307), published 2026-10-21T22:17Z
- **Window:** v2.1.306 (published 2026-10-21T03:16Z) and v2.1.307 (published 2026-10-21T22:17Z), the first stable releases after v2.1.305: 2 releases, 45 bullets (2 + 43; not individually indexed: no group was drafted). Publish times are the `datetime` attribute on each tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` sections in `anthropics/claude-code` (shallow clone; tags `v2.1.306` and `v2.1.307` listed by `git ls-remote --tags`); both tag pages were fetched (HTTP 200, neither flagged pre-release) for their publish times.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` 506f9d2 (the `persistent/vendor-changelog` checkout; origin/main is ahead), `adam-agentskills` 59c8c74, `adam-agentskills-private` 2465891, `adamdaniel.ai` ab557c5, `ai-usage-dashboard` 09d9845, `claude-memory-map` 676d7f9, `cms-platform` 3b0a799, `fastmail-actions` f3c799f, `GHA-bench` bf0f9875 (archived, read-only), `jodidaniel.com` 3626102, `repo-settings` a9dc5ea, `rss-inator` 346595f, `skills-evals` b644ea2, `wsl-automation` baa2d20.

No group met the name-the-surface bar. A grep of the fleet for `CLAUDE_CODE_DISABLE_1M_CONTEXT`, `claude_code.safety_event`, `OTEL_EXPORTER_OTLP_HEADERS`, `CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS`, `axScreenReader`, `CLAUDE_AX_SCREEN_READER`, `prompt-caching-scope`, `/autocompact`, `@claude review` and `setModel` found no dependence on a changed behavior; the one `claude-api` hit is a skill-name list in an `adam-agentskills` experiment note. The v2.1.306 fix for `claude -p` reporting "Not logged in" inside cloud sessions and the v2.1.307 MCP and subagent fixes remove failures without changing a documented command, flag or setting. No bullet adds a model, changes what an alias resolves to, or changes `/doctor` or instruction-file loading, so the prompt-audit sweep (step 4a) was not triggered (`sweep: none`).

### 2026-10-21 — 2.1.296 through 2.1.305

- **Checked:** 2026-10-21T01:35Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.305](https://github.com/anthropics/claude-code/releases/tag/v2.1.305), published 2026-10-20T20:46Z
- **Window:** eight stable releases after v2.1.295, in publish order: v2.1.296 (2026-10-09T19:28Z), v2.1.299 (2026-10-14T02:49Z), v2.1.300 (2026-10-14T18:24Z), v2.1.301 (2026-10-15T19:22Z), v2.1.302 (2026-10-16T20:53Z), v2.1.303 (2026-10-18T22:26Z), v2.1.304 (2026-10-19T21:34Z), v2.1.305 (2026-10-20T20:46Z): 586 bullets (79 + 190 + 88 + 51 + 63 + 1 + 76 + 38; not individually indexed, the quotes below are copied from the file by script). v2.1.297 and v2.1.298 have no tag or changelog section and were skipped as gaps. Publish times are the `datetime` attribute on each tag page, in UTC. The runs of 2026-10-10 through 2026-10-20 are not on this branch, so the window spans 12 days.
- **Source text:** the matching `CHANGELOG.md` sections in `anthropics/claude-code` (shallow clone; the eight tags listed by `git ls-remote --tags`); all eight tag pages were fetched (HTTP 200, none flagged pre-release) for their publish times, and the bodies of v2.1.302 and v2.1.305 were spot-checked against them.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet`, each reached (working-tree head read): `_agent-guidance` 7eb07f0 (origin/main; the checkout head `5b142c9` is this branch), `adam-agentskills` dd2852e, `adam-agentskills-private` 6b88a54, `adamdaniel.ai` 6afabe6, `ai-usage-dashboard` f6806a6, `claude-memory-map` 5f785a2, `cms-platform` 95579a3, `fastmail-actions` f4aafb1, `GHA-bench` bf0f9875, `jodidaniel.com` 815d6ba, `repo-settings` 942f203, `rss-inator` fe32eeb, `skills-evals` e97dce5, `wsl-automation` bc2862a.

#### 1. `claude -p` subagent defaults changed

- [v2.1.302](https://github.com/anthropics/claude-code/releases/tag/v2.1.302), published 2026-10-16T20:53Z
  > Changed fork subagents (`subagent_type: "fork"`) to be on by default in `claude -p`, the Agent SDK, the desktop app and VS Code, as they already are in the terminal; `CLAUDE_CODE_FORK_SUBAGENT=0` turns them off, and `claude mcp serve` never offers them
- [v2.1.302](https://github.com/anthropics/claude-code/releases/tag/v2.1.302), published 2026-10-16T20:53Z
  > Changed the desktop app and VS Code to always run the main conversation's subagents in the background, as the terminal does, rather than let Claude choose; a subagent's own Agent calls in `claude -p`, the Agent SDK, the desktop app and VS Code now run in the foreground by default

**Issues:** none filed: `GHA-bench` is archived (read-only; `POST /repos/Adam-S-Daniel/GHA-bench/issues` answered 403 "Repository was archived so is read-only"). The surface is `runner.py`'s `claude -p` command, where `CLAUDE_CODE_FORK_SUBAGENT` is not pinned; a future un-archive should re-check it. Trap: [_agent-guidance#307](https://github.com/Adam-S-Daniel/_agent-guidance/issues/307). `adam-agentskills`' credit lane runs a `claude -p` child (ADR 0018) but pins nothing a subagent default changes, so no issue.

#### 2. Prompt-audit sweep trigger

- [v2.1.299](https://github.com/anthropics/claude-code/releases/tag/v2.1.299), published 2026-10-14T02:49Z
  > Changed skill and command loading: one whose file or folder name has hidden, direction-changing or blank-like characters, `<` or `>` is no longer offered until renamed; such a `name:` is ignored
- [v2.1.299](https://github.com/anthropics/claude-code/releases/tag/v2.1.299), published 2026-10-14T02:49Z
  > Improved `/reload-skills` to say it re-sends the full skill list to Claude, which is how to get skills back after compaction
- [v2.1.301](https://github.com/anthropics/claude-code/releases/tag/v2.1.301), published 2026-10-15T19:22Z
  > Fixed the `fable` (and `best`) model alias erroring on the Anthropic API when the newest Fable model is not available to your organization: Claude Code now retries once on the previous Fable model
- [v2.1.301](https://github.com/anthropics/claude-code/releases/tag/v2.1.301), published 2026-10-15T19:22Z
  > Fixed skills no longer being listed to the model after using `/rewind`
- [v2.1.304](https://github.com/anthropics/claude-code/releases/tag/v2.1.304), published 2026-10-19T21:34Z
  > Fixed resumed and cloud sessions sending Claude the whole skill list again each time plugins reloaded at startup
- [v2.1.304](https://github.com/anthropics/claude-code/releases/tag/v2.1.304), published 2026-10-19T21:34Z
  > Changed compaction to send Claude the skill list again afterwards; `/reload-skills` now sends only new or changed skills

**Issues:** [_agent-guidance#308](https://github.com/Adam-S-Daniel/_agent-guidance/issues/308) (no label). Sweep fired: https://claude.ai/code/cse_01KRBZ2XyLTLMHoDwU5VfwvT.

No other group met the name-the-surface bar. A grep of the fleet for `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `mcp__`, `claude plugin eval`, `claude rc`, `claude agents`, `--bg`, `sessions/`, `sandbox.` and `unshare` found no dependence on a changed behavior. The credit lane's `UserPromptSubmit` hook (`adam-agentskills`, `hooks/credit-lane/on-prompt.sh`) always exits 0 and only prints context, so running before skill expansion changes nothing. `wsl-automation` pins `claude rc --spawn same-dir`, so the saved spawn mode added in v2.1.305 does not apply, and it passes neither removed flag. The `claude plugin eval` additions in v2.1.299 add no script-running grader, so the monitor-don't-wrap decision in `skills-evals`' `DESIGN.md` stands. `sandbox.allowNestedUserNamespaces` (v2.1.305) leaves the fleet's "run in a PID namespace, never escalate out of the sandbox" rule true. `GHA-bench`'s `models.py` has no Sonnet 5.5 row, so the v2.1.296 cache-read price change touches nothing there. Other bullets cover mods, VS Code, Claude Tag, Code Review, cloud-session and self-hosted-runner internals.

### 2026-10-09 — 2.1.294 through 2.1.295

- **Checked:** 2026-10-09T01:25Z, [release log](https://github.com/anthropics/claude-code/releases)
- **Latest version in the change log:** [v2.1.295](https://github.com/anthropics/claude-code/releases/tag/v2.1.295), published 2026-10-08T19:48Z
- **Window:** v2.1.294 (published 2026-10-08T05:03Z) and v2.1.295 (published 2026-10-08T19:48Z), the first stable releases after v2.1.293: 2 releases, 145 bullets (2 + 143; not individually indexed: no group was drafted). Publish times are the `datetime` attribute on each tag page, in UTC.
- **Source text:** the matching `CHANGELOG.md` sections in `anthropics/claude-code` (shallow clone; tags `v2.1.294` and `v2.1.295` listed by `git ls-remote --tags`); both tag pages were fetched (HTTP 200) for their publish times.
- **Repos considered:** all 14 in `repos.yml` `cron_coverage.fleet` (`ai-usage-dashboard` is new since the 2026-10-08 entry, which listed 13), each reached (working-tree head read): `_agent-guidance` 962a9d7, `adam-agentskills` 746881e, `adam-agentskills-private` 43e8448, `adamdaniel.ai` 3d1c2c0, `ai-usage-dashboard` 5115bdc (anonymous shallow clone, read-only), `claude-memory-map` 9873811, `cms-platform` 9dd9454, `fastmail-actions` 760ee52, `GHA-bench` bf0f987, `jodidaniel.com` 713c807, `repo-settings` c062ce2, `rss-inator` 49297d6, `skills-evals` 2fb97f8, `wsl-automation` 9303b57.

No group met the name-the-surface bar. A grep of the fleet for `"type": "prompt"`/`"agent"` hooks, `CLAUDE_ENV_FILE`, `FORCE_COLOR`, `CLAUDE_CODE_RETRY_WATCHDOG`, `onFailure`, `CLAUDE_AUTO_BACKGROUND_TASKS`, `seed-admin`, `forceLoginMethod`, async hooks, subagent `skills:` frontmatter and `claude -p` output formats found no dependence on a changed behavior. The only `prompt` hook type is a test fixture string in `adam-agentskills`' `test_check_consistency.py`, so the 2.1.294 fixes to instruction-style `prompt` and `agent` hooks touch no fleet hook. The `CLAUDE_ENV_FILE` hits (`setup-hooks.sh` in `adamdaniel.ai`, `cms-platform` and `jodidaniel.com`) append a `LANG` export once, which the `/resume` and `/branch` fix leaves alone. Every fleet `claude -p` call (`GHA-bench`, `skills-evals`) uses `--output-format json` or `stream-json`, not the text output the `claude -p` fix changes. No fleet repo has a `.claude/agents` directory, so the cap of 32 preloaded subagent skills changes no fleet instruction file; it did not mark the prompt-audit sweep (`sweep: none`). The `ai-usage-dashboard` collector reads `~/.claude/.credentials.json`, and no bullet changes that file. Other bullets cover the Claude apps gateway, mods and plugin hooks workers, Remote Control and cloud-session fixes, terminal rendering and vim mode, VS Code, Claude Tag and Code Review, none of which a fleet repo depends on.

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

**Rechecked 2026-10-04:** [README.md](../../README.md) already describes the
native fallback; [bridge-status.sh](../../scripts/bridge-status.sh) and the
[sync comments and generated prose](../../scripts/sync.sh) now agree. The
fleet retains its bridge pending the owner's migration decision in
[_agent-guidance#172](https://github.com/Adam-S-Daniel/_agent-guidance/issues/172).
Still pending: on CLI ≥ 2.1.281, test each `/config` Project instructions
option in bridged and unbridged temporary repos with a unique canary to
record once, twice or missing loading. The provider-extension quote above
is present in [CHANGELOG.md 2.1.281](https://github.com/anthropics/claude-code/blob/2bfb629dfaff0c8318047a4beb93cf1dc5b58b18/CHANGELOG.md#21281),
although the current release body omits it. No live CLI check was performed.

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

**Rechecked 2026-10-04:** The [memory-home registrar](../../scripts/register-memory-home-hook.sh),
[bootstrap registrar](../../scripts/register-bootstrap-hook.sh),
[repo settings](../../.claude/settings.json) and [tests](../../test/run-tests.sh)
already use `startup|resume|fork`; [fleet-memory.sh](../../.claude/hooks/fleet-memory.sh)
needs no matcher correction. Its loading-order premise remains unverified.
Static inspection of the [memory-home](../../.claude/hooks/memory-home.sh),
[skills-bootstrap](../../.claude/hooks/skills-bootstrap.sh) and fleet-memory
output paths found JSON serialization or fixed prose prefixes, with no path
deliberately emitting `{`-leading non-JSON text; this is not live parsing proof.
[_agent-guidance#173](https://github.com/Adam-S-Daniel/_agent-guidance/issues/173)
still needs a first-turn unique canary for startup, fork and resume on CLI
≥ 2.1.268, beginning with user memory absent, plus cancellation checks.
A conversation appearing early in the UI does not prove instruction-loading order.

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

**Rechecked 2026-10-04:** [base.md](../../agents-md/base.md) already removed
the disputed 2.1.273 floor and describes short names with a namespace fallback.
The quoted releases date terminal account sync to 2.1.275 and short menu names
to 2.1.281. No further guidance edit is needed for
[_agent-guidance#174](https://github.com/Adam-S-Daniel/_agent-guidance/issues/174).

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

**Rechecked 2026-10-04:** [base.md](../../agents-md/base.md) now requires CLI
≥ 2.1.280 for recorded commits and says to try update, recheck, then
uninstall/reinstall only if still stale. The 2.1.277/2.1.280 metadata fixes do
not prove bundle contents refresh or that the historical version gate is fixed;
2.1.283 file-recovery fixes do not prove content refresh either. Keep the
file-based check for [_agent-guidance#175](https://github.com/Adam-S-Daniel/_agent-guidance/issues/175)
until a controlled fixture on CLI ≥ 2.1.280 compares `installPath` contents
and recorded SHA before and after `claude plugin update`: pinned commits,
branch/tag sources, and a same-version new commit. Compare `claude plugin list
--json` fields against `installed_plugins.json` before switching the check's
source. No live plugin update was performed.

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

**Rechecked 2026-10-04:** [base.md](../../agents-md/base.md) already removed
the cross-owner refusal; its session-reach disclaimer remains valid. For
[_agent-guidance#177](https://github.com/Adam-S-Daniel/_agent-guidance/issues/177),
the owner still needs to attempt attaching a `jodidaniel` repo to an
`Adam-S-Daniel` cloud session and record the result, including a Slack-started
session if that variant is covered. No attachment was attempted.

#### 12. Cloud environment network access

- [v2.1.239](https://github.com/anthropics/claude-code/releases/tag/v2.1.239), published 2026-08-21T19:54Z
  > Claude Code on the web: requests from Bash and other tools to non-API anthropic.com hosts (e.g. www, docs) now go through the session's network proxy, so your environment's allowed domains apply
- [v2.1.275](https://github.com/anthropics/claude-code/releases/tag/v2.1.275), published 2026-09-17T22:33Z
  > [Claude Code on the web] Fixed cloud environments with a very long allowed-domains list saving fine and then failing every session start; saving now fails up front and says how much to trim
- [v2.1.277](https://github.com/anthropics/claude-code/releases/tag/v2.1.277), published 2026-09-18T18:06Z
  > [Claude Code on the web] Fixed a cloud environment saved with Custom network access and no domains silently reverting to Trusted; the dialog now asks for at least one domain

**Issues:** [_agent-guidance#178](https://github.com/Adam-S-Daniel/_agent-guidance/issues/178)

**Rechecked 2026-10-07 (UTC):** Release quotes and dates were re-fetched from
the GitHub release API; all three original quotes agree with the upstream
[CHANGELOG at `765f236fe1bfcc678e0ec59af9170fb7d6771a3a`](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21239), including [v2.1.275](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21275) and [v2.1.277](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21277).
The [network allowlist changelog](network-allowlist-claude-environments.CHANGELOG.md)
records the existing mechanics, and [merged PR #239](https://github.com/Adam-S-Daniel/_agent-guidance/pull/239)
already fixed them. No domain changes are justified from this workstation;
live probes of `docs.anthropic.com`, `code.claude.com` and `www.anthropic.com`
in the actual cloud environment's My Whitelist remain pending. This
`_agent-guidance` follow-up remains open; no live behavior was measured.

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

**Rechecked 2026-10-07 (UTC):** All nine quotes and release dates match the
fetched release bodies and the pinned upstream [CHANGELOG at
`765f236fe1bfcc678e0ec59af9170fb7d6771a3a`](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21214).
[memory-home.sh](../../.claude/hooks/memory-home.sh) and
[run-tests.sh](../../test/run-tests.sh) are covered by [merged PR #237](https://github.com/Adam-S-Daniel/_agent-guidance/pull/237),
which already tests timestamps, quoted hashes, URL anchors, literal short-path
hashes, and ordinary short and long project-directory fixtures. Those fixtures
do not measure the CLI-selected directory with
`CLAUDE_CODE_PROJECT_DIR_NAME`. The current [session documentation](https://code.claude.com/docs/en/sessions#name-the-project-directory-yourself),
checked 2026-10-07, documents names of 1–64 letters, digits, hyphens or
underscores, excluding Windows device names; it says the variable is ignored
unless `CLAUDE_CONFIG_DIR` is also set, and auto-memory uses
`projects/<name>/memory`. The [environment-variable documentation](https://code.claude.com/docs/en/env-vars)
confirms this is a launch environment variable, not a settings `env` entry.
The documented naming contract is unverified in a live CLI launch. This
`_agent-guidance` follow-up remains open; no live behavior was measured. No
parser edits were made.
The [README intro](../../README.md#memory-notes-outside-a-repo) and
[hook header](../../.claude/hooks/memory-home.sh) now describe the documented
project-name path. The existing `projects/*/memory/*.md` scan covers these
names, including the existing selected-name `p` fixture.

#### 14. Instruction-size warnings

- [v2.1.206](https://github.com/anthropics/claude-code/releases/tag/v2.1.206), published 2026-07-10T01:45Z
  > Added a `/doctor` check that proposes trimming checked-in `CLAUDE.md` files by cutting content Claude could derive from the codebase
- [v2.1.281](https://github.com/anthropics/claude-code/releases/tag/v2.1.281), published 2026-09-23T19:19Z
  > Improved the large CLAUDE.md startup notice to also count instruction files together, so many mid-sized files and @-imports are caught

**Issues:** [_agent-guidance#180](https://github.com/Adam-S-Daniel/_agent-guidance/issues/180)

**Rechecked 2026-10-07 (UTC):** Both quotes and dates match the fetched release
bodies and pinned upstream [CHANGELOG sections for v2.1.206 and v2.1.281](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21206), [including the v2.1.281 entry](https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md#21281).
The [README notice](../../README.md#claude-codes-instruction-size-notice)
and [merged PR #239](https://github.com/Adam-S-Daniel/_agent-guidance/pull/239)
already document the aggregated notice. Current
[memory documentation](https://code.claude.com/docs/en/memory#my-claudemd-is-too-large)
says the combined limit counts each `CLAUDE.md`, rules and `@import` file, but
does not establish a numeric limit. The README's bundle-derived threshold
remains identified as a 2.1.289 observation. The owner still needs to measure
the real multi-repo startup notice; no budget changes or real CLI runs were
made here. This confirms the notice's scope but gives no measured threshold for
this account's repositories. This `_agent-guidance` follow-up remains open; no
live behavior was measured.

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
