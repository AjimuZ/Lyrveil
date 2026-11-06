# Runs the frontend's lint, strict build and unit tests with Node.js on the host, from frontend/package.json scripts.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# Lint gates, as npm scripts of frontend/package.json. "build" fails on any build warning and on a network reference.
$script:LintScripts = @('lint', 'build')

function Test-FrontendComponent {
    <#
    Returns $true when the repository contains frontend/package.json, tracked or new and not ignored, and $false
    otherwise, as in a repository without application code or in the fixtures of the Pester tests.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    return (@(Get-RepositoryFile -Root $Root -Pattern @('frontend/package.json')).Length -gt 0)
}

function Invoke-FrontendNpm {
    # Runs npm with Argument in Root\frontend and returns its exit status; output goes to the console. Throws when
    # npm is not on PATH. Not exported.
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string[]]$Argument
    )

    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'The npm command is not available. Install Node.js 24 and try again.'
    }
    Push-Location -LiteralPath (Join-Path $Root 'frontend')
    try {
        & npm.cmd @Argument | Out-Host
        return [int]$LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function Install-FrontendDependency {
    <#
    Runs "npm ci --ignore-scripts" when frontend/node_modules is missing or older than package-lock.json, so every
    installed package comes from the committed lock file and an unchanged lock costs nothing. Returns $true when the
    dependencies are in place. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $lock = Join-Path $Root 'frontend\package-lock.json'
    $installed = Join-Path $Root 'frontend\node_modules\.package-lock.json'
    if ((Test-Path -LiteralPath $installed) -and
        ((Get-Item -LiteralPath $installed).LastWriteTimeUtc -ge (Get-Item -LiteralPath $lock).LastWriteTimeUtc)) {
        return $true
    }
    Write-Host 'Frontend: npm ci --ignore-scripts'
    return ((Invoke-FrontendNpm -Root $Root -Argument @('ci', '--ignore-scripts', '--no-audit', '--no-fund')) -eq 0)
}

function Invoke-FrontendScript {
    # Installs the dependencies if needed, then runs each npm script in Script and prints one line per script.
    # Returns the number of failed scripts, counting a failed install as one. Not exported.
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string[]]$Script
    )

    Write-Host 'Frontend (Node.js on the host, frontend/package.json)'
    if (-not (Install-FrontendDependency -Root $Root)) {
        Write-Host '  [FAIL] frontend: npm ci (see the npm output above)'
        return 1
    }
    $failed = 0
    foreach ($name in $Script) {
        $status = Invoke-FrontendNpm -Root $Root -Argument @('run', $name)
        $label = if ($status -eq 0) { 'ok  ' } else { 'FAIL' }
        Write-Host "  [$label] frontend: npm run $name (exit $status)"
        if ($status -ne 0) { $failed++ }
    }
    return $failed
}

function Invoke-FrontendLint {
    <#
    Runs the frontend's lint gates: ESLint with the T-SEC-09 rule and the token color check ("npm run lint"), then
    the production ng build, which fails on any warning or network reference ("npm run build"). Returns the number of
    failed gates, or 0 when the repository has no frontend.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not (Test-FrontendComponent -Root $Root)) { return 0 }
    return Invoke-FrontendScript -Root $Root -Script $script:LintScripts
}

function Invoke-FrontendUnitTest {
    <#
    Runs the frontend's unit tests: Vitest through ng test, then the Node tests of frontend/tools ("npm run test").
    Returns 1 when they fail or cannot run, and 0 when they pass. Requires the frontend to be present.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if ((Invoke-FrontendScript -Root $Root -Script @('test')) -gt 0) { return 1 }
    return 0
}

Export-ModuleMember -Function Invoke-FrontendLint, Invoke-FrontendUnitTest, Test-FrontendComponent
