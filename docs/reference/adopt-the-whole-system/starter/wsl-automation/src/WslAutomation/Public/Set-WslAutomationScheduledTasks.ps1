#requires -Version 7.6

function Set-WslAutomationScheduledTasks {
    <#
    .SYNOPSIS
        Registers or updates the scheduled tasks that drive ccstatusline config sync, Codex Cloud
        environment sync, the daily usage census publish, the every-minute waiting feed publish,
        and the daily session miner.

    .DESCRIPTION
        Creates five Windows Scheduled Tasks, or updates them in place if they already exist: a
        ccstatusline config sync task that runs scripts/sync-ccstatusline-config.ps1 on a short
        repeating interval; a Codex Cloud environment sync task that runs
        scripts/sync-codex-cloud-environments.ps1 at midnight and noon while the distro is running;
        a usage census publish task that runs scripts/publish-usage-census.sh inside the distro
        once a day, calling wsl.exe directly so no pwsh is involved; a waiting feed publish task
        that runs scripts/publish-waiting-feed.sh inside the distro every minute, the same way; and
        a session miner task that runs scripts/run-session-miner.sh inside the distro once a day,
        the same way.

        All five run as background S4U tasks (session 0), so they never flash a console window on
        the desktop. The two pwsh-based tasks require an MSI PowerShell 7 and the "Log on as a
        batch job" right - see -PwshPath and scripts/grant-keeper-batch-logon.ps1. The usage
        census, waiting feed and session miner tasks are S4U too, but their action is wsl.exe
        itself, so they need no pwsh at all.

        Every task's Settings and Principal are always (re)built fresh from this function's
        parameters, whether the task already exists or not, so their battery/idle behavior stays
        in sync. Re-running this function is idempotent for all five tasks. None of them sets
        StartWhenAvailable: a missed run stays missed rather than firing the instant the machine
        wakes, inside WSL's post-wake transition window (see AGENTS.md).

        Also archives any legacy scripts passed via -LegacyScriptsToArchive by renaming them
        out of the way, so a stale scheduled task still pointing at an old script path fails
        loudly instead of silently continuing to run superseded automation.

        Windows-only: throws immediately if $IsWindows is false.

    .PARAMETER ScriptsDir
        Directory containing sync-ccstatusline-config.ps1, sync-codex-cloud-environments.ps1,
        publish-usage-census.sh, publish-waiting-feed.sh and run-session-miner.sh.

    .PARAMETER DistroName
        Name of the WSL distro the tasks run in. Defaults to 'Ubuntu'.

    .PARAMETER CcstatuslineTaskName
        Name of the scheduled task that syncs the ccstatusline config from WSL. Defaults to
        'ccstatusline Config Sync'.

    .PARAMETER CcstatuslineIntervalMinutes
        How often, in minutes, the ccstatusline config sync task repeats indefinitely. Defaults
        to 5.

    .PARAMETER CodexCloudEnvironmentSyncTaskName
        Name of the scheduled task that reconciles Codex Cloud environments. Defaults to
        'Codex Cloud Environment Sync'.

    .PARAMETER UsageCensusTaskName
        Name of the scheduled task that publishes the daily usage census from WSL. Defaults to
        'Usage Census Publish'.

    .PARAMETER UsageCensusTime
        Time of day (HH:mm) the usage census task's daily trigger fires. Defaults to '12:37':
        the laptop is usually awake at midday, and :37 is off the hour, clear of other jobs that
        start on it.

    .PARAMETER WaitingFeedTaskName
        Name of the scheduled task that publishes the waiting feed (the Claude Code cloud
        sessions waiting on you) from WSL every minute. Defaults to
        'Claude Waiting Feed Publish'.

    .PARAMETER SessionMinerTaskName
        Name of the scheduled task that runs the session miner from WSL (skills-evals'
        scripts/run_session_miner.sh through scripts/run-session-miner.sh). Defaults to
        'Session Miner'.

    .PARAMETER SessionMinerTime
        Time of day (HH:mm) the session miner task's daily trigger fires. Defaults to '07:17':
        off the hour, and hours away from the usage census, which reads the same transcripts.

    .PARAMETER WslExePath
        Path to wsl.exe used as the usage census, waiting feed and session miner tasks' action.
        Defaults to %SystemRoot%\System32\wsl.exe.

    .PARAMETER PwshPath
        Path to pwsh.exe used as the action executable for the pwsh-based tasks. Defaults to an
        MSI install of PowerShell 7 (C:\Program Files\PowerShell\7) when present - required for
        the S4U ccstatusline and Codex Cloud tasks, which run in session 0 where a Store-packaged
        pwsh cannot launch - then the per-user WindowsApps alias, then the pwsh.exe found on PATH.
        A warning is emitted if a Store-packaged path would be used.

    .PARAMETER LegacyScriptsToArchive
        Paths to legacy scripts that should be renamed out of the way because this module
        supersedes them. Workaround included for invocation via `pwsh -File ... -LegacyScriptsToArchive
        "C:\a.ps1","C:\b.ps1"` from another shell (notably Windows PowerShell 5.1): -File mode
        never re-parses commas, so the two paths can arrive flattened into one literal string
        "C:\a.ps1,C:\b.ps1". Any element that is a comma-joined list of rooted Windows paths
        (each looking like `C:\...` or `\\server\share\...`) is automatically expanded back into
        separate paths before archiving.

    .PARAMETER SkipTaskHistory
        Skips enabling the Microsoft-Windows-TaskScheduler/Operational event log (the "History"
        tab in Task Scheduler), which this function otherwise enables once per run if it is
        currently disabled. A failure to enable it only warns; it never aborts task
        registration. See README.md's "Task history" section for what the log records and how
        to read it.

    .EXAMPLE
        Set-WslAutomationScheduledTasks -ScriptsDir 'C:\Users\<you>\repos\wsl-automation\scripts'

        Registers (or updates) all five scheduled tasks using default names, times and intervals.

    .EXAMPLE
        Set-WslAutomationScheduledTasks -ScriptsDir $PSScriptRoot -WhatIf

        Shows what would change without actually registering, updating, or renaming anything.
    #>
    [System.Diagnostics.CodeAnalysis.SuppressMessage(
        'PSUseSingularNouns',
        '',
        Justification = 'This function manages five related scheduled tasks by design; Set-WslAutomationScheduledTasks is the name specified by the project spec.')]
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [Parameter(Mandatory)]
        [string]$ScriptsDir,

        [string]$DistroName = 'Ubuntu',

        [string]$CcstatuslineTaskName = 'ccstatusline Config Sync',

        [int]$CcstatuslineIntervalMinutes = 5,

        [string]$CodexCloudEnvironmentSyncTaskName = 'Codex Cloud Environment Sync',

        [string]$UsageCensusTaskName = 'Usage Census Publish',

        [string]$UsageCensusTime = '12:37',

        [string]$WaitingFeedTaskName = 'Claude Waiting Feed Publish',

        [string]$SessionMinerTaskName = 'Session Miner',

        [string]$SessionMinerTime = '07:17',

        [string]$WslExePath = (Join-Path $env:SystemRoot 'System32\wsl.exe'),

        [string]$PwshPath = (Get-WslAutomationDefaultPwshPath),

        [string[]]$LegacyScriptsToArchive = @(),

        [switch]$SkipTaskHistory
    )

    if (-not $IsWindows) {
        throw 'Set-WslAutomationScheduledTasks can only run on Windows.'
    }

    # A trailing separator is a common way to type or tab-complete a directory (for example
    # 'C:\Users\<you>\repos\wsl-automation\scripts\'). Embedded raw into a double-quoted native
    # command-line argument, a trailing backslash escapes the closing quote instead of terminating
    # the value, corrupting every argument after it - for the wsl.exe tasks, --exec and the script
    # it runs. Trim defensively for every user-supplied path embedded in a quoted argument string
    # below.
    $ScriptsDir = $ScriptsDir.TrimEnd('\')
    $PwshPath = $PwshPath.TrimEnd('\')

    # A Store-packaged pwsh (per-user WindowsApps alias or version-pinned WindowsApps package
    # path) cannot be activated in the session 0 the S4U pwsh tasks (ccstatusline sync, Codex
    # Cloud sync) run in - the task fails with access denied every interval, silently. Warn
    # loudly so the operator installs PowerShell 7 via MSI (see the README and
    # scripts/grant-keeper-batch-logon.ps1) instead.
    $windowsAppsRoots = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'),
        (Join-Path $env:ProgramFiles 'WindowsApps')
    )
    foreach ($windowsAppsRoot in $windowsAppsRoots) {
        if ($PwshPath -and $PwshPath.StartsWith($windowsAppsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Warning ("Scheduled task pwsh '$PwshPath' is a Store-packaged path; a Store pwsh cannot launch in the " +
                'non-interactive session 0 the S4U tasks use, so they would fail every interval. Install ' +
                'PowerShell 7 via MSI (C:\Program Files\PowerShell\7) and re-run, or pass -PwshPath to a non-Store pwsh.')
            break
        }
    }

    # --- ccstatusline task: background config sync -------------------------
    # Purely background work (reads the ccstatusline config out of WSL) that never needs a window,
    # so it runs as an S4U/session-0 task. Otherwise it would flash its own pwsh console every
    # interval. -WindowStyle Hidden is not used: Task Scheduler still creates the pwsh console
    # window before pwsh can hide it, so it only shrinks the flash; S4U ("run whether the user is
    # logged on or not", no stored password) runs in session 0, which has no desktop to draw one
    # on. Session-0 requirements: an MSI pwsh (see the warning above) and the batch-logon right.
    $ccstatuslineScriptPath = Join-Path $ScriptsDir 'sync-ccstatusline-config.ps1'
    $ccstatuslineArguments = "-NoProfile -File `"$ccstatuslineScriptPath`" -DistroName $DistroName"
    $ccstatuslineAction = New-ScheduledTaskAction -Execute $PwshPath -Argument $ccstatuslineArguments

    # -RepetitionInterval at creation time is the construction pwsh 7.6's ScheduledTasks module
    # actually supports for an indefinitely-repeating trigger: a -Once trigger built without it
    # has Repetition = $null (mutating .Repetition.Interval on it afterward throws "The property
    # 'Interval' cannot be found on this object"), and separately assigning Repetition.Duration =
    # '' is rejected by the Task Scheduler XML validator at registration time. Leaving
    # -RepetitionDuration unset here is required, not incidental - it is what makes the
    # repetition indefinite.
    $ccstatuslineTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $CcstatuslineIntervalMinutes)

    # Settings/Principal are always rebuilt fresh for the ccstatusline task too, regardless of
    # whether it already exists, so its battery/idle behavior always matches this function's
    # defaults.
    $ccstatuslineSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
    $ccstatuslinePrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

    $existingCcstatuslineTask = Get-ScheduledTask -TaskName $CcstatuslineTaskName -ErrorAction SilentlyContinue
    if ($existingCcstatuslineTask) {
        if ($PSCmdlet.ShouldProcess($CcstatuslineTaskName, 'Update scheduled task')) {
            Set-WslScheduledTask -TaskName $CcstatuslineTaskName -Action $ccstatuslineAction -Trigger $ccstatuslineTrigger `
                -Settings $ccstatuslineSettings -Principal $ccstatuslinePrincipal
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess($CcstatuslineTaskName, 'Register scheduled task')) {
            Register-WslScheduledTask -TaskName $CcstatuslineTaskName -Action $ccstatuslineAction -Trigger $ccstatuslineTrigger `
                -Settings $ccstatuslineSettings -Principal $ccstatuslinePrincipal
        }
    }

    # --- Codex Cloud environment sync: exactly two daily triggers ---------
    # This S4U task is background-only like the ccstatusline sync. It deliberately
    # has no StartWhenAvailable setting: it preserves the exact noon/midnight attempts and a
    # missed time stays missed. The wrapper checks state but never starts WSL.
    $codexCloudSyncScriptPath = Join-Path $ScriptsDir 'sync-codex-cloud-environments.ps1'
    $codexCloudSyncArguments = "-NoProfile -File `"$codexCloudSyncScriptPath`" -DistroName $DistroName"
    $codexCloudSyncAction = New-ScheduledTaskAction -Execute $PwshPath -Argument $codexCloudSyncArguments
    $codexCloudSyncTriggers = @(
        (New-ScheduledTaskTrigger -Daily -At '00:00'),
        (New-ScheduledTaskTrigger -Daily -At '12:00')
    )
    $codexCloudSyncSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
    $codexCloudSyncPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

    $existingCodexCloudSyncTask = Get-ScheduledTask -TaskName $CodexCloudEnvironmentSyncTaskName -ErrorAction SilentlyContinue
    if ($existingCodexCloudSyncTask) {
        if ($PSCmdlet.ShouldProcess($CodexCloudEnvironmentSyncTaskName, 'Update scheduled task')) {
            Set-WslScheduledTask -TaskName $CodexCloudEnvironmentSyncTaskName -Action $codexCloudSyncAction -Trigger $codexCloudSyncTriggers `
                -Settings $codexCloudSyncSettings -Principal $codexCloudSyncPrincipal
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess($CodexCloudEnvironmentSyncTaskName, 'Register scheduled task')) {
            Register-WslScheduledTask -TaskName $CodexCloudEnvironmentSyncTaskName -Action $codexCloudSyncAction -Trigger $codexCloudSyncTriggers `
                -Settings $codexCloudSyncSettings -Principal $codexCloudSyncPrincipal
        }
    }

    # --- Usage census publish: one daily trigger, wsl.exe as the action -----
    # Background-only like the Codex Cloud sync, but the action is wsl.exe DIRECTLY (no pwsh in
    # between) with --cd into the scripts directory and bash running the wrapper. As an S4U task
    # it runs in session 0, so no console window can ever appear. The owner's WSL git
    # authenticates through `gh auth git-credential` with a file-stored token, which works in an
    # S4U logon. There is deliberately no StartWhenAvailable: a missed day stays missed (the
    # census's 14-day freshness window and 6-day re-publish absorb it), and a catch-up at wake
    # would land in WSL's post-wake transition window (see AGENTS.md). $ScriptsDir was trimmed of
    # any trailing backslash above, which would otherwise escape the closing quote.
    $usageCensusArguments = "--distribution $DistroName --cd `"$ScriptsDir`" --exec /bin/bash ./publish-usage-census.sh"
    $usageCensusAction = New-ScheduledTaskAction -Execute $WslExePath -Argument $usageCensusArguments
    $usageCensusTrigger = New-ScheduledTaskTrigger -Daily -At $UsageCensusTime
    $usageCensusSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
    $usageCensusPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

    $existingUsageCensusTask = Get-ScheduledTask -TaskName $UsageCensusTaskName -ErrorAction SilentlyContinue
    if ($existingUsageCensusTask) {
        if ($PSCmdlet.ShouldProcess($UsageCensusTaskName, 'Update scheduled task')) {
            Set-WslScheduledTask -TaskName $UsageCensusTaskName -Action $usageCensusAction -Trigger $usageCensusTrigger `
                -Settings $usageCensusSettings -Principal $usageCensusPrincipal
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess($UsageCensusTaskName, 'Register scheduled task')) {
            Register-WslScheduledTask -TaskName $UsageCensusTaskName -Action $usageCensusAction -Trigger $usageCensusTrigger `
                -Settings $usageCensusSettings -Principal $usageCensusPrincipal
        }
    }

    # --- Waiting feed publish: every minute, wsl.exe as the action ----------
    # Same shape as the usage census (wsl.exe directly, S4U, session 0), but repeating every
    # minute with the indefinite-repetition construction the ccstatusline trigger documents above
    # (-Once + creation-time -RepetitionInterval, no -RepetitionDuration). The script is cheap
    # when nothing changed, so a 5 minute limit is generous; IgnoreNew keeps a slow run (a hung
    # push) from stacking up behind itself. No StartWhenAvailable: the feed is a live view, so a
    # missed minute is just skipped.
    $waitingFeedArguments = "--distribution $DistroName --cd `"$ScriptsDir`" --exec /bin/bash ./publish-waiting-feed.sh"
    $waitingFeedAction = New-ScheduledTaskAction -Execute $WslExePath -Argument $waitingFeedArguments
    $waitingFeedTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 1)
    $waitingFeedSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
    $waitingFeedPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

    $existingWaitingFeedTask = Get-ScheduledTask -TaskName $WaitingFeedTaskName -ErrorAction SilentlyContinue
    if ($existingWaitingFeedTask) {
        if ($PSCmdlet.ShouldProcess($WaitingFeedTaskName, 'Update scheduled task')) {
            Set-WslScheduledTask -TaskName $WaitingFeedTaskName -Action $waitingFeedAction -Trigger $waitingFeedTrigger `
                -Settings $waitingFeedSettings -Principal $waitingFeedPrincipal
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess($WaitingFeedTaskName, 'Register scheduled task')) {
            Register-WslScheduledTask -TaskName $WaitingFeedTaskName -Action $waitingFeedAction -Trigger $waitingFeedTrigger `
                -Settings $waitingFeedSettings -Principal $waitingFeedPrincipal
        }
    }

    # --- Session miner: one daily trigger, wsl.exe as the action ------------
    # Same shape as the usage census (wsl.exe directly, S4U, session 0, no StartWhenAvailable: a
    # missed day stays missed, and the next run reads every session since the last one, so
    # nothing is lost). The wrapper clones skills-evals main and runs that copy's
    # scripts/run_session_miner.sh, which reads this machine's Claude Code transcripts and pushes
    # lesson records to the private registry's persistent/lessons branch. It refuses a scheduled
    # run until a --dry-run has been made by hand (see README.md), so registering the task before
    # setup is safe. A 30 minute limit covers a first run, which reads every session on the
    # machine; IgnoreNew keeps a slow run from doubling up.
    $sessionMinerArguments = "--distribution $DistroName --cd `"$ScriptsDir`" --exec /bin/bash ./run-session-miner.sh"
    $sessionMinerAction = New-ScheduledTaskAction -Execute $WslExePath -Argument $sessionMinerArguments
    $sessionMinerTrigger = New-ScheduledTaskTrigger -Daily -At $SessionMinerTime
    $sessionMinerSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    $sessionMinerPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

    $existingSessionMinerTask = Get-ScheduledTask -TaskName $SessionMinerTaskName -ErrorAction SilentlyContinue
    if ($existingSessionMinerTask) {
        if ($PSCmdlet.ShouldProcess($SessionMinerTaskName, 'Update scheduled task')) {
            Set-WslScheduledTask -TaskName $SessionMinerTaskName -Action $sessionMinerAction -Trigger $sessionMinerTrigger `
                -Settings $sessionMinerSettings -Principal $sessionMinerPrincipal
        }
    }
    else {
        if ($PSCmdlet.ShouldProcess($SessionMinerTaskName, 'Register scheduled task')) {
            Register-WslScheduledTask -TaskName $SessionMinerTaskName -Action $sessionMinerAction -Trigger $sessionMinerTrigger `
                -Settings $sessionMinerSettings -Principal $sessionMinerPrincipal
        }
    }

    # --- Archive legacy scripts this module supersedes ----------------------
    $archiveTimestamp = Get-Date -Format 'yyyyMMdd'

    # When register-tasks.ps1 is invoked as `pwsh -File ... -LegacyScriptsToArchive
    # "C:\a.ps1","C:\b.ps1"` from another shell (notably Windows PowerShell 5.1), native-command
    # argument handling flattens the two array elements into ONE literal string
    # "C:\a.ps1,C:\b.ps1" before pwsh's -File mode ever sees it - -File never re-parses commas
    # into an array the way a real PowerShell-to-PowerShell call would. Test-Path then fails on
    # the joined string and archiving silently does nothing. Work around it by expanding any
    # element that is a comma-joined list of rooted Windows paths back into its parts; an element
    # without a comma, or whose comma-split fragments don't all look like rooted paths, is left
    # alone (a legitimate single path may itself contain a comma).
    $expandedLegacyScriptsToArchive = foreach ($rawLegacyPath in $LegacyScriptsToArchive) {
        if ($rawLegacyPath -notlike '*,*') {
            $rawLegacyPath
            continue
        }

        $legacyPathFragments = $rawLegacyPath -split ','
        $anyFragmentNotRooted = $legacyPathFragments | Where-Object {
            $_ -notmatch '^[A-Za-z]:[\\/]' -and $_ -notmatch '^\\\\'
        }

        if ($anyFragmentNotRooted) {
            $rawLegacyPath
        }
        else {
            $legacyPathFragments
        }
    }

    foreach ($legacyPath in $expandedLegacyScriptsToArchive) {
        if (-not (Test-Path -LiteralPath $legacyPath)) {
            $legacyLeafForMissingCheck = Split-Path -Path $legacyPath -Leaf
            $legacyParentForMissingCheck = Split-Path -Path $legacyPath -Parent
            $supersededSiblingPattern = "$legacyLeafForMissingCheck.superseded-*"
            $supersededSiblingPath = if ($legacyParentForMissingCheck) {
                Join-Path $legacyParentForMissingCheck $supersededSiblingPattern
            }
            else {
                $supersededSiblingPattern
            }

            # An idempotent re-run naturally hits this: a previous run already renamed the
            # legacy script out of the way, so it no longer exists at its original path. Only
            # warn when there's no evidence that already happened.
            if (-not (Test-Path -Path $supersededSiblingPath)) {
                Write-Warning "Legacy script not found, nothing archived: $legacyPath"
            }
            continue
        }

        $archiveName = (Split-Path -Path $legacyPath -Leaf) + ".superseded-$archiveTimestamp"
        $legacyParent = Split-Path -Path $legacyPath -Parent
        $archivePath = if ($legacyParent) { Join-Path $legacyParent $archiveName } else { $archiveName }

        if (Test-Path -LiteralPath $archivePath) {
            Write-Warning "Archive target already exists, skipping: $archivePath"
            continue
        }

        if ($PSCmdlet.ShouldProcess($legacyPath, "Rename to $archiveName")) {
            Rename-Item -LiteralPath $legacyPath -NewName $archiveName
        }
    }

    # --- Task Scheduler operational log (History tab) ----------------------
    # Disabled on a fresh Windows install. Without it a scheduled task leaves only its last
    # result code behind - no start/finish times, no per-run exit code, no record of a run that
    # was terminated at its execution time limit (see AGENTS.md's interactive-prompt section).
    # This is best-effort and never fatal: every task above has already been registered or
    # updated by the time this runs, so a failure here only warns.
    if ($SkipTaskHistory) {
        $taskHistoryResult = 'Skipped (-SkipTaskHistory)'
    }
    else {
        try {
            $taskHistoryResult = Enable-TaskSchedulerHistory -WhatIf:$WhatIfPreference -Confirm:$false
        }
        catch {
            Write-Warning "Failed to enable Task Scheduler history (Microsoft-Windows-TaskScheduler/Operational): $_"
            $taskHistoryResult = 'Failed'
        }
    }

    # --- Summary -------------------------------------------------------------
    Write-Information -MessageData "ccstatusline task '$CcstatuslineTaskName' (background/S4U): $ccstatuslineArguments (repeats every $CcstatuslineIntervalMinutes min, indefinitely)" -InformationAction Continue
    Write-Information -MessageData "Codex Cloud task '$CodexCloudEnvironmentSyncTaskName' (background/S4U): $codexCloudSyncArguments (daily at 00:00 and 12:00)" -InformationAction Continue
    Write-Information -MessageData "Usage census task '$UsageCensusTaskName' (background/S4U): $WslExePath $usageCensusArguments (daily at $UsageCensusTime)" -InformationAction Continue
    Write-Information -MessageData "Waiting feed task '$WaitingFeedTaskName' (background/S4U): $WslExePath $waitingFeedArguments (repeats every 1 min, indefinitely)" -InformationAction Continue
    Write-Information -MessageData "Session miner task '$SessionMinerTaskName' (background/S4U): $WslExePath $sessionMinerArguments (daily at $SessionMinerTime)" -InformationAction Continue
    Write-Information -MessageData "Task Scheduler history (Microsoft-Windows-TaskScheduler/Operational): $taskHistoryResult" -InformationAction Continue
}
