# Runs the levels of "scripts\lyrveil.cmd test" and never reports a pass for a level that ran no test.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'DocumentationChecks.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'DockerfileChecks.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'PinnedTools.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'PythonComponents.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'FrontendComponent.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'EndToEnd.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'GpuLevel.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'GpuTuning.psm1') -Force

# Every level named in docs/testing.md. A level without a runner has no tests yet and always fails.
$script:Runners = [ordered]@{
    lint        = 'Invoke-LintLevel'
    unit        = 'Invoke-UnitLevel'
    integration = 'Invoke-IntegrationLevel'
    e2e         = 'Invoke-EndToEndLevel'
    docker      = 'Invoke-DockerLevel'
    gpu         = 'Invoke-GpuTestLevel'
    quality     = $null
}

# The status a runner returns when its level cannot run on this machine, as gpu without a GPU. It is reported as
# skipped, and the command still exits 1, because no test ran.
$script:SkippedStatus = 2

# "all" runs every level except gpu and quality, which need the reference hardware and are always explicit.
$script:AllLevels = @('lint', 'unit', 'integration', 'e2e', 'docker')

# Levels that accept arguments after their name: unit passes them to pytest, and gpu takes them as the trials of the
# tuning procedure to run. Every other level refuses them.
$script:ArgumentLevels = @('unit', 'gpu')

# The gpu test file that a trial selection runs alone.
$script:TuningTestFile = 'tests/gpu/Tuning.Tests.ps1'

function Write-CheckLine {
    # Prints one counted result and its failures. Not exported.
    param([string]$Name, [int]$Checked, [int]$Failed, [string]$Unit, [string[]]$Failures)

    $status = if ($Failed -eq 0) { 'ok  ' } else { 'FAIL' }
    Write-Host ("  [{0}] {1}: {2} checked, {3} {4}" -f $status, $Name, $Checked, $Failed, $Unit)
    foreach ($failure in @($Failures)) {
        if ($failure) { Write-Host "         $failure" }
    }
}

function Invoke-ScriptLint {
    <#
    Runs the parts of the lint level that need no container: the documentation checks on the Git-tracked Markdown,
    then the pinned PSScriptAnalyzer on every Git-tracked .ps1 and .psm1 with scripts/PSScriptAnalyzerSettings.psd1.
    Returns 0 when both pass and 1 otherwise. Invoke-LintLevel runs it first, and the Windows job of the CI workflow
    runs it alone, because a hosted Windows runner cannot start the Linux containers of the other parts.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $failed = $false

    Write-Host 'Documentation checks'
    foreach ($result in (Invoke-DocumentationCheck -Root $Root)) {
        $failures = [string[]]$result.Failures.ToArray()
        Write-CheckLine -Name $result.Name -Checked $result.Checked -Failed $failures.Length -Unit $result.Unit -Failures $failures
        if ($failures.Length -gt 0) { $failed = $true }
    }

    Write-Host 'Dockerfile checks'
    $pins = Invoke-DockerfileCheck -Root $Root
    $pinFailures = [string[]]$pins.Failures.ToArray()
    Write-CheckLine -Name $pins.Name -Checked $pins.Checked -Failed $pinFailures.Length -Unit $pins.Unit -Failures $pinFailures
    if ($pinFailures.Length -gt 0) { $failed = $true }

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

function Invoke-LintLevel {
    <#
    Runs the lint level: Invoke-ScriptLint (documentation checks and PSScriptAnalyzer), then ruff check, ruff format
    --check and mypy --strict in the test stage of each Python component present, then, when frontend/ is present,
    ESLint, the token color check and the strict production ng build with Node.js on the host. Returns 0 when
    everything passes and 1 otherwise. Every part always runs, so one report shows every problem. Not exported.
    #>
    param([string]$Root)

    $failed = $false
    if ((Invoke-ScriptLint -Root $Root) -ne 0) { $failed = $true }
    if ((Invoke-PythonLint -Root $Root) -gt 0) { $failed = $true }
    if ((Invoke-FrontendLint -Root $Root) -gt 0) { $failed = $true }

    if ($failed) { return 1 }
    return 0
}

function Invoke-UnitLevel {
    <#
    Runs the unit level: the Pester tests of the scripts, tests/scripts/*.Tests.ps1, with the pinned Pester, then
    pytest on tests/unit in the test stage of each Python component present, then the frontend's Vitest and Node
    tests when frontend/ is present. With Argument, only pytest runs, with those arguments, so one test can run
    alone. Returns 0 only when every part ran tests and none failed; skipped tests are reported and never count as
    passed. Returns 1 when there is nothing to run. Not exported.
    #>
    param([string]$Root, [string[]]$Argument = @())

    $pytestArguments = [string[]]@($Argument)
    $components = @(Get-PythonComponent -Root $Root)
    $files = @()
    $frontend = $false
    if ($pytestArguments.Length -eq 0) {
        $files = @(Get-RepositoryFile -Root $Root -Pattern @('tests/scripts/*.Tests.ps1'))
        $frontend = Test-FrontendComponent -Root $Root
    }
    if ($files.Length -eq 0 -and $components.Length -eq 0 -and -not $frontend) {
        Write-Host "Test level 'unit' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }

    $failed = $false
    if ($files.Length -gt 0 -and (Invoke-PesterUnitTest -Root $Root -File $files) -ne 0) { $failed = $true }
    if ($components.Length -gt 0 -and (Invoke-PythonUnitTest -Root $Root -PytestArgument $pytestArguments) -gt 0) {
        $failed = $true
    }
    if ($frontend -and (Invoke-FrontendUnitTest -Root $Root) -ne 0) { $failed = $true }
    if ($failed) { return 1 }
    return 0
}

function Invoke-IntegrationLevel {
    <#
    Runs the integration level: pytest on tests/integration in the test stage of each Python component present, with
    SQLite on the container's own file system and no network. Returns 0 only when every component ran tests and none
    failed, and 1 when there is no Python component, so the level cannot pass without running a test. Not exported.
    #>
    param([string]$Root)

    if (@(Get-PythonComponent -Root $Root).Length -eq 0) {
        Write-Host "Test level 'integration' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }
    if ((Invoke-PythonIntegrationTest -Root $Root) -gt 0) { return 1 }
    return 0
}

function Invoke-DockerLevel {
    <#
    Runs the docker level: the Pester tests in tests/docker/*.Tests.ps1, which build and inspect images and Compose
    projects with the host's Docker. Returns 0 only when at least one test ran and none failed, and 1 when the folder
    holds no test file. Not exported.
    #>
    param([string]$Root)

    $files = @(Get-RepositoryFile -Root $Root -Pattern @('tests/docker/*.Tests.ps1'))
    if ($files.Length -eq 0) {
        Write-Host "Test level 'docker' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }
    return (Invoke-PesterUnitTest -Root $Root -File $files)
}

function Invoke-GpuTestLevel {
    <#
    Runs the gpu level: Invoke-GpuLevel of GpuLevel.psm1 with the Pester files tests/gpu/*.Tests.ps1, run with the
    pinned Pester against the real runtimes. When every argument is the name of a test file of tests/gpu without
    ".Tests.ps1" (letters, digits and hyphens, such as StructuredOutput), only those files run, so one test can run
    without the hour-long tuning procedure. Otherwise the arguments are trial selectors "<profile>/<trial>" with "*"
    wildcards: it runs only tests/gpu/Tuning.Tests.ps1 and only the trials they select, so the procedure can run in
    parts; a selector that is malformed or selects no trial of the plan returns 1 before any GPU or Docker call. Returns 0
    only when the tests ran and passed and the test project is gone, 2 (the skip status) when the machine has no GPU,
    and 1 otherwise, also when there is no test file to run. Not exported.
    #>
    param([string]$Root, [string[]]$Argument = @())

    $selection = [string[]]@($Argument | Where-Object { $_ })
    $data = $null
    # A file name has no "/" or "*", so no trial selector is ever read as one, nor a name as a Git pathspec glob.
    $named = [string[]]@()
    if ($selection.Length -gt 0 -and @($selection | Where-Object { $_ -cnotmatch '^[A-Za-z0-9-]+$' }).Length -eq 0) {
        $named = [string[]]@($selection | ForEach-Object { Get-RepositoryFile -Root $Root -Pattern @("tests/gpu/$_.Tests.ps1") })
    }
    if ($selection.Length -gt 0 -and $named.Length -eq $selection.Length) {
        # Every argument names a test file of tests/gpu, such as StructuredOutput: those files run alone, in that order.
        $files = $named
        Write-Host "GPU: only $($files -join ', ')"
    }
    elseif ($selection.Length -gt 0) {
        $problems = @(Test-GpuTuningSelection -Pattern $selection)
        if ($problems.Length -eq 0) {
            try {
                $catalog = (Read-Utf8Text -Path (Join-Path $Root 'config\model-catalog.json')) | ConvertFrom-Json
                $plan = @(Get-GpuTuningPlan -Catalog $catalog)
                $problems = @(Test-GpuTuningSelection -Plan $plan -Pattern $selection)
                if ($problems.Length -gt 0) {
                    $names = @($plan | ForEach-Object { "$($_.Profile)/$($_.Name)" })
                    $problems += "The trials are: $($names -join ', ')."
                }
            }
            catch { $problems = @("The tuning plan could not be read: $($_.Exception.Message)") }
        }
        if ($problems.Length -gt 0) {
            foreach ($problem in $problems) { Write-Host $problem }
            Write-Host "Test level 'gpu' takes trial selectors such as rocinante-x-12b/A or rocinante-x-12b/batch-*. Nothing ran."
            return 1
        }
        $files = [string[]]@(Get-RepositoryFile -Root $Root -Pattern @($script:TuningTestFile))
        $data = @{ Trial = $selection }
        Write-Host "GPU: only the tuning trials $($selection -join ' ') of $script:TuningTestFile"
    }
    else {
        $files = [string[]]@(Get-RepositoryFile -Root $Root -Pattern @('tests/gpu/*.Tests.ps1'))
    }
    $runner = {
        param([string]$RunRoot, [string[]]$RunFile, [hashtable]$RunData)
        Invoke-PesterUnitTest -Root $RunRoot -File $RunFile -Data $RunData
    }
    return (Invoke-GpuLevel -Root $Root -File $files -Runner $runner -Data $data)
}

function Invoke-PesterUnitTest {
    <#
    Runs the Pester test files File, repository-relative, with the pinned Pester, passing Data, when given, as the
    parameters of every file. Returns 0 only when at least one test ran and none failed, and 1 otherwise. Skipped
    tests do not fail the run and are reported in the summary line. Not exported.
    #>
    param([string]$Root, [string[]]$File, [hashtable]$Data = $null)

    $files = @($File)
    $manifest = Install-PinnedModule -Name 'Pester' -Root $Root
    # The inbox Pester 3.4.0 may already be loaded; two Pester versions in one session conflict.
    Get-Module Pester | Remove-Module -Force
    Import-Module $manifest -Force
    $configuration = New-PesterConfiguration
    $paths = [string[]]@($files | ForEach-Object { Join-Path $Root $_ })
    if ($null -ne $Data) {
        $configuration.Run.Container = @($paths | ForEach-Object { New-PesterContainer -Path $_ -Data $Data })
    }
    else { $configuration.Run.Path = $paths }
    $configuration.Run.PassThru = $true
    $configuration.Output.Verbosity = 'Detailed'
    $run = Invoke-Pester -Configuration $configuration

    Write-Host ("Pester {0}: {1} passed, {2} failed, {3} skipped, {4} not run" -f (Get-Module Pester).Version, $run.PassedCount, $run.FailedCount, $run.SkippedCount, $run.NotRunCount)
    if ($run.Result -ne 'Passed' -or $run.FailedCount -gt 0 -or $run.PassedCount -eq 0) { return 1 }
    return 0
}

function Invoke-LyrveilTest {
    <#
    Runs "test <level> [arguments]" for the repository at Root, where Argument holds the level and then its
    arguments, and returns the exit status: 0 when the level ran tests and all passed, 1 otherwise. An unknown or
    missing level, arguments for a level that takes none, and a level with no tests yet all return 1 with a
    message, so no level reports a pass without running tests. A level that cannot run on this machine, as gpu without
    a GPU, is reported as SKIPPED and also returns 1. Only "unit" takes arguments, which go to pytest, and "gpu",
    which takes the names of its test files or trial selectors of the tuning procedure.
    "all" runs lint, unit, integration, e2e and docker, and passes only when each of them does.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @()
    )

    $known = @($script:Runners.Keys) + @('all')
    $given = @($Argument | Where-Object { $null -ne $_ })
    if ($given.Length -eq 0) {
        Write-Host "Usage: scripts\lyrveil.cmd test <level> [pytest arguments]. Levels: $($known -join ', ')."
        return 1
    }
    $name = $given[0]
    if ($known -notcontains $name) {
        Write-Host "Unknown test level '$name'. Levels: $($known -join ', ')."
        return 1
    }
    $extra = [string[]]@($given | Select-Object -Skip 1)
    if ($extra.Length -gt 0 -and $script:ArgumentLevels -notcontains $name) {
        Write-Host "Test level '$name' takes no arguments. Only unit (pytest arguments) and gpu (test files or tuning trials) take them."
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
        if ($extra.Length -gt 0) {
            $statuses[$item] = [int](& $runner -Root $Root -Argument $extra)
        }
        else {
            $statuses[$item] = [int](& $runner -Root $Root)
        }
    }

    $failedLevels = @($statuses.Keys | Where-Object { $statuses[$_] -ne 0 -and $statuses[$_] -ne $script:SkippedStatus })
    $skippedLevels = @($statuses.Keys | Where-Object { $statuses[$_] -eq $script:SkippedStatus })
    if ($failedLevels.Length -gt 0) {
        Write-Host "test ${name}: FAILED ($($failedLevels -join ', '))"
        return 1
    }
    if ($skippedLevels.Length -gt 0) {
        Write-Host "test ${name}: SKIPPED ($($skippedLevels -join ', ')). No test ran, so it did not pass."
        return 1
    }
    Write-Host "test ${name}: passed"
    return 0
}

Export-ModuleMember -Function Invoke-LyrveilTest, Invoke-ScriptLint, Invoke-PesterUnitTest
