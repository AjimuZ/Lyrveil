# PSScriptAnalyzer settings for every PowerShell script of the repository, used by "scripts\lyrveil.cmd test lint".
@{
    Severity     = @('Error', 'Warning')
    # The scripts are interactive console commands: Write-Host is their user interface, not a logging shortcut.
    ExcludeRules = @('PSAvoidUsingWriteHost')
}
