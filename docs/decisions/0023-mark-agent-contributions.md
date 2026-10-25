# 0023 — Mark every agent-made GitHub contribution, on Adam's account for now

**Status:** Accepted (2026-10-20)

## Context

Every write an agent makes on GitHub goes out as Adam's own account: commits,
PRs, issues, comments, reviews, labels and merges, through `gh`, git, the
session `mcp__github__*` server and the claude.ai `github-mcp` connector.

Only two of those carry any sign that an agent wrote them, and both are
Claude Code defaults that nothing in the fleet set on purpose: the
`Co-Authored-By: Claude` commit trailer and the PR-body footer. An issue
comment, a review, an issue body or a review reply carries nothing, so a
reader cannot tell it from something Adam wrote himself. Codex writes carry
neither default.

Before this change the fleet had no `attribution` key in user settings, no
PreToolUse hook on GitHub writes and no agent label in `repo-settings`'
`fleet.yml`.

## Decision

Owner decision, 2026-10-20: **enforced markers on Adam's account now, and a
dedicated GitHub App identity later** (Phase 2, below).

The markers, stated as a fleet rule in
[`agents-md/base.md`](../../agents-md/base.md) ("Agent-made GitHub
contributions carry a marker"), so Claude Code and Codex both receive it:

- **Every PR or issue body, issue or PR comment, review body or review
  reply** an agent writes ends with one footer line:
  `<sub>🤖 Written by <agent> on behalf of @Adam-S-Daniel</sub>`, where
  `<agent>` is `Claude Code` or `Codex`. It goes in the body, not the title,
  so it survives edits and stays greppable.
- **PRs and issues an agent opens** get the `agent-authored` label.
- **Commits** keep the `Co-Authored-By:` trailer, which GitHub renders as a
  co-author avatar, and add a machine-readable `Agent: claude-code` or
  `Agent: codex` trailer.
- **A merge, close or label change** is followed by a footer-marked comment
  saying what was done and why.
- **An agent never submits an APPROVE review on Adam's account.**

[`.claude/hooks/github-write-marker.sh`](../../.claude/hooks/github-write-marker.sh)
enforces the body rule and the approval rule as a Claude Code PreToolUse hook.
It blocks (exit 2) a GitHub write whose body lacks the token
`on behalf of @Adam-S-Daniel`, and any APPROVE review, and its message names
the exact footer to append. It does not rewrite the body itself: the agent
corrects the write and retries. MCP tools are matched by a regex on the tool
name, never by a connector prefix, because the claude.ai connector's prefix
varies by surface. The marker check is lexical, which the fleet's AST rule
permits for a token. Whether a Bash command is a GitHub write is a question of
shape, so the hook decides it by tokenizing the command with Python's `shlex`
(heredoc bodies cut out first) and treating only a `gh` in command position as
a write: `echo "gh issue comment ..."` or a `gh` string passed to a script is
not one. A `--body-file` path is expanded (inline `NAME=value` assignments,
the environment, `~`) before it is read, and a path the hook still cannot read
is named in the block message. A command `shlex` cannot tokenize falls back to
the earlier regex over the whole text. Its tests are
`test_github_write_marker_hook` in `test/run-tests.sh`.

### What cannot be marked

GitHub records some acts with only an actor and no body: who merged a PR, who
added or removed a label, who closed an issue, who approved. On Adam's account
the actor is always Adam. The follow-up comment covers merges, closes and
label changes. An approval is different: it is the act a branch rule counts as
a human sign-off, and a later comment does not change what it counts as. That
is why an agent never approves rather than approving and commenting.

### The delivery gap

This change ships the hook script, its tests and the rule. It does **not**
register the hook in each repo's `.claude/settings.json`, so in a cloud
session, where only repo settings load, nothing runs it yet.

The registrar `scripts/sync.sh` uses (`scripts/register-bootstrap-hook.sh`,
re-pointed for `fleet-memory.sh` around `sync.sh` lines 1272-1285 through the
`BOOTSTRAP_HOOK_*` env seam) **cannot register a PreToolUse hook as it
stands**. The seam covers the command (`BOOTSTRAP_HOOK_COMMAND`), the matcher
(`BOOTSTRAP_HOOK_MATCHER`), the timeout (`BOOTSTRAP_HOOK_TIMEOUT`) and the
basename that identifies the hook (`BOOTSTRAP_HOOK_BASENAME`). The event key
is hard-coded: the script reads, migrates and appends only under
`hooks.SessionStart`, and `scripts/bootstrap-status.sh` reads only
`hooks.SessionStart`. Setting the matcher to a tool-name regex would write a
SessionStart group that never fires on a tool call. Its matcher migration
(`startup|resume` widened to `startup|resume|fork`) is SessionStart-specific
too. The sync also copies only `fleet-memory.sh` and `skills-bootstrap.sh`
into consumer repos, not every file in `.claude/hooks/`.

Registration is a follow-up: an event seam on both scripts, then a third
delivery block in `sync.sh`. Until then:

- **Local machines** register the hook in user settings
  (`~/.claude/settings.json` `hooks.PreToolUse`), outside this repo.
- **Cloud sessions** have the `base.md` rule as their control. It reaches
  them through the fleet-memory hook, but nothing enforces it there.

## Consequences

- Every agent body on GitHub is greppable by one token, and a reader can tell
  an agent's comment from Adam's at a glance.
- A write that forgot the footer now fails once and is retried, which costs
  one extra tool call. A body-less write (`gh issue edit --add-label x`, an
  `issue_write` with labels only) passes untouched.
- The marker check is lexical, so it can be fooled: a command that holds the
  token anywhere passes as a whole, and `--body-file -` (stdin) is only
  checked through the command text. It is a guard against forgetting, not
  against an agent working around it.
- The write detection has known gaps. Quote context is lost after
  tokenizing, so a single-quoted `'$(gh pr comment ...)'` or backtick span is
  still checked as a write (an extra block, not a miss), and a single-quoted
  `'$VAR'` body-file path is expanded. A body-file path is resolved against
  the hook's `cwd`, not a `cd` earlier in the command. A `gh` reached through
  anything the hook's short wrapper list does not name (`ssh host gh ...`,
  `find -exec gh ...`, a shell function or alias) is not seen.
- The merge, label and close actors still read as Adam; only the follow-up
  comment says otherwise.
- Adam cannot get a second reviewer from an agent: approval stays his alone
  until Phase 2.
- Room for the rule in `base.md` came from shortening citations and wording
  in six other sections; the full-mode build was 1 byte under its
  29696-byte budget before this change. The trims are listed in
  [`docs/guidance-impact.md`](../guidance-impact.md).

## Phase 2: a separate identity

A GitHub App (for example `adam-agent`) installed on both owners,
`Adam-S-Daniel` and `jodidaniel`, with `gh` and git using a token minted per
session, so commits are authored as `adam-agent[bot]` and Adam can approve an
agent's PR as a real second reviewer. Blockers, for the follow-up issue:

- **The claude.ai `github-mcp` connector and the Claude Code cloud git proxy
  are authenticated as Adam** and cannot be re-pointed at an App.
- **The App's private key would have to reach cloud sessions**, which today
  receive no such secret.
- **Ruleset bypass.** The fleet's default branches are PR-only by ruleset,
  and a bot that writes needs a bypass actor declared in `repo-settings`'
  `fleet.yml` ([repo-settings ADR 0001](https://github.com/Adam-S-Daniel/repo-settings/blob/main/docs/decisions/0001-bot-writes-results-branch-or-declared-bypass.md)).

## Alternatives considered

- **A GitHub App identity now.** Rejected for Phase 1: the blockers above
  leave every connector write and every cloud git push on Adam's account, so
  the App alone would mark only some writes and mislead about the rest.
- **The rule alone, no hook.** Rejected: the defaults already showed that a
  marker nobody checks is applied only where a tool happens to add it.
- **A hook that appends the footer itself.** Rejected: a PreToolUse hook
  rewriting `tool_input` would change what the agent believes it posted. A
  block with the exact line to append keeps the agent's view and the posted
  text the same.
- **Marking the title.** Rejected: titles get edited and truncated, and a
  marker there crowds out the summary.
