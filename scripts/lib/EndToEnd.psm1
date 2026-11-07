# Runs the end-to-end level: Playwright on the host against the compose.test.yaml project, which is always removed afterwards.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# The project of compose.test.yaml. Passed with -p so that no COMPOSE_PROJECT_NAME can point these commands at "lyrveil".
$script:ProjectName = 'lyrveil-test'

# The services the suite needs: the frontend (which starts the backend it waits for), the orchestrator over its fakes,
# which grants the reply leases, and the two fake runtimes. "up --wait" waits for the health check of each.
$script:Services = @('lyrveil-frontend', 'lyrveil-orchestrator', 'lyrveil-llm', 'lyrveil-image')

function Test-EndToEndComponent {
    <#
    Returns $true when the repository contains tests/e2e/package.json, tracked or new and not ignored, and $false
    otherwise, as in a repository without end-to-end tests or in the fixtures of the Pester tests.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    return (@(Get-RepositoryFile -Root $Root -Pattern @('tests/e2e/package.json')).Length -gt 0)
}

function Invoke-EndToEndProcess {
    # Runs a command with Argument in Directory and returns its exit status; output goes to the console. A command
    # that cannot start returns 1 with the reason, so the caller's cleanup still runs. Not exported.
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Directory,
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @()
    )

    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        Write-Host "  The command '$Command' is not available."
        return 1
    }
    Push-Location -LiteralPath $Directory
    try {
        & $Command @Argument | Out-Host
        return [int]$LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

function Get-ProjectLeftover {
    <#
    Returns a line for every container and volume that still carries the Compose project label of the test project,
    and none when it is gone. Returns a line saying so when Docker cannot be asked, so a failed check never reads as
    a clean one. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return @('docker is not available, so the removal is unverified.') }
    $label = "label=com.docker.compose.project=$script:ProjectName"
    $leftovers = New-Object System.Collections.Generic.List[string]
    $containers = @(& docker ps --all --quiet --filter $label)
    if ($LASTEXITCODE -ne 0) { return @('docker ps failed, so the removal of the containers is unverified.') }
    foreach ($id in $containers) { $leftovers.Add("container $id") }
    $volumes = @(& docker volume ls --quiet --filter $label)
    if ($LASTEXITCODE -ne 0) { return @('docker volume ls failed, so the removal of the volumes is unverified.') }
    foreach ($name in $volumes) { $leftovers.Add("volume $name") }
    return [string[]]$leftovers.ToArray()
}

function Remove-EndToEndProject {
    <#
    Removes the test project's containers, networks and volumes, orphans included, and its images stay for the next
    build's cache. Returns $true only when "down" succeeded and no container or volume with the project's label is
    left. Only the project "lyrveil-test" is named, so a running Lyrveil and its volumes are never touched. Not
    exported.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not $PSCmdlet.ShouldProcess($script:ProjectName, 'Remove the test project and its volumes')) { return $true }
    $status = Invoke-EndToEndProcess -Directory $Root -Command 'docker' -Argument @(
        'compose', '-p', $script:ProjectName, '-f', 'compose.test.yaml', 'down', '--volumes', '--remove-orphans', '--timeout', '10')
    $leftovers = @(Get-ProjectLeftover)
    foreach ($leftover in $leftovers) { Write-Host "  Left behind: $leftover" }
    return ($status -eq 0 -and $leftovers.Length -eq 0)
}

function Install-EndToEndDependency {
    <#
    Runs "npm ci --ignore-scripts" in tests/e2e when node_modules is missing or older than package-lock.json, so the
    packages come from the committed lock file, then downloads the Chromium build of the locked Playwright into the
    user's Playwright cache (a no-op when it is there). Returns $true when both are in place. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $directory = Join-Path $Root 'tests\e2e'
    $lock = Join-Path $directory 'package-lock.json'
    $installed = Join-Path $directory 'node_modules\.package-lock.json'
    $current = (Test-Path -LiteralPath $installed) -and
        ((Get-Item -LiteralPath $installed).LastWriteTimeUtc -ge (Get-Item -LiteralPath $lock).LastWriteTimeUtc)
    if (-not $current) {
        Write-Host 'End-to-end: npm ci --ignore-scripts'
        $status = Invoke-EndToEndProcess -Directory $directory -Command 'npm.cmd' -Argument @('ci', '--ignore-scripts', '--no-audit', '--no-fund')
        if ($status -ne 0) { return $false }
    }
    Write-Host 'End-to-end: playwright install chromium'
    return ((Invoke-EndToEndProcess -Directory $directory -Command 'npx.cmd' -Argument @('playwright', 'install', 'chromium')) -eq 0)
}

function Invoke-EndToEndLevel {
    <#
    Runs the e2e level: installs the locked Playwright, builds and starts the compose.test.yaml project and waits for
    its health checks, runs the Playwright suite of tests/e2e against it, then always removes the project's
    containers, networks and volumes, also when the build, the start or a test fails and when the run is interrupted.
    Returns 0 only when the suite ran and passed and the project is gone, and 1 otherwise, also when tests/e2e does
    not exist. The project has its own name, port and volumes, so a running Lyrveil and its data stay untouched; a
    LYRVEIL_TEST_HTTP_PORT of 4200 is refused for that reason. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not (Test-EndToEndComponent -Root $Root)) {
        Write-Host "Test level 'e2e' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }
    $port = if ($env:LYRVEIL_TEST_HTTP_PORT) { $env:LYRVEIL_TEST_HTTP_PORT } else { '14200' }
    if ($port -eq '4200') {
        Write-Host 'LYRVEIL_TEST_HTTP_PORT is 4200, the port of a real Lyrveil. Choose another port.'
        return 1
    }
    if (-not (Install-EndToEndDependency -Root $Root)) {
        Write-Host '  [FAIL] end-to-end: installing Playwright (see the output above)'
        return 1
    }

    $status = 1
    try {
        # A project left by an interrupted run would keep its volumes, and the suite relies on them being empty.
        Write-Host "End-to-end: clearing any earlier Compose project '$script:ProjectName'"
        $cleared = Remove-EndToEndProject -Root $Root
        Write-Host "End-to-end: starting the Compose project '$script:ProjectName' on 127.0.0.1:$port"
        $up = 1
        if ($cleared) {
            $up = Invoke-EndToEndProcess -Directory $Root -Command 'docker' -Argument (@(
                    'compose', '-p', $script:ProjectName, '-f', 'compose.test.yaml', 'up', '--build', '--detach', '--wait') + $script:Services)
        }
        if (-not $cleared) {
            Write-Host '  [FAIL] end-to-end: an earlier test project could not be removed'
        }
        elseif ($up -ne 0) {
            Write-Host "  [FAIL] end-to-end: the test project did not start (exit $up)"
            $logs = Invoke-EndToEndProcess -Directory $Root -Command 'docker' -Argument @(
                'compose', '-p', $script:ProjectName, '-f', 'compose.test.yaml', 'logs', '--tail', '40')
            if ($logs -ne 0) { Write-Host '  The logs of the test project could not be read.' }
        }
        else {
            $status = Invoke-EndToEndProcess -Directory (Join-Path $Root 'tests\e2e') -Command 'npx.cmd' -Argument @('playwright', 'test')
            $label = if ($status -eq 0) { 'ok  ' } else { 'FAIL' }
            Write-Host "  [$label] end-to-end: playwright test (exit $status)"
        }
    }
    finally {
        Write-Host "End-to-end: removing the Compose project '$script:ProjectName' and its volumes"
        if (Remove-EndToEndProject -Root $Root) {
            Write-Host '  [ok  ] end-to-end: no container or volume of the test project is left'
        }
        else {
            Write-Host '  [FAIL] end-to-end: the test project was not fully removed'
            $status = 1
        }
    }
    return $status
}

Export-ModuleMember -Function Invoke-EndToEndLevel
