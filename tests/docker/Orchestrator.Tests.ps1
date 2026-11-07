# Docker-level tests of the production orchestrator image (P02.6c): the "runtime" stage of orchestrator/Dockerfile starts
# with "python -m lyrveil_orchestrator.main", reaches healthy under compose.yaml's health check, answers /v1/health
# without the token and nothing else without it (T-ORC-10), drives the real Docker gate and NVML, and cannot import the
# fakes of tests/fakes. It runs tests/docker/compose.orchestrator-test.yaml as the project "lyrveil-orchestrator-test",
# with no Docker socket, no GPU, no published port and an empty env file, so a real installation is never touched.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:ProjectName = 'lyrveil-orchestrator-test'
    $script:ComposeFile = Join-Path $PSScriptRoot 'compose.orchestrator-test.yaml'
    $script:Image = 'lyrveil/orchestrator:orchestrator-test'
    $script:Service = 'lyrveil-orchestrator'
    $script:Token = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
    # Sends a GET to the control API from inside its container and prints the status, then the body. Arguments: the path,
    # then the bearer token when one is sent. An opener without the error processor returns 4xx answers instead of
    # raising. Single quotes only, so Windows PowerShell passes it to docker unchanged.
    $script:Request = "import sys, urllib.request as u; o = u.OpenerDirector(); o.add_handler(u.HTTPHandler()); " +
        "h = {'Authorization': 'Bearer ' + sys.argv[2]} if len(sys.argv) > 2 else {}; " +
        "r = o.open(u.Request('http://127.0.0.1:8100' + sys.argv[1], headers=h), timeout=5); " +
        "print(r.status); print(r.read().decode())"

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Invoke-DockerCapture {
        # Runs Docker and returns its exit Status and every line it printed, the error stream included, as Text.
        # Never throws, so a test can expect a failure.
        param([string[]]$Arguments)
        $ErrorActionPreference = 'Continue'
        $lines = @(& docker @Arguments 2>&1 | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ Status = $LASTEXITCODE; Text = ($lines -join "`n") }
    }

    function Get-ComposeArgument {
        # Returns the leading arguments of a "docker compose" call on the test project, with the empty env file.
        return @('compose', '-p', $script:ProjectName, '--env-file', $script:EmptyEnv, '-f', $script:ComposeFile)
    }

    function Clear-OrchestratorTestProject {
        # Removes the containers and the network of the test project only. It has no volume.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-ComposeArgument) + @('down', '--volumes', '--remove-orphans', '--timeout', '5')) 2>&1
    }

    function Invoke-ControlApi {
        # Returns the Status and the parsed Body of a GET on Path, with Token as the bearer token unless it is empty.
        param([string]$Path, [string]$Token = '')
        $arguments = (Get-ComposeArgument) + @('exec', '-T', $script:Service, 'python', '-c', $script:Request, $Path)
        if ($Token) { $arguments += $Token }
        $lines = @(Invoke-Docker $arguments)
        return [pscustomobject]@{ Status = [int]$lines[0]; Body = (($lines | Select-Object -Skip 1) -join "`n") | ConvertFrom-Json }
    }

    function Get-ServiceSetting {
        # Returns the items of the setting Name of a service in the resolved configuration, or none when it lacks it.
        param($Service, [string]$Name)
        $property = $Service.PSObject.Properties[$Name]
        if ($null -eq $property) { return @() }
        return @($property.Value)
    }

    $script:EmptyEnv = Join-Path $TestDrive 'empty.env'
    [System.IO.File]::WriteAllText($script:EmptyEnv, '')

    # compose.yaml is only resolved, never started.
    $script:Production = (Invoke-Docker @('compose', '--env-file', $script:EmptyEnv, '-f', (Join-Path $script:RepositoryRoot 'compose.yaml'),
            'config', '--format', 'json')) -join "`n" | ConvertFrom-Json
    $script:TestProject = (Invoke-Docker ((Get-ComposeArgument) + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
    $script:TestProject.name | Should -Be $script:ProjectName

    Clear-OrchestratorTestProject
    $null = Invoke-Docker ((Get-ComposeArgument) + @('up', '--build', '--detach', '--wait', '--wait-timeout', '120'))
    $script:Container = @(Invoke-Docker ((Get-ComposeArgument) + @('ps', '--quiet', $script:Service)) | Where-Object { $_ })[0]
}

AfterAll {
    Clear-OrchestratorTestProject
}

Describe 'The production orchestrator image' {
    It 'starts with python -m lyrveil_orchestrator.main, which compose.yaml does not override' {
        (Invoke-Docker @('image', 'inspect', '--format', '{{json .Config.Cmd}}', $script:Image)) -join '' |
            Should -Be '["python","-m","lyrveil_orchestrator.main"]'
        $service = $script:Production.services.'lyrveil-orchestrator'
        @(Get-ServiceSetting -Service $service -Name 'command') | Should -BeNullOrEmpty
        @(Get-ServiceSetting -Service $service -Name 'entrypoint') | Should -BeNullOrEmpty
    }

    It 'reaches healthy under the health check, user and hardening of compose.yaml' {
        (Invoke-Docker @('inspect', '--format', '{{.State.Health.Status}}', $script:Container)) -join '' | Should -Be 'healthy'
        $production = $script:Production.services.'lyrveil-orchestrator'
        $test = $script:TestProject.services.'lyrveil-orchestrator'
        foreach ($name in @('healthcheck', 'user', 'read_only', 'cap_drop', 'security_opt', 'init', 'tmpfs')) {
            ($test.$name | ConvertTo-Json -Compress -Depth 5) | Should -Be ($production.$name | ConvertTo-Json -Compress -Depth 5) -Because "$name must match compose.yaml"
        }
    }

    It 'answers 200 on /v1/health without a token (T-ORC-10)' {
        $answer = Invoke-ControlApi -Path '/v1/health'
        $answer.Status | Should -Be 200
        $answer.Body.status | Should -Be 'alive'
    }

    It 'answers 401 UNAUTHORIZED on <Path> without the right token (T-ORC-10)' -ForEach @(
        @{ Path = '/v1/state'; Token = '' }
        @{ Path = '/v1/preflight'; Token = '' }
        @{ Path = '/v1/no-such-route'; Token = '' }
        @{ Path = '/v1/state'; Token = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' }
    ) {
        $answer = Invoke-ControlApi -Path $Path -Token $Token
        $answer.Status | Should -Be 401
        $answer.Body.code | Should -Be 'UNAUTHORIZED'
    }

    It 'drives the real Docker gate: without the socket the state ends in ERROR with DOCKER_UNAVAILABLE' {
        # A fake gate would report the text runtime ready instead. The first reconcile runs right after start.
        $deadline = (Get-Date).AddSeconds(20)
        do {
            $answer = Invoke-ControlApi -Path '/v1/state' -Token $script:Token
            if ($answer.Body.state -eq 'ERROR') { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        $answer.Status | Should -Be 200
        $answer.Body.state | Should -Be 'ERROR'
        $answer.Body.last_error | Should -Be 'DOCKER_UNAVAILABLE'
        $answer.Body.vram | Should -BeNullOrEmpty -Because 'NVML cannot be read without a GPU'
    }

    It 'reads the card through NVML: without a GPU the preflight fails its GPU row' {
        $answer = Invoke-ControlApi -Path '/v1/preflight' -Token $script:Token
        $answer.Status | Should -Be 200
        $answer.Body.ok | Should -BeFalse
        @($answer.Body.checks)[0].check | Should -Be 'gpu_in_container'
        @($answer.Body.checks)[0].status | Should -Be 'failure'
    }

    It 'cannot import the fakes of tests/fakes' {
        $run = Invoke-DockerCapture ((Get-ComposeArgument) + @('exec', '-T', $script:Service, 'python', '-c', 'import fakes'))
        $run.Status | Should -Not -Be 0
        $run.Text | Should -Match "ModuleNotFoundError: No module named 'fakes'"
    }
}
