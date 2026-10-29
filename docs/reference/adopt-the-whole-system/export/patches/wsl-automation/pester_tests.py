"""X10 in the Pester tests, and the tests of the kit's session miner task.

tests/Tasks.Tests.ps1
  - The -BackupDir/-ResticRepoDir fixture and the same two arguments on every
    Set-WslAutomationScheduledTasks call go (the parameters are gone).
  - Removed, because their subject is left out (X10): the backup task's Its
    (action, -Format, -ResticRepoDir, daily trigger, registration, WakeToRun,
    StartWhenAvailable, battery, limit, repetition, the two trailing-backslash
    Its) and its "already exists" Context; the keeper's Its (action,
    registration, interval); both launchers' Its, -CodexLauncherTaskName, and
    the Codex launcher's "already exists" Context.
  - Changed to the five tasks that remain: the S4U count (six -> five, and
    the census principal's count with it), the no-WindowStyle check (the
    ccstatusline action only), the interactive-principal check (two -> none;
    the census task stands in for the backup as the S4U-for-the-current-user
    example), the history failure check (it names the census and ccstatusline
    tasks instead of the backup and keeper).
  - The AST bootstrap test discovers every `--exec /bin/bash ./<entry>.sh`
    action instead of naming the census and the waiting feed, so the session
    miner (and any later wsl.exe task) is covered without a list to update;
    its counts become five actions, five scripts.
  - Added for the session miner: registration shape, parameter overrides,
    trailing-backslash sanitizing, and the "already exists" Context.

tests/Helpers.Tests.ps1
  Removed: the Describes for the backup lock (New-, Test-, Remove-WslBackupLock)
  and the two launcher argument builders, whose files are left out.

tests/RepoUpdate.Tests.ps1
  'Existing task entries fail open after checkout warnings' keeps its
  ccstatusline and Codex Cloud rows; the keeper and manual-backup entries,
  their mocks and parameters, and the keeper's dirty-branch It go.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("kit_wsl_text", Path(__file__).with_name("_text.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

TASKS = "tests/Tasks.Tests.ps1"
HELPERS = "tests/Helpers.Tests.ps1"
REPO_UPDATE = "tests/RepoUpdate.Tests.ps1"

DROPPED_ITS = [
    "It 'creates the backup task with a wsl-restic-backup.ps1 action",
    "It 'no longer schedules the wsl --export backup or passes -Format'",
    "It 'requires -ResticRepoDir'",
    "It 'creates the keeper task with an action",
    "It 'creates exactly one daily trigger for the backup task and no logon trigger'",
    "It 'registers the backup task via Register-WslScheduledTask",
    "It 'registers the keeper task via Register-WslScheduledTask",
    "It 'registers the interactive launcher task",
    "It 'registers the interactive Codex remote control launcher task",
    "It 'honors -CodexLauncherTaskName'",
    "It 'configures the keeper task repetition interval",
    "It 'registers the backup task without -WakeToRun by default",
    "It 'registers the backup task with -WakeToRun when -WakeBackupToRun is set'",
    "It 'registers the backup task with StartWhenAvailable = $false",
    "It 'registers the backup task to run on battery",
    "It 'registers the backup task with a 2 hour limit",
    "It 'builds the backup trigger with Repetition.Interval PT1H",
    "It 'builds the backup trigger repetition interval from",
    "It 'sanitizes a trailing backslash in BackupDir",
    "It 'sanitizes a trailing backslash in ResticRepoDir",
]
DROPPED_CONTEXTS = [
    "Context 'when the backup task already exists'",
    "Context 'when the Codex remote control launcher task already exists'",
]

FIXTURE_OLD = """        $script:scriptsDir = Join-Path $TestDrive 'scripts'
        $script:backupDir = Join-Path $TestDrive 'backups'
        $script:resticRepoDir = 'D:\\Backups\\wsl-restic'
        New-Item -ItemType Directory -Path $script:scriptsDir -Force | Out-Null
        New-Item -ItemType Directory -Path $script:backupDir -Force | Out-Null
"""
FIXTURE_NEW = """        $script:scriptsDir = Join-Path $TestDrive 'scripts'
        New-Item -ItemType Directory -Path $script:scriptsDir -Force | Out-Null
"""

ARGS_OLD = " -BackupDir $script:backupDir -ResticRepoDir $script:resticRepoDir `\n"
ARGS_NEW = " `\n"

S4U_OLD = """        It 'runs all six background tasks as S4U in session 0, where no desktop window can flash' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            # The backup, keeper, ccstatusline sync, Codex Cloud sync, usage census, and waiting feed
            # publish are windowless background tasks.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 6 -Exactly -ParameterFilter {
"""
S4U_NEW = """        It 'runs all five background tasks as S4U in session 0, where no desktop window can flash' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            # The ccstatusline sync, Codex Cloud sync, usage census, waiting feed publish and
            # session miner are windowless background tasks.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 5 -Exactly -ParameterFilter {
"""

WINDOWSTYLE_OLD = """            $keeperAction = $script:capturedActions | Where-Object { $_.Argument -match 'ensure-claude-session\\.ps1' }
            $keeperAction.Argument | Should -Not -Match 'WindowStyle'
            $ccstatuslineAction"""
WINDOWSTYLE_NEW = """            $ccstatuslineAction"""

INTERACTIVE_OLD_START = "        It 'runs only the two launchers interactively; the backup task is S4U for the current user, never SYSTEM' {\n"
INTERACTIVE_OLD_END = "        It 'creates the ccstatusline sync task with an action"
INTERACTIVE_NEW = """        It 'runs no task interactively; the usage census task is S4U for the current user, never SYSTEM' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            # Every task runs in the background; none needs to show a window.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 0 -Exactly -ParameterFilter {
                $LogonType -eq 'Interactive'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish' -and
                $Principal.LogonType -eq 'S4U' -and $Principal.UserId -eq "$env:USERDOMAIN\\$env:USERNAME"
            }
        }

"""

CENSUS_PRINCIPAL_OLD = """            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 6 -Exactly -ParameterFilter {
                $LogonType -eq 'S4U' -and $UserId -eq "$env:USERDOMAIN\\$env:USERNAME"
"""
CENSUS_PRINCIPAL_NEW = """            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 5 -Exactly -ParameterFilter {
                $LogonType -eq 'S4U' -and $UserId -eq "$env:USERDOMAIN\\$env:USERNAME"
"""

MINER_ITS_ANCHOR = "        It 'sanitizes a trailing backslash in ScriptsDir so the waiting feed --cd argument stays well-formed' {\n"
MINER_ITS = """        It 'registers the session miner task as a direct wsl.exe action with one daily trigger, S4U, a 30 minute limit, and no catch-up setting' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner' -and
                $Action.Execute -like '*wsl.exe' -and
                @($Trigger).Count -eq 1 -and $Trigger.IsDaily -and $Trigger.At -eq '07:17' -and
                $Principal.FakePrincipal -and $Principal.LogonType -eq 'S4U' -and
                $Principal.UserId -eq "$env:USERDOMAIN\\$env:USERNAME" -and
                $Settings.ExecutionTimeLimit.TotalMinutes -eq 30 -and $Settings.MultipleInstances -eq 'IgnoreNew' -and
                $Settings.DisallowStartIfOnBatteries -eq $false -and $Settings.StopIfGoingOnBatteries -eq $false -and
                -not $Settings.StartWhenAvailable
            }

            $minerAction = @($script:capturedActions | Where-Object { $_.Argument -match 'run-session-miner\\.sh' })
            $minerAction.Count | Should -Be 1
            $minerAction[0].Argument | Should -Match '--distribution Ubuntu'
            $minerAction[0].Argument.Contains('--cd "' + $script:scriptsDir + '"') | Should -BeTrue
            $minerAction[0].Argument | Should -Match '--exec /bin/bash \\./run-session-miner\\.sh$'
            $minerAction[0].Argument | Should -Not -Match 'pwsh'
        }

        It 'honors -SessionMinerTime, -SessionMinerTaskName, -WslExePath and -DistroName for the session miner task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -DistroName 'Debian' -SessionMinerTime '05:43' `
                -SessionMinerTaskName 'Custom Miner' -WslExePath 'C:\\fake\\wsl.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Custom Miner' -and
                $Action.Execute -eq 'C:\\fake\\wsl.exe' -and
                $Action.Argument -match '--distribution Debian' -and
                $Action.Argument -match 'run-session-miner\\.sh' -and
                $Trigger.At -eq '05:43'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner'
            }
        }

        It 'sanitizes a trailing backslash in ScriptsDir so the session miner --cd argument stays well-formed' {
            Set-WslAutomationScheduledTasks -ScriptsDir ($script:scriptsDir + '\\') `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            $minerAction = $script:capturedActions | Where-Object { $_.Argument -match 'run-session-miner\\.sh' }

            # A raw trailing backslash before the closing quote would escape it and swallow --exec.
            $minerAction.Argument.Contains('--cd "' + $script:scriptsDir + '" --exec') | Should -BeTrue
            $minerAction.Argument | Should -Not -Match '\\\\"'
        }

"""

MINER_CONTEXT_ANCHOR = "    Context 'legacy script archiving' {\n"
MINER_CONTEXT = """    Context 'when the session miner task already exists' {

        BeforeEach {
            Mock -ModuleName WslAutomation Get-ScheduledTask {
                if ($TaskName -eq 'Session Miner') {
                    return [pscustomobject]@{ TaskName = 'Session Miner' }
                }
                return $null
            }
        }

        It 'rebuilds settings and principal while replacing the action and trigger in place, and does not create a second one' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\\fake\\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner' -and
                $Action.Argument -match 'run-session-miner\\.sh' -and
                $Trigger.IsDaily -and $Settings.FakeSettings -and $Principal.FakePrincipal
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner'
            }
        }
    }

"""

HISTORY_OLD = """            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'WSL Ubuntu Daily Backup'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Code Session Keeper'
            }
"""
HISTORY_NEW = """            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'ccstatusline Config Sync'
            }
"""

DISCOVERY_OLD = """                if ($expanded.Value.EndsWith('--exec /bin/bash ./publish-usage-census.sh')) { 'publish-usage-census.sh' }
                if ($expanded.Value.EndsWith('--exec /bin/bash ./publish-waiting-feed.sh')) { 'publish-waiting-feed.sh' }
"""
DISCOVERY_NEW = """                # Every wsl.exe action runs one Bash entry from the scripts directory; discover it by
                # shape, so a wsl.exe task added later is covered without editing this test.
                if ($expanded.Value -match '--exec /bin/bash \\./([A-Za-z0-9._-]+\\.sh)$') { $Matches[1] }
"""

COVERS_OLD = """    It 'covers all six script actions among eight registered task actions' {
        $script:actionCount | Should -Be 8
        $script:actionScripts.Count | Should -Be 6
        @($script:actionScripts | Sort-Object) | Should -Be @(
            'ensure-claude-session.ps1', 'publish-usage-census.sh', 'publish-waiting-feed.sh',
            'sync-ccstatusline-config.ps1', 'sync-codex-cloud-environments.ps1', 'wsl-restic-backup.ps1'
        )
"""
COVERS_NEW = """    It 'covers all five script actions among five registered task actions' {
        $script:actionCount | Should -Be 5
        $script:actionScripts.Count | Should -Be 5
        @($script:actionScripts | Sort-Object) | Should -Be @(
            'publish-usage-census.sh', 'publish-waiting-feed.sh', 'run-session-miner.sh',
            'sync-ccstatusline-config.ps1', 'sync-codex-cloud-environments.ps1'
        )
"""

FOREACH_OLD = """        @{ Entry = 'ensure-claude-session.ps1'; Work = 'Invoke-ClaudeSessionKeeper' },
        @{ Entry = 'sync-ccstatusline-config.ps1'; Work = 'Update-CcstatuslineConfig' },
        @{ Entry = 'sync-codex-cloud-environments.ps1'; Work = 'Invoke-CodexCloudEnvironmentSync' },
        @{ Entry = 'wsl-restic-backup.ps1'; Work = 'Invoke-WslResticBackup' }
"""
FOREACH_NEW = """        @{ Entry = 'sync-ccstatusline-config.ps1'; Work = 'Update-CcstatuslineConfig' },
        @{ Entry = 'sync-codex-cloud-environments.ps1'; Work = 'Invoke-CodexCloudEnvironmentSync' }
"""


def _tasks(text: str, where: str) -> str:
    for opener in DROPPED_ITS:
        text = T.drop_block(text, opener, where, " " * 8)
    for opener in DROPPED_CONTEXTS:
        text = T.drop_block(text, opener, where, " " * 4)
    text = T.sub_once(text, FIXTURE_OLD, FIXTURE_NEW, where)
    n = text.count(ARGS_OLD)
    if n != 32:
        raise T.PatchError(f"{where}: expected 32 -BackupDir/-ResticRepoDir argument lines after the removals, found {n}")
    text = text.replace(ARGS_OLD, ARGS_NEW)
    text = T.sub_once(text, S4U_OLD, S4U_NEW, where)
    text = T.sub_once(text, WINDOWSTYLE_OLD, WINDOWSTYLE_NEW, where)
    text = T.replace_between(text, INTERACTIVE_OLD_START, INTERACTIVE_OLD_END, INTERACTIVE_NEW, where)
    text = T.sub_once(text, CENSUS_PRINCIPAL_OLD, CENSUS_PRINCIPAL_NEW, where)
    text = T.sub_once(text, MINER_ITS_ANCHOR, MINER_ITS + MINER_ITS_ANCHOR, where)
    text = T.sub_once(text, MINER_CONTEXT_ANCHOR, MINER_CONTEXT + MINER_CONTEXT_ANCHOR, where)
    text = T.sub_once(text, HISTORY_OLD, HISTORY_NEW, where)
    text = T.sub_once(text, DISCOVERY_OLD, DISCOVERY_NEW, where)
    text = T.sub_once(text, COVERS_OLD, COVERS_NEW, where)
    text = T.sub_once(text, FOREACH_OLD, FOREACH_NEW, where)
    for gone in ("BackupDir", "ResticRepoDir", "backupDir", "resticRepoDir", "Keeper", "keeper",
                 "Launcher", "wt.exe", "restic", "WakeBackupToRun", "WSL Ubuntu Daily Backup"):
        if gone in text:
            raise T.PatchError(f"{where}: {gone!r} is still mentioned")
    return text


def _helpers(text: str, where: str) -> str:
    for name in ("New-WslBackupLock", "Test-WslBackupLock", "Remove-WslBackupLock",
                 "Get-ClaudeSessionWtArgumentList", "Get-CodexRemoteControlWtArgumentList"):
        text = T.drop_block(text, f"Describe '{name}' {{", where, " " * 4)
    for gone in ("BackupLock", "WtArgumentList"):
        if gone in text:
            raise T.PatchError(f"{where}: {gone!r} is still mentioned")
    return text


def _repo_update(text: str, where: str) -> str:
    text = T.sub_once(
        text,
        "foreach ($entry in @('ensure-claude-session.ps1', 'sync-ccstatusline-config.ps1', 'sync-codex-cloud-environments.ps1', 'wsl-ubuntu-backup.ps1')) {",
        "foreach ($entry in @('sync-ccstatusline-config.ps1', 'sync-codex-cloud-environments.ps1')) {",
        where,
    )
    text = T.sub_once(
        text,
        "        # No task may reach any real distro, session launcher, config file, or backup.\n"
        "        Mock Invoke-ClaudeSessionKeeper { [pscustomobject]@{ Status = 'AlreadyRunning'; WaitedSeconds = 0; ResumedSessionCount = 0; CodexRemoteControlLaunched = $false } }\n",
        "        # No task may reach any real distro, config file, or Codex Cloud environment.\n",
        where,
    )
    text = T.sub_once(text, "        Mock Invoke-WslBackup { [pscustomobject]@{ Status = 'Deferred'; FilePath = '' } }\n", "", where)
    text = T.sub_once(
        text,
        "        @{ Entry = 'ensure-claude-session.ps1'; Work = 'Invoke-ClaudeSessionKeeper' },\n"
        "        @{ Entry = 'sync-ccstatusline-config.ps1'; Work = 'Update-CcstatuslineConfig' },\n"
        "        @{ Entry = 'sync-codex-cloud-environments.ps1'; Work = 'Invoke-CodexCloudEnvironmentSync' },\n"
        "        @{ Entry = 'wsl-ubuntu-backup.ps1'; Work = 'Invoke-WslBackup' }\n",
        "        @{ Entry = 'sync-ccstatusline-config.ps1'; Work = 'Update-CcstatuslineConfig' },\n"
        "        @{ Entry = 'sync-codex-cloud-environments.ps1'; Work = 'Invoke-CodexCloudEnvironmentSync' }\n",
        where,
    )
    text = T.sub_once(
        text,
        "        if ($Entry -eq 'wsl-ubuntu-backup.ps1') { $parameters.BackupDir = 'C:\\example'; $parameters.NoPause = $true }\n",
        "",
        where,
    )
    text = T.drop_block(text, "It 'continues the keeper on a dirty feature branch without switching or merging' {", where, " " * 4)
    for gone in ("ensure-claude-session", "wsl-ubuntu-backup", "Invoke-ClaudeSessionKeeper", "Invoke-WslBackup"):
        if gone in text:
            raise T.PatchError(f"{where}: {gone!r} is still mentioned")
    return text


def apply(root: Path, ctx) -> None:
    T.edit_file(root, TASKS, _tasks)
    T.edit_file(root, HELPERS, _helpers)
    T.edit_file(root, REPO_UPDATE, _repo_update)
    for rel in (TASKS, HELPERS, REPO_UPDATE):
        T.check_ascii(root, rel)
