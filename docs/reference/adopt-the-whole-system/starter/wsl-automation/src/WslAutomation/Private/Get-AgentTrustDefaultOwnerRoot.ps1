function Get-AgentTrustDefaultOwnerRoot {
    <#
    .SYNOPSIS
        Returns the default owner roots for agent workspace trust on this platform.
    .DESCRIPTION
        Windows: C:\repos\example-adopter and C:\repos\example-adopter-two. Anywhere else (WSL/Linux, where
        clones live flat under ~/repos): the single directory ~/repos.
    .PARAMETER IsWindowsHost
        Which platform to answer for. Defaults to $IsWindows; tests pass it explicitly.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [bool]$IsWindowsHost = $IsWindows
    )

    if ($IsWindowsHost) {
        return @('C:\repos\example-adopter', 'C:\repos\example-adopter-two')
    }
    return @(Join-Path $HOME 'repos')
}
