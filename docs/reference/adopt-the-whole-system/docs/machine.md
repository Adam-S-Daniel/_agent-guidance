# Machines and cloud environments

The repos hold the system; each machine you work on needs a few things set
once, and so does each claude.ai cloud environment.

## Install on each durable machine

A durable machine is one you keep (a laptop, a desktop, a WSL distro), as
opposed to a cloud session that starts from a snapshot. On each one:

```bash
bash bootstrap/install-machine.sh             # add --dry-run to see it first
bash bootstrap/install-machine.sh --with-codex  # also wire Codex, if ~/.codex exists
```

It clones (or fast-forwards, when the clone is clean and on its default
branch) your `agentskills` and `_agent-guidance` into `REPOS_DIR` from
`fleet.env`, refuses a clone whose `origin` is not your repo, and then runs
each repo's own installer:

1. `agentskills`' `setup.sh --owner-machine`: adds your marketplace and
   enables the plugins in `~/.claude/settings.json`.
2. `agentskills`' `scripts/install-agent-hooks.sh`: the config-based git
   hooks (Git 2.54+) that run `.githooks/agent-checks` on every commit and
   push, in clones of your two owners' repos only (an `includeIf` per owner).
3. `_agent-guidance`'s `scripts/register-memory-home-hook.sh`: the
   user-level `memory-home.sh` hook ([memory](guidance.md#memory-that-points-at-its-home)).
4. With `--with-codex`, `_agent-guidance`'s `scripts/register-codex-hook.sh`;
   then run `codex`, type `/hooks` and trust the hook once.

Every installer is idempotent, so run it again after a plugin is added or
moved. The fleet guidance itself needs nothing here: the `fleet-memory`
hook each repo carries installs it into `~/.claude/CLAUDE.md` at the first
session in any fleet repo.

## Scheduled jobs (wsl-automation)

[`wsl-automation`](../starter/wsl-automation/README.md) schedules the jobs
that must run on your machine, because they read its transcripts or its
login:

| Job | When | What it does |
|---|---|---|
| session miner | daily at 07:17 | turns your transcripts into short, redacted lesson records on `agentskills-private`'s `persistent/lessons` |
| usage census | daily at 12:37 | publishes which models and skills you ran each week to `skills-evals`' `persistent/eval-results`; the roster picks eval models from it and the planner improves the most used skills first |
| waiting feed | every minute (optional) | publishes the sessions waiting on you to a private `waiting-feed` repo |

**On Windows** (WSL), from the clone, once per machine:

1. Install PowerShell 7 from the MSI, not the Microsoft Store
   (`winget install --id Microsoft.PowerShell --scope machine`).
2. From an elevated prompt, `.\scripts\grant-keeper-batch-logon.ps1`, which
   gives your account the "Log on as a batch job" right the background tasks
   need.
3. From an elevated PowerShell 7 prompt, `.\scripts\register-tasks.ps1`
   (`-WhatIf` shows the change first).
4. From a normal prompt, `.\scripts\install-ccstatusline.ps1`.

**On Linux or macOS**:
`bash scripts/unix/install-schedule.sh <systemd|launchd|cron>` (add
`--jobs usage-census,session-miner,waiting-feed`; `--print` changes
nothing). macOS needs `brew install flock coreutils`; systemd needs
`loginctl enable-linger "$USER"` for jobs to run while you are logged out.

**The session miner refuses to run** until you set it up and run it by hand
once, in the distro: a privacy denylist outside every repo
(`~/.config/privacy-denylist.txt`, the same patterns as the `PRIVACY_DENYLIST`
secret), a clone of `agentskills-private` at `~/repos/agentskills-private`
with a git credential that works without a desktop session
(`gh auth setup-git` with a file-stored token), `python3-yaml`, and then
`bash scripts/run-session-miner.sh --dry-run`. Filing issues stays off until
you set `FILE_ISSUES=on`, after you have read a sample of its records. The
README's "Session miner task" has every setting.

**The waiting feed** needs its own private repo:
`bash bootstrap/create-repos.sh --with-optional` creates `waiting-feed`.

## Cloud environments

A claude.ai cloud environment (https://claude.ai/code, the environment
menu) sets the network access and a setup script for every session started
in it. Create one for your fleet work and give it:

- **Network access:** "Custom", with the domains in `_agent-guidance`'s
  [`docs/reference/network-allowlist-claude-environments.txt`](../starter/agent-guidance/docs/reference/network-allowlist-claude-environments.txt).
  Its header says how to change it.
- **Setup script:** the one in `agentskills`'
  [`docs/multi-repo-delivery.md`](../starter/agent-skills/docs/multi-repo-delivery.md),
  "The wiring (cloud only)", with its project directory set to the one your
  sessions open. A single-repo session needs none: the repo's own
  `skills-bootstrap.sh` hook installs its locked skills. A session opened on
  several repos needs the script, because hooks are read only from the
  working directory and `~`. It must end with `exit 0`; a setup script that
  fails stops the session from starting.

The routines each name their environment ([routines](routines.md)). Codex
Cloud environments are kept in step by `wsl-automation`'s
`scripts/sync-codex-cloud-environments.sh` (its README, "Codex Cloud
environment sync task"), after
[the Codex Cloud pin](generated-files.md#the-codex-cloud-pin).
