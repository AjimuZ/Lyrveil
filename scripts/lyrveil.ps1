# Entry point of the Lyrveil management commands, run with Windows PowerShell 5.1 by scripts\lyrveil.cmd and the root .cmd files.
#
# Usage: scripts\lyrveil.cmd <command> [arguments]
# Delivered commands: setup [options], start, stop, restart, status [-Json] and logs [service ...] [options], run by
# the root .cmd files of the same names, and test <level> [pytest arguments]. models, import-model, backup and restore
# are planned (docs/operations.md).
# Exit status follows docs/operations.md: 0 on success, 1 on any failure, including an unknown command or level, 2 when
# a preflight check fails, and 3 when a health check fails or times out.
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
$lifecycle = Join-Path $PSScriptRoot 'lib\Lifecycle.psm1'

try {
    switch ($command) {
        'setup' {
            Import-Module (Join-Path $PSScriptRoot 'lib\Setup.psm1') -Force
            $status = Invoke-LyrveilSetup -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'start' {
            Import-Module $lifecycle -Force
            $status = Invoke-LyrveilStart -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'stop' {
            Import-Module $lifecycle -Force
            $status = Invoke-LyrveilStop -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'restart' {
            Import-Module $lifecycle -Force
            $status = Invoke-LyrveilRestart -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'status' {
            Import-Module $lifecycle -Force
            $status = Invoke-LyrveilStatus -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'logs' {
            Import-Module $lifecycle -Force
            $status = Invoke-LyrveilLog -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        'test' {
            Import-Module (Join-Path $PSScriptRoot 'lib\TestCommand.psm1') -Force
            $status = Invoke-LyrveilTest -Root $repositoryRoot -Argument $rest
            exit ([int]$status)
        }
        default {
            Write-Host "Unknown command '$command'. Usage: scripts\lyrveil.cmd <command>, or the .cmd files in the repository root."
            Write-Host 'Delivered: setup, start, stop, restart, status, logs and test. models, import-model, backup and restore are planned (docs/operations.md).'
            exit 1
        }
    }
}
catch {
    # Any unexpected error ends the command with status 1 and its message, never with a partial success.
    $Host.UI.WriteErrorLine("lyrveil: $($_.Exception.Message)")
    exit 1
}
