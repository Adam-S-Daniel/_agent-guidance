# 0016 — The freshest delivery wins the shared global block

**Status:** Accepted (2026-09-28)

## Context

Every checkout's [`fleet-memory.sh`](../../.claude/hooks/fleet-memory.sh)
hook writes into the same global `CLAUDE.md` and Codex instructions file on a
machine. A session in a stale sibling checkout replaced guidance version
`b8c76f08` with older version `6489d431`. The content hash identified both
payloads but could not tell which had arrived later. The hook also reported
installation to Codex's `AGENTS.md` while a nonempty `AGENTS.override.md`
shadowed it. Its in-place `cp` truncated the destination before copying, so a
concurrent reader could see partial instructions even when the copy succeeded.

The payload must remain byte-for-byte equal to
[`agents-md/base.md`](../../agents-md/base.md), so it cannot carry delivery
metadata. Both global files contain personal content outside the managed
block, and normal hook mode must still run offline with portable Bash tools.
[ADR 0012](0012-codex-gets-the-guidance-as-user-instructions.md)
explains why the guidance lives in global user instructions.

## Decision

The normal hook records a `fleet-guidance-delivered` integer epoch beside the
managed block's content hash. It uses the payload's last commit time in the
checkout, its file mtime when dirty or untracked, and zero when Git cannot
provide a value. Each destination is compared independently: an equal hash
only raises an older stamp and reports `current`; for different hashes, the
local payload replaces an equal or older stamp, while a newer installed stamp
is kept. A mixed `installed` verdict names only destinations carrying the
local hash and separately names each kept newer version, so the line describes
what the agents will read.

For Codex, a nonempty readable `AGENTS.override.md` is the effective normal
destination; an invalid override causes a visible failure instead of silent
fallback. Skip removes managed blocks from both Codex files. Replacements use
a temporary file in the resolved target directory, preserve the existing
mode and personal content, and rename over the target while keeping a
destination symlink intact. The `--codex-cloud` path is unchanged. The
[hook](../../.claude/hooks/fleet-memory.sh) and its
[regressions](../../test/run-tests.sh) implement and check these rules.

## Consequences

Opening an older checkout no longer downgrades either global block, and a
mixed run says which version each destination retained. The protection travels
with the hook, though: a checkout last pulled before this change carries the
old hook along with its old payload, so it still downgrades both blocks until
it is pulled once. A dirty payload can win by mtime until a later commit has a
greater stamp. Without Git, or outside a Git work tree, the stamp is zero and
cannot displace a different version with a positive stamp.
Commit times and file mtimes assume reasonably ordered clocks, so the stamp is
a practical freshness signal rather than proof of causal order. There is no
cross-process lock: simultaneous hooks can still race, and the last atomic
rename wins.

The alternatives were to keep last-writer-wins behavior, compare hashes
alone, or keep copying in place. The first permits the observed downgrade;
hashes cannot order different content; copying in place risks a partial
global instructions file. The stamp and atomic rename close those failures
without changing the payload or requiring network access.
