# AGENTS.md Drift Report

> Last generated: 2026-07-21 00:25 UTC

## testorg

> Organization: `testorg` — 7 repo(s) scanned

| Repository | Status | Has marker | CLAUDE.md bridge | Open PR | Sections | Notes |
|------------|--------|------------|-------------------|---------|----------|-------|
| [`testorg/repo-existing-no-marker`](https://github.com/testorg/repo-existing-no-marker) | **pr-open** | yes | bridge-ok | #null | python |  |
| [`testorg/repo-fix-claude`](https://github.com/testorg/repo-fix-claude) | **pr-open** | yes | bridge-ok | #null | python |  |
| [`testorg/repo-no-sync`](https://github.com/testorg/repo-no-sync) | **pr-open** | yes | bridge-ok | #null | rust |  |
| [`testorg/repo-up-to-date-no-claude`](https://github.com/testorg/repo-up-to-date-no-claude) | **pr-open** | yes | bridge-ok | #null | python |  |
| [`testorg/repo-with-claude-md`](https://github.com/testorg/repo-with-claude-md) | **pr-open** | yes | **no-import** | #null | typescript |  |
| [`testorg/repo-with-existing`](https://github.com/testorg/repo-with-existing) | **pr-open** | yes | bridge-ok | #null | go |  |
| [`testorg/repo-with-sync`](https://github.com/testorg/repo-with-sync) | **pr-open** | yes | bridge-ok | #null | python docker |  |

## testorg2

> Organization: `testorg2` — 1 repo(s) scanned

| Repository | Status | Has marker | CLAUDE.md bridge | Open PR | Sections | Notes |
|------------|--------|------------|-------------------|---------|----------|-------|
| [`testorg2/repo-owner2-only`](https://github.com/testorg2/repo-owner2-only) | **pr-open** | yes | bridge-ok | #null | rust |  |

---

**Status legend**

| Status | Meaning |
|--------|---------|
| **up-to-date** | Managed section matches the expected output |
| **drift-detected** | Managed section has diverged — needs sync |
| **pr-open** | A sync PR is already open for this repo |
| **no-agents-md** | Repo does not have an AGENTS.md yet |
| **update-failed** | An error occurred while checking this repo |

**CLAUDE.md bridge legend**

| Bridge status | Meaning |
|---------------|---------|
| bridge-ok | CLAUDE.md imports `@AGENTS.md` (line-start, outside code fences) |
| **no-import** | CLAUDE.md exists but never imports `@AGENTS.md` — Claude Code will not see the managed guidance |
| missing | No CLAUDE.md yet — sync adds the bridge in its next PR |
