# lessons/

Redacted lesson records and routed proposals mined from the owner's real Claude Code
sessions. The miner is skills-evals'
[`scripts/mine_sessions.py`](https://github.com/example-adopter/skills-evals/blob/main/scripts/mine_sessions.py),
run daily by
[`scripts/run_session_miner.sh`](https://github.com/example-adopter/skills-evals/blob/main/scripts/run_session_miner.sh)
on the owner's own computer only, never in CI or a cloud session (skills-evals
ADR 0015, the owner, 2026-10-24). Raw transcripts never leave that machine; only
what is described below is committed here.

## Where the real data is

On the **`persistent/lessons`** branch, not `main`. `main` is PR-only, and a
daily bot commit there would be rejected (GH013); a `persistent/` branch is
where the fleet keeps generated data. This directory on `main` holds only this
README and [`example/`](example/), which is invented.

A cloud reader with Contents: read on this repo reads the newest
`lessons/proposals/<YYYY-MM-DD>/` directory with `ref=persistent/lessons`.

```text
lessons/
  records/<YYYY-MM-DD>.jsonl                   one lesson record per line, appended by each day's run
  proposals/<YYYY-MM-DD>/<subject>__<kind>.json one proposal per file: that day's full snapshot
```

Each day's proposal directory is rewritten whole from every record so far, so
the newest directory is the current set; older ones are history.

## Proposal file schema (`schema_version: 1`)

One JSON object per file. This is the primary sink: issues are optional.

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | int | `1` |
| `id` | string | 12 hex chars, a hash of `subject` and `kind`; stable across days |
| `subject` | string | `skill:<skill basename>`, `guidance:<section id>` (an `id` in `_agent-guidance` `agents-md/eval-coverage.yml`), `memory`, or `none` |
| `kind` | string | `agent-check`, `skill-edit`, `new-skill`, `guidance-edit`, `memory-note` or `fixture-candidate` |
| `evidence_count` | int | records clustered into this proposal |
| `first_seen`, `last_seen` | string | `YYYY-MM-DD` of the oldest and newest record |
| `examples` | array of strings | up to 5 newest records, each `"<date> <repo> [<signal>] <redacted excerpt>"` |
| `repos` | array of strings | the repos' directory names, sorted |
| `check_sketch` | string | **only when `kind` is `agent-check`**: one line per check (up to 3, `; `-separated) naming the hook and what it would refuse |

The file name is `<subject>__<kind>.json` with `:` and any other character
outside `[A-Za-z0-9_.-]` replaced by `-`, e.g.
`skill-writing-adrs__fixture-candidate.json`.

### How a kind is chosen

Git hooks come first (the owner, 2026-10-26): a lesson whose excerpt names a
concrete token (a backticked string or a file name) in a commit- or
push-time shape (a path that must not be committed, a required file that was
missing, a naming rule, a string that must not appear in a diff) becomes
`agent-check`: a new check in the fleet's `.githooks/agent-checks` runner
(_agent-guidance ADR 0021, delivered by its sync, enforced by the required
`agent-checks` CI job). Only lessons no hook can see fall through:

| Signal | Subject | Kind |
|---|---|---|
| `rule` | skill | `skill-edit` |
| `rule` | guidance | `guidance-edit` |
| `rule` | memory or none | `memory-note` |
| any failure signal | skill or guidance | `fixture-candidate` (a concrete case an eval fixture can reproduce; the loop then improves the subject because the fixture shows headroom) |
| any failure signal | memory | `memory-note` |
| any failure signal | none | `new-skill` once seen in 3 or more sessions, else nothing |

Failure signals: `correction`, `redirection`, `interrupted`,
`repeated-tool-failure`, `red-ci-after-push`, `abandoned`. `--classify` lets
the local `claude -p` replace the kind with one of the same six words.

## Record schema (`schema_version: 1`), one per line

| Key | Meaning |
|---|---|
| `schema_version` | `1` |
| `record_id` | 16 hex chars, a hash of session, signal and position: the dedupe key |
| `session` | 16 hex chars, sha256 of the session id (never the id itself) |
| `date` | `YYYY-MM-DD` of the entry |
| `source` | the root's label: `wsl`, `windows` or `desktop` |
| `repo` | the cwd's directory name (a worktree resolves to its repo), or `~` |
| `signal` | a failure signal above, `rule`, or a usage signal (`skill-invoked`, `guidance-cited`) that the router does not turn into proposals |
| `excerpt` | at most 160 characters, redacted |
| `subject` | `{"kind": "skill" \| "guidance" \| "memory" \| "none", "name": "..."}` |

On the mining machine, `python3 scripts/mine_sessions.py locate <session>`
prints the transcript a record came from.

## Redaction

Before anything is written: every match of the privacy denylist (kept outside
every repo, passed as `PRIVACY_DENYLIST_FILE`; without it nothing is mined),
emails, tokens and `key=value` secrets, UUIDs and session ids, home-directory
user names, and the machine's login and host names. An issue is filed only if
its full text is unchanged by a second pass of the same scrub.

## Issues (optional)

`run_session_miner.sh --file-issues` also files each of the day's proposals
as an issue labeled `lesson-proposal` (and `agent-authored`) here, one per
proposal `id`, never twice. Off by default.
