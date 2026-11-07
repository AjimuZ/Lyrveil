# Docker-level tests of the lifecycle commands: T-DCK-01 (start from a clean installation), T-DCK-02 (stop, also when
# already stopped), T-DCK-03 (restart), T-DCK-04 (data survives container recreation and an image upgrade) and T-DCK-05
# (health checks reach healthy, and a failing one is reported), with status, status -Json and logs. The commands run
# against the project "lyrveil-lifecycle-test" of tests/docker/compose.lifecycle-test.yaml, with its own volumes, image
# tags, port 14210 and a .env in Pester's temporary drive, so a real installation, its volumes "lyrveil-data" and
# "lyrveil-models", its .env and its backup folder are never touched. The project and its two volumes are removed
# afterwards. The GPU rows are skipped, so the tests need no GPU.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\Lifecycle.psm1') -Force

    $script:ProjectName = 'lyrveil-lifecycle-test'
    $script:ComposeFile = @('compose.yaml', 'tests/docker/compose.lifecycle-test.yaml')
    $script:UnhealthyFile = 'tests/docker/compose.lifecycle-unhealthy.yaml'
    $script:VolumeNames = @('lyrveil-lifecycle-test-data', 'lyrveil-lifecycle-test-models')
    $script:DataVolume = 'lyrveil-lifecycle-test-data'
    $script:Images = @('lyrveil/backend:lifecycle-test', 'lyrveil/frontend:lifecycle-test', 'lyrveil/orchestrator:lifecycle-test')
    $script:BackendImage = 'lyrveil/backend:lifecycle-test'
    $script:Example = Join-Path $script:RepositoryRoot '.env.example'
    $script:Url = 'http://localhost:14210'
    $script:ReadyLine = "Lyrveil $([char]0x00E8) pronto: $script:Url"

    # A machine on which every preflight row passes, as in Setup.Tests.ps1. The real Docker still runs every command.
    $script:PassingFact = @{
        GitVersion = 'git version 2.47.0.windows.1'; DockerCli = $true; DockerReachable = $true
        ComposeVersion = [version]'5.6.1'; ApiVersion = [version]'1.52'; MemoryBytes = [long]24GB
        WindowsMemoryBytes = [long]32GB; DiskDrive = 'C:'; FreeDiskBytes = [long]100GB; AllowLowMemory = $false
    }

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Get-TestComposeArgument {
        # Returns the leading arguments of a "docker compose" call on the test project, with the template as env file.
        $arguments = @('compose', '-p', $script:ProjectName, '--env-file', $script:Example)
        foreach ($file in $script:ComposeFile) { $arguments += @('-f', (Join-Path $script:RepositoryRoot $file)) }
        return $arguments
    }

    function Invoke-Captured {
        # Runs the lifecycle function Name with Parameters and returns its Status, the Text it printed with Write-Host,
        # and the last line of that text that is not empty.
        param([string]$Name, [hashtable]$Parameters)
        $items = @(& $Name @Parameters 6>&1)
        $status = @($items | Where-Object { $_ -is [int] })
        $lines = @($items | Where-Object { $_ -isnot [int] } | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
        $last = if ($lines.Length -gt 0) { $lines[-1] } else { '' }
        return [pscustomobject]@{ Status = $status[-1]; Text = ($lines -join "`n"); Last = $last }
    }

    function Test-TestVolume {
        # Returns $true when the test volume Name exists. Its "no such volume" message is discarded.
        param([string]$Name)
        $ErrorActionPreference = 'Continue'
        $null = & docker volume inspect $Name 2>&1
        return ($LASTEXITCODE -eq 0)
    }

    function Clear-TestContainer {
        # Removes the containers and networks of the test project, never its volumes, which are external.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-TestComposeArgument) + @('--profile', 'ai', '--profile', 'tools', 'down', '--remove-orphans', '--timeout', '5')) 2>&1
    }

    function Clear-LifecycleTestProject {
        # Removes the containers and networks of the test project, then its two volumes. Only test names are used.
        Clear-TestContainer
        $ErrorActionPreference = 'Continue'
        foreach ($name in $script:VolumeNames) {
            if (Test-TestVolume -Name $name) { $null = & docker volume rm $name 2>&1 }
        }
    }

    function Get-ServiceHealth {
        # Returns "<service> <state> <health>" for each container of the test project, sorted.
        $names = @(Invoke-Docker @('ps', '--all', '--filter', "label=com.docker.compose.project=$script:ProjectName", '--format', '{{.Names}}') | Where-Object { $_ })
        $rows = foreach ($name in $names) {
            $state = (Invoke-Docker @('inspect', '--format', '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}', $name)) -join ''
            "$name $state".Trim()
        }
        return [string[]]@($rows | Sort-Object)
    }

    function Invoke-DataVolume {
        # Runs Command with sh in a one-off container that mounts the test data volume at /volume as the service user,
        # with no network, and returns its output.
        param([string]$Command)
        return @(Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '10001:10001', '--entrypoint', 'sh',
                '--volume', "${script:DataVolume}:/volume", $script:BackendImage, '-c', $Command))
    }
}

Describe 'Lifecycle commands against a test project (T-DCK-01 to T-DCK-05)' {
    BeforeAll {
        # Refuse to run unless the resolved configuration names only the test project, its volumes and its port.
        $config = (Invoke-Docker ((Get-TestComposeArgument) + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
        $config.name | Should -Be $script:ProjectName
        foreach ($key in @('lyrveil-data', 'lyrveil-models')) {
            $script:VolumeNames | Should -Contain $config.volumes.$key.name
        }
        foreach ($service in $config.services.PSObject.Properties) {
            $service.Value.PSObject.Properties.Name | Should -Not -Contain 'container_name' -Because "$($service.Name) must not take the name of an installation's container"
        }
        @($config.services.'lyrveil-frontend'.ports)[0].published | Should -Be '14210'

        $installation = Join-Path $TestDrive 'installation'
        $null = New-Item -ItemType Directory -Force -Path $installation
        $script:EnvPath = Join-Path $installation '.env'
        $script:Common = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:EnvPath; ExamplePath = $script:Example
        }
        $script:StartParameters = $script:Common + @{ PreflightFact = $script:PassingFact; SkipGpuPreflight = $true }
        Clear-LifecycleTestProject
    }

    AfterAll {
        Clear-LifecycleTestProject
        foreach ($name in $script:VolumeNames) {
            Test-TestVolume -Name $name | Should -BeFalse -Because "the test volume $name must be removed"
        }
    }

    It 'starts from a clean installation: runs setup, and ends with the ready line (T-DCK-01)' {
        Test-Path -LiteralPath $script:EnvPath | Should -BeFalse
        $run = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:StartParameters
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'Missing: \.env, volume lyrveil-lifecycle-test-data'
        $run.Text | Should -Match 'Setup complete\.'
        $run.Text | Should -Match 'PASS     Data volume: lyrveil-lifecycle-test-data exists with the label'
        $run.Last | Should -BeExactly $script:ReadyLine
        Test-Path -LiteralPath $script:EnvPath | Should -BeTrue
    }

    It 'warns that the model catalog, the runtimes and the orchestrator are not available, without failing' {
        $run = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:StartParameters
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'present'
        $run.Text | Should -Not -Match 'so setup runs'
        $run.Text | Should -Match 'WARNING  Model files: not checked, because the model catalog is not available yet'
        $run.Text | Should -Match 'WARNING  Text model: not started, because the model catalog and the runtimes are not available yet'
        $run.Text | Should -Match 'WARNING  lyrveil-orchestrator: not started'
        $run.Last | Should -BeExactly $script:ReadyLine
    }

    It 'reaches healthy, and the empty app answers at the URL (T-DCK-05)' {
        $health = Get-ServiceHealth
        $health | Should -Contain 'lyrveil-lifecycle-test-lyrveil-backend-1 running healthy'
        $health | Should -Contain 'lyrveil-lifecycle-test-lyrveil-frontend-1 running healthy'
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:14210/' -UseBasicParsing -TimeoutSec 10
        $response.StatusCode | Should -Be 200
    }

    It 'reports WAL, FTS5 and the migrations as ok from inside the real backend image (P01 exit)' {
        # The image's own Python and SQLite answer. The backend refuses a Host other than its own port, 4200, and the
        # test project publishes 14210 only on the frontend, so the request goes to the backend with the Host it expects.
        # A 503 makes urlopen raise, which makes exec exit with a status other than 0 and Invoke-Docker throw.
        $code = "import urllib.request as u; r = u.Request('http://127.0.0.1:8000/api/v1/health/ready', headers={'Host': 'localhost:4200'}); print(u.urlopen(r, timeout=5).read().decode())"
        $output = (Invoke-Docker ((Get-TestComposeArgument) + @('exec', '-T', 'lyrveil-backend', 'python', '-c', $code))) -join "`n"
        $report = $output | ConvertFrom-Json
        $report.status | Should -Be 'ready' -Because $output
        $report.checks.database.ok | Should -BeTrue -Because $output
        $report.checks.database.journal_mode | Should -Be 'wal' -Because $output
        $report.checks.fts5.ok | Should -BeTrue -Because $output
        $report.checks.migrations.ok | Should -BeTrue -Because $output
    }

    It 'reports services, health and the URL with status' {
        $run = Invoke-Captured -Name 'Invoke-LyrveilStatus' -Parameters $script:Common
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'lyrveil-backend\s+running, healthy'
        $run.Text | Should -Match 'lyrveil-frontend\s+running, healthy'
        $run.Text | Should -Match 'lyrveil-orchestrator\s+missing \(not started by start\.cmd until P02\)'
        $run.Text | Should -Match ([regex]::Escape("App: $script:Url, answers"))
        $run.Text | Should -Match 'Lyrveil: ready'
        $run.Text | Should -Match 'WARNING  Model verification: not available yet'
    }

    It 'reports the same as JSON with status -Json' {
        $run = Invoke-Captured -Name 'Invoke-LyrveilStatus' -Parameters ($script:Common + @{ Argument = @('-Json') })
        $run.Status | Should -Be 0 -Because $run.Text
        $report = $run.Text | ConvertFrom-Json
        $report.project | Should -Be $script:ProjectName
        $report.url | Should -Be $script:Url
        $report.app_answers | Should -BeTrue
        $report.ready | Should -BeTrue
        $backend = @($report.services | Where-Object { $_.name -eq 'lyrveil-backend' })[0]
        $backend.state | Should -Be 'running'
        $backend.health | Should -Be 'healthy'
        @($report.services | Where-Object { $_.name -eq 'lyrveil-orchestrator' })[0].started_by_start | Should -BeFalse
        $report.gpu | Should -BeNullOrEmpty
        $report.active_model | Should -BeNullOrEmpty
        @($report.warnings).Count | Should -BeGreaterThan 0
        $report.disk.free_bytes | Should -BeGreaterThan 0
    }

    It 'prints the logs of one service, and refuses an option without its value' {
        (Invoke-Captured -Name 'Invoke-LyrveilLog' -Parameters ($script:Common + @{ Argument = @('lyrveil-backend', '-Tail', '5', '-Since', '10m') })).Status | Should -Be 0
        $missing = Invoke-Captured -Name 'Invoke-LyrveilLog' -Parameters ($script:Common + @{ Argument = @('-Since') })
        $missing.Status | Should -Be 1
        $missing.Text | Should -Match '-Since needs a value'
        (Invoke-Captured -Name 'Invoke-LyrveilLog' -Parameters ($script:Common + @{ Argument = @('no-such-service') })).Status | Should -Be 1
    }

    It 'stops the services and keeps containers and volumes, also when already stopped (T-DCK-02)' {
        $first = Invoke-Captured -Name 'Invoke-LyrveilStop' -Parameters $script:Common
        $first.Status | Should -Be 0 -Because $first.Text
        $first.Text | Should -Match 'Lyrveil is stopped'
        # A stopped container keeps its last health status, so only the state is compared.
        $health = (Get-ServiceHealth) -join "`n"
        $health | Should -Match '(?m)^lyrveil-lifecycle-test-lyrveil-backend-1 exited'
        $health | Should -Match '(?m)^lyrveil-lifecycle-test-lyrveil-frontend-1 exited'

        $second = Invoke-Captured -Name 'Invoke-LyrveilStop' -Parameters $script:Common
        $second.Status | Should -Be 0 -Because $second.Text
        foreach ($name in $script:VolumeNames) { Test-TestVolume -Name $name | Should -BeTrue }

        $status = Invoke-Captured -Name 'Invoke-LyrveilStatus' -Parameters $script:Common
        $status.Status | Should -Be 0
        $status.Text | Should -Match 'Lyrveil: not ready'
    }

    It 'never runs docker compose down' {
        $source = Get-Content -Raw -LiteralPath (Join-Path $script:RepositoryRoot 'scripts\lib\Lifecycle.psm1')
        $source | Should -Not -Match "'down'"
    }

    It 'restarts from stopped and from running services (T-DCK-03)' {
        $fromStopped = Invoke-Captured -Name 'Invoke-LyrveilRestart' -Parameters $script:StartParameters
        $fromStopped.Status | Should -Be 0 -Because $fromStopped.Text
        $fromStopped.Text | Should -Match 'Lyrveil is stopped'
        $fromStopped.Last | Should -BeExactly $script:ReadyLine

        $fromRunning = Invoke-Captured -Name 'Invoke-LyrveilRestart' -Parameters $script:StartParameters
        $fromRunning.Status | Should -Be 0 -Because $fromRunning.Text
        $fromRunning.Last | Should -BeExactly $script:ReadyLine
        Get-ServiceHealth | Should -Contain 'lyrveil-lifecycle-test-lyrveil-backend-1 running healthy'
    }

    It 'keeps the data through container recreation and an image upgrade (T-DCK-04)' {
        Invoke-DataVolume -Command 'echo kept > /volume/t-dck-04.txt' | Out-Null
        Invoke-DataVolume -Command 'test -e /volume/db/lyrveil.sqlite3 && echo database' | Should -Be 'database'

        # Recreation: the containers are removed and start creates new ones.
        Clear-TestContainer
        @(Get-ServiceHealth) | Should -BeNullOrEmpty
        $recreated = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:StartParameters
        $recreated.Status | Should -Be 0 -Because $recreated.Text
        Invoke-DataVolume -Command 'cat /volume/t-dck-04.txt' | Should -Be 'kept'

        # Upgrade: the images are removed, so start runs setup, which builds them again before new containers start.
        Clear-TestContainer
        foreach ($image in $script:Images) { Invoke-Docker @('image', 'rm', $image) | Out-Null }
        $upgraded = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:StartParameters
        $upgraded.Status | Should -Be 0 -Because $upgraded.Text
        $upgraded.Text | Should -Match 'Missing: image lyrveil/backend:lifecycle-test'
        $upgraded.Text | Should -Match 'Images: built'
        $upgraded.Last | Should -BeExactly $script:ReadyLine
        Invoke-DataVolume -Command 'cat /volume/t-dck-04.txt' | Should -Be 'kept'
        Invoke-DataVolume -Command 'test -e /volume/db/lyrveil.sqlite3 && echo database' | Should -Be 'database'
    }

    It 'reports a health check that fails with exit status 3 and the last logs (T-DCK-05)' {
        $parameters = $script:StartParameters.Clone()
        $parameters.ComposeFile = $script:ComposeFile + @($script:UnhealthyFile)
        $parameters.WaitSeconds = 60
        $run = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $parameters
        $run.Status | Should -Be 3 -Because $run.Text
        $run.Text | Should -Match 'FAILURE  Health checks: lyrveil-backend and lyrveil-frontend did not become healthy within 60 s'
        $run.Text | Should -Match 'Last 50 log lines'
        $run.Text | Should -Match 'exit status 3'
        $run.Text | Should -Not -Match 'pronto'
    }
}

Describe 'Lifecycle commands without Docker' {
    BeforeAll {
        # A stand-in docker.cmd, first on PATH, fails every call as a stopped Docker Desktop does.
        $deadDocker = Join-Path $TestDrive 'dead-docker'
        $null = New-Item -ItemType Directory -Force -Path $deadDocker
        [System.IO.File]::WriteAllText((Join-Path $deadDocker 'docker.cmd'), "@echo error during connect: the engine is not running 1>&2`r`n@exit /b 1`r`n")
        $script:SavedPath = $env:PATH
        $env:PATH = "$deadDocker;$env:PATH"
        $script:Offline = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = (Join-Path $TestDrive 'offline\.env'); ExamplePath = $script:Example
        }
    }

    AfterAll {
        $env:PATH = $script:SavedPath
    }

    It 'stops with exit status 0, because nothing can be running (T-DCK-02)' {
        $run = Invoke-Captured -Name 'Invoke-LyrveilStop' -Parameters $script:Offline
        $run.Status | Should -Be 0
        $run.Text | Should -Match 'Docker is not running'
    }

    It 'fails start, status and logs with exit status 2 and the advice to start Docker Desktop' {
        $start = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:Offline
        $start.Status | Should -Be 2
        $start.Text | Should -Match 'FAILURE  Docker daemon: unreachable'
        (Invoke-Captured -Name 'Invoke-LyrveilStatus' -Parameters $script:Offline).Status | Should -Be 2
        $json = Invoke-Captured -Name 'Invoke-LyrveilStatus' -Parameters ($script:Offline + @{ Argument = @('-Json') })
        ($json.Text | ConvertFrom-Json).docker_reachable | Should -BeFalse
        (Invoke-Captured -Name 'Invoke-LyrveilLog' -Parameters $script:Offline).Status | Should -Be 2
    }

    It 'refuses an unknown argument through <File> with exit status 1' -ForEach @(
        @{ File = 'start.cmd'; Arguments = @('-NoSuchOption') }
        @{ File = 'stop.cmd'; Arguments = @('-Force') }
        @{ File = 'restart.cmd'; Arguments = @('now') }
        @{ File = 'status.cmd'; Arguments = @('-Pretty') }
        @{ File = 'logs.cmd'; Arguments = @('-Tail') }
    ) {
        # The dead docker stays first on PATH, so even a broken option check could not reach a real installation.
        $null = & (Join-Path $script:RepositoryRoot $File) @Arguments
        $LASTEXITCODE | Should -Be 1
    }
}
