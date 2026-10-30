#requires -Version 7.6
Set-StrictMode -Version Latest

$moduleRoot = $PSScriptRoot

$privateFiles = @(Get-ChildItem -Path (Join-Path -Path $moduleRoot -ChildPath 'Private') -Filter '*.ps1' -ErrorAction SilentlyContinue)
$publicFiles = @(Get-ChildItem -Path (Join-Path -Path $moduleRoot -ChildPath 'Public') -Filter '*.ps1' -ErrorAction SilentlyContinue)

foreach ($file in $privateFiles) {
    . $file.FullName
}

foreach ($file in $publicFiles) {
    . $file.FullName
}

Export-ModuleMember -Function @(
    'Get-WslDistroState',
    'Set-WslAutomationScheduledTasks',
    'Update-CcstatuslineConfig',
    'Set-CcstatuslineStatusLine',
    'Invoke-CodexCloudEnvironmentSync',
    'Update-WslAutomationRepo',
    'Set-AgentWorkspaceTrust',
    'Install-AgentTrustGitHook'
)
