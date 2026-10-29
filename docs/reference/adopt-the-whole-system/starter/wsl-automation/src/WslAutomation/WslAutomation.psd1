@{
    RootModule        = 'WslAutomation.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '52f5cbb9-1545-4433-9d48-fc843b03ded4'
    Author            = 'Example Adopter'
    CompanyName       = 'Unknown'
    Copyright         = '(c) 2026 Example Adopter. All rights reserved.'
    Description       = 'Scheduled-task automation for WSL: checkout updater, usage census, waiting feed, session miner, ccstatusline sync, Codex Cloud environment sync.'
    PowerShellVersion = '7.6'

    FunctionsToExport = @(
        'Get-WslDistroState',
        'Set-WslAutomationScheduledTasks',
        'Update-CcstatuslineConfig',
        'Set-CcstatuslineStatusLine',
        'Invoke-CodexCloudEnvironmentSync',
        'Update-WslAutomationRepo',
        'Set-AgentWorkspaceTrust',
        'Install-AgentTrustGitHook'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData       = @{
        PSData = @{
            Tags       = @('WSL', 'ClaudeCode', 'ScheduledTask')
            LicenseUri = ''
            ProjectUri = ''
        }
    }
}
