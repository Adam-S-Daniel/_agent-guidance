#requires -Version 7.6

BeforeAll {
    Import-Module (Join-Path $PSScriptRoot '..' 'src' 'WslAutomation') -Force
}

Describe 'WslAutomation helpers' {

    BeforeEach {
        # Absolute safety net: no test in this file may ever reach a real wsl.exe.
        # Individual tests below override this with more specific mocks as needed.
        Mock -ModuleName WslAutomation Invoke-WslExe {
            [pscustomobject]@{ ExitCode = 0; Output = @() }
        }
    }

    Describe 'Write-WslAutomationLog' {
        # Write-WslAutomationLog is a private (non-exported) module member, so it can only be
        # called directly from -ModuleName WslAutomation's own scope, via InModuleScope. Only the
        # call itself runs there (the module runs under Set-StrictMode -Version Latest); setup and
        # assertions stay in this file's own, non-strict scope so plain Get-Content results (which
        # can be a single string, not an array) behave normally for -Count/index checks below.

        It 'appends a line matching the standard timestamp format' {
            $logFile = Join-Path $TestDrive 'plain.log'

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'Hello world' -LogFile $LogFile
            }

            # @() forces array semantics even for a single-line file, where Get-Content would
            # otherwise return a plain [string] and $content[0] would index its first character.
            $content = @(Get-Content -Path $logFile)
            $content.Count | Should -Be 1
            $content[0] | Should -Match '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  .+$'
            $content[0] | Should -Match 'Hello world'
        }

        It 'creates the parent directory when it does not exist' {
            $logFile = Join-Path $TestDrive 'nested' 'deeper' 'test.log'

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'nested write' -LogFile $LogFile
            }

            Test-Path -Path $logFile | Should -BeTrue
        }

        It 'appends rather than overwrites on repeated calls' {
            $logFile = Join-Path $TestDrive 'append.log'

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'first line' -LogFile $LogFile
                Write-WslAutomationLog -Message 'second line' -LogFile $LogFile
            }

            $content = Get-Content -Path $logFile
            $content.Count | Should -Be 2
            $content[0] | Should -Match 'first line'
            $content[1] | Should -Match 'second line'
        }

        It 'rotates the log to <LogFile>.1 when it exceeds MaxSizeMB' {
            $logFile = Join-Path $TestDrive 'rotate.log'
            $rotated = "$logFile.1"

            $lines = 1..1200 | ForEach-Object { 'x' * 1024 }
            Set-Content -Path $logFile -Value $lines -Encoding utf8

            (Get-Item -Path $logFile).Length | Should -BeGreaterThan 1MB

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'trigger rotation' -LogFile $LogFile -MaxSizeMB 1
            }

            Test-Path -Path $rotated | Should -BeTrue

            # @() forces array semantics: after rotation this file has a single line, and
            # Get-Content would otherwise return a plain [string] here.
            $newContent = @(Get-Content -Path $logFile)
            $newContent.Count | Should -Be 1
            $newContent[0] | Should -Match 'trigger rotation'
        }

        It 'overwrites a pre-existing .1 file when rotating' {
            $logFile = Join-Path $TestDrive 'rotate2.log'
            $rotated = "$logFile.1"

            Set-Content -Path $rotated -Value 'stale rotated content' -Encoding utf8

            $lines = 1..1200 | ForEach-Object { 'x' * 1024 }
            Set-Content -Path $logFile -Value $lines -Encoding utf8

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'second rotation' -LogFile $LogFile -MaxSizeMB 1
            }

            $rotatedContent = Get-Content -Path $rotated -Raw
            $rotatedContent | Should -Not -Match 'stale rotated content'
        }

        It 'does not rotate when MaxSizeMB is 0, however large the file' {
            $logFile = Join-Path $TestDrive 'norotate.log'

            $lines = 1..1200 | ForEach-Object { 'x' * 1024 }
            Set-Content -Path $logFile -Value $lines -Encoding utf8

            InModuleScope WslAutomation -Parameters @{ LogFile = $logFile } {
                param($LogFile)
                Write-WslAutomationLog -Message 'no rotation here' -LogFile $LogFile -MaxSizeMB 0
            }

            Test-Path -Path "$logFile.1" | Should -BeFalse

            $content = Get-Content -Path $logFile
            ($content | Where-Object { $_ -match 'no rotation here' }).Count | Should -Be 1
        }
    }

    Describe 'Get-WslDistroState' {

        It 'returns Running for a distro marked as the current default (leading *)' {
            Mock -ModuleName WslAutomation Invoke-WslExe {
                [pscustomobject]@{
                    ExitCode = 0
                    Output   = @(
                        'NAME                   STATE           VERSION'
                        '* Ubuntu                Running         2'
                        '  docker-desktop        Stopped         2'
                    )
                }
            }

            Get-WslDistroState -DistroName 'ubuntu' | Should -Be 'Running'

            Should -Invoke -ModuleName WslAutomation Invoke-WslExe -Times 1 -Exactly -ParameterFilter {
                ($Arguments -join ' ') -eq '--list --verbose'
            }
        }

        It 'returns Stopped for a stopped distro' {
            Mock -ModuleName WslAutomation Invoke-WslExe {
                [pscustomobject]@{
                    ExitCode = 0
                    Output   = @(
                        'NAME                   STATE           VERSION'
                        '* Ubuntu                Running         2'
                        '  docker-desktop        Stopped         2'
                    )
                }
            }

            Get-WslDistroState -DistroName 'docker-desktop' | Should -Be 'Stopped'
        }

        It 'returns NotFound when no distro name matches' {
            Mock -ModuleName WslAutomation Invoke-WslExe {
                [pscustomobject]@{
                    ExitCode = 0
                    Output   = @(
                        'NAME                   STATE           VERSION'
                        '* Ubuntu                Running         2'
                        '  docker-desktop        Stopped         2'
                    )
                }
            }

            Get-WslDistroState -DistroName 'no-such-distro' | Should -Be 'NotFound'
        }

        It 'returns NotFound when wsl.exe exits with a nonzero code' {
            Mock -ModuleName WslAutomation Invoke-WslExe {
                [pscustomobject]@{ ExitCode = 1; Output = @() }
            }

            Get-WslDistroState -DistroName 'Ubuntu' | Should -Be 'NotFound'
        }
    }
}

Describe 'Invoke-WslExe' {
    # Every test above mocks Invoke-WslExe itself (the module's single approved mock seam), so
    # Invoke-WslExe's own body - including its NUL-stripping - never actually runs anywhere else
    # in this file. These tests instead mock the native wsl.exe command one level down, so the
    # real Invoke-WslExe function executes, while still guaranteeing no real wsl.exe is ever
    # invoked.

    BeforeAll {
        Import-Module (Join-Path $PSScriptRoot '..' 'src' 'WslAutomation') -Force

        function ConvertTo-Utf16AsUtf8Artifact {
            <#
            Simulates the real artifact of decoding a UTF-16LE byte stream as UTF-8: every
            character arrives with a trailing NUL (e.g. 'U<NUL>b<NUL>u<NUL>n<NUL>t<NUL>u<NUL>'),
            which is what wsl.exe's own management output looks like once Invoke-WslExe reads it
            back under UTF8 console encoding.
            #>
            param([Parameter(Mandatory)][string]$Text)
            -join ($Text.ToCharArray() | ForEach-Object { "$_`0" })
        }
    }

    It 'strips interleaved NULs from simulated wsl.exe management output' {
        $script:headerLine = ConvertTo-Utf16AsUtf8Artifact 'NAME                   STATE           VERSION'
        $script:dataLine = ConvertTo-Utf16AsUtf8Artifact '* Ubuntu                Running         2'

        Mock -CommandName wsl.exe -ModuleName WslAutomation {
            $global:LASTEXITCODE = 0
            $script:headerLine
            $script:dataLine
        }

        $result = InModuleScope WslAutomation {
            Invoke-WslExe -Arguments @('--list', '--verbose')
        }

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Be @(
            'NAME                   STATE           VERSION'
            '* Ubuntu                Running         2'
        )
    }

    It 'lets Get-WslDistroState parse Running correctly end-to-end through the real NUL-stripping' {
        $script:headerLine = ConvertTo-Utf16AsUtf8Artifact 'NAME                   STATE           VERSION'
        $script:dataLine = ConvertTo-Utf16AsUtf8Artifact '* Ubuntu                Running         2'

        Mock -CommandName wsl.exe -ModuleName WslAutomation {
            $global:LASTEXITCODE = 0
            $script:headerLine
            $script:dataLine
        }

        Get-WslDistroState -DistroName 'Ubuntu' | Should -Be 'Running'
    }
}
