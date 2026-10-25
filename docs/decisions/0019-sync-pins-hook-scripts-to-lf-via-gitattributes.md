# 0019 — The sync pins the hook scripts to LF through each repo's `.gitattributes`

**Status:** Accepted (2026-10-08)

## Context

The sync delivers `.claude/hooks/fleet-memory.sh` to every consumer repo, and
`.claude/hooks/skills-bootstrap.sh` to the allowlisted ones. The repository
holds them with LF. Git for Windows installs with a system-level
`core.autocrlf=true`, so a clone under `D:\repos\...` checks them out with
CRLF; `git ls-files --eol` reads `i/lf w/crlf`. The owner's WSL git now has
`core.autocrlf=true` too, for clones under `/mnt/`.

Bash cannot parse a CRLF script. On both hooks it stops with errors like
`syntax error near unexpected token $'{\r'`. That breaks:

- **WSL Claude Code sessions started in a `/mnt/d` clone.** They run
  `bash .claude/hooks/*.sh` at SessionStart, so the guidance and skills never
  load.
- **WSL Codex sessions in such clones.** The user-level Codex hook execs
  `bash` on `<git root>/.claude/hooks/fleet-memory.sh`.

Native Windows sessions are not affected, because Git for Windows' bash
tolerates CRLF. Cloud sessions and CI runners are not affected either: they
check out on Linux with no autocrlf.

## Decision

`scripts/sync.sh` keeps one line, with a one-line comment above it, in every
synced repo's root `.gitattributes`:

```
# managed by _agent-guidance: hook scripts must stay LF for bash (see ADR 0019)
.claude/hooks/*.sh text eol=lf
```

- `scripts/hook-eol-gitattributes.sh` owns the line. `sync.sh` writes it and
  `drift-report.sh` reports it (`gitattr-ok` / `gitattr-missing` in Notes),
  so one script decides whether it is present.
- **Append, never rewrite.** The script creates the file if it is absent and
  appends the pair if the line is missing. Every other byte stays, including a
  missing final newline (it adds one first) and CRLF line endings (it appends
  with CRLF). A line that matches once surrounding whitespace is stripped
  counts as present. If the comment is there without the line, only the line
  is added. If the line is there without the comment, nothing is added.
- **Appending is enough.** For each attribute, git uses the last matching line.
  So the line at the end of a file that starts with `* -text` (as in
  adam-agentskills) applies to the hooks and changes nothing else.
- **Same delivery path as the hooks.** The change goes in the same commit as
  the rest of the repo's sync: a direct push, or the fallback PR. A repo that
  lacks only the line still gets a commit. A repo the sync skips gets nothing.
  The sync also refuses, with a warning and without failing the repo, when
  `.gitattributes` is gitignored or is not a regular file.
- **This repo**, which the sync excludes (`SYNC_SELF_REPO`), carries the same
  pair in a committed `.gitattributes`. CI's "Self-guidance is current" step
  and `test_self_hosted_gitattributes` check it.

### Why `.gitattributes` and not per-machine config

A machine-level fix (`core.autocrlf=input`, or a per-clone
`core.eol`) has to be applied on every machine and in every clone. It is lost
on the next reinstall or fresh clone, and it cannot reach a machine this fleet
does not manage. Attributes are committed, so they travel with the repo and
apply to every clone on every machine. `eol=lf` also overrides
`core.autocrlf` for the paths it names.

### Why only `.claude/hooks/*.sh` and not a fleet-wide `* text=auto`

The sync writes into repos it does not own, so it changes as little as it can.
`* text=auto` would change how every text file in every consumer repo is
normalized. On a repo with CRLF already in its index, that turns the next
commit into a renormalization diff. The hook scripts are the files this repo
delivers and the files that break, so the line names only them.

## Consequences

- **A clone that already exists keeps its CRLF copies.** A new
  `.gitattributes` does not rewrite the working tree. The hook blobs did not
  change, so a pull leaves them alone. Git's index still treats the files as
  clean, so `git status` shows nothing. Measured on git 2.54.0:
  `git checkout -- .claude/hooks/`, `git restore` and `git reset --hard` all
  leave a stat-clean CRLF copy CRLF. Only removing the files and checking them
  out again rewrites them:

  ```bash
  git status --short -- .claude/hooks/   # must print nothing first
  rm .claude/hooks/*.sh && git checkout -- .claude/hooks/
  ```

  Run that only when `git status` is clean for those files, or local edits
  are lost. A fresh clone needs nothing.
- Every consumer repo gets one more managed file. A `.gitattributes` that
  already had rules gets two more lines at the end.
- The drift report shows `gitattr-missing` for every repo until the first sync
  after this change. The note does not change a row's status.
- Only the line's presence is checked. A later line that overrides it (say,
  `.claude/hooks/*.sh -text` added after it) would still read as present,
  and the sync would not fix it.
- Hook scripts with other extensions, and any `.sh` outside `.claude/hooks/`,
  are not covered.
