#requires -Version 7.6
#requires -RunAsAdministrator
<#
.SYNOPSIS
    Wrapper script for Set-WslAutomationScheduledTasks; installs or updates the ccstatusline
    config sync, Codex Cloud environment sync, usage census publish, waiting feed publish and
    session miner scheduled tasks.

.DESCRIPTION
    Thin wrapper that imports the WslAutomation module and calls
    Set-WslAutomationScheduledTasks with the given parameters. Must be run from an elevated
    (Administrator) PowerShell session. Supports -WhatIf, which is passed straight through to
    Set-WslAutomationScheduledTasks so nothing is actually registered, updated, or renamed
    during a dry run.

.PARAMETER ScriptsDir
    Directory containing sync-ccstatusline-config.ps1, sync-codex-cloud-environments.ps1,
    publish-usage-census.sh, publish-waiting-feed.sh and run-session-miner.sh. Defaults to the
    directory this script lives in.

.PARAMETER DistroName
    Name of the WSL distro the tasks run in. Defaults to 'Ubuntu'.

.PARAMETER CcstatuslineTaskName
    Name of the scheduled task that syncs the ccstatusline config from WSL. Defaults to
    'ccstatusline Config Sync'.

.PARAMETER CcstatuslineIntervalMinutes
    How often, in minutes, the ccstatusline config sync task repeats indefinitely. Defaults to 5.

.PARAMETER CodexCloudEnvironmentSyncTaskName
    Name of the scheduled task that reconciles Codex Cloud environments at midnight and noon.
    Defaults to 'Codex Cloud Environment Sync'.

.PARAMETER UsageCensusTaskName
    Name of the scheduled task that publishes the daily usage census from WSL. Defaults to
    'Usage Census Publish'.

.PARAMETER UsageCensusTime
    Time of day (HH:mm) the usage census task's daily trigger fires. Defaults to '12:37'.

.PARAMETER WaitingFeedTaskName
    Name of the scheduled task that publishes the waiting feed every minute. Defaults to
    'Claude Waiting Feed Publish'.

.PARAMETER SessionMinerTaskName
    Name of the scheduled task that runs the session miner. Defaults to 'Session Miner'.

.PARAMETER SessionMinerTime
    Time of day (HH:mm) the session miner task's daily trigger fires. Defaults to '07:17'.

.PARAMETER WslExePath
    Path to wsl.exe used as the usage census, waiting feed and session miner tasks' action.
    Defaults to %SystemRoot%\System32\wsl.exe.

.PARAMETER PwshPath
    Path to pwsh.exe used as the action executable for the pwsh-based tasks. Defaults to an MSI
    install of PowerShell 7 (C:\Program Files\PowerShell\7\pwsh.exe) when present - required for
    the S4U background ccstatusline and Codex Cloud tasks, which run in session 0 where a
    Store-packaged pwsh cannot launch - then the per-user WindowsApps alias, then the pwsh.exe
    found on PATH.

.PARAMETER LegacyScriptsToArchive
    Paths to legacy scripts that should be renamed out of the way because this module
    supersedes them. Workaround included for invocation via `pwsh -File ... -LegacyScriptsToArchive
    "C:\a.ps1","C:\b.ps1"` from another shell (notably Windows PowerShell 5.1): -File mode
    never re-parses commas, so the two paths can arrive flattened into one literal string
    "C:\a.ps1,C:\b.ps1". Any element that is a comma-joined list of rooted Windows paths (each
    looking like `C:\...` or `\\server\share\...`) is automatically expanded back into separate
    paths before archiving.

.PARAMETER SkipTaskHistory
    Skips enabling the Microsoft-Windows-TaskScheduler/Operational event log (the "History" tab
    in Task Scheduler), which this installer otherwise enables once per run if it is currently
    disabled. A failure to enable it only warns; it never aborts task registration. See
    README.md's "Task history" section for what the log records and how to read it.

.EXAMPLE
    ./register-tasks.ps1

    Registers (or updates) all the scheduled tasks from an elevated prompt, using default names,
    times, and intervals.

.EXAMPLE
    ./register-tasks.ps1 -WhatIf

    Shows what would change without actually registering, updating, or renaming anything.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ScriptsDir = $PSScriptRoot,

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

    # This script runs before Import-Module (the module isn't loaded until the body below), so
    # it cannot call the module's private Get-WslAutomationDefaultPwshPath helper and instead
    # inlines the same MSI-preferring logic. An MSI PowerShell 7 (C:\Program Files\PowerShell\7)
    # is both stable across updates and launchable from the non-interactive session 0 the S4U
    # tasks run in; a Store pwsh (per-user WindowsApps alias, or a version-pinned
    # "C:\Program Files\WindowsApps\Microsoft.PowerShell_<ver>_..." path that is removed on the
    # next package update) cannot be activated in session 0, so it is only a fallback.
    [string]$PwshPath = $(
        $msiInstallPath = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
        $stableAliasPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
        if (Test-Path -LiteralPath $msiInstallPath) {
            $msiInstallPath
        }
        elseif (Test-Path -LiteralPath $stableAliasPath) {
            $stableAliasPath
        }
        else {
            $resolvedPath = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
            $versionPinnedRoot = Join-Path $env:ProgramFiles 'WindowsApps'
            if ($resolvedPath -and $resolvedPath.StartsWith($versionPinnedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                Write-Warning -Message ("Resolved pwsh.exe path '$resolvedPath' is a version-pinned Microsoft Store " +
                    'package path and will break on the next PowerShell update. Pass -PwshPath explicitly with a ' +
                    'stable MSI install path under C:\Program Files\PowerShell\7.')
            }
            $resolvedPath
        }
    ),

    [string[]]$LegacyScriptsToArchive = @(),

    [switch]$SkipTaskHistory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot '..' 'src' 'WslAutomation') -Force

try {
    Set-WslAutomationScheduledTasks -ScriptsDir $ScriptsDir -DistroName $DistroName `
        -CcstatuslineTaskName $CcstatuslineTaskName -CcstatuslineIntervalMinutes $CcstatuslineIntervalMinutes `
        -PwshPath $PwshPath -CodexCloudEnvironmentSyncTaskName $CodexCloudEnvironmentSyncTaskName `
        -UsageCensusTaskName $UsageCensusTaskName -UsageCensusTime $UsageCensusTime `
        -WaitingFeedTaskName $WaitingFeedTaskName -SessionMinerTaskName $SessionMinerTaskName `
        -SessionMinerTime $SessionMinerTime -WslExePath $WslExePath `
        -LegacyScriptsToArchive $LegacyScriptsToArchive -SkipTaskHistory:$SkipTaskHistory -WhatIf:$WhatIfPreference

    exit 0
}
catch {
    Write-Error -ErrorRecord $_
    exit 1
}
