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
| Scheduled routines | `remote_trigger` | set on all 700 | not recorded separately (`remote` is the OR of both) | `full` on all 700; a check value on 634 of the 697 in November | 2026-11-16 | The Stop hook's uploaded records ("Records, 2026-11-16" above) |

## Records, 2026-11-16

The Stop hook (`guidance-load-record.sh`) uploads one record per cloud
session. Each record carries the transcript's `entrypoint` as `surface`, plus
`remote`, which is true exactly when the owner gate's signal is present
(`CLAUDE_CODE_REMOTE=true` or a nonempty `CLAUDE_CODE_REMOTE_SESSION_ID`).
The records are ingested into the private `events/<YYYY-MM>/cloud-guidance.jsonl`
on adam-agentskills-private's `persistent/usage-events` branch. That file was
read on 2026-11-16 and tallied by `surface`, `remote`, `state` and whether a
check value was reported. Only counts are given here.

| Surface (`entrypoint`) | Sessions, 2026-10 and 2026-11 to date | `remote` true | Guidance `state` | Check value reported |
|---|---|---|---|---|
| `remote_projects` (Claude Code on the web, project threads) | 125 | all | all `full` | 112 of the 115 in November; none of the 10 in October, before the check value existed |
| `remote_trigger` (scheduled routines and API-fired triggers) | 700 | all | all `full` | 634 of the 697 in November; none of the 3 in October |
| `sdk-cli` (one session, 2026-11-15, CLI 2.1.285) | 1 | yes | `full` | yes |

What this adds:

- **Scheduled routines pass the gate.** Every `remote_trigger` session
  carried the signal and received the full guidance. The "Scheduled
  routines" row below is settled by these records.
- **An `sdk-cli` cloud session also carried the signal.** This is one
  session, so it is recorded but not generalized.
- **Records cannot show a surface that fails the gate.** The Stop hook runs
  in a session only if the delivered hooks run there at all, so a surface
  with no records is either unused or ungated, and the records cannot tell
  which. No `remote_mobile`, Slack or Teams entrypoint appears in either
  month.

## What this settles, and what it does not

- **Settled:** a Claude Code on the web session started from a claude.ai
  project thread sets both variables, so it passes the gate, and the guidance
  was delivered (`installed`, then `current`) with a check value in context.
- **Also settled (2026-11-16):** scheduled routines (`remote_trigger`), from
  700 uploaded session records.
- **Not covered:** every other row (`remote_mobile`, other `remote*`
  entrypoints, Claude in Slack and Teams, and a GitHub Action run). ADR 0042
  lists them as not covered, with how a gap would show. Codex Cloud is
  covered by `--codex-cloud`, not by either variable.
- **Codex Cloud** does not rely on either variable. ADR 0042 names
  `--codex-cloud`, the explicit setup mode its own setup script runs, as a
  signal by itself, so the row records the verdict a real Codex Cloud session
  reports, not the variables.
- **A GitHub Action run of Claude Code** is not treated as hosted by the ADR
  (see "Rejected": a runner may be self-hosted and durable). Its row records
  whether the run still gets the guidance by another route, such as the
  skills-evals harness's payload.
