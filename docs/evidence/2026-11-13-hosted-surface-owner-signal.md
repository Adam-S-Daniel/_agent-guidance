# 2026-11-13: which hosted surfaces carry a signal the owner gate accepts

Evidence for [ADR 0042](../decisions/0042-the-guidance-hooks-are-opt-in-on-a-durable-machine.md),
context and decision 1: a hosted session passes the owner gate when
`CLAUDE_CODE_REMOTE=true` or `CLAUDE_CODE_REMOTE_SESSION_ID` is nonempty. That
was taken from what the hooks already keyed on, and not tested per surface. A
surface that sets neither would stop receiving the guidance and fall back to
the repo stub. This file keeps the per-surface record, under
[ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md);
the question was
[issue 525](https://github.com/Adam-S-Daniel/_agent-guidance/issues/525).

Only whether a variable is set was recorded, never a value. "Verdict seen" is
the `fleet-memory` SessionStart verdict (`installed`, `current`, `DEGRADED`,
`skipped`) and whether the session reported a `fleet-guidance check` value.

## Table, 2026-11-13

The surface names are the ones ADR 0042 and
[`agents-md/base.md`](../../agents-md/base.md) (the "Two setup gaps" section's
hosted test) name. A row stays "not tested" until a real session on that
surface reports its variables.

| Surface | Entrypoint | `CLAUDE_CODE_REMOTE` | `CLAUDE_CODE_REMOTE_SESSION_ID` | Verdict seen | Date | How checked |
|---|---|---|---|---|---|---|
| Claude Code on the web, run as a claude.ai project thread | `remote_projects` | set (`true`) | set | `installed`, then `current`; a `fleet-guidance check value:` line was in context | 2026-11-13 17:30 UTC | A fleet-queue session printed set or unset for each variable from its own shell, and read its SessionStart verdicts. `CLAUDE_PROJECT_DIR`, `FLEET_GUIDANCE_PAYLOAD` and `FLEET_GUIDANCE_OWNER` were unset. |
| Same surface, a second session (a worker of the fleet queue, 2026-11-13) | `remote_projects` | set (`true`) | set | a `fleet-guidance check value:` line was in context; this session could not see the SessionStart verdict line itself | 2026-11-13 | The worker printed set or unset for each variable from its own shell. `CLAUDE_PROJECT_DIR`, `FLEET_GUIDANCE_PAYLOAD` and `FLEET_GUIDANCE_OWNER` were unset. Same surface as the row above, so a second observation, not a second surface. |
| Claude Code on the web, other entrypoints (`remote*`) | not tested | not tested | not tested | not tested: needs a real session on that surface | | |
| Claude Code on mobile | `remote_mobile` | not tested | not tested | not tested: needs a real session on that surface | | |
| Claude in Slack | `claude_in_slack` / `claude-in-slack` | not tested | not tested | not tested: needs a real session on that surface | | |
| Claude in Teams | `claude-in-teams` | not tested | not tested | not tested: needs a real session on that surface | | |
| Codex Cloud | not applicable | not tested | not tested | not tested: needs a real session on that surface | | |
| A GitHub Action run of Claude Code | not tested | not tested | not tested | not tested: needs a real session on that surface | | |
| Scheduled routines | not tested | not tested | not tested | not tested: needs a real session on that surface | | |

## What this settles, and what it does not

- **Settled:** a Claude Code on the web session started from a claude.ai
  project thread sets both variables, so it passes the gate, and the guidance
  was delivered (`installed`, then `current`) with a check value in context.
- **Not settled:** every other row. The issue stays open until each hosted
  surface the fleet uses has a real session behind its row, or ADR 0042 lists
  it as not covered.
- **Codex Cloud** does not rely on either variable. ADR 0042 names
  `--codex-cloud`, the explicit setup mode its own setup script runs, as a
  signal by itself, so the row records the verdict a real Codex Cloud session
  reports, not the variables.
- **A GitHub Action run of Claude Code** is not treated as hosted by the ADR
  (see "Rejected": a runner may be self-hosted and durable). Its row records
  whether the run still gets the guidance by another route, such as the
  skills-evals harness's payload.
