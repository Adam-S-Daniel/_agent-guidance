# AGENTS.md Drift Report

> Last generated: 2026-09-05 10:04 UTC

## Adam-S-Daniel

> Organization: `Adam-S-Daniel` — 15 repo(s) scanned

| Repository | Status | Has marker | CLAUDE.md bridge | skills-bootstrap | Open PR | Sections | Notes |
|------------|--------|------------|-------------------|------------------|---------|----------|-------|
| [`Adam-S-Daniel/4A`](https://github.com/Adam-S-Daniel/4A) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/GHA-bench`](https://github.com/Adam-S-Daniel/GHA-bench) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff |
| [`Adam-S-Daniel/adamdaniel.ai`](https://github.com/Adam-S-Daniel/adamdaniel.ai) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff + cms-platform@02b1cf0 |
| [`Adam-S-Daniel/agentskills`](https://github.com/Adam-S-Daniel/agentskills) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/agentskills-private`](https://github.com/Adam-S-Daniel/agentskills-private) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@47aaede |
| [`Adam-S-Daniel/claude-memory-map`](https://github.com/Adam-S-Daniel/claude-memory-map) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff |
| [`Adam-S-Daniel/cms-platform`](https://github.com/Adam-S-Daniel/cms-platform) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff |
| [`Adam-S-Daniel/fastmail-actions`](https://github.com/Adam-S-Daniel/fastmail-actions) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@3bdf5bd |
| [`Adam-S-Daniel/jc`](https://github.com/Adam-S-Daniel/jc) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/repo-settings`](https://github.com/Adam-S-Daniel/repo-settings) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@f2b86d2 |
| [`Adam-S-Daniel/rss-inator`](https://github.com/Adam-S-Daniel/rss-inator) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/scratch-claude-001`](https://github.com/Adam-S-Daniel/scratch-claude-001) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/scratch-jules-001`](https://github.com/Adam-S-Daniel/scratch-jules-001) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/skills-evals`](https://github.com/Adam-S-Daniel/skills-evals) | **up-to-date** | yes | bridge-ok | — | none | none |  |
| [`Adam-S-Daniel/wsl-automation`](https://github.com/Adam-S-Daniel/wsl-automation) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff |

## jodidaniel

> Organization: `jodidaniel` — 3 repo(s) scanned

| Repository | Status | Has marker | CLAUDE.md bridge | skills-bootstrap | Open PR | Sections | Notes |
|------------|--------|------------|-------------------|------------------|---------|----------|-------|
| [`jodidaniel/jodidaniel.com`](https://github.com/jodidaniel/jodidaniel.com) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@8765dff + cms-platform@02b1cf0 |
| [`jodidaniel/scratch-claude-002`](https://github.com/jodidaniel/scratch-claude-002) | **up-to-date** | yes | bridge-ok | ok | none | none | lock: agentskills@3bdf5bd |
| [`jodidaniel/squarespacetemp`](https://github.com/jodidaniel/squarespacetemp) | **up-to-date** | yes | bridge-ok | — | none | none |  |

---

**Status legend**

| Status | Meaning |
|--------|---------|
| **up-to-date** | Managed section matches the expected output |
| **drift-detected** | Managed section has diverged — needs sync |
| **pr-open** | A sync PR is already open for this repo |
| **no-agents-md** | Repo does not have an AGENTS.md yet |
| **update-failed** | An error occurred while checking this repo |
| **fetch-failed** | A file this row is built from could not be read, or could not be understood — the request failed for a reason this run could not resolve to a plain absence (a 401, a 403, a rate limit, a 5xx, a network fault, or a 404 on a repo the credential cannot see at all), or the decoded byte count disagreed with the API's own `size`, or the bytes arrived whole and would not parse. **Notes** names the file; every column it feeds is withheld as `?` rather than guessed; see issue #81 |

**CLAUDE.md bridge legend**

| Bridge status | Meaning |
|---------------|---------|
| bridge-ok | CLAUDE.md imports `@AGENTS.md` (line-start, outside code fences) |
| **no-import** | CLAUDE.md exists but never imports `@AGENTS.md` — Claude Code will not see the managed guidance |
| missing | No CLAUDE.md yet — sync adds the bridge in its next PR |
| ? | `CLAUDE.md` could not be read — withheld, not guessed (the row reads **fetch-failed**) |

**skills-bootstrap legend**

Delivery is opt-in and double-keyed: the repo must be listed in
`repos.yml`'s `skills_bootstrap.repos` **and** already carry its own
`skills.lock`. See `docs/decisions/0001-skills-bootstrap-delivery-is-opt-in.md`.

| Status | Meaning |
|--------|---------|
| ok | Hook present, byte-equal to the pinned copy, and registered in `.claude/settings.json` |
| **no-entry** | Hook is present but **nothing runs it** — no SessionStart entry names it. Silently dead |
| **drifted** | Hook present but differs from the pinned copy — the next sync overwrites it |
| **missing** | Allowlisted and has a lock, but no hook — the next sync delivers it (unless the pinned hook was unavailable fleet-wide that run; the sync log says `pinned hook unavailable this run`) |
| **blocked** | Allowlisted and has a lock, but the repo gitignores `.claude/` — `git add` cannot stage the hook, so every sync skips it with a warning. Does **not** self-heal: change that repo's `.gitignore`, or drop it from the allowlist |
| **refused** | Allowlisted and has a lock, but `.claude/settings.json` is not parseable JSON — the sync will not edit it, and withholds the hook rather than leave one nothing runs. Does **not** self-heal: fix that file |
| **degraded** | Hook present in a repo with no `skills.lock` — it prints `skills: DEGRADED` into every session and no sync will revisit it. Commit a lock, or remove the hook |
| no-lock | Allowlisted, no `skills.lock` yet — delivery deliberately withheld until the repo declares its bundles |
| **unmanaged** | Hook present in a repo that is **not** allowlisted — it still runs; the sync has no delete path, so remove it by hand |
| unverified | Could not fetch the pinned hook this run — the drift comparison was skipped |
| — | Not allowlisted and no hook present |
| ? | A file this column is decided from (the hook, `skills.lock`, `.claude/settings.json`, or the repo's ignore rules) could not be read — withheld, not guessed (the row reads **fetch-failed**) |

**Cron-coverage classification**

`scripts/check-cron-coverage.js` audits a disk, so it cannot notice a
repo that is not checked out; `repos.yml`'s `cron_coverage.fleet` is what
makes an absent one an error there. This report is the only thing that
sees the whole account, so it is where a repo classified by neither key
surfaces — as the block above, or not at all when every repo is
classified. See `docs/decisions/0003-cron-coverage-is-fleet-listed.md`.

The **Notes** column carries each allowlisted repo's lock pins
(`registry@shortref`, one per federated source). Nothing else in the
fleet surfaces a stale lock: a lock pinned far behind still installs
cleanly and still reports `OK` in-session, by design.
