# Runs the levels of "scripts\lyrveil.cmd test" and never reports a pass for a level that ran no test.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'DocumentationChecks.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'PinnedTools.psm1') -Force

# Every level named in docs/testing.md. A level without a runner has no tests yet and always fails.
$script:Runners = [ordered]@{
    lint        = 'Invoke-LintLevel'
    unit        = 'Invoke-UnitLevel'
    integration = $null
    e2e         = $null
    docker      = $null
    gpu         = $null
    quality     = $null
}

# "all" runs every level except gpu and quality, which need the reference hardware and are always explicit.
$script:AllLevels = @('lint', 'unit', 'integration', 'e2e', 'docker')

function Write-CheckLine {
    # Prints one counted result and its failures. Not exported.
    param([string]$Name, [int]$Checked, [int]$Failed, [string]$Unit, [string[]]$Failures)

    $status = if ($Failed -eq 0) { 'ok  ' } else { 'FAIL' }
    Write-Host ("  [{0}] {1}: {2} checked, {3} {4}" -f $status, $Name, $Checked, $Failed, $Unit)
    foreach ($failure in @($Failures)) {
        if ($failure) { Write-Host "         $failure" }
    }
}

function Invoke-LintLevel {
    <#
    Runs the lint level: the documentation checks on the Git-tracked Markdown, then the pinned PSScriptAnalyzer on
    every Git-tracked .ps1 and .psm1 with scripts/PSScriptAnalyzerSettings.psd1. Returns 0 when everything passes and
    1 otherwise. Both parts always run, so one report shows every problem. Not exported.
    #>
    param([string]$Root)

    $failed = $false

    Write-Host 'Documentation checks'
    foreach ($result in (Invoke-DocumentationCheck -Root $Root)) {
        $failures = [string[]]$result.Failures.ToArray()
        Write-CheckLine -Name $result.Name -Checked $result.Checked -Failed $failures.Length -Unit $result.Unit -Failures $failures
        if ($failures.Length -gt 0) { $failed = $true }
    }

    $scripts = @(Get-RepositoryFile -Root $Root -Pattern @('*.ps1', '*.psm1'))
    $manifest = Install-PinnedModule -Name 'PSScriptAnalyzer' -Root $Root
    Import-Module $manifest -Force
    $version = (Get-Module PSScriptAnalyzer).Version
    $settings = Join-Path $Root 'scripts\PSScriptAnalyzerSettings.psd1'
    $findings = New-Object System.Collections.Generic.List[string]
    foreach ($file in $scripts) {
        foreach ($record in @(Invoke-ScriptAnalyzer -Path (Join-Path $Root $file) -Settings $settings)) {
            $findings.Add("${file}:$($record.Line): [$($record.Severity)] $($record.RuleName): $($record.Message)")
        }
    }
    Write-Host "PSScriptAnalyzer $version"
    $scriptFailures = [string[]]$findings.ToArray()
    if ($scripts.Length -eq 0) {
        $scriptFailures = @('No PowerShell script is tracked: nothing was analyzed.')
    }
    Write-CheckLine -Name 'PowerShell scripts analyzed' -Checked $scripts.Length -Failed $scriptFailures.Length -Unit 'findings' -Failures $scriptFailures
    if ($scriptFailures.Length -gt 0) { $failed = $true }

    if ($failed) { return 1 }
    return 0
}

function Invoke-UnitLevel {
    <#
    Runs the unit level: the Pester tests of the scripts, tests/scripts/*.Tests.ps1, with the pinned Pester. Returns
    0 only when at least one test ran and none failed; skipped tests are reported and never count as passed. Returns
    1 when there is no test file. Not exported.
    #>
    param([string]$Root)

    $files = @(Get-RepositoryFile -Root $Root -Pattern @('tests/scripts/*.Tests.ps1'))
    if ($files.Length -eq 0) {
        Write-Host "Test level 'unit' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }

    $manifest = Install-PinnedModule -Name 'Pester' -Root $Root
    # The inbox Pester 3.4.0 may already be loaded; two Pester versions in one session conflict.
    Get-Module Pester | Remove-Module -Force
    Import-Module $manifest -Force
    $configuration = New-PesterConfiguration
    $configuration.Run.Path = [string[]]@($files | ForEach-Object { Join-Path $Root $_ })
    $configuration.Run.PassThru = $true
    $configuration.Output.Verbosity = 'Detailed'
    $run = Invoke-Pester -Configuration $configuration

    Write-Host ("Pester {0}: {1} passed, {2} failed, {3} skipped, {4} not run" -f (Get-Module Pester).Version, $run.PassedCount, $run.FailedCount, $run.SkippedCount, $run.NotRunCount)
    if ($run.Result -ne 'Passed' -or $run.FailedCount -gt 0 -or $run.PassedCount -eq 0) { return 1 }
    return 0
}

function Invoke-LyrveilTest {
    <#
    Runs "test <level>" for the repository at Root and returns the exit status: 0 when the level ran tests and all
    passed, 1 otherwise. An unknown level, a missing or extra argument, and a level with no tests yet all return 1
    with a message, so no level reports a pass without running tests. "all" runs lint, unit, integration, e2e and
    docker, and passes only when each of them does.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Level = @()
    )

    $known = @($script:Runners.Keys) + @('all')
    if (@($Level).Length -ne 1) {
        Write-Host "Usage: scripts\lyrveil.cmd test <level>. Levels: $($known -join ', ')."
        return 1
    }
    $name = $Level[0]
    if ($known -notcontains $name) {
        Write-Host "Unknown test level '$name'. Levels: $($known -join ', ')."
        return 1
    }

    $selected = if ($name -eq 'all') { $script:AllLevels } else { @($name) }
    $statuses = [ordered]@{}
    foreach ($item in $selected) {
        $runner = $script:Runners[$item]
        if ($null -eq $runner) {
            Write-Host "Test level '$item' has no tests yet. Nothing ran, so it cannot pass."
            $statuses[$item] = 1
            continue
        }
        Write-Host "== test $item"
        $statuses[$item] = [int](& $runner -Root $Root)
    }

    $failedLevels = @($statuses.Keys | Where-Object { $statuses[$_] -ne 0 })
    if ($failedLevels.Length -gt 0) {
        Write-Host "test ${name}: FAILED ($($failedLevels -join ', '))"
        return 1
    }
    Write-Host "test ${name}: passed"
    return 0
}

Export-ModuleMember -Function Invoke-LyrveilTest
