# Runs the quality gates and tests of the Python components inside the test stage of each component's image.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# Python components in check order. Each has <name>/Dockerfile with a "test" stage holding its sources and tests.
$script:Components = @('backend', 'orchestrator')

# Lint gates, run in the component folder of the test stage. Each must exit 0.
$script:LintGates = [ordered]@{
    'ruff check'          = @('ruff', 'check', '.')
    'ruff format --check' = @('ruff', 'format', '--check', '.')
    'mypy --strict'       = @('mypy', '--strict', 'src', 'tests')
}

function Get-PythonComponent {
    <#
    Returns the names of the Python components whose Dockerfile the repository contains, tracked or new and not
    ignored, in check order. Returns an empty array when there is none, as in a repository without application code.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $present = @(Get-RepositoryFile -Root $Root -Pattern @($script:Components | ForEach-Object { "$_/Dockerfile" }))
    return [string[]]@($script:Components | Where-Object { $present -contains "$_/Dockerfile" })
}

function Get-TestImageName {
    # Returns the local tag of the test stage image of Component.
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Component)

    return "lyrveil/$Component-test:local"
}

function Invoke-TestImageBuild {
    <#
    Builds the test stage of Component from the repository root, the build context of every image, and tags it
    with Get-TestImageName. Returns $true when the build succeeded. Build errors go to the console; Docker's own
    cache makes a build without changes take a few seconds. Throws when the Docker command is not available.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Component
    )

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'The docker command is not available. Start Docker Desktop and try again.'
    }
    $dockerfile = Join-Path $Root "$Component\Dockerfile"
    # The image ID that --quiet prints is not part of the result.
    $null = & docker build --quiet --file $dockerfile --target test --tag (Get-TestImageName -Component $Component) $Root
    return ($LASTEXITCODE -eq 0)
}

function Invoke-TestContainer {
    <#
    Runs Command in a new container of the test stage image of Component, without network access, removes the
    container, and returns its exit status. The command's output goes to the console. Requires a successful
    Invoke-TestImageBuild for Component first.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Component,
        [Parameter(Mandatory = $true)] [string[]]$Command
    )

    & docker run --rm --network none (Get-TestImageName -Component $Component) @Command | Out-Host
    return [int]$LASTEXITCODE
}

function Invoke-PythonLint {
    <#
    Builds the test stage of every Python component and runs ruff check, ruff format --check and mypy --strict in
    it. Prints one line per gate and returns the number of failed gates, counting a failed build as one. Returns 0
    when the repository has no Python component.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $failed = 0
    foreach ($component in @(Get-PythonComponent -Root $Root)) {
        Write-Host "Python component '$component' (test stage of $component/Dockerfile)"
        if (-not (Invoke-TestImageBuild -Root $Root -Component $component)) {
            Write-Host "  [FAIL] $component image build: see the Docker output above"
            $failed++
            continue
        }
        foreach ($gate in $script:LintGates.Keys) {
            $status = Invoke-TestContainer -Component $component -Command $script:LintGates[$gate]
            $label = if ($status -eq 0) { 'ok  ' } else { 'FAIL' }
            Write-Host "  [$label] ${component}: $gate (exit $status)"
            if ($status -ne 0) { $failed++ }
        }
    }
    return $failed
}

function Invoke-PythonUnitTest {
    <#
    Builds the test stage of every Python component and runs pytest on its tests/unit folder, with PytestArgument
    appended. When an argument is a path or node ID under tests/, it replaces tests/unit, so one test can run alone,
    and only the components that hold every such path run. Returns the number of components whose build or pytest
    run failed, or 1 when no component ran; pytest also fails when it collects no test.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$PytestArgument = @()
    )

    return Invoke-PythonTestFolder -Root $Root -Folder 'tests/unit' -PytestArgument $PytestArgument
}

function Invoke-PythonIntegrationTest {
    <#
    Builds the test stage of every Python component that has a tests/integration folder and runs pytest on it. The
    tests run in the same image and on the same SQLite build as production, without network access. Returns the
    number of components whose build or pytest run failed, or 1 when no component has the folder; pytest also fails
    when it collects no test.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    return Invoke-PythonTestFolder -Root $Root -Folder 'tests/integration'
}

function Invoke-PythonTestFolder {
    # Runs pytest on Folder, or on the paths in PytestArgument when it names one under tests/, in the test stage of
    # every Python component that holds Folder, or every named path. A component without them is skipped with a
    # message, since each component has its own tests. Returns the number of failed components, or 1 when no
    # component ran, so a level never passes without running a test. Not exported.
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Folder,
        [string[]]$PytestArgument = @()
    )

    $arguments = @($PytestArgument)
    $paths = @($arguments | Where-Object { $_ -like 'tests/*' -or $_ -like 'tests\*' })
    # Assigned in two steps: an if expression would unroll a one-item array into a string.
    $targets = [string[]]@()
    $required = [string[]]@($Folder)
    if ($paths.Length -gt 0) {
        # A node ID such as tests/unit/test_a.py::test_b names the file before "::".
        $required = [string[]]@($paths | ForEach-Object { ($_ -split '::', 2)[0] })
    }
    else {
        $targets = [string[]]@($Folder)
    }
    $failed = 0
    $ran = 0
    foreach ($component in @(Get-PythonComponent -Root $Root)) {
        $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path (Join-Path $Root $component) $_)) })
        if ($missing.Length -gt 0) {
            Write-Host "Python component '$component': skipped, it has no $($missing -join ', ')"
            continue
        }
        $ran++
        Write-Host "Python component '$component': pytest $(@($targets + $arguments) -join ' ')"
        if (-not (Invoke-TestImageBuild -Root $Root -Component $component)) {
            Write-Host "  [FAIL] $component image build: see the Docker output above"
            $failed++
            continue
        }
        $status = Invoke-TestContainer -Component $component -Command (@('pytest') + $targets + $arguments)
        $label = if ($status -eq 0) { 'ok  ' } else { 'FAIL' }
        Write-Host "  [$label] ${component}: pytest (exit $status)"
        if ($status -ne 0) { $failed++ }
    }
    if ($ran -eq 0) {
        Write-Host "  [FAIL] no Python component has $($required -join ', '): nothing ran"
        $failed++
    }
    return $failed
}

Export-ModuleMember -Function Get-PythonComponent, Invoke-PythonIntegrationTest, Invoke-PythonLint, Invoke-PythonUnitTest
