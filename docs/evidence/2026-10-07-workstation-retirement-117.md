# Workstation retirement: repository fixes and remaining verification

Recorded 2026-10-07 for [issue 117](https://github.com/Adam-S-Daniel/_agent-guidance/issues/117).
The repository changes are complete; machine-side migration and retirement
remain unverified. This checkpoint does not establish that the issue can close.

## Repository evidence

The worktree was clean before fetching and fast-forwarding to
[default-branch commit 855932f](https://github.com/Adam-S-Daniel/_agent-guidance/commit/855932f79bb379e003960ab55966f3c9bf048c14).
The issue has no `on-hold` label, and the open-PR search for
`117` returned no results. Its timeline and the linked PRs were read directly.

| Item | Verified result | Evidence |
| --- | --- | --- |
| Workstation layout | Host-neutral Windows and WSL clone conventions are already on main. | [Merged PR 157](https://github.com/Adam-S-Daniel/_agent-guidance/pull/157), [current source](../../agents-md/base.md#workstation-layout) |
| Session-cutoff guidance | The obsolete section is absent from current source. | [Merged PR 213](https://github.com/Adam-S-Daniel/_agent-guidance/pull/213) |
| Earlier session-cutoff proposal | Closed without merging; it is not evidence of a merged fix. | [Closed PR 156](https://github.com/Adam-S-Daniel/_agent-guidance/pull/156) |
| Live hostname references | A case-insensitive search of `agents-md/` and the delivered payload on the fetched default branch found no ZENDA references. | [Guidance source](../../agents-md/base.md), [delivered payload](../../.claude/hooks/fleet-guidance.md) |

The remaining ZENDA mentions in this repository occur in the dated
[guidance-impact entry](../guidance-impact.md),
[memory-home ADR](../decisions/0013-a-memory-note-outside-a-repo-names-its-home.md),
[August handoff](../handoffs/2026-08-20-gitleaks-and-silent-failures/C-history-rewrite.md),
and [routine run history](../routines/guidance-centralization.md).
They were preserved as history. The guidance-impact entry's statement that
the host is retired is a historical motivation, not a current task-removal
receipt. This checkpoint does not claim a new fleet-wide audit.

## Machine-side verification limit

The [owner's inventory comment](https://github.com/Adam-S-Daniel/_agent-guidance/issues/117#issuecomment-5781097285)
was last updated September 23. Its September 22 check reported missing task
registrations and missing MSI PowerShell on the replacement host. Neither
finding establishes the machine's current state.

A read-only PowerShell query was attempted from this WSL session. It would
have checked the replacement-host match, MSI PowerShell presence, and exact
root registrations for these five tasks:

- `WSL Ubuntu Daily Backup`
- `Claude Code Session Keeper`
- `Claude Code Session Launcher`
- `ccstatusline Config Sync`
- `Codex Cloud Environment Sync`

The command exited 1 before PowerShell started:

```text
WSL ERROR: UtilBindVsockAnyPort:309: socket failed 1
```

No task inventory was returned. Task absence, presence, enabled state, and
successful execution remain unknown. The old host was not accessed. No
machine configuration or task registration was changed, and no elevated
retry was attempted.

## Remaining owner decision

Keep [issue 117](https://github.com/Adam-S-Daniel/_agent-guidance/issues/117)
open until the owner provides a current migration and retirement receipt or
explicitly narrows its scope to repository guidance. A completion receipt
should verify the actual root task registrations and required executable on
the replacement machine, and confirm the old registrations were removed or
the old host cannot run them. Registration alone does not establish that the
tasks execute successfully.

The machine-side commands in the
[owner's checklist](https://github.com/Adam-S-Daniel/_agent-guidance/issues/117#issuecomment-5781097285)
remain operator work. This session neither performed those commands nor
inferred completion from their age.
