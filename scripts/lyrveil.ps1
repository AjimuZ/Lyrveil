# Entry point of the Lyrveil management commands, run with Windows PowerShell 5.1 by scripts\lyrveil.cmd.
#
# Usage: scripts\lyrveil.cmd <command> [arguments]
# Delivered commands: test <level>. The other commands of docs/operations.md are planned.
# Exit status follows docs/operations.md: 0 on success, 1 on any failure, including an unknown command or level.

[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string]$Command = '',
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]]$Argument = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot

try {
    switch ($Command) {
        'test' {
            Import-Module (Join-Path $PSScriptRoot 'lib\TestCommand.psm1') -Force
            $status = Invoke-LyrveilTest -Root $repositoryRoot -Level $Argument
            exit ([int]$status)
        }
        default {
            Write-Host "Unknown command '$Command'. Usage: scripts\lyrveil.cmd test <level>."
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
