<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->
<!-- Mode: stub -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## Fleet guidance is delivered once per session — not by this file

The account's full guidance — incidents, fleet policy, machine layout, the
traps that cost real outages — is installed into **user memory**
(`~/.claude/CLAUDE.md`) by the `fleet-memory` SessionStart hook, so it is
loaded **once per session** no matter how many repos are attached
([2026-08-29: inlined per repo, it took 332.3k tokens](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/2026-08-29-guidance-inlined-in-every-repo.md)).

**Check the session-start verdict before you rely on it.** The hook prints one
line:

- `fleet-guidance: installed (v<id>, <n> bytes)` or `fleet-guidance: current` —
  the full guidance is in context. Use it.
- `fleet-guidance: DEGRADED — <reason>` — it is **not** in context. You have
  only what is below. Read `agents-md/base.md` in the `_agent-guidance`
  checkout (or on GitHub) before non-trivial work, and say in your reply that
  you were running degraded.
- `fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)` — also not in context,
  but by the machine owner's deliberate choice, not a fault. User memory is
  GLOBAL on a durable machine, so the guidance would otherwise load in every
  unrelated project on that box; `FLEET_GUIDANCE_SKIP` opts out and removes any
  block an earlier session installed. Read `agents-md/base.md` the same way you
  would when degraded — just don't report it as a problem or try to "fix" it.

No verdict at all means the hook never ran — treat that as DEGRADED.

## Codex reads the same block, from `~/.codex/AGENTS.md`

The hook writes the same block to `~/.codex/AGENTS.md` whenever `~/.codex`
exists — Codex's global **user** instructions, outside its 32 KiB
`project_doc_max_bytes` project-doc budget. Register it once per machine with
`scripts/register-codex-hook.sh` from an `_agent-guidance` checkout, then
trust it in `/hooks`. `codex debug prompt-input` renders instructions loaded
from disk by its own diagnostic process; it does not run SessionStart hooks
or inspect an existing session or daemon. Verify delivery in the launched
session's initial instructions and verdict; see the
[2026-10-04 evidence](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/codex-trust-and-daemon-0160.md#prompt-debugging-source-read-2026-10-04-cli-01600-rust-v01600).

For Codex Cloud, use **Manual** environment setup with persistent
`CODEX_HOME=/opt/codex`. Preserve the repository's dependency setup and run
`bash .claude/hooks/fleet-memory.sh --codex-cloud` in both setup and
maintenance; reset the cache for the first verification. Fresh setup and
cached maintenance were verified in the `_agent-guidance` environment. See
[`docs/codex-cloud.md`](https://github.com/example-adopter/_agent-guidance/blob/main/docs/codex-cloud.md).
If the Cloud shell has no `codex debug prompt-input`, the saved task response's
raw initial instruction envelope is the echo-free proof of model-visible
delivery.

## The floor: rules that hold even when the guidance did not load

These are the ones with teeth. They are restated here, deliberately, because a
session that lost the guidance must not also lose these.

- **Branch protection is real.** Fleet repos are PR-only on their default
  branch; a direct push is rejected (GH013), even from the repo's own
  workflows. Never design a bot that pushes to a protected default branch.
- **Every `uses:` is pinned through `.github/workflows/actions.lock`: an
  exact tag, locked, checked by a required `gh actions-lock --verify`
  failing on any pin finding.** A repo not yet migrated keeps the legacy
  form, a full 40-character commit SHA with no trailing version comment,
  until its migration PR; never mix the two in one repo. Cross-repo
  reusable workflows take a full SHA either way. The one carve-out is a ref
  into this account's own `example-site-platform`, which stays on its release tag.
- **Never commit secrets or `.env` files, and never print personal data to a
  CI log** — logs, artifacts and git history on a public repo are public.
- **A successful `git push` does not mean your commit exists.** A refused
  pre-commit hook still lets the push report success. Verify with
  `git merge-base --is-ancestor <sha> origin/<branch>` — it is the only check
  that names both the commit and the ref.
- **"The watch finished" is not "CI passed."** Read the parsed conclusions;
  never infer pass/fail from a watch command's exit code.
- **A GitHub 404 means "not authorized", not "not there."** Never report a
  repo, PR or branch as gone on a 404 alone.
- **The fleet spans TWO owners** — `example-adopter` and `example-adopter-two`. A query
  scoped to one returns a plausible, complete-shaped, wrong answer.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`); do not amend
  published commits or force-push shared branches.
- **Keep this file under 32 KiB.** Codex truncates project instructions at
  that byte silently; the sync warns and the drift report flags
  `codex-truncated`.

<!-- END MANAGED SECTION -->
## Repo-specific additions

<!-- Add your repo-specific agent guidance below this line -->

### There is no WSL lifecycle event to trigger on

An event-driven trigger ("run a job when WSL stops") was ruled out
empirically against the modern MSI WSL package. Don't re-investigate; a fixed
daily time is the answer.

- **There is no WSL event log or provider at all.**
  `Get-WinEvent -ListLog *Lxss*,*WSL*,*Subsystem*` returns nothing.
- **There is no `LxssManager` service to watch.** Modern WSL's `WSLService`
  (`C:\Program Files\WSL\wslservice.exe`) stays `Running` while the utility VM
  comes and goes underneath it, so it never emits the SCM 7036 start/stop pair.
  Expect a handful of WSL-related SCM events per *quarter*, not one per session.
- **The only VM-lifecycle signal is the wrong VM.**
  `Microsoft-Windows-Hyper-V-VmSwitch-Operational` Id 66 (switch delete) and Id
  124/125 (restart) track the shared utility VM, not the distro — and any other
  Hyper-V consumer on the box keeps that VM alive independently of WSL. The
  classic Hyper-V-Worker / VMMS / Compute logs are not present.
- **If you ever need cold-boot vs. resume**, the discriminator that does work is
  `Kernel-Boot` Id 27: `0x0` full boot, `0x1` fast startup, `0x2` resume. With
  Fast Startup enabled (`HiberbootEnabled=1`) most "shutdowns" are a hybrid
  hibernate and surface as `0x1`, never `0x0`.
- **An at-logon trigger is no substitute for a daily time either.** It fires
  only on a real logon: not on unlock (that is a separate
  `SessionStateChangeTrigger` with `StateChange=SessionUnlock`) and not on
  resume from sleep. Measured over a multi-week window from
  `TerminalServices-LocalSessionManager/Operational` Id 21, logons clustered
  onto under half the days on a laptop that mostly sleeps, including one
  stretch of nearly two weeks with none at all.

Re-open this only if Microsoft ships a WSL event provider.

### Don't trigger work inside WSL's post-wake transition window

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

### Never leave an interactive prompt in a scheduled-task code path

`Read-Host`, `pause`, and any `-Confirm` prompt must be unreachable when a
script runs unattended. A scheduled task has no console to answer them, so the
run does not fail — it **hangs until `ExecutionTimeLimit` kills it**.

So every script wired into a task must be prompt-free when it runs unattended,
and the short-interval tasks (ccstatusline sync every 5 minutes, the waiting
feed every minute, both `-MultipleInstances IgnoreNew`) are where it bites
hardest: one hung instance suppresses every subsequent interval for the whole
execution-time limit, not just the next one.

**Recognising it:** the run starts on time, the log stops a few seconds in, and
Task Scheduler Id 329 terminates it at *exactly* `ExecutionTimeLimit` later (5
min for ccstatusline and the waiting feed, 15 min for the census and the Codex
Cloud sync, 30 min for the session miner).
`LastTaskResult=267014` (`0x41306`, `SCHED_S_TASK_TERMINATED`) is the signature
— a stuck prompt, not a slow run. Note it is a *terminated* result, not an
error one, so failure-only alerting will not see it.

### Keep the PowerShell sources ASCII-only

Every tracked `.ps1` / `.psm1` / `.psd1` here is pure ASCII and carries no BOM.
Keep it that way — in comments, comment-based help, and log strings alike. Use
`-` rather than an em dash and `->` rather than an arrow character.

Windows PowerShell 5.1 reads a BOM-less file as the active ANSI code page
(Windows-1252 on a US-English install), not UTF-8. An earlier script in
this repo contained non-ASCII punctuation, and under 5.1 the three
bytes of `->` as an arrow (`E2 86 92`) decode to three cp1252 characters ending
in a smart quote — which 5.1 treats as a **string delimiter**, so the whole file
fails to parse.

What makes this latent rather than loud: pwsh 7 reads BOM-less UTF-8 correctly,
and CI runs `shell: pwsh` on `windows-latest` — so CI will never catch it. The
exposure is a human running `scripts\register-tasks.ps1` (or pasting it) from a
5.1 prompt, which is a normal thing to do on Windows.

A UTF-8 BOM also fixes it, but editors and tooling strip BOMs silently, so
staying ASCII is the invariant that actually holds. Check with:

```
LC_ALL=C git grep -nP '[\x80-\xff]' -- '*.ps1' '*.psm1' '*.psd1'
```

It must find nothing. (Scope the pathspec — the Markdown files legitimately use
em dashes.)

### Task principal is the owning user — never SYSTEM

Every task builds its principal from `$env:USERDOMAIN\$env:USERNAME`. Do
not switch one to `NT AUTHORITY\SYSTEM` to dodge an elevation or
stored-password problem — the S4U tasks in particular make it look tempting.

**Why it is not merely wrong but dangerous:** WSL distros are registered *per
Windows user account*. A task running as SYSTEM sees no distro at all, so every
`wsl.exe` call against it is meaningless. It registers cleanly, runs on
schedule, exits, and does nothing — no error to notice.

The S4U tasks run with that user principal whether or not anyone is logged on,
so the user principal costs nothing here.

### PowerShell invoked from WSL is never elevated

The rule itself — `powershell.exe` / `pwsh.exe` launched from WSL holds a
filtered token, so reads succeed while elevation-requiring writes fail with
"Access is denied", and no flag, retry or downgraded principal fixes it — and
the hand-over procedure live in the **`windows-elevation-from-wsl`** skill
(`coding-local` plugin in `agentskills`;
`/coding-local:windows-elevation-from-wsl`). This section keeps only what
is specific to this repo:

- **Both writes this repo makes need elevation.** `scripts\register-tasks.ps1`
  carries `#requires -RunAsAdministrator`, so from WSL it refuses before the
  first line runs ("The script cannot be run because it contains a '#requires'
  statement for running as Administrator") rather than failing part-way with
  "Access is denied" — and that holds for `-WhatIf` too, so even a dry run
  needs the elevated prompt. `scripts\grant-keeper-batch-logon.ps1` is an LSA
  rights grant (`SeBatchLogonRight`), the other denied shape.
- **Reads are the WSL-side tool.** `Get-ScheduledTask`, `Get-ScheduledTaskInfo`
  and `Export-ScheduledTask` against the tasks work from WSL; use them to
  investigate and to export what a re-registration will replace.
- **The line to hand over is the installer, as the README's step 3 gives it:**
  from an elevated PowerShell 7.6+ prompt in the Windows checkout
  (`C:\repos\example-adopter\wsl-automation`),
  `.\scripts\register-tasks.ps1` —
  re-running it updates the existing tasks in place.

### Linux and macOS: one runner, three schedulers

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

### CI: three required checks that always run

`.github/workflows/ci.yml` has three jobs, `shell` (ubuntu), `test` (windows)
and `test-linux` (ubuntu), intended as required status checks; they are made
required in repo-settings' `fleet.yml` after this lands. A required check
cannot use `on.paths` (a skipped check blocks the merge), so all three run on
every pull request and skip early instead: each job's `salient` step diffs the
pull request's base and head and gates every later step on it, and a final
step reports success when nothing salient changed. The salient paths are
`src/**`, `scripts/**`, `tests/**`, `.github/workflows/ci.yml`,
`.github/workflows/actions.lock` and `PSScriptAnalyzerSettings.psd1`. On any
other event, or when the changed files cannot be determined, the job runs in
full. Keep the job ids, and add no `name:`, `matrix` or `concurrency:`;
`tests/CiWorkflow.Tests.ps1` enforces that shape.

**Action pins.** Workflows name actions by release tag, locked to a commit in
`.github/workflows/actions.lock`
([ADR 0024](https://github.com/example-adopter/_agent-guidance/blob/main/docs/decisions/0024-actions-pin-through-lockfiles.md)).

- The `shell` job runs the shared
  [`Adam-S-Daniel/verify-actions-lock`](https://github.com/Adam-S-Daniel/verify-actions-lock)
  check, which verifies the lockfile only when a pull request touches workflow
  dependencies.
- After changing a `uses:`, regenerate with `gh actions-lock --no-narrow`.
- A moved or unreachable pin: find out why first, then recover with
  `gh actions-lock --accept-moved`.
- `reachability-unknown` means GitHub's API was unreachable; re-run the job.
- Cross-repo reusable workflows (`jobs.<id>.uses`) are not covered by the
  lockfile and take a full 40-character commit SHA.
