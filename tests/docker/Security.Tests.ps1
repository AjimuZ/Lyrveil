# Docker-level tests of the network and container rules of docs/security.md and ADR 0003: T-DCK-08 (the backend, the
# orchestrator and the runtimes have no route to the Internet), T-DCK-09 (the production Compose file publishes only
# 127.0.0.1:4200) and T-DCK-10 (the images hold no data, weights, .env or fakes, and every service runs as 10001:10001).
# The running checks use compose.test.yaml, with the fake runtimes, as the project "lyrveil-security-test" on port
# 14213, with an empty env file in Pester's temporary drive and the project's own volumes, which are removed afterwards.
# compose.yaml is only resolved with "config", never started, so a real installation, its .env, its volumes
# "lyrveil-data" and "lyrveil-models", its ":local" images and its backup folder are never touched. No test needs the
# Internet: a missing default route is what proves there is no way out, and the frontend's route is the control.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:ProjectName = 'lyrveil-security-test'
    $script:TestPort = '14213'
    $script:ServiceUser = '10001:10001'
    # The services that must have no route out: everything but the frontend, which serves the published port.
    $script:ConfinedServices = @('lyrveil-backend', 'lyrveil-llm', 'lyrveil-image')
    # Names that exist only in tests/fakes and in the image built from it, as in TestFakes.Tests.ps1.
    $script:FakePattern = '(^|/)fakes(/|$)|fake_(kobold|comfy|docker|nvml)|scripted_server'

    # Prints whether the container has a default route, IPv4 or IPv6 (the IPv6 reject route on "lo" does not count),
    # then whether a TCP connection to a public address succeeds. Single quotes only, so Windows PowerShell passes it
    # to docker unchanged.
    $script:RouteProbe = "import os, socket; v4 = [l.split() for l in open('/proc/net/route').readlines()[1:]]; " +
        "v6 = [l.split() for l in open('/proc/net/ipv6_route')] if os.path.exists('/proc/net/ipv6_route') else []; " +
        "out = any(r[1] == '00000000' for r in v4) or any(r[0] == '0' * 32 and r[1] == '00' and r[-1] != 'lo' for r in v6); " +
        "print('default-route' if out else 'no-default-route'); s = socket.socket(); s.settimeout(3); " +
        "print('connected' if s.connect_ex(('1.1.1.1', 443)) == 0 else 'unreachable')"

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Get-TestComposeArgument {
        # Returns the leading arguments of a "docker compose" call on the test project, with the empty env file.
        return @('compose', '-p', $script:ProjectName, '--env-file', $script:EmptyEnv, '-f', (Join-Path $script:RepositoryRoot 'compose.test.yaml'))
    }

    function Clear-SecurityTestProject {
        # Removes the containers, networks and volumes of the test project only. Its volumes belong to the project.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-TestComposeArgument) + @('down', '--volumes', '--remove-orphans', '--timeout', '5')) 2>&1
    }

    function Get-ServiceContainer {
        # Returns the identifier of the running container of Service in the test project. Throws when there is none.
        param([string]$Service)
        $id = @(Invoke-Docker ((Get-TestComposeArgument) + @('ps', '--quiet', $Service)) | Where-Object { $_ })
        if ($id.Count -ne 1) { throw "the test project has $($id.Count) running containers of $Service" }
        return $id[0]
    }

    function Get-ServiceSetting {
        # Returns the items of the setting Name of a service in the resolved configuration, or none when it lacks it.
        param($Service, [string]$Name)
        $property = $Service.PSObject.Properties[$Name]
        if ($null -eq $property) { return @() }
        return @($property.Value)
    }

    function Get-ImageFile {
        # Returns the path of every entry in the file system of Image, listed from an exported container that never runs.
        param([string]$Image)
        $container = @(Invoke-Docker @('create', $Image))[0]
        $archive = Join-Path $TestDrive ("$($container.Substring(0, 12)).tar")
        try {
            $null = Invoke-Docker @('export', '--output', $archive, $container)
            # Windows' own bsdtar: from Git Bash, "tar" is GNU tar, which reads "C:\..." as a remote host and fails.
            $names = @(& (Join-Path $env:SystemRoot 'System32\tar.exe') -tf $archive)
            if ($LASTEXITCODE -ne 0) { throw "tar could not list the export of $Image" }
            return $names
        }
        finally {
            $null = & docker rm --force $container
        }
    }

    function Get-ForbiddenImagePath {
        # Returns the entries of Path, as listed by tar, that an image must never hold: an .env file, model weights, a
        # database, anything inside /data, /models or /backups (the empty mount points are allowed), and the tests or
        # fakes of the repository.
        param([string[]]$Path)
        $patterns = @(
            '(^|/)\.env(\.[^/]+)?$'
            '\.(gguf|safetensors|ckpt|pt|pth)$'
            '\.sqlite3?$'
            '\.sqlite3?-(wal|shm|journal)$'
            '^/?(data|models|backups)/.+'
            '^/?app/(tests|fakes)(/|$)'
            $script:FakePattern
        )
        return @($Path | Where-Object { $entry = $_; @($patterns | Where-Object { $entry -match $_ }).Count -gt 0 })
    }

    $script:EmptyEnv = Join-Path $TestDrive 'empty.env'
    [System.IO.File]::WriteAllText($script:EmptyEnv, '')
}

Describe 'The forbidden-path check of the image tests' {
    It 'finds <Path>' -ForEach @(
        @{ Path = 'app/.env' }
        @{ Path = '.env.local' }
        @{ Path = 'models/llm/model.gguf' }
        @{ Path = 'opt/weights/model.safetensors' }
        @{ Path = 'data/db/lyrveil.sqlite3' }
        @{ Path = 'tmp/lyrveil.sqlite3-wal' }
        @{ Path = 'data/runtime/launch.json' }
        @{ Path = 'app/tests/conftest.py' }
        @{ Path = 'app/fakes/fake_kobold.py' }
    ) {
        Get-ForbiddenImagePath -Path @($Path) | Should -Be $Path
    }

    It 'accepts <Path>' -ForEach @(
        @{ Path = 'data/' }
        @{ Path = '.dockerenv' }
        @{ Path = 'app/src/lyrveil/main.py' }
        @{ Path = 'usr/local/lib/python3.12/sqlite3/__init__.py' }
        @{ Path = 'app/.venv/lib/python3.12/site-packages/dotenv_like/env.py' }
        @{ Path = 'usr/share/nginx/html/index.html' }
    ) {
        @(Get-ForbiddenImagePath -Path @($Path)) | Should -BeNullOrEmpty
    }
}

Describe 'The production Compose file (T-DCK-09, T-DCK-10)' {
    BeforeAll {
        # Defaults only: an empty env file, and no bind address or port from this process, so the file itself is judged.
        $script:SavedBind = $env:LYRVEIL_BIND_ADDRESS
        $script:SavedPort = $env:LYRVEIL_HTTP_PORT
        Remove-Item Env:\LYRVEIL_BIND_ADDRESS, Env:\LYRVEIL_HTTP_PORT -ErrorAction SilentlyContinue
        try {
            $script:Production = (Invoke-Docker @('compose', '--env-file', $script:EmptyEnv, '-f', (Join-Path $script:RepositoryRoot 'compose.yaml'),
                    '--profile', 'ai', '--profile', 'tools', 'config', '--format', 'json')) -join "`n" | ConvertFrom-Json
        }
        finally {
            if ($null -ne $script:SavedBind) { $env:LYRVEIL_BIND_ADDRESS = $script:SavedBind }
            if ($null -ne $script:SavedPort) { $env:LYRVEIL_HTTP_PORT = $script:SavedPort }
        }
        $script:Services = @($script:Production.services.PSObject.Properties)
    }

    It 'publishes one port, 127.0.0.1:4200, on the frontend only (T-DCK-09)' {
        $published = @(foreach ($service in $script:Services) {
                foreach ($port in @(Get-ServiceSetting -Service $service.Value -Name 'ports')) {
                    [pscustomobject]@{ Service = $service.Name; HostIp = $port.host_ip; Published = [string]$port.published; Target = [string]$port.target }
                }
            })
        $published.Count | Should -Be 1
        $published[0].Service | Should -Be 'lyrveil-frontend'
        $published[0].HostIp | Should -Be '127.0.0.1'
        $published[0].Published | Should -Be '4200'
        $published[0].Target | Should -Be '8080'
    }

    It 'shares no host network namespace with any service (T-DCK-09)' {
        foreach ($service in $script:Services) {
            @(Get-ServiceSetting -Service $service.Value -Name 'network_mode') | Should -BeNullOrEmpty -Because "$($service.Name) must use the project's networks"
        }
    }

    It 'keeps the backend, the orchestrator and the runtimes on internal networks only (T-DCK-08)' {
        $networks = $script:Production.networks
        foreach ($service in $script:Services) {
            if ($service.Name -in @('lyrveil-frontend', 'lyrveil-models')) { continue }
            $names = @($service.Value.networks.PSObject.Properties.Name)
            $names.Count | Should -BeGreaterThan 0
            foreach ($name in $names) {
                @(Get-ServiceSetting -Service $networks.$name -Name 'internal') | Should -Be $true -Because "$($service.Name) joins $name, which must have no route out"
            }
        }
        # The control: the frontend serves the published port from a bridge that is not internal.
        @(Get-ServiceSetting -Service $networks.'lyrveil-edge' -Name 'internal') | Should -BeNullOrEmpty
    }

    It 'runs every service as 10001:10001 (T-DCK-10)' {
        $script:Services.Count | Should -BeGreaterThan 2
        foreach ($service in $script:Services) {
            (@(Get-ServiceSetting -Service $service.Value -Name 'user') -join '') | Should -Be $script:ServiceUser -Because "$($service.Name) must not run as root or as an image's own user"
        }
    }
}

Describe 'A running test project with the fake runtimes (T-DCK-08 to T-DCK-10)' {
    BeforeAll {
        $script:SavedTestPort = $env:LYRVEIL_TEST_HTTP_PORT
        $env:LYRVEIL_TEST_HTTP_PORT = $script:TestPort

        # Refuse to run unless the resolved configuration names only the test project, its own volumes and its port.
        $config = (Invoke-Docker ((Get-TestComposeArgument) + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
        $config.name | Should -Be $script:ProjectName
        foreach ($volume in @($config.volumes.PSObject.Properties)) {
            $volume.Value.name | Should -BeLike "$script:ProjectName`_*"
        }
        @($config.services.'lyrveil-frontend'.ports)[0].published | Should -Be $script:TestPort

        Clear-SecurityTestProject
        $null = Invoke-Docker ((Get-TestComposeArgument) + @('up', '--build', '--detach', '--wait', '--wait-timeout', '180'))
        # The test project's orchestrator is the test-only image over the fakes, so the production stage is built here
        # under the ":test" tag, and the image checks below judge it.
        Push-Location $script:RepositoryRoot
        try {
            $null = Invoke-Docker @('build', '--quiet', '--file', 'orchestrator/Dockerfile', '--target', 'runtime',
                '--tag', 'lyrveil/orchestrator:test', '.')
        }
        finally {
            Pop-Location
        }
    }

    AfterAll {
        Clear-SecurityTestProject
        if ($null -ne $script:SavedTestPort) { $env:LYRVEIL_TEST_HTTP_PORT = $script:SavedTestPort }
        else { Remove-Item Env:\LYRVEIL_TEST_HTTP_PORT -ErrorAction SilentlyContinue }
    }

    It 'gives <Service> no default route and no connection out (T-DCK-08)' -ForEach @(
        @{ Service = 'lyrveil-backend' }
        @{ Service = 'lyrveil-llm' }
        @{ Service = 'lyrveil-image' }
    ) {
        $result = @(Invoke-Docker @('exec', (Get-ServiceContainer -Service $Service), 'python', '-c', $script:RouteProbe))
        ($result -join ',') | Should -Be 'no-default-route,unreachable'
    }

    It 'gives the orchestrator no default route and no connection out (T-DCK-08)' {
        # A one-off container of the service joins the same networks, with the same user, and needs no main module.
        $result = @(Invoke-Docker ((Get-TestComposeArgument) + @('run', '--rm', '--no-deps', '-T', '--entrypoint', 'python',
                    'lyrveil-orchestrator', '-c', $script:RouteProbe)))
        ($result -join ',') | Should -Be 'no-default-route,unreachable'
    }

    It 'gives the frontend a default route, so the route check can fail' {
        $routes = @(Invoke-Docker @('exec', (Get-ServiceContainer -Service 'lyrveil-frontend'), 'cat', '/proc/net/route'))
        @($routes | Where-Object { @($_ -split '\s+')[1] -eq '00000000' }).Count | Should -BeGreaterThan 0
    }

    It 'marks every network but the edge as internal (T-DCK-08)' {
        foreach ($name in @('lyrveil-app', 'lyrveil-control', 'lyrveil-runtime')) {
            (Invoke-Docker @('network', 'inspect', '--format', '{{.Internal}}', "$script:ProjectName`_$name")) -join '' | Should -Be 'true'
        }
        (Invoke-Docker @('network', 'inspect', '--format', '{{.Internal}}', "$script:ProjectName`_lyrveil-edge")) -join '' | Should -Be 'false'
    }

    It 'publishes only the frontend, on 127.0.0.1 (T-DCK-09)' {
        (@(Invoke-Docker @('port', (Get-ServiceContainer -Service 'lyrveil-frontend')) | Where-Object { $_ }) -join ',') |
            Should -Be "8080/tcp -> 127.0.0.1:$script:TestPort"
        foreach ($service in $script:ConfinedServices) {
            @(Invoke-Docker @('port', (Get-ServiceContainer -Service $service)) | Where-Object { $_ }) | Should -BeNullOrEmpty -Because "$service must publish nothing"
        }
    }

    It 'runs <Service> as 10001:10001 (T-DCK-10)' -ForEach @(
        @{ Service = 'lyrveil-frontend' }
        @{ Service = 'lyrveil-backend' }
        @{ Service = 'lyrveil-llm' }
        @{ Service = 'lyrveil-image' }
    ) {
        (Invoke-Docker @('exec', (Get-ServiceContainer -Service $Service), 'sh', '-c', 'echo $(id -u):$(id -g)')) -join '' |
            Should -Be $script:ServiceUser
    }

    It 'runs the orchestrator as 10001:10001 (T-DCK-10)' {
        (Invoke-Docker ((Get-TestComposeArgument) + @('run', '--rm', '--no-deps', '-T', '--entrypoint', 'sh', 'lyrveil-orchestrator',
                    '-c', 'echo $(id -u):$(id -g)'))) -join '' | Should -Be $script:ServiceUser
    }

    It 'sets the user 10001:10001 in image <Image> itself (T-DCK-10)' -ForEach @(
        @{ Image = 'lyrveil/frontend:test' }
        @{ Image = 'lyrveil/backend:test' }
        @{ Image = 'lyrveil/orchestrator:test' }
        @{ Image = 'lyrveil/orchestrator-fakes:test' }
        @{ Image = 'lyrveil/fakes:test' }
    ) {
        (Invoke-Docker @('image', 'inspect', '--format', '{{.Config.User}}', $Image)) -join '' | Should -Be $script:ServiceUser
    }

    It 'holds no data, weights, .env or fakes in <Image> (T-DCK-10)' -ForEach @(
        @{ Image = 'lyrveil/frontend:test' }
        @{ Image = 'lyrveil/backend:test' }
        @{ Image = 'lyrveil/orchestrator:test' }
    ) {
        $files = @(Get-ImageFile -Image $Image)
        $files.Count | Should -BeGreaterThan 100 -Because 'an empty listing would pass for the wrong reason'
        @(Get-ForbiddenImagePath -Path $files) | Should -BeNullOrEmpty
    }

    It 'sees the fakes in the test-only image <Image>, so the image check can fail' -ForEach @(
        @{ Image = 'lyrveil/fakes:test' }
        @{ Image = 'lyrveil/orchestrator-fakes:test' }
    ) {
        @(Get-ForbiddenImagePath -Path @(Get-ImageFile -Image $Image)).Count | Should -BeGreaterThan 3
    }
}
