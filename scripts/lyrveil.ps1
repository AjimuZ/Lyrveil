# Entry point of the Lyrveil management commands, run with Windows PowerShell 5.1 by scripts\lyrveil.cmd.
#
# Usage: scripts\lyrveil.cmd <command> [arguments]
# Delivered commands: test <level> [pytest arguments]. The other commands of docs/operations.md are planned.
# Exit status follows docs/operations.md: 0 on success, 1 on any failure, including an unknown command or level.
#
# The script declares no parameters on purpose: every argument reaches $args unchanged, so pytest options such as
# -k or -x pass through instead of being parsed as PowerShell parameters.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$commandLine = @($args | ForEach-Object { [string]$_ })
# Plain assignments: an if expression would turn an empty array into $null under Windows PowerShell 5.1.
$command = ''
$rest = @()
if ($commandLine.Count -gt 0) { $command = $commandLine[0] }
if ($commandLine.Count -gt 1) { $rest = @($commandLine | Select-Object -Skip 1) }

try {
    switch ($command) {
        'test' {
            Import-Module (Join-Path $PSScriptRoot 'lib\TestCommand.psm1') -Force
            $status = Invoke-LyrveilTest -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        default {
            Write-Host "Unknown command '$command'. Usage: scripts\lyrveil.cmd test <level>."
            Write-Host 'Only "test" is delivered; the other commands in docs/operations.md are planned.'
            exit 1
        }
    }
}
catch {
    # Any unexpected error ends the command with status 1 and its message, never with a partial success.
    $Host.UI.WriteErrorLine("lyrveil: $($_.Exception.Message)")
    exit 1
}
