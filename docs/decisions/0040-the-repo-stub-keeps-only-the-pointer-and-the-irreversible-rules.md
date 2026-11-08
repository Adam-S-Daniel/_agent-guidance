# 0040 — The repo stub keeps only the pointer and the irreversible rules

**Status:** Accepted (2026-11-08).

## Context

Every consumer repo's managed `AGENTS.md` is `agents-md/stub.md`. It restated
a ~3 KB "floor" of rules so that a session whose `fleet-memory` hook failed
would still see them. A session loads one copy per attached repo on every
model call: a five-repo project thread carried about 20 KB of identical stub
text, roughly 6k to 15k context tokens per call. Every floor rule is already
in `agents-md/base.md`, which the hook installs once into user memory.
[_agent-guidance#473](https://github.com/Adam-S-Daniel/_agent-guidance/issues/473)
lists four options; the owner chose option 3 (floor in the installed block, a
~1 KB stub with the degraded fallback) plus attaching fewer repos.

## Decision

`stub.md` keeps the `fleet-stub` marker, the verdict pointer (read `base.md`
when the verdict is not `installed`/`current`), the check-value request
([ADR 0035](0035-sessions-prove-the-guidance-loaded-with-a-check-value.md)), and
one sentence with the irreversible rules: no push to a default branch, no
force-push of a shared branch, no committed secret, no personal data in a CI
log. The rest of the floor lives only in `base.md`. `test_agents_md_size_budget`
caps the stub build at 2048 bytes.

## Consequences

- A degraded session that never reads `base.md` loses the reversible floor
  rules (404 handling, watch exit codes, the two owners, links, merge style).
  It keeps the irreversible ones and is told to read `base.md` first.
- A session with one repo attached saves ~3.5 KB per call; with five, ~17 KB.
- The `fleet-memory.sh` comment that every repo carries "the load-bearing rules
  inline" now means the one irreversible-rules sentence.
