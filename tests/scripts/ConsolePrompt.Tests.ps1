# Tests of scripts/lib/ConsolePrompt.psm1: a question asked with no console input is answered "no" at once, even when the
# piped input says yes, so an unattended script never proceeds by default.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Module = (Resolve-Path (Join-Path $PSScriptRoot '..\..\scripts\lib\ConsolePrompt.psm1')).Path
    $script:PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Exits 0 when Read-Confirmation answers yes and 5, the "declined" status of docs/operations.md, otherwise.
    $script:Probe = "Import-Module '$script:Module'; if (Read-Confirmation -Question 'Continue?') { exit 0 } else { exit 5 }"
}

Describe 'Read-Confirmation' {
    It 'answers no without waiting when standard input is a pipe, even one that says yes' {
        $output = @('y' | & $script:PowerShell -NoProfile -ExecutionPolicy Bypass -Command $script:Probe)
        $LASTEXITCODE | Should -Be 5
        ($output -join "`n") | Should -Match 'No console input is available, so the answer is no'
    }

    It 'answers no without waiting in a non-interactive PowerShell' {
        $output = @(& $script:PowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $script:Probe)
        $LASTEXITCODE | Should -Be 5
        ($output -join "`n") | Should -Match 'No console input is available'
    }
}
