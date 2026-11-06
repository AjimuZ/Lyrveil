# Asks yes-or-no questions on the console, and answers "no" by itself when nobody can answer, so an unattended run never proceeds.

Set-StrictMode -Version Latest

function Test-ConsoleInput {
    <#
    Returns $true only when a person can answer a question: the process is interactive, PowerShell was not started
    with -NonInteractive, and standard input is the console rather than a pipe, a file or nothing. Returns $false
    when any of these cannot be read, so a doubt never counts as an answer.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    if (-not [Environment]::UserInteractive) { return $false }
    foreach ($argument in [Environment]::GetCommandLineArgs()) {
        if ($argument -match '^-noni(nteractive)?$') { return $false }
    }
    try {
        if ([Console]::IsInputRedirected) { return $false }
    }
    catch {
        return $false
    }
    return $true
}

function Read-Confirmation {
    <#
    Asks Question and returns $true only when the user types y or yes, in any case. Any other answer, an empty one
    included, returns $false. When Test-ConsoleInput finds no console input, it prints the question with the reason
    and returns $false at once, without waiting; a host that cannot read input returns $false the same way. Callers
    map $false to exit status 5 (docs/operations.md, Scripts).
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Question)

    if (-not (Test-ConsoleInput)) {
        Write-Host "$Question No console input is available, so the answer is no."
        return $false
    }
    try {
        $answer = Read-Host "$Question [y/N]"
    }
    catch {
        Write-Host "$Question The answer could not be read, so it is no."
        return $false
    }
    return ([string]$answer -match '^\s*(y|yes)\s*$')
}

Export-ModuleMember -Function Test-ConsoleInput, Read-Confirmation
