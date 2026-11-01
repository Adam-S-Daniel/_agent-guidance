# 0035 — Sessions prove the guidance loaded with a check value

**Status:** Accepted (2026-11-01). Builds on [ADR 0016](0016-the-freshest-delivery-wins-the-shared-global-block.md), which defines the installed block, and [ADR 0032](0032-guidance-evals-deliver-through-user-memory-and-prove-it-per-arm.md), which proves delivery per eval arm with a token.

## Context

The repo stub ([`agents-md/stub.md`](../../agents-md/stub.md), the managed half of every repo's `AGENTS.md`) tells a session to read the verdict that [`fleet-memory.sh`](../../.claude/hooks/fleet-memory.sh) prints at session start and to act on it. A session can say "the full guidance loaded" when it did not, and nothing could tell.

The [`guidance-load-record.sh`](../../.claude/hooks/guidance-load-record.sh) Stop hook records mechanically whether the full guidance loaded. It reads the transcript's `instructions` attachment, which lists every memory file Claude Code loaded and its text. Claude Code strips HTML comments from that text, so the hook counts a visible marker line instead of the `<!-- fleet-guidance-version -->` comment. What it could not do is compare the session's own statement with that record.

Two obvious values do not work. The version id in the verdict is printed by the hook whether or not memory loaded, so a session that never saw the guidance can quote it. And anything in an HTML comment never reaches the model.

## Decision

1. **The installed block ends with a check value.** `fleet-memory.sh` keeps the full sha256 of the payload. `version` stays the first 8 hex. `check` is the last 8 hex. After the payload and before the END marker the hook writes exactly one plain-text line, `fleet-guidance check value: <check>`, first adding a newline if the delivered payload does not end with one. With no working digest tool the version is `unknown` and no line is written. `--codex-cloud` mode writes the same line, computed the same way from the same payload bytes.
2. **A block without it is rewritten.** When the installed block has the same version but not the exact line, the hook does not report `current`. It rewrites the block and reports `installed`, recording the higher of the existing and new `fleet-guidance-delivered` stamps so the ordering key of [ADR 0016](0016-the-freshest-delivery-wins-the-shared-global-block.md) never drops. The Codex Cloud writer rebuilds the whole block each run, so a block without the line differs and is rewritten the same way.
3. **The value never leaves the block.** It is not on stdout (verdict or workspace line) or in the delivery receipt, and it is not in `agents-md/base.md`, its payload copy, or any repo's copy of them. A session that read `base.md` by hand cannot quote it. It is the last 8 hex rather than a literal in `base.md` so that it rotates with every payload change and needs no edit.
4. **The stub asks the session to quote it.** In its first reply to the user in a session, it begins with `fleet-guidance check: <value>`, copied from the line, or `fleet-guidance check: none` when no such line is in its context. Only in replies to the user, never in commits, PR or issue text, files or prompts for other agents.
5. **The Stop hook compares.** The record gains two fields and holds no value string. `check_expected` is true when a loaded User-type instruction file has a line matching `^fleet-guidance check value: ([0-9a-f]{8})$`, false when none does, and null when the state is `unknown`. `check_reported` comes from the first match of `fleet-guidance check: ([0-9a-f]{8}|none)\b` in the assistant's text blocks, in transcript order: `match` (the hex equals the expected value), `mismatch` (a hex, with no expected value or a different one), `none`, or `absent`. The transcript is parsed as JSON, never searched as raw text. `v` stays 1: the only code in this repo that pins the record's keys is its own test, and adding keys does not break a reader that looks fields up by name.

## Consequences

- Every session spends one line of its first reply on the report.
- skills-evals arms' first replies gain that line, so a grader that compares exact text has to allow it.
- Only Claude Code's record checks it. Codex gets the same block and the same stub request, but nothing checks its sessions.
- A session that reads `~/.claude/CLAUDE.md` or the hook source can produce the value. The check catches careless false claims, not adversarial ones.
- A session with no repo attached, for example one served only by a plugin hook, loads no stub, so it is not asked to report. Its `check_reported` reads `absent`.
- The record is written once, at the first Stop that has an `instructions` entry, so a report that arrives after that stays `absent`.
- A block written without the line is rewritten once, on the normal replace path, so it moves to the end of its file. An older checkout's hook that rewrites a block, because its payload differs, writes it without the line, and a newer hook restores it on its next run.
- [`scripts/check-codex-cloud-context.py`](../../scripts/check-codex-cloud-context.py) builds the expected Codex Cloud block, so it expects the line too.

## Alternatives considered

- **A value written into `base.md`, with an exemption from the guidance-impact touch gate.** Rejected: it needs an edit per release plus a gate exemption, and every repo copy of `base.md` would carry it, so a session that only read the file by hand could quote it.
- **The version id the verdict prints.** Rejected: the hook prints it whether or not memory loaded, so quoting it proves only that the hook ran.
- **An HTML comment.** Rejected: Claude Code strips HTML comments from memory files before the model sees them.
