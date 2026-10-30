#requires -Version 7.6

BeforeAll {
    Import-Module (Join-Path $PSScriptRoot '..' 'src' 'WslAutomation') -Force
}

Describe 'Set-WslAutomationScheduledTasks' -Skip:(-not $IsWindows) {

    BeforeEach {
        # Absolute safety net: no test in this file may ever reach a real wsl.exe.
        Mock -ModuleName WslAutomation Invoke-WslExe {
            [pscustomobject]@{ ExitCode = 0; Output = @() }
        }

        $script:scriptsDir = Join-Path $TestDrive 'scripts'
        New-Item -ItemType Directory -Path $script:scriptsDir -Force | Out-Null

        $script:capturedActions = @()
        $script:capturedTriggers = @()

        # None of these real Task Scheduler / rename cmdlets may ever execute for real from
        # this test file: every one of them is mocked at module scope so nothing here can
        # touch the actual Windows Task Scheduler, no matter what the implementation does.
        Mock -ModuleName WslAutomation New-ScheduledTaskAction {
            $action = [pscustomobject]@{ Execute = $Execute; Argument = $Argument }
            $script:capturedActions += $action
            return $action
        }

        Mock -ModuleName WslAutomation New-ScheduledTaskTrigger {
            # Mirror the real ScheduledTasks contract: a trigger's Repetition property is $null
            # unless -RepetitionInterval was actually passed to New-ScheduledTaskTrigger (a
            # -Once trigger created without it has Repetition = $null, and mutating .Interval on
            # a null Repetition throws). Only -RepetitionInterval-based construction is
            # supported now - the implementation no longer mutates .Repetition after creation.
            $repetition = $null
            if ($RepetitionInterval) {
                $repetition = [pscustomobject]@{
                    Interval = "PT$([int]$RepetitionInterval.TotalMinutes)M"
                    Duration = if ($RepetitionDuration) { "PT$([int]$RepetitionDuration.TotalMinutes)M" } else { $null }
                }
            }
            $trigger = [pscustomobject]@{
                IsDaily    = [bool]$Daily
                IsOnce     = [bool]$Once
                IsAtLogOn  = [bool]$AtLogOn
                At         = $At
                Repetition = $repetition
            }
            $script:capturedTriggers += $trigger
            return $trigger
        }

        Mock -ModuleName WslAutomation New-ScheduledTaskSettingsSet {
            # Mirrors the real contract for the battery flags: DisallowStartIfOnBatteries and
            # StopIfGoingOnBatteries default to $true and only -AllowStartIfOnBatteries /
            # -DontStopIfGoingOnBatteries turn them off, so a task's battery behavior is
            # observable on the Settings object it is registered with.
            [pscustomobject]@{
                FakeSettings               = $true
                DisallowStartIfOnBatteries = -not $AllowStartIfOnBatteries
                StopIfGoingOnBatteries     = -not $DontStopIfGoingOnBatteries
                StartWhenAvailable         = [bool]$StartWhenAvailable
                ExecutionTimeLimit         = $ExecutionTimeLimit
                MultipleInstances          = $MultipleInstances
                WakeToRun                  = [bool]$WakeToRun
            }
        }

        Mock -ModuleName WslAutomation New-ScheduledTaskPrincipal {
            [pscustomobject]@{ FakePrincipal = $true; UserId = $UserId; LogonType = $LogonType }
        }

        # Register-/Set-ScheduledTask are real cmdletization (CDXML) functions on Windows whose
        # -Action/-Trigger/-Settings/-Principal parameters are strongly typed to CimInstance;
        # their dynamic-parameter binding does not mock reliably under Pester 5.7.1 even with
        # -RemoveParameterType (it works under 6.0.0 only). The module instead calls private
        # Register-WslScheduledTask / Set-WslScheduledTask wrapper functions (plain, untyped
        # PowerShell functions - see src/WslAutomation/Private) so those can be mocked directly,
        # consistently, under both Pester versions. The real CDXML cmdlets are still mocked here
        # too, purely as a defensive backstop so this test file can never reach the real Task
        # Scheduler no matter what the implementation calls.
        Mock -ModuleName WslAutomation Register-WslScheduledTask { }
        Mock -ModuleName WslAutomation Set-WslScheduledTask { }
        Mock -ModuleName WslAutomation Register-ScheduledTask { }
        Mock -ModuleName WslAutomation Set-ScheduledTask { }
        Mock -ModuleName WslAutomation Unregister-ScheduledTask { }
        Mock -ModuleName WslAutomation Rename-Item { }

        # Absolute safety net for the Task Scheduler history log, mirroring the Invoke-WslExe
        # mock above: no test in this file may ever reach the real event log. Every test that
        # reaches Set-WslAutomationScheduledTasks - which now calls Enable-TaskSchedulerHistory
        # once per run - mocks this seam, default-shaped as a currently-disabled log with a
        # SaveChanges ScriptMethod tests can assert against or override per-test.
        $script:taskHistorySaveChangesCallCount = 0
        Mock -ModuleName WslAutomation Get-TaskSchedulerHistoryLog {
            [pscustomobject]@{
                IsEnabled = $false
            } | Add-Member -MemberType ScriptMethod -Name SaveChanges -Value {
                $script:taskHistorySaveChangesCallCount++
            } -PassThru
        }
    }

    Context 'when none of the scheduled tasks already exists' {

        BeforeEach {
            Mock -ModuleName WslAutomation Get-ScheduledTask { $null }
        }

        It 'runs all five background tasks as S4U in session 0, where no desktop window can flash' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            # The ccstatusline sync, Codex Cloud sync, usage census, waiting feed publish and
            # session miner are windowless background tasks.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 5 -Exactly -ParameterFilter {
                $LogonType -eq 'S4U'
            }
        }

        It 'no longer relies on -WindowStyle Hidden in the background task actions (they need no window trick)' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            $ccstatuslineAction = $script:capturedActions | Where-Object { $_.Argument -match 'sync-ccstatusline-config\.ps1' }
            $ccstatuslineAction.Argument | Should -Not -Match 'WindowStyle'
        }

        It 'runs no task interactively; the usage census task is S4U for the current user, never SYSTEM' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            # Every task runs in the background; none needs to show a window.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 0 -Exactly -ParameterFilter {
                $LogonType -eq 'Interactive'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish' -and
                $Principal.LogonType -eq 'S4U' -and $Principal.UserId -eq "$env:USERDOMAIN\$env:USERNAME"
            }
        }

        It 'creates the ccstatusline sync task with an action that references sync-ccstatusline-config.ps1 and threads DistroName' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -DistroName 'Debian' -Confirm:$false

            $ccstatuslineAction = $script:capturedActions | Where-Object { $_.Argument -match 'sync-ccstatusline-config\.ps1' }

            $ccstatuslineAction | Should -Not -BeNullOrEmpty
            $ccstatuslineAction.Argument | Should -Match '-DistroName Debian'
        }

        It 'registers the ccstatusline task via Register-WslScheduledTask with the sync action and a once/repeating trigger at the configured interval' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -CcstatuslineIntervalMinutes 5

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'ccstatusline Config Sync' -and
                $Action.Argument -match 'sync-ccstatusline-config\.ps1' -and
                $Trigger.IsOnce -eq $true -and
                $Trigger.Repetition.Interval -eq 'PT5M'
            }
        }

        It 'registers the Codex Cloud sync task with only midnight and noon triggers and no catch-up setting' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -DistroName 'Debian' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Codex Cloud Environment Sync' -and
                $Action.Argument -match 'sync-codex-cloud-environments\.ps1' -and
                $Action.Argument -match '-DistroName Debian' -and
                $Trigger.Count -eq 2 -and $Trigger[0].IsDaily -and $Trigger[0].At -eq '00:00' -and
                $Trigger[1].IsDaily -and $Trigger[1].At -eq '12:00'
            }
            # The Codex Cloud sync and usage census tasks share these settings.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskSettingsSet -Times 2 -Exactly -ParameterFilter {
                $ExecutionTimeLimit.TotalMinutes -eq 15 -and $MultipleInstances -eq 'IgnoreNew' -and
                $AllowStartIfOnBatteries -and $DontStopIfGoingOnBatteries -and -not $StartWhenAvailable
            }
        }

        It 'registers the usage census task as a direct wsl.exe action with one daily trigger, S4U, and no catch-up setting' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish' -and
                $Action.Execute -like '*wsl.exe' -and
                @($Trigger).Count -eq 1 -and $Trigger.IsDaily -and $Trigger.At -eq '12:37' -and
                $Principal.FakePrincipal
            }
            # The Codex Cloud sync and usage census tasks share these settings.
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskSettingsSet -Times 2 -Exactly -ParameterFilter {
                $ExecutionTimeLimit.TotalMinutes -eq 15 -and $MultipleInstances -eq 'IgnoreNew' -and
                $AllowStartIfOnBatteries -and $DontStopIfGoingOnBatteries -and -not $StartWhenAvailable
            }

            $censusAction = @($script:capturedActions | Where-Object { $_.Argument -match 'publish-usage-census\.sh' })
            $censusAction.Count | Should -Be 1
            $censusAction[0].Argument | Should -Match '--distribution Ubuntu'
            $censusAction[0].Argument.Contains('--cd "' + $script:scriptsDir + '"') | Should -BeTrue
            $censusAction[0].Argument | Should -Match '--exec /bin/bash \./publish-usage-census\.sh$'
            $censusAction[0].Argument | Should -Not -Match 'pwsh'
        }

        It 'builds the usage census principal for the current user, never SYSTEM, as S4U' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish' -and $Principal.UserId -eq "$env:USERDOMAIN\$env:USERNAME"
            }
            Should -Invoke -ModuleName WslAutomation New-ScheduledTaskPrincipal -Times 5 -Exactly -ParameterFilter {
                $LogonType -eq 'S4U' -and $UserId -eq "$env:USERDOMAIN\$env:USERNAME"
            }
        }

        It 'honors -UsageCensusTime, -UsageCensusTaskName, -WslExePath and -DistroName for the usage census task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -DistroName 'Debian' -UsageCensusTime '03:15' `
                -UsageCensusTaskName 'Custom Census' -WslExePath 'C:\fake\wsl.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Custom Census' -and
                $Action.Execute -eq 'C:\fake\wsl.exe' -and
                $Action.Argument -match '--distribution Debian' -and
                $Trigger.At -eq '03:15'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish'
            }
        }

        It 'sanitizes a trailing backslash in ScriptsDir so the usage census --cd argument stays well-formed' {
            Set-WslAutomationScheduledTasks -ScriptsDir ($script:scriptsDir + '\') `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            $censusAction = $script:capturedActions | Where-Object { $_.Argument -match 'publish-usage-census\.sh' }

            # A raw trailing backslash before the closing quote would escape it and swallow --exec.
            $censusAction.Argument.Contains('--cd "' + $script:scriptsDir + '" --exec') | Should -BeTrue
            $censusAction.Argument | Should -Not -Match '\\"'
        }

        It 'registers the waiting feed task as a direct wsl.exe action repeating every minute indefinitely, S4U, IgnoreNew' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Waiting Feed Publish' -and
                $Action.Execute -like '*wsl.exe' -and
                @($Trigger).Count -eq 1 -and $Trigger.IsOnce -eq $true -and
                $Trigger.Repetition.Interval -eq 'PT1M' -and $null -eq $Trigger.Repetition.Duration -and
                $Principal.FakePrincipal -and $Principal.LogonType -eq 'S4U' -and
                $Settings.MultipleInstances -eq 'IgnoreNew' -and -not $Settings.StartWhenAvailable
            }

            $feedAction = @($script:capturedActions | Where-Object { $_.Argument -match 'publish-waiting-feed\.sh' })
            $feedAction.Count | Should -Be 1
            $feedAction[0].Argument | Should -Match '--distribution Ubuntu'
            $feedAction[0].Argument.Contains('--cd "' + $script:scriptsDir + '"') | Should -BeTrue
            $feedAction[0].Argument | Should -Match '--exec /bin/bash \./publish-waiting-feed\.sh$'
            $feedAction[0].Argument | Should -Not -Match 'pwsh'
        }

        It 'builds the waiting feed principal for the current user, never SYSTEM, as S4U' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Waiting Feed Publish' -and $Principal.UserId -eq "$env:USERDOMAIN\$env:USERNAME"
            }
        }

        It 'honors -WaitingFeedTaskName, -WslExePath and -DistroName for the waiting feed task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -DistroName 'Debian' -WaitingFeedTaskName 'Custom Feed' `
                -WslExePath 'C:\fake\wsl.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Custom Feed' -and
                $Action.Execute -eq 'C:\fake\wsl.exe' -and
                $Action.Argument -match '--distribution Debian' -and
                $Action.Argument -match 'publish-waiting-feed\.sh'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Waiting Feed Publish'
            }
        }

        It 'registers the session miner task as a direct wsl.exe action with one daily trigger, S4U, a 30 minute limit, and no catch-up setting' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner' -and
                $Action.Execute -like '*wsl.exe' -and
                @($Trigger).Count -eq 1 -and $Trigger.IsDaily -and $Trigger.At -eq '07:17' -and
                $Principal.FakePrincipal -and $Principal.LogonType -eq 'S4U' -and
                $Principal.UserId -eq "$env:USERDOMAIN\$env:USERNAME" -and
                $Settings.ExecutionTimeLimit.TotalMinutes -eq 30 -and $Settings.MultipleInstances -eq 'IgnoreNew' -and
                $Settings.DisallowStartIfOnBatteries -eq $false -and $Settings.StopIfGoingOnBatteries -eq $false -and
                -not $Settings.StartWhenAvailable
            }

            $minerAction = @($script:capturedActions | Where-Object { $_.Argument -match 'run-session-miner\.sh' })
            $minerAction.Count | Should -Be 1
            $minerAction[0].Argument | Should -Match '--distribution Ubuntu'
            $minerAction[0].Argument.Contains('--cd "' + $script:scriptsDir + '"') | Should -BeTrue
            $minerAction[0].Argument | Should -Match '--exec /bin/bash \./run-session-miner\.sh$'
            $minerAction[0].Argument | Should -Not -Match 'pwsh'
        }

        It 'honors -SessionMinerTime, -SessionMinerTaskName, -WslExePath and -DistroName for the session miner task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -DistroName 'Debian' -SessionMinerTime '05:43' `
                -SessionMinerTaskName 'Custom Miner' -WslExePath 'C:\fake\wsl.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Custom Miner' -and
                $Action.Execute -eq 'C:\fake\wsl.exe' -and
                $Action.Argument -match '--distribution Debian' -and
                $Action.Argument -match 'run-session-miner\.sh' -and
                $Trigger.At -eq '05:43'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner'
            }
        }

        It 'sanitizes a trailing backslash in ScriptsDir so the session miner --cd argument stays well-formed' {
            Set-WslAutomationScheduledTasks -ScriptsDir ($script:scriptsDir + '\') `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            $minerAction = $script:capturedActions | Where-Object { $_.Argument -match 'run-session-miner\.sh' }

            # A raw trailing backslash before the closing quote would escape it and swallow --exec.
            $minerAction.Argument.Contains('--cd "' + $script:scriptsDir + '" --exec') | Should -BeTrue
            $minerAction.Argument | Should -Not -Match '\\"'
        }

        It 'sanitizes a trailing backslash in ScriptsDir so the waiting feed --cd argument stays well-formed' {
            Set-WslAutomationScheduledTasks -ScriptsDir ($script:scriptsDir + '\') `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            $feedAction = $script:capturedActions | Where-Object { $_.Argument -match 'publish-waiting-feed\.sh' }

            # A raw trailing backslash before the closing quote would escape it and swallow --exec.
            $feedAction.Argument.Contains('--cd "' + $script:scriptsDir + '" --exec') | Should -BeTrue
            $feedAction.Argument | Should -Not -Match '\\"'
        }
    }

    Context 'when the ccstatusline task already exists' {

        BeforeEach {
            $script:existingCcstatuslineTask = [pscustomobject]@{
                TaskName  = 'ccstatusline Config Sync'
                Settings  = [pscustomobject]@{ ExistingSettings = $true }
                Principal = [pscustomobject]@{ ExistingPrincipal = $true }
            }

            Mock -ModuleName WslAutomation Get-ScheduledTask {
                if ($TaskName -eq 'ccstatusline Config Sync') {
                    return $script:existingCcstatuslineTask
                }
                return $null
            }
        }

        It 'updates it via Set-WslScheduledTask' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'ccstatusline Config Sync'
            }
        }

        It 'does not create a second ccstatusline task via Register-WslScheduledTask' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'ccstatusline Config Sync'
            }
        }
    }

    Context 'when the Codex Cloud sync task already exists' {

        BeforeEach {
            $script:existingCodexCloudTask = [pscustomobject]@{
                TaskName  = 'Codex Cloud Environment Sync'
                Settings  = [pscustomobject]@{ ExistingSettings = $true }
                Principal = [pscustomobject]@{ ExistingPrincipal = $true }
            }

            Mock -ModuleName WslAutomation Get-ScheduledTask {
                if ($TaskName -eq 'Codex Cloud Environment Sync') {
                    return $script:existingCodexCloudTask
                }
                return $null
            }
        }

        It 'rebuilds settings and principal while replacing the action with the two daily triggers' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Codex Cloud Environment Sync' -and
                $Action.Argument -match 'sync-codex-cloud-environments\.ps1' -and
                $Trigger.Count -eq 2 -and $Settings.FakeSettings -and $Principal.FakePrincipal
            }
        }

        It 'does not create a second Codex Cloud sync task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Codex Cloud Environment Sync'
            }
        }
    }

    Context 'when the usage census task already exists' {

        BeforeEach {
            $script:existingUsageCensusTask = [pscustomobject]@{
                TaskName  = 'Usage Census Publish'
                Settings  = [pscustomobject]@{ ExistingSettings = $true }
                Principal = [pscustomobject]@{ ExistingPrincipal = $true }
            }

            Mock -ModuleName WslAutomation Get-ScheduledTask {
                if ($TaskName -eq 'Usage Census Publish') {
                    return $script:existingUsageCensusTask
                }
                return $null
            }
        }

        It 'rebuilds settings and principal while replacing the action and trigger in place' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish' -and
                $Action.Argument -match 'publish-usage-census\.sh' -and
                $Trigger.IsDaily -and $Settings.FakeSettings -and $Principal.FakePrincipal
            }
        }

        It 'does not create a second usage census task' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish'
            }
        }
    }

    Context 'when the waiting feed task already exists' {

        BeforeEach {
            Mock -ModuleName WslAutomation Get-ScheduledTask {
                if ($TaskName -eq 'Claude Waiting Feed Publish') {
                    return [pscustomobject]@{ TaskName = 'Claude Waiting Feed Publish' }
                }
                return $null
            }
        }

        It 'updates the task in place and does not create a second one' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Waiting Feed Publish' -and
                $Action.Argument -match 'publish-waiting-feed\.sh' -and
                $Trigger.Repetition.Interval -eq 'PT1M' -and $Settings.FakeSettings -and $Principal.FakePrincipal
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Claude Waiting Feed Publish'
            }
        }
    }

    Context 'when the session miner task already exists' {

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
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false

            Should -Invoke -ModuleName WslAutomation Set-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner' -and
                $Action.Argument -match 'run-session-miner\.sh' -and
                $Trigger.IsDaily -and $Settings.FakeSettings -and $Principal.FakePrincipal
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 0 -Exactly -ParameterFilter {
                $TaskName -eq 'Session Miner'
            }
        }
    }

    Context 'legacy script archiving' {

        BeforeEach {
            Mock -ModuleName WslAutomation Get-ScheduledTask { $null }
        }

        It 'renames a legacy script to a .superseded-<yyyyMMdd> name when it exists' {
            $legacyPath = Join-Path $TestDrive 'old-backup.ps1'
            Set-Content -Path $legacyPath -Value '# legacy script' -Encoding utf8
            $todaySuffix = (Get-Date).ToString('yyyyMMdd')

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($legacyPath)

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 1 -Exactly -ParameterFilter {
                ($Path -eq $legacyPath -or $LiteralPath -eq $legacyPath) -and
                ($NewName -match [regex]::Escape("superseded-$todaySuffix"))
            }
        }

        It 'does not attempt to rename a legacy path that does not exist' {
            $missingPath = Join-Path $TestDrive 'does-not-exist.ps1'

            {
                Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                    -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($missingPath)
            } | Should -Not -Throw

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 0 -Exactly
        }

        It 'skips and warns instead of renaming when the archived target name already exists' {
            $legacyPath = Join-Path $TestDrive 'already-archived.ps1'
            Set-Content -Path $legacyPath -Value '# legacy script' -Encoding utf8
            $todaySuffix = (Get-Date).ToString('yyyyMMdd')
            $existingTargetPath = "$legacyPath.superseded-$todaySuffix"
            Set-Content -Path $existingTargetPath -Value '# already archived earlier today' -Encoding utf8

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($legacyPath) -WarningVariable warnings

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 0 -Exactly -ParameterFilter {
                ($Path -eq $legacyPath -or $LiteralPath -eq $legacyPath)
            }
            $warnings | Should -Not -BeNullOrEmpty
        }

        It 'expands a single comma-joined element of two rooted paths and renames both (pwsh -File comma-flattening workaround)' {
            # Simulates the proven -File argument-binding footgun: two array elements arriving
            # from another shell (e.g. Windows PowerShell 5.1 calling `pwsh -File ...`) flattened
            # into one literal comma-joined string, rather than -LegacyScriptsToArchive actually
            # receiving a two-element array.
            $legacyPathOne = Join-Path $TestDrive 'legacy-one.ps1'
            $legacyPathTwo = Join-Path $TestDrive 'legacy-two.ps1'
            Set-Content -Path $legacyPathOne -Value '# legacy script one' -Encoding utf8
            Set-Content -Path $legacyPathTwo -Value '# legacy script two' -Encoding utf8
            $todaySuffix = (Get-Date).ToString('yyyyMMdd')
            $flattenedElement = "$legacyPathOne,$legacyPathTwo"

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($flattenedElement)

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 1 -Exactly -ParameterFilter {
                ($Path -eq $legacyPathOne -or $LiteralPath -eq $legacyPathOne) -and
                ($NewName -match [regex]::Escape("superseded-$todaySuffix"))
            }
            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 1 -Exactly -ParameterFilter {
                ($Path -eq $legacyPathTwo -or $LiteralPath -eq $legacyPathTwo) -and
                ($NewName -match [regex]::Escape("superseded-$todaySuffix"))
            }
        }

        It 'warns when a legacy path does not exist and no .superseded-* sibling is present' {
            $missingPath = Join-Path $TestDrive 'no-sibling-yet.ps1'

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($missingPath) -WarningVariable warnings

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 0 -Exactly
            $warnings | Where-Object { $_ -match 'Legacy script not found' } | Should -Not -BeNullOrEmpty
        }

        It 'stays silent when a legacy path does not exist but a .superseded-* sibling from a previous run is present' {
            # An idempotent re-run: a prior invocation already renamed this legacy script out of
            # the way, so it no longer exists at its original path. That's expected, not an error.
            $missingPath = Join-Path $TestDrive 'archived-on-prior-run.ps1'
            $todaySuffix = (Get-Date).ToString('yyyyMMdd')
            $priorSupersededPath = "$missingPath.superseded-$todaySuffix"
            Set-Content -Path $priorSupersededPath -Value '# archived on a prior run' -Encoding utf8

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -LegacyScriptsToArchive @($missingPath) -WarningVariable warnings

            Should -Invoke -ModuleName WslAutomation Rename-Item -Times 0 -Exactly
            $warnings | Where-Object { $_ -match 'Legacy script not found' } | Should -BeNullOrEmpty
        }
    }

    Context 'Task Scheduler history' {

        BeforeEach {
            Mock -ModuleName WslAutomation Get-ScheduledTask { $null }
        }

        It 'enables the log and reports Enabled when it is currently disabled' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -InformationVariable infoMessages | Out-Null

            $script:taskHistorySaveChangesCallCount | Should -Be 1
            ($infoMessages | Out-String) | Should -Match ([regex]::Escape('Task Scheduler history (Microsoft-Windows-TaskScheduler/Operational): Enabled'))
        }

        It 'does not call SaveChanges when the log is already enabled, and reports AlreadyEnabled' {
            Mock -ModuleName WslAutomation Get-TaskSchedulerHistoryLog {
                [pscustomobject]@{ IsEnabled = $true } | Add-Member -MemberType ScriptMethod -Name SaveChanges -Value {
                    $script:taskHistorySaveChangesCallCount++
                } -PassThru
            }

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -InformationVariable infoMessages | Out-Null

            $script:taskHistorySaveChangesCallCount | Should -Be 0
            ($infoMessages | Out-String) | Should -Match ([regex]::Escape('Task Scheduler history (Microsoft-Windows-TaskScheduler/Operational): AlreadyEnabled'))
        }

        It 'does not call SaveChanges under -WhatIf' {
            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -WhatIf | Out-Null

            $script:taskHistorySaveChangesCallCount | Should -Be 0
        }

        It 'never invokes the task-history helper when -SkipTaskHistory is passed' {
            Mock -ModuleName WslAutomation Enable-TaskSchedulerHistory { 'Enabled' }

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -SkipTaskHistory

            Should -Invoke -ModuleName WslAutomation Enable-TaskSchedulerHistory -Times 0 -Exactly
        }

        It 'warns but still registers the other tasks when SaveChanges throws' {
            Mock -ModuleName WslAutomation Get-TaskSchedulerHistoryLog {
                [pscustomobject]@{ IsEnabled = $false } | Add-Member -MemberType ScriptMethod -Name SaveChanges -Value {
                    throw 'access denied'
                } -PassThru
            }

            Set-WslAutomationScheduledTasks -ScriptsDir $script:scriptsDir `
                -PwshPath 'C:\fake\pwsh.exe' -Confirm:$false -WarningVariable warnings -WarningAction SilentlyContinue

            $warnings | Where-Object { $_ -match 'Failed to enable Task Scheduler history' } | Should -Not -BeNullOrEmpty
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'Usage Census Publish'
            }
            Should -Invoke -ModuleName WslAutomation Register-WslScheduledTask -Times 1 -Exactly -ParameterFilter {
                $TaskName -eq 'ccstatusline Config Sync'
            }
        }
    }
}

Describe 'Scheduled task checkout bootstrap coverage (PowerShell AST)' {
    BeforeAll {
        $script:repoRoot = Split-Path $PSScriptRoot -Parent
        $parseErrors = $null
        $script:taskAst = [Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $script:repoRoot 'src/WslAutomation/Public/Set-WslAutomationScheduledTasks.ps1'),
            [ref]$null, [ref]$parseErrors)
        $parseErrors.Count | Should -Be 0
        $script:assignments = @{}
        foreach ($assignment in $script:taskAst.FindAll({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] }, $true)) {
            if ($assignment.Left -is [Management.Automation.Language.VariableExpressionAst]) {
                $script:assignments[$assignment.Left.VariablePath.UserPath] = $assignment.Right
            }
        }
        function Get-TaskActionScript {
            param($Node, [string[]]$Seen = @())
            foreach ($constant in $Node.FindAll({ param($n) $n -is [Management.Automation.Language.StringConstantExpressionAst] }, $true)) {
                if ($constant.Value.EndsWith('.ps1')) { $constant.Value }
            }
            foreach ($expanded in $Node.FindAll({ param($n) $n -is [Management.Automation.Language.ExpandableStringExpressionAst] }, $true)) {
                # Every wsl.exe action runs one Bash entry from the scripts directory; discover it by
                # shape, so a wsl.exe task added later is covered without editing this test.
                if ($expanded.Value -match '--exec /bin/bash \./([A-Za-z0-9._-]+\.sh)$') { $Matches[1] }
            }
            foreach ($variable in $Node.FindAll({ param($n) $n -is [Management.Automation.Language.VariableExpressionAst] }, $true)) {
                $name = $variable.VariablePath.UserPath
                if ($script:assignments.ContainsKey($name) -and $name -notin $Seen) {
                    Get-TaskActionScript -Node $script:assignments[$name] -Seen ($Seen + $name)
                }
            }
        }
        $script:actionScripts = @()
        $actions = @($script:taskAst.FindAll({ param($node)
            $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'New-ScheduledTaskAction'
        }, $true))
        foreach ($action in $actions) {
            $argumentIndex = 0
            for ($i = 0; $i -lt $action.CommandElements.Count; $i++) {
                $element = $action.CommandElements[$i]
                if ($element -is [Management.Automation.Language.CommandParameterAst] -and $element.ParameterName -eq 'Argument') { $argumentIndex = $i + 1 }
            }
            $script:actionScripts += @(Get-TaskActionScript -Node $action.CommandElements[$argumentIndex])
        }
        $script:actionCount = $actions.Count
    }

    It 'covers all five script actions among five registered task actions' {
        $script:actionCount | Should -Be 5
        $script:actionScripts.Count | Should -Be 5
        @($script:actionScripts | Sort-Object) | Should -Be @(
            'publish-usage-census.sh', 'publish-waiting-feed.sh', 'run-session-miner.sh',
            'sync-ccstatusline-config.ps1', 'sync-codex-cloud-environments.ps1'
        )
    }

    It 'bootstraps every script action, so a new task cannot skip the checkout update' {
        # Discovered from the installer rather than listed, so a task added later fails here
        # until its entry goes through one of the two shared bootstraps.
        foreach ($entry in $script:actionScripts) {
            $path = Join-Path $script:repoRoot 'scripts' $entry
            if ($entry.EndsWith('.ps1')) {
                $source = Get-Content $path -Raw
                $source | Should -Match '(?m)^\. \(Join-Path \$PSScriptRoot ''update-task-checkout\.ps1''\)\r?$' -Because $entry
                $source | Should -Match '(?m)^\$taskBootstrap = Initialize-WslAutomationTask ' -Because $entry
                continue
            }
            $entry | Should -BeLike '*.sh'
            # Before the call: only comments, set -euo pipefail, and the two lines that find
            # and source the shared Bash bootstrap.
            $allowed = @(
                'set -euo pipefail',
                'scripts_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)',
                '. "$scripts_dir/update-task-checkout.sh"'
            )
            $call = "wsl_automation_task_bootstrap `"`$scripts_dir/$entry`" "
            $lines = @(Get-Content $path)
            $callIndex = @(for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i].StartsWith($call)) { $i } })
            $callIndex.Count | Should -Be 1 -Because "$entry calls the bootstrap once, for itself"
            $lines[$callIndex[0]] | Should -Match ' \d+ \d+ "\$@"$' -Because "$entry passes its interval, deadline and arguments"
            $before = @($lines[0..($callIndex[0] - 1)] | Where-Object { $_.Trim() -ne '' -and -not $_.StartsWith('#') })
            $before | Should -Be $allowed -Because "$entry updates before any other work"
        }
    }

    It 'bootstraps <Entry> before importing and invoking task code' -ForEach @(
        @{ Entry = 'sync-ccstatusline-config.ps1'; Work = 'Update-CcstatuslineConfig' },
        @{ Entry = 'sync-codex-cloud-environments.ps1'; Work = 'Invoke-CodexCloudEnvironmentSync' }
    ) {
        $script:actionScripts | Should -Contain $Entry
        $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $script:repoRoot 'scripts' $Entry), [ref]$null, [ref]$errors)
        $errors.Count | Should -Be 0
        $commands = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true))
        $bootstrap = @($commands | Where-Object { $_.GetCommandName() -eq 'Initialize-WslAutomationTask' })
        $import = @($commands | Where-Object { $_.GetCommandName() -eq 'Import-Module' })
        $workCommand = @($commands | Where-Object { $_.GetCommandName() -eq $Work })
        $bootstrap.Count | Should -Be 1
        $import.Count | Should -Be 1
        $workCommand.Count | Should -Be 1
        $bootstrap[0].Extent.StartOffset | Should -BeLessThan $import[0].Extent.StartOffset
        $bootstrap[0].Extent.StartOffset | Should -BeLessThan $workCommand[0].Extent.StartOffset
        $sourced = @($commands | Where-Object { $_.InvocationOperator -eq 'Dot' })
        $sourced.Count | Should -Be 1
        @($sourced[0].FindAll({ param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] -and $node.Value -eq 'update-task-checkout.ps1' }, $true)).Count | Should -Be 1
        $sourced[0].Extent.StartOffset | Should -BeLessThan $bootstrap[0].Extent.StartOffset
        $exitBlocks = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.IfStatementAst] }, $true) | Where-Object {
            @($_.Clauses[0].Item1.FindAll({ param($n) $n -is [Management.Automation.Language.MemberExpressionAst] -and $n.Member.Value -eq 'Relaunched' }, $true)).Count -gt 0
        })
        $exitBlocks.Count | Should -Be 1
        @($exitBlocks[0].FindAll({ param($n) $n -is [Management.Automation.Language.ExitStatementAst] }, $true)).Count | Should -Be 1
        $exitBlocks[0].Extent.EndOffset | Should -BeLessThan $import[0].Extent.StartOffset
    }
}
