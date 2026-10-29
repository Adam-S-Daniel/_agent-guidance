# The usage census

`scripts/model_usage_census.py` counts which Claude models this account
actually ran, per ISO week, from the local Claude Code transcripts under
`~/.claude/projects`. `scripts/publish_usage_census.sh` runs it and publishes
the result as `usage/latest.json` on the `persistent/eval-results` branch. `harness/roster.py`
reads that file to decide which models earn an arm's seat; when it is absent or
older than 14 days the roster falls back to "newest model per tier" and says so
in every arm's reason ("no fresh census"). Nothing fails silently — but nothing
refreshes the census either, until somebody schedules it.

## Skill usage

The same run also counts how often each skill is invoked, per ISO week, and
the publish script commits it beside the model census as
`usage/weekly-local.json`. The improvement planner reads it through
`harness/skill_usage.py`, so a frequently used skill is improved first; a
missing or stale file reads as unknown, never as zero.

The schema is defined once, in `harness/skill_usage.py` (`row()`,
`hook_row()`, `document()`, `ROW_FIELDS`); hook rows (below) go into the same
file through it:

```json
{"generated_at": "2026-09-04T12:00:00Z", "schema": 1,
 "sources": [{"surface": "local", "scope": "transcripts",
              "from": "2026-07-13T00:00:00Z", "to": "2026-09-04T12:00:00Z",
              "sessions_scanned": 42}],
 "rows": [{"week": "2026-08-31", "kind": "skill", "name": "workflow-path-audit",
           "repo": null, "surface": "local", "invocations": 3, "sessions": 2,
           "blocked": null, "overridden": null, "outcome": null}]}
```

`week` is the Monday (UTC) of the ISO week; rows are sorted by week, kind and
name. `sessions` is the number of distinct transcript files that invoked the
name that week, and `sessions_scanned` the number of transcript files read
inside the window. Neither carries an id. A new run is published when the
rows or the window start changed; `to` and `sessions_scanned` alone do not
trigger a commit.

- **What is counted.** Each `Skill` tool call (once per tool-use id per
  transcript), and each typed slash command whose name is a skill. A plugin
  prefix is dropped (everything up to the last `:`):
  `coding-anywhere:workflow-path-audit`, and an old prefix such as
  `my:` or `my-local:`, count as the bare skill name.
- **Allowlist, then `other`.** A skill name is published only when this repo
  already names it publicly: a skill fixture under `evals/`, or an
  `evals/non-coverage.yml` row for a registry that is not private. Every other
  Skill tool call counts under `other`; a typed command that is not an
  allowlisted skill (`/clear`) is not counted at all.
- **Blind spots.** Cloud sessions are not in local transcripts. A skill loaded
  without the Skill tool (read straight from its `SKILL.md`) is not counted.
  Local eval sessions do not inflate the counts: `local_eval.py` runs a
  one-turn arm without session persistence and moves a multi-turn arm's
  transcripts out of `~/.claude/projects` when it ends.

## Transcript roots

`--projects` may be repeated or carry several roots separated by `:`. On WSL
the publish script reads, besides `~/.claude/projects`:

- every existing `/mnt/c/Users/*/.claude/projects`: desktop Code-tab and
  remote-control sessions write to the Windows root;
- every `.claude/projects` directory below
  `/mnt/c/Users/*/AppData/Local/Claude-Data/*/local-agent-mode-sessions`: the
  desktop app's Cowork sandbox.

`CENSUS_PROJECTS` replaces all of them. A session reachable from two roots
counts once: a turn, Skill call or typed command whose id an earlier root
counted is skipped. Inside a Cowork root the same holds across files,
because Cowork stores each session twice; inside an ordinary root a resumed
session still counts in each file, as before.

Claude Code on Windows deletes transcripts older than 30 days unless
`cleanupPeriodDays` is set in its settings, so the Windows root covers about
four of the census's eight weeks until that is raised.

## Hook rows

The fleet's git hook runner (agent-checks, managed in `_agent-guidance`)
appends one JSON line per local hook run to
`${AGENT_CHECKS_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-checks/runs.jsonl}`:
`{"v":1,"ts":"…Z","event":"pre-commit|pre-push","repo":"owner/name"|null,"checks":[ids],"failed":[ids]}`.
The census reads that file (`--hook-logs`, `:`-separated; on WSL the publish
script adds each existing `/mnt/c/Users/*/.local/state/agent-checks/runs.jsonl`,
and `USAGE_HOOK_LOGS` replaces the list) and adds `kind: "hook"` rows:

- `name` is the check id when it matches `^[a-z0-9][a-z0-9-]{0,40}$`, else
  `other`; one `all` row per week counts every run, so totals do not depend
  on the id allowlist.
- `repo` is `owner/name` only for a public repository: a registry in
  `harness/registries.yml` that is not private, `example-adopter/skills-evals`
  or `example-adopter/_agent-guidance`. Any other repository is `other`; a run
  with no repository is null.
- `invocations` is the runs in which the check ran, `blocked` the runs in
  which it failed; `sessions` and `overridden` are null.
- Malformed lines are skipped, and a line found in two logs counts once.
  A `scope: "hooks"` source with `runs_scanned` is added only when a log was
  read, so no hook source means unknown, not zero.

## Private usage events

Fine-grained records, kept for refined reporting later, live **only** in the
private repository: branch `persistent/usage-events` of
`example-adopter/agentskills-private`, as
`events/<YYYY-MM>/<machine>.jsonl`. The census writes them with
`--events-out`; the publish script merges them in after the public publish.

- **Skill records:** `{"v":1,"id","ts","kind":"skill","name","surface":"local","root","machine","session_id","session_title","cwd_repo","branch"}`.
  `id` is the tool-use id, or the user entry's uuid for a typed command;
  `name` is the skill id as invoked (`plugin:skill`), raw but capped;
  `root` is `wsl`, `windows` or `cowork`; `session_title` is the
  transcript's latest summary or custom title; `cwd_repo` is the `owner/name`
  of the session directory's github.com `origin` when that directory still
  exists.
- **Hook records:** `{"v":1,"id","ts","kind":"hook","event","repo","checks","failed","surface":"local","root","machine"}`,
  `id` being the SHA-256 of the machine name and the raw log line.
- **Merge.** By `id`: existing lines are kept as they are, new ids are added,
  each file stays sorted by `ts`. A commit (`usage events [skip ci]`) is made
  only when something was added; the push gets one `pull --rebase` retry and
  is verified with `git merge-base --is-ancestor`. Only `events/…` files (and
  the README when the branch is created) may be staged, and a path through a
  symbolic link is refused, so nothing is ever written under `lessons/`.
- **Privacy.** The records carry timestamps, session ids and titles,
  repositories and branches, which is why they never reach the public
  branch. The publish script's output names counts only.
- **Regenerable for the last 8 weeks only.** If the branch is deleted, the
  next run recreates it as an orphan with a README, from what the machine
  still holds: the census window. Anything older exists only on the branch.
- **No ruleset.** Rulesets cannot protect a branch of that private repository
  on this plan, so nothing guards it against deletion or a force push.
- **Failure is isolated.** A private publish that fails prints one line
  naming the step and nothing else, and the script exits 1 after the public
  census has been published.

## Why it runs on the owner's durable machine

It reads transcripts, and only the machine where Claude Code is used day to day
has them. A GitHub runner has none, and a cloud session has only its own. So
this is neither a workflow nor a Routine: it is a scheduled task on the owner's
own machine, which is why the owner sets it up himself (below).

It used to ride along as a step of the Tier-3 account-store Routine. That
Routine, and the audit it existed for, were retired on 2026-09-28 (see
`HANDOFF.md`, the 2026-09-28 amendment near the top), so the census needed its own home.

## The privacy contract

The public output is committed to a **public** branch, so it is the narrowest thing
that answers the question: `{model_id: {iso_week: count}}`, plus the metadata
keys `generated_at` and `weeks`. No project names, no paths, no prompt or reply
text, no session ids, no timestamp finer than a week. The transcripts carry every
one of those; `~/.claude/projects/` encodes the project path in the directory
name alone.

- Weekly buckets, not daily: a daily series over one account is a record of when
  a person was at their desk, and the roster policy only asks about 4- and
  8-week windows.
- Keys are not trusted either. `message.model` is whatever the routing layer
  wrote, and has been observed carrying a Bedrock ARN (an AWS account number), a
  Vertex path (a GCP project id) and free prose. Only values shaped like a model
  id become keys; everything else is counted under the single key `other`.
- The guard is a test, not a convention:
  `test/run_tests.py::TestIssue67::test_census_emits_only_model_week_counts_and_leaks_nothing`
  (and its `TestIssue67Review` siblings) runs the parser over a transcript
  carrying a project path and prose and asserts neither survives into the output.
  `TestPublishUsageCensus` runs the same shape end to end through the publish
  script against a local bare repository and asserts the pushed file holds only
  those keys and that the script's one summary line holds totals only.
- The publish script prints one line — model count, turn total, skill
  invocation total, hook run total, week count, and how many private events
  were new — with no path under `$HOME`, no per-model or per-skill figures and
  no record content. It handles no token: it uses the git credentials the
  machine already has.

## Setting it up (the owner does this once)

Prerequisites on the machine: `git` with push access to
`example-adopter/skills-evals` and `example-adopter/agentskills-private` over
HTTPS or SSH (the credentials you already use),
`python3`, and PyYAML (`python3 -m pip install --user pyyaml`; the census reads
the tier ladder from `evals/roster-policy.yml` through `harness/roster.py`).

Try it first. A dry run does everything except the push:

```bash
bash /path/to/skills-evals/scripts/publish_usage_census.sh --dry-run
```

It prints e.g. `usage census: 4 models, 1234 assistant turns, 56 skill
invocations, 210 hook runs, 8 weeks; dry run, committed locally and pushed
nothing; private events: 12 new of 266, dry run`. The script clones skills-evals into a temp
dir itself, so it always runs the census from `main`, and it re-publishes at least
every 6 days even when the counts have not moved, so an idle account does not age
out of the roster's 14-day freshness window.

### Option A: a Claude Desktop scheduled task

Create a scheduled task, daily, with this prompt (replace the path with where
your checkout of skills-evals lives on that machine):

```text
Run exactly this command and report its single output line, or its error if it
fails. Do not edit any file, do not run anything else, and do not print the
contents of any Claude Code transcript.

bash ~/repos/example-adopter/skills-evals/scripts/publish_usage_census.sh
```

### Option B: cron (or a systemd timer) in WSL

Daily at 06:37 local time, with the output kept in a log you can read:

```cron
37 6 * * * bash "$HOME/repos/example-adopter/skills-evals/scripts/publish_usage_census.sh" >> "$HOME/.cache/usage-census.log" 2>&1
```

Add it with `crontab -e`. WSL only runs cron while a WSL instance is running and
the `cron` service is started, so a laptop that sleeps will skip days; the
14-day freshness window tolerates that. A systemd user timer that runs the same
`bash …/publish_usage_census.sh` line is equivalent.

### Option C: a Windows Task Scheduler task that runs `wsl.exe` invisibly

On a Windows machine where Claude Code runs inside WSL, register the census
alongside the other WSL tasks from
[`wsl-automation`](https://github.com/example-adopter/wsl-automation). Its
`scripts\register-tasks.ps1` (elevated PowerShell 7) now adds a task named
**Usage Census Publish** whose action is `wsl.exe` itself, with no PowerShell in
between:

```text
C:\Windows\System32\wsl.exe --distribution Ubuntu --cd "<wsl-automation checkout>\scripts" --exec /bin/bash ./publish-usage-census.sh
```

- **Invisible.** The task runs as the owning user with an S4U logon ("run
  whether the user is logged on or not", no stored password), so it runs in
  session 0, where no console window can appear. It needs the "Log on as a
  batch job" right, which wsl-automation's other background tasks already
  require (`scripts\grant-keeper-batch-logon.ps1`).
- **Always `main`.** `publish-usage-census.sh` shallow-clones skills-evals and
  runs that clone's `publish_usage_census.sh`, so what runs does not depend on
  which branch a local checkout happens to be on.
- **Daily at 12:37**, with no catch-up: a day the machine sleeps through is
  skipped, which the 14-day window and the 6-day re-publish absorb. A catch-up
  at wake would land in WSL's post-wake transition window, and 12:37 stays
  clear of the backup task's on-the-hour retries (`wsl --export` stops the
  distro).
- **Git credentials come from WSL.** An S4U logon has no stored password, so
  do not count on a Windows-side credential helper such as Git Credential
  Manager. `gh auth git-credential` with the token in `~/.config/gh/hosts.yml`
  works.
- **A log you can read.** Each run appends one line to `~/.cache/usage-census.log`
  inside WSL: a UTC timestamp, the exit code and the script's summary line.

### Checking that it works

`git ls-remote https://github.com/example-adopter/skills-evals persistent/eval-results` should
move after a run that found changed counts, and the branch's
`usage/latest.json` carries the run's `generated_at`. The next roster run
(`roster/latest.json`) reports the census timestamp in its `source.census_at`.

For Option C, run it once on demand and read the result:

```powershell
Start-ScheduledTask -TaskName 'Usage Census Publish'
(Get-ScheduledTaskInfo -TaskName 'Usage Census Publish').LastTaskResult   # 0 once it finishes
wsl.exe -d Ubuntu -- tail -n 3 '~/.cache/usage-census.log'
```

`LastTaskResult` is `267009` while the task is still running. Quote the `~`:
PowerShell 7 would otherwise expand it to the Windows home before `wsl.exe`
sees it.

## Known gap: Windows-native publishing

Under WSL the Windows and Cowork roots are read (see "Transcript roots").
Running the publish script natively on Windows, outside WSL, is not
supported.
