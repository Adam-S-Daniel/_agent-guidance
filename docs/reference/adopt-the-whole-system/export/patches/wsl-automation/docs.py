"""X10 and the kit's additions, in README.md and AGENTS.md.

README.md documented eight tasks, a manual export and the keeper; with the
restic backup, the manual `wsl --export` backup and the session keeper left
out (DECISIONS X10), their sections go ("Why staging", "Lock protocol",
install step 3, the backup, manual export, keeper and both launcher task
sections, "Keeper semantics"), and the sentences in kept sections that named
them are reworded. The kit's additions get their own sections: the session
miner task and the Linux and macOS schedule.

AGENTS.md's repo-specific half keeps every measured lesson that still applies
to a kept task: there is no WSL lifecycle event (with the at-logon
measurement folded in), the post-wake transition window, the interactive
prompt hang, ASCII-only PowerShell, the user principal, PowerShell from WSL
and the CI shape. The sections that only explain the backup or the keeper
go. A short section on the Linux and macOS schedule is added.

This patch runs on the substituted tree, so its anchors are the exported
text. Every anchor must occur exactly once.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location("kit_wsl_text", Path(__file__).with_name("_text.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def _section(text: str, heading: str, where: str) -> tuple[int, int]:
    """Span of a Markdown section: its heading line up to the next heading of
    the same or a higher level, ignoring lines inside code fences."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line == heading]
    if len(starts) != 1:
        raise T.PatchError(f"{where}: expected 1 heading {heading!r}, found {len(starts)}")
    s = starts[0]
    fence = False
    e = len(lines)
    for i in range(s + 1, len(lines)):
        if lines[i].startswith("```"):
            fence = not fence
            continue
        m = re.match(r"^(#+) ", lines[i])
        if not fence and m and len(m.group(1)) <= level:
            e = i
            break
    start = sum(len(line) + 1 for line in lines[:s])
    end = sum(len(line) + 1 for line in lines[:e])
    return start, min(end, len(text))


def drop_section(text: str, heading: str, where: str) -> str:
    s, e = _section(text, heading, where)
    return text[:s] + text[e:]


def replace_section(text: str, heading: str, new: str, where: str) -> str:
    s, e = _section(text, heading, where)
    return text[:s] + new + text[e:]


def insert_before(text: str, heading: str, new: str, where: str) -> str:
    s, _ = _section(text, heading, where)
    return text[:s] + new + text[s:]


README_TOP = """# wsl-automation

PowerShell automation for a WSL2 Ubuntu distro on Windows, with the same
scheduled jobs available natively on Linux and macOS:

- A daily usage census publish (see "Usage census task" below).
- An every-minute feed of the Claude Code cloud sessions waiting on you (see
  "Waiting feed task" below).
- A daily session miner run that turns this machine's Claude Code transcripts
  into short, redacted lesson records (see "Session miner task" below).
- A sync of the WSL ccstatusline layout to Windows, and a twice-daily sync of
  Codex Cloud environments.
- A scheduled-task installer that wires all of the above into Windows Task
  Scheduler, and scripts that pre-trust your workspaces for Claude Code and
  Codex.

## What this is

`wsl-automation` is a small PowerShell 7.6+ module (`WslAutomation`) plus a
handful of thin wrapper scripts meant to be run directly or from Windows
Task Scheduler. `scripts\\register-tasks.ps1` creates or updates the scheduled
tasks idempotently, and archives any legacy ad-hoc scripts they replace. The
jobs that run inside the distro are Bash, so on Linux or macOS
`scripts/unix/install-schedule.sh` schedules them with systemd, launchd or cron
instead (see "Linux and macOS" below).

"""

README_INSTALL = """## Install

1. Clone this repository anywhere on the machine, for example:

   ```
   C:\\Users\\<you>\\repos\\wsl-automation
   ```

2. **Background-task prerequisites (once per machine).** Every task runs as a
   background (S4U, session 0) task, so a run never flashes a window on the
   desktop. That imposes two one-time requirements:

   - **PowerShell 7 installed via MSI** (not the Microsoft Store build). A
     Store-packaged pwsh cannot be activated in session 0, so the pwsh-based
     tasks need a non-packaged pwsh at `C:\\Program Files\\PowerShell\\7\\`.
     Install it with `winget install --id Microsoft.PowerShell --scope
     machine` or the MSI from the PowerShell releases page. `register-tasks`
     prefers this path automatically and warns if only a Store pwsh is found.
   - **The "Log on as a batch job" right** for your account, which a
     background (S4U) task needs. On a machine where you are a local admin
     this is *not* covered by the default Administrators grant (an S4U logon
     gets a UAC-filtered token in which Administrators is deny-only). Grant
     it to your own account from an elevated prompt:

     ```powershell
     .\\scripts\\grant-keeper-batch-logon.ps1
     ```

3. Open an elevated PowerShell 7.6+ prompt (scheduled task registration
   needs administrator rights) and run the task installer:

   ```powershell
   cd C:\\Users\\<you>\\repos\\wsl-automation
   .\\scripts\\register-tasks.ps1
   ```

   This creates/updates the scheduled tasks described below. Re-running it is
   safe and idempotent - it will update the existing tasks in place rather
   than duplicating them. `-WhatIf` shows what it would change.

4. **Windows ccstatusline runtime (once per machine).** Run
   `.\\scripts\\install-ccstatusline.ps1` from a normal PowerShell 7 prompt; see
   [Windows runtime (one-time)](#windows-runtime-one-time).

5. **Session miner (once per machine).** The session miner task refuses to
   mine until you have set it up and run it by hand once; see
   [Session miner task](#session-miner-task-default-name-session-miner).

"""

README_MINER = """### Session miner task (default name: `Session Miner`)

- Runs `scripts/run-session-miner.sh` inside the distro once a day at 07:17
  (`-SessionMinerTime`). Like the census, the wrapper shallow-clones
  skills-evals `main` and runs that copy's `scripts/run_session_miner.sh`
  (skills-evals ADR 0015), so a scheduled run always executes the reviewed
  `main` version.
- It reads this machine's real Claude Code transcripts. What leaves the
  machine is short, redacted, hashed lesson records, pushed to the
  `persistent/lessons` branch of your private skills registry. The wrapper never
  copies a transcript anywhere, and it refuses to run in CI or in a cloud
  session (`CI`, `GITHUB_ACTIONS`, `CLAUDE_CODE_REMOTE` or
  `CLAUDE_CODE_REMOTE_SESSION_ID` set).
- The action is `wsl.exe` directly, as for the census:

  ```
  C:\\Windows\\System32\\wsl.exe --distribution Ubuntu --cd "<scripts dir>" --exec /bin/bash ./run-session-miner.sh
  ```

- Runs as a background S4U task for the current Windows user (session 0).
  Allowed on battery, 30 minute execution limit, ignores a second overlapping
  instance, no `-StartWhenAvailable` (a missed day stays missed; the next run
  reads what it missed).
- **One-time setup, in the distro.** Until a dry run has succeeded, a
  scheduled run exits 3 ("not set up yet") and mines nothing.
  1. A privacy denylist, one pattern per line, kept **outside every
     repository**: `~/.config/privacy-denylist.txt` (`PRIVACY_DENYLIST_FILE`).
     Without a readable one the wrapper exits 2 and nothing is mined.
  2. A clone of your private skills registry whose `origin` receives
     `persistent/lessons`: `~/repos/acsi-agentskills-private` (`LESSONS_CLONE`).
     Its git credential must work with no desktop session: `gh auth setup-git`
     with a file-stored token, as for the census.
  3. python3 with PyYAML (`sudo apt install python3-yaml`).
  4. Optionally, settings in `~/.config/wsl-automation/session-miner.env`
     (`SESSION_MINER_CONFIG`), one `KEY=value` per line. A scheduled action has
     no environment of its own, so this is where settings go. The file is read
     as data, never sourced; only `PRIVACY_DENYLIST_FILE`, `LESSONS_CLONE`,
     `GUIDANCE_MANIFEST`, `MINER_REGISTRIES`, `WIN_HOME`, `MINER_STATE`,
     `SKILLS_EVALS_URL`, `PYTHON` and `FILE_ISSUES` are accepted, a leading
     `~/` is expanded, and a variable already in the environment wins.
  5. One run by hand: `bash scripts/run-session-miner.sh --dry-run`. It mines
     and reports without pushing, and on success records
     `~/.local/state/session-miner/dry-run-ok`, which arms the scheduled run.
- **`WIN_HOME`** (the Windows profile as a WSL path, for example
  `/mnt/c/Users/<you>`) adds the Windows transcripts and the desktop app's
  metadata, which lets the miner drop scheduled-task sessions as noise.
  Without it the wrapper reads only `~/.claude/projects` in WSL, rather than
  have the miner ask `cmd.exe` from a session 0 logon, and says so in its log
  line.
- **Filing issues stays off.** `--file-issues` exits 2 unless `FILE_ISSUES=on`
  is set; turn it on only after you have rated a sample of records for
  usefulness and privacy.
- Appends one line per run to `~/.cache/session-miner.log`: UTC time, exit
  code, and the miner's own `session miner: ...` count lines. Any other output
  (a Python traceback, for example) could carry a path or an excerpt, so it is
  counted, not copied. Exit codes: 0 success; 1 a missing prerequisite (the
  registry clone, PyYAML) or a failed clone of skills-evals; 2 a refusal (CI or
  cloud, no denylist, a bad settings line, `--file-issues` while off); 3 not
  set up yet; anything else is the miner's own exit code.
- Run it now: `Start-ScheduledTask -TaskName 'Session Miner'`; check
  `(Get-ScheduledTaskInfo -TaskName 'Session Miner').LastTaskResult` is `0`
  and `tail ~/.cache/session-miner.log` in WSL.

"""

README_UNIX = """## Linux and macOS

The census, waiting feed and session miner jobs are Bash, so they run natively
on Linux and macOS too. `scripts/unix/install-schedule.sh` schedules them; every
entry it writes runs `scripts/unix/run-task.sh <job>`, which does for each run
what Task Scheduler and the checkout bootstrap do on Windows:

- **No overlap.** A per-job `flock -n` lock: a run that finds the previous one
  still going logs `skipped=running` and exits 0, like `IgnoreNew`.
- **A time limit.** coreutils `timeout` with the Windows task's limit (census
  15 minutes, waiting feed 5, session miner 30) and a 10 second kill grace.
- **The checkout update.** Fetch `origin`, switch a clean checkout to `main`,
  fast-forward to `origin/main`, then re-exec from disk; a dirty, diverged,
  offline or slow checkout warns and runs its current code (the waiting feed
  updates at most every 5 minutes).
- **One log line per run** in `~/.cache/wsl-automation-tasks.log`.

macOS needs `brew install flock coreutils` (the runner refuses to start a job
without `flock` and `timeout`), and the waiting feed needs GNU `date`, which
coreutils' `gnubin` directory provides; the runner puts it first on `PATH`.

```bash
bash scripts/unix/install-schedule.sh systemd                 # Linux: user timers
bash scripts/unix/install-schedule.sh launchd                 # macOS: LaunchAgents
bash scripts/unix/install-schedule.sh cron                    # anywhere with cron
bash scripts/unix/install-schedule.sh systemd --jobs usage-census,waiting-feed,session-miner
bash scripts/unix/install-schedule.sh cron --print            # show it, change nothing
```

| Job | Schedule |
| --- | --- |
| `usage-census` (the default) | daily at 12:37 |
| `session-miner` | daily at 07:17 |
| `waiting-feed` | every minute |

- **systemd** writes a oneshot service and a timer per job to
  `~/.config/systemd/user` and enables the timers. The timers are
  `Persistent=true`: a run missed while the machine was off or asleep starts
  once at the next boot or wake. Native Linux has no WSL post-wake transition
  window, so that catch-up is wanted there. User timers stop when you log out
  unless linger is on: `loginctl enable-linger "$USER"` (the installer says
  when it is off).
- **launchd** writes `~/Library/LaunchAgents/local.wsl-automation.<job>.plist`
  and loads it into your login session. launchd runs a calendar job missed
  during sleep once on wake.
- **cron** keeps one marked block in your crontab, replaced on every run; the
  rest of the crontab is left alone. Plain cron has no catch-up.
- Re-running the installer is idempotent. To remove a schedule:
  `systemctl --user disable --now wsl-automation-<job>.timer` and delete its two
  units; `launchctl bootout gui/$(id -u)/local.wsl-automation.<job>` and delete
  the plist; or delete the block from `crontab -e`.
- A scheduler starts jobs with no profile and no desktop session, so keep git
  credentials where such a job can reach them (`gh auth setup-git` with a
  file-stored token) and settings in the session miner's settings file.

"""

README_TESTING = """## Testing

Tests are written in Pester v5 syntax and are compatible with both Pester
5.7.1 and 6.0.0. From the repository root:

```powershell
Invoke-Pester ./tests
```

No test invokes real `wsl.exe` - every test mocks the module's `Invoke-WslExe`
seam - and all filesystem paths used by the tests live under Pester's
`TestDrive:`. The bash scripts have their own tests (`bash tests/<name>.test.sh`),
which put stubs on `PATH` instead of running Windows programs or reaching the
network; `tests/unix-schedule.test.sh` covers the Linux and macOS runner and
installer, and `tests/run-session-miner.test.sh` the session miner wrapper.

## Requirements

- PowerShell 7.6 or later, and for the background tasks an **MSI** install of
  PowerShell 7 (`C:\\Program Files\\PowerShell\\7\\`) - a Store-packaged pwsh
  cannot run in the session 0 they use.
- Windows (Task Scheduler integration is Windows-only; the module's
  non-scheduling functions are otherwise plain PowerShell 7.6+).
- The "Log on as a batch job" right for the account running the tasks (see
  `scripts\\grant-keeper-batch-logon.ps1`).
- WSL2 with the distro the tasks run in already installed.
- `winget` (for the one-time ccstatusline runtime install).
- For Codex Cloud synchronization: authenticated Codex and GitHub CLIs, plus
  `curl`, `jq`, `flock`, and `mktemp` in that distro.
- For the waiting feed: `curl`, `jq`, `flock` and coreutils `timeout` in the
  distro, and a signed-in Claude Code there.
- For the session miner: python3 with PyYAML and the setup in its section.
- On Linux or macOS instead: bash, git, `flock` and coreutils `timeout`
  (`brew install flock coreutils` on macOS).
"""


def readme(text: str, where: str) -> str:
    text = T.replace_between(text, "# wsl-automation\n", "## Install\n", README_TOP, where)
    text = replace_section(text, "## Install", README_INSTALL, where)

    text = T.sub_once(
        text,
        "Every task action that runs a repo script (backup, keeper, ccstatusline sync,\n"
        "Codex Cloud sync, usage census, and waiting feed) first updates",
        "Every task action runs a repo script (ccstatusline sync, Codex Cloud sync,\n"
        "usage census, waiting feed, and session miner), and first updates",
        where,
    )
    text = T.sub_once(
        text,
        "task code. The two on-demand launcher actions invoke their CLIs directly and have\n"
        "no repo script to update. All six run from",
        "task code. All five run from",
        where,
    )
    text = T.sub_once(
        text,
        "| PowerShell (backup, keeper, ccstatusline, Codex Cloud) |",
        "| PowerShell (ccstatusline, Codex Cloud) |",
        where,
    )
    text = T.sub_once(
        text,
        "| Bash in WSL: usage census (daily) |",
        "| Bash in WSL: usage census and session miner (daily) |",
        where,
    )

    for heading in (
        "### Backup task (default name: `WSL Ubuntu Daily Backup`)",
        "### Manual full export (`Invoke-WslBackup`)",
        "### Keeper task (default name: `Claude Code Session Keeper`)",
        "### Launcher task (default name: `Claude Code Session Launcher`)",
        "### Codex remote control launcher task (default name: `Codex Remote Control Launcher`)",
        "## Keeper semantics",
    ):
        text = drop_section(text, heading, where)

    text = T.sub_once(
        text,
        "S4U task for the current Windows user (same MSI PowerShell and batch-logon\n"
        "  prerequisites as the keeper).",
        "S4U task for the current Windows user (the MSI PowerShell and batch-logon\n"
        "  prerequisites of Install step 2).",
        where,
    )
    text = T.sub_once(
        text,
        "It uses the same MSI PowerShell and batch-logon prerequisites as\n  the keeper.",
        "It uses the MSI PowerShell and batch-logon prerequisites of\n  Install step 2.",
        where,
    )
    # notes Q6: the link pinned the source registry's commit, zeroed in the export.
    link = re.compile(r"downloads a reviewed \[immutable bootstrap revision\]\([^)\s]+\), checks its SHA-256\n  digest,")
    if len(link.findall(text)) != 1:
        raise T.PatchError(f"{where}: expected 1 bootstrap revision link")
    text = link.sub(
        "downloads your registry's `.claude/hooks/skills-bootstrap.sh` at one\n"
        "  reviewed, immutable commit, checks its SHA-256 digest (both pinned at the\n"
        "  top of `scripts/sync-codex-cloud-environments.sh`, and all zeros until you\n"
        "  set them, which fails closed: setup reports skills DEGRADED),",
        text,
    )
    text = T.sub_once(
        text,
        "12:37 keeps\n  it well clear of the backup's `:00` hourly retries.",
        "12:37 keeps\n  it off the hour.",
        where,
    )

    text = insert_before(text, "### Legacy scripts", README_MINER, where)
    text = insert_before(text, "## Task history", README_UNIX, where)
    s, _ = _section(text, "## Testing", where)
    text = text[:s] + README_TESTING
    return text


AGENTS_LIFECYCLE_INTRO = (
    "An event-driven trigger (\"run a job when WSL stops\") was ruled out\n"
    "empirically against the modern MSI WSL package. Don't re-investigate; a fixed\n"
    "daily time is the answer.\n"
)

AGENTS_AT_LOGON = """- **An at-logon trigger is no substitute for a daily time either.** It fires
  only on a real logon: not on unlock (that is a separate
  `SessionStateChangeTrigger` with `StateChange=SessionUnlock`) and not on
  resume from sleep. Measured over a multi-week window from
  `TerminalServices-LocalSessionManager/Operational` Id 21, logons clustered
  onto under half the days on a laptop that mostly sleeps, including one
  stretch of nearly two weeks with none at all.

"""

AGENTS_TRANSITION = """### Don't trigger work inside WSL's post-wake transition window

WSL is fragile for a few minutes after a boot or a resume. Both observed
`exit -1` failures of `wsl --export` (the backup the adoption kit leaves out) hit WSL
mid-transition: one where a scheduled wake pulled the machine out of sleep and
the call died seconds later, and one where a `-StartWhenAvailable` catch-up
fired a few minutes after a cold boot. The corroborating signal in the same
window is SCM 7011 - "timeout (30000 ms) ... waiting for a transaction response
from the WSLService service".

- **The WSL-init window runs up to roughly 270 seconds after boot.** Anything
  triggered off boot or logon therefore needs a delay of ten minutes, not five;
  five would have cleared the observed worst case by about a minute.
- **This is why no Windows task here uses `-StartWhenAvailable`:** a catch-up
  at wake would land in that window. A missed run stays missed. Any work that
  adds a boot- or resume-adjacent trigger owes the same delay. (Native Linux
  has no such window, so the systemd timers there are `Persistent=true`.)

"""

AGENTS_UNIX = """### Linux and macOS: one runner, three schedulers

`scripts/unix/install-schedule.sh` writes systemd user units, launchd agents or
a crontab block, and every one of them runs `scripts/unix/run-task.sh <job>`.
Keep the overlap lock, the time limit, the checkout update and the log line in
the runner, not in the scheduler entries, so the three schedulers cannot drift
apart; `tests/unix-schedule.test.sh` covers the runner and all three outputs.

- **The lock is taken once, before the checkout update**, on fd 9, which stays
  open across the runner's re-exec and into the job. Do not move the `flock`
  after the update or into the re-executed copy: a second run could then slip
  in while the first is updating.
- **systemd user timers need linger** to run without a login
  (`loginctl enable-linger "$USER"`, which polkit lets a user set for
  themselves). A process that kills every process of the user can take the
  user manager down with it, and systemd does not start it again by itself;
  `loginctl enable-linger` starts it (observed once in WSL, after a test sent
  `kill(-1, SIGKILL)`). Do not turn linger off to "clean up".
- The runner sets `WSL_AUTOMATION_TASK_REEXEC` to the entry's path, so the
  entry's own Windows checkout bootstrap (`update-task-checkout.sh`) stays off
  outside WSL. Keep the two guard names in step.

"""


def agents(text: str, where: str) -> str:
    text = T.sub_once(
        text,
        "An event-driven trigger (\"run the backup when WSL stops\") was ruled out\n"
        "empirically against the modern MSI WSL package. Don't re-investigate; the daily\n"
        "timer is the answer.\n",
        AGENTS_LIFECYCLE_INTRO,
        where,
    )
    text = T.sub_once(
        text,
        "  hibernate and surface as `0x1`, never `0x0`.\n\nRe-open this only",
        "  hibernate and surface as `0x1`, never `0x0`.\n" + AGENTS_AT_LOGON + "Re-open this only",
        where,
    )
    for heading in (
        "### Why the backup is a daily timer and not an at-logon trigger",
        "### The daily backup is a live restic backup, not `wsl --export`",
        "### `wsl --export` stops the running distro",
        "### Idle Claude Code sessions do not count as backup activity",
        "### The keeper restores lost Claude sessions from a snapshot",
        "### `wsl --export --vhd` and throwaway distros",
        "### The keeper restarts a dead WSL user manager, and linger stays on",
    ):
        text = drop_section(text, heading, where)
    text = replace_section(
        text, "### `wsl --export` fails on a *transitioning* WSL, not (only) a busy one", AGENTS_TRANSITION, where
    )

    text = T.replace_between(
        text,
        "This is why `wsl-restic-backup.ps1`, the script the registered backup task runs,\n",
        "**Recognising it:**",
        "So every script wired into a task must be prompt-free when it runs unattended,\n"
        "and the short-interval tasks (ccstatusline sync every 5 minutes, the waiting\n"
        "feed every minute, both `-MultipleInstances IgnoreNew`) are where it bites\n"
        "hardest: one hung instance suppresses every subsequent interval for the whole\n"
        "execution-time limit, not just the next one.\n\n",
        where,
    )
    text = T.sub_once(
        text,
        "Task Scheduler Id 329 terminates it at *exactly* `ExecutionTimeLimit` later (2h\n"
        "for the backup and the keeper, 5 min for ccstatusline).\n",
        "Task Scheduler Id 329 terminates it at *exactly* `ExecutionTimeLimit` later (5\n"
        "min for ccstatusline and the waiting feed, 15 min for the census and the Codex\n"
        "Cloud sync, 30 min for the session miner).\n",
        where,
    )
    text = T.sub_once(text, "— a stuck prompt, not a slow export.", "— a stuck prompt, not a slow run.", where)
    text = T.replace_between(
        text,
        "The backup lock is not the tell:",
        "### Keep the PowerShell sources ASCII-only",
        "",
        where,
    )

    text = T.sub_once(
        text,
        "A predecessor of\n`wsl-ubuntu-backup.ps1` contained non-ASCII punctuation,",
        "An earlier script in\nthis repo contained non-ASCII punctuation,",
        where,
    )

    text = T.sub_once(
        text,
        "schedule, exits, and backs up nothing — no error to notice.",
        "schedule, exits, and does nothing — no error to notice.",
        where,
    )
    text = T.replace_between(
        text,
        "The accepted cost of a user principal is that the interactive tasks cannot run\n",
        "### PowerShell invoked from WSL is never elevated",
        "The S4U tasks run with that user principal whether or not anyone is logged on,\n"
        "so the user principal costs nothing here.\n\n",
        where,
    )

    text = T.sub_once(
        text,
        "  `.\\scripts\\register-tasks.ps1 -BackupDir '<dir>' -ResticRepoDir '<dir>'` —\n",
        "  `.\\scripts\\register-tasks.ps1` —\n",
        where,
    )
    text = T.sub_once(
        text,
        "**The line to hand over is the installer, as the README's step 4 gives it:**",
        "**The line to hand over is the installer, as the README's step 3 gives it:**",
        where,
    )

    text = insert_before(text, "### CI: three required checks that always run", AGENTS_UNIX, where)
    return text


def apply(root: Path, ctx) -> None:
    T.edit_file(root, "README.md", readme)
    T.edit_file(root, "AGENTS.md", agents)
    for rel in ("README.md", "AGENTS.md"):
        text = (root / rel).read_text(encoding="utf-8")
        for gone in ("restic", "Keeper", "keeper task", "the keeper", "backup.lock", "Invoke-WslBackup",
                     "wt.exe", "-BackupDir", "Test-WslActivity", "Repair-WslUserRuntime"):
            if gone in text:
                raise T.PatchError(f"{rel}: still mentions {gone!r}")
