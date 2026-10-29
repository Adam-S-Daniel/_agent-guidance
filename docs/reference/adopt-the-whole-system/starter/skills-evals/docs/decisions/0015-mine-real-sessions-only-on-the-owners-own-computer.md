# ADR 0015: Real sessions are mined only on the owner's own computer, into redacted records in the private registry

- **Status:** accepted (2026-10-26). Built; not yet scheduled on the machine.
- **Deciders:** the owner, directly: real session transcripts are read only on his
  own computer, never in the cloud (2026-10-24); proposal files, not issues,
  are the primary sink, because cloud readers hold only Contents: read on
  acsi-agentskills-private (2026-10-26); a git hook is the first home for a
  lesson whenever one can enforce it, ahead of a skill, guidance text or
  memory (2026-10-26, 14:21 UTC).
- **Gaps:** G1 (nothing reads real sessions), G2 (no lesson records or
  routing), G7 (proposals cover only SKILL.md edits) of the 2026-10-26
  closed-loop gap list, kept outside this repository.

## Context

The improvement loop reads only eval-trial transcripts. The richest signal,
the owner's own Claude Code sessions, sits on his machine: about 400 real-work
sessions of about 4,500 under WSL `~/.claude/projects`, about 45 interactive
ones under the Windows profile, and the desktop app's sessions, whose
metadata lives under the MSIX package's real path
(`AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude`, not
the virtualized `AppData/Roaming/Claude`). The rest is automation: LLM-judge
and propose-skill-edit runs under `/tmp`, benchmark cells, sprint graders,
pollers and the hourly issue-worker scheduled task. Those transcripts carry
paths, names, addresses and tokens, and this repository is public.

## Decision

1. `scripts/mine_sessions.py` runs only on the owner's own computer, from WSL, by
   `scripts/run_session_miner.sh`. No workflow, routine or cloud session runs
   it, and no real transcript is ever copied off that machine. Tests and docs
   here use invented transcripts only.
2. **mine** keeps real-work sessions (the noise classes above are dropped by
   cwd, first prompt, or the desktop metadata's `scheduledTaskId`; subagent
   transcripts are skipped) and applies deterministic rules: user
   corrections, redirections and stated rules, interruptions, repeated
   failing tool calls, red CI after a push, sessions that end unfinished, and
   which skills and guidance sections were invoked or cited. A session is
   read once it has been quiet for 24 hours; a state file of hashed session
   ids and sizes, kept on the machine, makes each run read only new or grown
   sessions.
3. What leaves a transcript is a **lesson record**: a session-id hash, a
   date, the repo's directory name, the signal, an excerpt of at most 160
   characters after redaction, and the suspected subject (a skill, a
   guidance section id from `_agent-guidance` `agents-md/eval-coverage.yml`,
   memory, or none). Redaction applies the privacy denylist (the format of
   acsi-agentskills' `scripts/check_privacy_denylist.py`, from a file outside
   every repo; without one nothing is mined) and scrubs emails, tokens,
   UUIDs and session ids, home-directory user names, and the local login and
   host names.
4. **route** clusters records into one **proposal file** per subject and kind,
   in `agent-check`, `skill-edit`, `new-skill`, `guidance-edit`,
   `memory-note` or `fixture-candidate`. A lesson a commit- or push-time
   check can see (a path, a required file, a naming rule, a string in a diff)
   becomes `agent-check` first, with a `check_sketch` for the fleet's
   `.githooks/agent-checks` runner (_agent-guidance ADR 0021). Otherwise a
   failure attributed to a skill or a guidance section becomes a
   `fixture-candidate`: the main output, since a fixture that shows headroom
   is what makes the loop improve the subject. `--classify` (off by default)
   lets the local `claude -p` replace the kind; it runs in `/tmp`, so its own
   session is dropped as noise on the next run.
5. Records and proposals are committed to acsi-agentskills-private on its
   `persistent/lessons` branch, `lessons/records/<date>.jsonl` and
   `lessons/proposals/<date>/<subject>__<kind>.json`. That branch, not
   `main`, because `main` is PR-only; the schema is in that repo's
   `lessons/README.md` on `main`. A proposal file is the primary sink.
6. Issues are optional: `--file-issues` files each proposal that passes a
   second redaction pass as a `lesson-proposal` issue in that repo, once per
   proposal id. Off by default; the Python subcommand is a dry run unless
   `--apply`.

## Consequences

- Nothing about a real session reaches a public repository; the private
  repository receives only short, redacted, hashed records.
- The rules are word patterns and will misfire. Plan step 3.3 still holds:
  The owner rates a sample of records for usefulness and privacy before
  `--file-issues` is turned on.
- The state file lives on the machine. Losing it re-reads every session;
  record ids are stable, so the router still counts each record once.
- `persistent/lessons` has no deletion guard. acsi-agentskills-private is in
  repo-settings' `fleet.yml` private group, where the engine skips every
  ruleset without GitHub Pro (`extra_rulesets` included), and the owner affirmed
  running private repos without rulesets on 2026-08-29. The branch name keeps
  the `persistent/<purpose>` convention so stale-branch cleanups skip it.
- A cloud reader must read the branch by ref (`ref=persistent/lessons`).
- Turning a `fixture-candidate` into a fixture, and an `agent-check` into a
  check, is later work (the scaffolder, and a change to
  `_agent-guidance/templates/agent-checks/`).
