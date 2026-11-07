# Docker-level tests of setup: T-DCK-06 (two runs, one result), T-DCK-07 (preflight advice and exit status) and T-DCK-13
# (external volumes, root .cmd files that only delegate, PSScriptAnalyzer). Setup runs against the project
# "lyrveil-setup-test" of tests/docker/compose.setup-test.yaml, with its own volumes, image tags and a .env in Pester's
# temporary drive, so a real installation, its volumes "lyrveil-data" and "lyrveil-models", its .env and its backup
# folder are never touched. The project and its two volumes are removed afterwards.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\RepositoryFiles.psm1') -Force
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\PinnedTools.psm1') -Force
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\Setup.psm1') -Force

    $script:ProjectName = 'lyrveil-setup-test'
    $script:ComposeFile = @('compose.yaml', 'tests/docker/compose.setup-test.yaml')
    $script:VolumeNames = @('lyrveil-setup-test-data', 'lyrveil-setup-test-models')
    $script:Example = Join-Path $script:RepositoryRoot '.env.example'
    $script:OwnerImage = 'lyrveil/backend:setup-test'

    # A machine on which every preflight row passes. Each case of T-DCK-07 changes one fact.
    $script:PassingFact = @{
        GitVersion = 'git version 2.47.0.windows.1'; DockerCli = $true; DockerReachable = $true
        ComposeVersion = [version]'5.6.1'; ApiVersion = [version]'1.52'; MemoryBytes = [long]24GB
        WindowsMemoryBytes = [long]32GB; DiskDrive = 'C:'; FreeDiskBytes = [long]100GB; AllowLowMemory = $false
    }

    function Get-Fact {
        # Returns a copy of the passing facts with the entries of Change replaced.
        param([hashtable]$Change)
        $fact = $script:PassingFact.Clone()
        foreach ($key in $Change.Keys) { $fact[$key] = $Change[$key] }
        return $fact
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

    function Invoke-CapturedSetup {
        # Runs Invoke-LyrveilSetup with Parameters and returns its Status and the Text it printed with Write-Host.
        param([hashtable]$Parameters)
        $items = @(Invoke-LyrveilSetup @Parameters 6>&1)
        $status = @($items | Where-Object { $_ -is [int] })
        $text = @($items | Where-Object { $_ -isnot [int] } | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ Status = $status[-1]; Text = ($text -join "`n") }
    }

    function Test-TestVolume {
        # Returns $true when the test volume Name exists. Its "no such volume" message is discarded.
        param([string]$Name)
        $ErrorActionPreference = 'Continue'
        $null = & docker volume inspect $Name 2>&1
        return ($LASTEXITCODE -eq 0)
    }

    function Clear-SetupTestProject {
        # Removes the containers and networks of the test project, then its two volumes, which are external and so
        # survive "down -v". Only the names of the test project are used.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-TestComposeArgument) + @('--profile', 'ai', '--profile', 'tools', 'down', '--volumes', '--remove-orphans', '--timeout', '5')) 2>&1
        foreach ($name in $script:VolumeNames) {
            if (Test-TestVolume -Name $name) { $null = & docker volume rm $name 2>&1 }
        }
    }

    function Get-SetupState {
        # Returns what setup leaves behind: the .env text, each volume's label and owner, and the project's containers.
        param([string]$EnvPath)
        $volumes = foreach ($name in $script:VolumeNames) {
            $labels = (Invoke-Docker @('volume', 'inspect', '--format', '{{json .Labels}}', $name)) -join '' | ConvertFrom-Json
            $owner = (Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '0:0', '--entrypoint', 'stat',
                    '--volume', "${name}:/volume:ro", $script:OwnerImage, '-c', '%u:%g', '/volume')) -join ''
            "$name label=$($labels.'io.lyrveil.volume') owner=$owner"
        }
        # No quotes in the template: Windows PowerShell 5.1 drops the quotes inside a native argument.
        $containers = @(Invoke-Docker @('ps', '--all', '--filter', "label=com.docker.compose.project=$script:ProjectName",
                '--format', '{{.Names}} {{.ID}} {{.State}}'))
        return [pscustomobject]@{
            Env        = Read-Utf8Text -Path $EnvPath
            Volumes    = [string[]]@($volumes)
            Containers = [string[]]@($containers | Sort-Object)
        }
    }
}

Describe 'Preflight failures (T-DCK-07)' {
    BeforeAll {
        $script:FailureEnv = Join-Path $TestDrive 'preflight\.env'
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script:FailureEnv)
        $script:FailureParameters = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:FailureEnv; ExamplePath = $script:Example
        }
    }

    It 'passes every row on a machine that meets the requirements' {
        $rows = @(Get-PreflightResult -Fact $script:PassingFact)
        $rows.Count | Should -Be 6
        @($rows | Where-Object { $_.Level -ne 'PASS' }) | Should -BeNullOrEmpty
    }

    It 'fails <Row> with its advice, exits 2 and changes nothing' -ForEach @(
        @{ Row = 'Git'; Change = @{ GitVersion = $null }; Advice = 'Install Git for Windows' }
        @{ Row = 'Docker daemon'; Change = @{ DockerCli = $false; DockerReachable = $false; ComposeVersion = $null; ApiVersion = $null; MemoryBytes = $null }; Advice = 'Install Docker Desktop with the WSL2 backend' }
        @{ Row = 'Docker daemon'; Change = @{ DockerReachable = $false; ApiVersion = $null; MemoryBytes = $null }; Advice = 'Start Docker Desktop' }
        @{ Row = 'Compose'; Change = @{ ComposeVersion = [version]'2.29.7' }; Advice = 'Compose v2\.30 or later' }
        @{ Row = 'Engine API'; Change = @{ ApiVersion = [version]'1.44' }; Advice = 'Engine API 1\.45 or later' }
        @{ Row = 'Memory seen by Docker'; Change = @{ MemoryBytes = [long]8GB }; Advice = 'memory=24GB and swap=8GB' }
        @{ Row = 'Memory seen by Docker'; Change = @{ MemoryBytes = [long]8GB; WindowsMemoryBytes = [long]16GB }; Advice = 'memory=12GB and swap=16GB' }
        @{ Row = 'Free disk'; Change = @{ FreeDiskBytes = [long]4GB }; Advice = 'Free space on C:' }
    ) {
        # Variable names ignore case, so the judged row must not be called $row: that would overwrite $Row.
        $rows = @(Get-PreflightResult -Fact (Get-Fact -Change $Change))
        $judged = @($rows | Where-Object { $_.Name -eq $Row })[0]
        $judged.Level | Should -Be 'FAILURE'
        $judged.Advice | Should -Match $Advice

        $run = Invoke-CapturedSetup -Parameters ($script:FailureParameters + @{ PreflightFact = (Get-Fact -Change $Change) })
        $run.Status | Should -Be 2
        $run.Text | Should -Match "FAILURE  ${Row}:"
        $run.Text | Should -Match $Advice
        $run.Text | Should -Match 'exit status 2'
        Test-Path -LiteralPath $script:FailureEnv | Should -BeFalse -Because 'a failed preflight changes nothing'
    }

    It 'warns without failing on <Name>' -ForEach @(
        @{ Name = 'memory of the reduced tier'; Change = @{ MemoryBytes = [long]15GB } }
        @{ Name = 'low memory allowed by LYRVEIL_ALLOW_LOW_MEMORY'; Change = @{ MemoryBytes = [long]8GB; AllowLowMemory = $true } }
        @{ Name = 'free disk under 20 GiB'; Change = @{ FreeDiskBytes = [long]10GB } }
    ) {
        $rows = @(Get-PreflightResult -Fact (Get-Fact -Change $Change))
        @($rows | Where-Object { $_.Level -eq 'FAILURE' }) | Should -BeNullOrEmpty
        @($rows | Where-Object { $_.Level -eq 'WARNING' }).Count | Should -Be 1
    }

    It 'never passes a row that was not read: an unreachable daemon fails the rows that need it' {
        $rows = @(Get-PreflightResult -Fact (Get-Fact -Change @{ DockerReachable = $false; ApiVersion = $null; MemoryBytes = $null }))
        foreach ($name in @('Docker daemon', 'Engine API', 'Memory seen by Docker')) {
            @($rows | Where-Object { $_.Name -eq $name })[0].Level | Should -Be 'FAILURE'
        }
    }

    Context 'with a docker command whose daemon never answers' {
        BeforeAll {
            # A stand-in docker.cmd, first on PATH, fails every call as a stopped Docker Desktop does.
            $script:DeadDocker = Join-Path $TestDrive 'dead-docker'
            $null = New-Item -ItemType Directory -Force -Path $script:DeadDocker
            [System.IO.File]::WriteAllText((Join-Path $script:DeadDocker 'docker.cmd'), "@echo error during connect: the engine is not running 1>&2`r`n@exit /b 1`r`n")
            $script:SavedPath = $env:PATH
            $env:PATH = "$script:DeadDocker;$env:PATH"
        }

        AfterAll {
            $env:PATH = $script:SavedPath
        }

        It 'reads the facts from the machine, prints the advice to start Docker Desktop and exits 2' {
            $run = Invoke-CapturedSetup -Parameters $script:FailureParameters
            $run.Status | Should -Be 2
            $run.Text | Should -Match 'FAILURE  Docker daemon: unreachable'
            $run.Text | Should -Match 'Start Docker Desktop'
            Test-Path -LiteralPath $script:FailureEnv | Should -BeFalse
        }

        It 'refuses an unknown and a planned option through setup.cmd with exit status 1' {
            # The dead docker stays first on PATH, so even a broken option check would stop at the preflight.
            $setup = Join-Path $script:RepositoryRoot 'setup.cmd'
            $null = & $setup -NoSuchOption
            $LASTEXITCODE | Should -Be 1
            $null = & $setup -VerifyModels
            $LASTEXITCODE | Should -Be 1
        }
    }
}

Describe 'The .env file' {
    BeforeAll {
        $script:Template = Join-Path $TestDrive 'template.env'
        [System.IO.File]::WriteAllText($script:Template, "LYRVEIL_HTTP_PORT=4200`r`nLYRVEIL_CONTROL_TOKEN=generated-by-setup`r`nLYRVEIL_TZ=Europe/Rome`r`n")
    }

    It 'is never overwritten' {
        $path = Join-Path $TestDrive 'existing.env'
        [System.IO.File]::WriteAllText($path, "LYRVEIL_CONTROL_TOKEN=mine`n")
        Initialize-EnvironmentFile -Path $path -Example $script:Template | Should -Be 'kept'
        Read-Utf8Text -Path $path | Should -BeExactly "LYRVEIL_CONTROL_TOKEN=mine`n"
    }

    It 'gets a new random token each time, and keeps the other lines of the template' {
        $first = Join-Path $TestDrive 'first.env'
        $second = Join-Path $TestDrive 'second.env'
        Initialize-EnvironmentFile -Path $first -Example $script:Template | Should -Be 'created'
        Initialize-EnvironmentFile -Path $second -Example $script:Template | Should -Be 'created'
        $pattern = '(?m)^LYRVEIL_CONTROL_TOKEN=([0-9a-f]{64})\r?$'
        $one = [regex]::Match((Read-Utf8Text -Path $first), $pattern)
        $two = [regex]::Match((Read-Utf8Text -Path $second), $pattern)
        $one.Success | Should -BeTrue
        $two.Success | Should -BeTrue
        $one.Groups[1].Value | Should -Not -Be $two.Groups[1].Value
        Read-Utf8Text -Path $first | Should -Match '(?m)^LYRVEIL_TZ=Europe/Rome\r?$'
        Test-Path -LiteralPath "$first.setup-tmp" | Should -BeFalse
    }

    It 'grants access to the current user only, with no inherited entry' {
        $path = Join-Path $TestDrive 'acl.env'
        $null = Initialize-EnvironmentFile -Path $path -Example $script:Template
        $acl = Get-Acl -Path $path
        $acl.AreAccessRulesProtected | Should -BeTrue
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $rules = @($acl.Access)
        $rules.Count | Should -BeGreaterThan 0
        foreach ($rule in $rules) {
            $rule.IsInherited | Should -BeFalse
            $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value | Should -Be $sid
        }
    }

    It 'is not created when the template is missing' {
        $path = Join-Path $TestDrive 'orphan.env'
        { Initialize-EnvironmentFile -Path $path -Example (Join-Path $TestDrive 'missing.env') } | Should -Throw
        Test-Path -LiteralPath $path | Should -BeFalse
    }
}

Describe 'Setup run twice against a test project (T-DCK-06, T-DCK-13)' {
    BeforeAll {
        # Refuse to run unless the resolved configuration names only the test volumes.
        $config = (Invoke-Docker ((Get-TestComposeArgument) + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
        $config.name | Should -Be $script:ProjectName
        foreach ($key in @('lyrveil-data', 'lyrveil-models')) {
            $script:VolumeNames | Should -Contain $config.volumes.$key.name
        }

        $script:Installation = Join-Path $TestDrive 'installation'
        $null = New-Item -ItemType Directory -Force -Path $script:Installation
        $script:EnvPath = Join-Path $script:Installation '.env'
        $script:SetupParameters = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:EnvPath; ExamplePath = $script:Example
        }

        $script:FirstState = $null
        $script:SecondState = $null
        Clear-SetupTestProject
        $script:First = Invoke-CapturedSetup -Parameters $script:SetupParameters
        if ($script:First.Status -eq 0) { $script:FirstState = Get-SetupState -EnvPath $script:EnvPath }
        $script:Second = Invoke-CapturedSetup -Parameters $script:SetupParameters
        if ($script:Second.Status -eq 0) { $script:SecondState = Get-SetupState -EnvPath $script:EnvPath }
    }

    AfterAll {
        Clear-SetupTestProject
        foreach ($name in $script:VolumeNames) {
            Test-TestVolume -Name $name | Should -BeFalse -Because "the test volume $name must be removed"
        }
    }

    It 'succeeds on the first run and creates .env, the volumes and the runtime container' {
        $script:First.Status | Should -Be 0 -Because $script:First.Text
        $script:First.Text | Should -Match 'created with a new control token'
        $script:First.Text | Should -Match 'Volume lyrveil-setup-test-data: created'
        $script:First.Text | Should -Match 'lyrveil-llm, lyrveil-image created, not started'
    }

    It 'succeeds on the second run and repeats nothing' {
        $script:Second.Status | Should -Be 0 -Because $script:Second.Text
        $script:Second.Text | Should -Match 'present, left unchanged'
        $script:Second.Text | Should -Match 'Images: all present'
        $script:Second.Text | Should -Not -Match 'Volume \S+: created'
        $script:Second.Text | Should -Not -Match 'owner set to'
    }

    It 'leaves the same .env, volumes and containers after both runs' {
        $script:SecondState.Env | Should -BeExactly $script:FirstState.Env
        $script:SecondState.Volumes | Should -Be $script:FirstState.Volumes
        $script:SecondState.Containers | Should -Be $script:FirstState.Containers
    }

    It 'writes a 64-character hexadecimal token in place of the placeholder' {
        $script:FirstState.Env | Should -Match '(?m)^LYRVEIL_CONTROL_TOKEN=[0-9a-f]{64}\r?$'
        $script:FirstState.Env | Should -Not -Match 'generated-by-setup'
    }

    It 'labels both volumes and gives their root to 10001:10001' {
        $script:FirstState.Volumes | Should -Contain 'lyrveil-setup-test-data label=data owner=10001:10001'
        $script:FirstState.Volumes | Should -Contain 'lyrveil-setup-test-models label=models owner=10001:10001'
    }

    It 'creates the runtime containers without starting them' {
        # Sorted by name, as Get-SetupState returns them.
        $script:FirstState.Containers.Count | Should -Be 2
        $script:FirstState.Containers[0] | Should -Match '^lyrveil-setup-test-lyrveil-image-1 \S+ created$'
        $script:FirstState.Containers[1] | Should -Match '^lyrveil-setup-test-lyrveil-llm-1 \S+ created$'
    }

    It 'keeps both volumes through docker compose down -v, because they are external' {
        Invoke-Docker ((Get-TestComposeArgument) + @('--profile', 'ai', '--profile', 'tools', 'down', '--volumes', '--remove-orphans', '--timeout', '5')) | Out-Null
        foreach ($name in $script:VolumeNames) {
            (Invoke-Docker @('volume', 'inspect', '--format', '{{.Name}}', $name)) -join '' | Should -Be $name
        }
    }
}

Describe 'Entry points and volumes of an installation (T-DCK-13)' {
    It 'declares lyrveil-data and lyrveil-models as external volumes in compose.yaml' {
        $config = (Invoke-Docker @('compose', '--env-file', $script:Example, '-f', (Join-Path $script:RepositoryRoot 'compose.yaml'), 'config', '--format', 'json')) -join "`n" | ConvertFrom-Json
        foreach ($key in @('lyrveil-data', 'lyrveil-models')) {
            $config.volumes.$key.external | Should -BeTrue
            $config.volumes.$key.name | Should -Be $key
        }
    }

    It 'has root .cmd files that only run scripts\lyrveil.ps1 with their command and arguments' {
        $files = @(Get-RepositoryFile -Root $script:RepositoryRoot -Pattern @('*.cmd') | Where-Object { $_ -notmatch '/' })
        $files | Should -Contain 'setup.cmd'
        $dispatcher = Read-Utf8Text -Path (Join-Path $script:RepositoryRoot 'scripts\lyrveil.ps1')
        foreach ($file in $files) {
            $command = [System.IO.Path]::GetFileNameWithoutExtension($file)
            $lines = @((Read-Utf8Text -Path (Join-Path $script:RepositoryRoot $file)) -split "`r?`n" | Where-Object { $_.Trim() })
            $lines.Count | Should -Be 3 -Because "$file holds a comment, the delegation and the exit"
            $lines[0] | Should -Match '^@rem '
            $lines[1] | Should -BeExactly ('@"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lyrveil.ps1" ' + $command + ' %*')
            $lines[2] | Should -BeExactly '@exit /b %ERRORLEVEL%'
            $dispatcher | Should -Match ([regex]::Escape("'$command' {")) -Because "scripts\lyrveil.ps1 must handle the command of $file"
        }
    }

    It 'passes PSScriptAnalyzer on every PowerShell script' {
        Import-Module (Install-PinnedModule -Name 'PSScriptAnalyzer' -Root $script:RepositoryRoot) -Force
        $settings = Join-Path $script:RepositoryRoot 'scripts\PSScriptAnalyzerSettings.psd1'
        $scripts = @(Get-RepositoryFile -Root $script:RepositoryRoot -Pattern @('*.ps1', '*.psm1'))
        $scripts.Count | Should -BeGreaterThan 5
        $findings = foreach ($file in $scripts) {
            foreach ($record in @(Invoke-ScriptAnalyzer -Path (Join-Path $script:RepositoryRoot $file) -Settings $settings)) {
                "${file}:$($record.Line): $($record.RuleName): $($record.Message)"
            }
        }
        @($findings) | Should -BeNullOrEmpty
    }
}
