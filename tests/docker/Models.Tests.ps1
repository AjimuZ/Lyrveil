# Docker-level test of the models command, T-DCK-11: a download resumes with a range request, a size or hash mismatch
# keeps nothing and exits 4, a file recorded in .verified.json is skipped, only HTTPS is followed, and -List, -Verify
# and -Remove behave as documented. Licenses come first: with no console input and no -AcceptModelLicenses nothing is
# downloaded and the command exits 5.
# The command runs against the project "lyrveil-models-test" of tests/docker/compose.models-test.yaml, with its own
# volumes and image tags, a test catalog and a local HTTPS server, models-server, on the download network. A real
# installation, its volumes "lyrveil-data" and "lyrveil-models", its .env and its backup folder are never touched, and
# no URL leaves the project. The project and its volumes are removed afterwards.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:ModelsModule = Join-Path $script:RepositoryRoot 'scripts\lib\Models.psm1'
    Import-Module $script:ModelsModule -Force

    $script:ProjectName = 'lyrveil-models-test'
    $script:ComposeFile = @('compose.yaml', 'tests/docker/compose.models-test.yaml')
    $script:VolumeLabels = [ordered]@{ 'lyrveil-models-test-data' = 'data'; 'lyrveil-models-test-models' = 'models' }
    $script:ModelsVolume = 'lyrveil-models-test-models'
    $script:BackendImage = 'lyrveil/backend:models-test'
    $script:Example = Join-Path $script:RepositoryRoot '.env.example'
    # The volume's files once the defaults are downloaded: no .part file and no file of a failed download.
    $script:DefaultListing = '.verified.json,image/checkpoints/test-image.safetensors,llm/test-resume.gguf'

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Get-TestComposeArgument {
        # Returns the leading arguments of a "docker compose" call on the test project, with the tools profile.
        $arguments = @('compose', '-p', $script:ProjectName, '--env-file', $script:Example)
        foreach ($file in $script:ComposeFile) { $arguments += @('-f', (Join-Path $script:RepositoryRoot $file)) }
        return ($arguments + @('--profile', 'tools'))
    }

    function Test-TestVolume {
        # Returns $true when the test volume Name exists. Its "no such volume" message is discarded.
        param([string]$Name)
        $ErrorActionPreference = 'Continue'
        $null = & docker volume inspect $Name 2>&1
        return ($LASTEXITCODE -eq 0)
    }

    function Clear-ModelsTestProject {
        # Removes the containers, networks and own volume of the test project, then its two external volumes.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-TestComposeArgument) + @('down', '--volumes', '--remove-orphans', '--timeout', '5')) 2>&1
        foreach ($name in $script:VolumeLabels.Keys) {
            if (Test-TestVolume -Name $name) { $null = & docker volume rm $name 2>&1 }
        }
    }

    function Invoke-ModelsCommand {
        <#
        Runs the models command with Arguments on the test project and returns its Status and everything it printed
        as Text, the tool's lines included. The installation .env does not exist, so Compose interpolates from
        .env.example, and the tier is fixed to target. The Describe block replaces only the console probe, so every
        question meets the real Read-Confirmation with no console input, as in an unattended run.
        #>
        param([string[]]$Arguments = @())
        $parameters = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:EnvPath; ExamplePath = $script:Example; Tier = 'target'; Argument = $Arguments
        }
        $started = Get-Date
        $items = @(Invoke-LyrveilModel @parameters 6>&1)
        $status = @($items | Where-Object { $_ -is [int] })
        $lines = @($items | Where-Object { $_ -isnot [int] } | ForEach-Object { [string]$_ })
        $seconds = [int]((Get-Date) - $started).TotalSeconds
        return [pscustomobject]@{ Status = $status[-1]; Text = (($lines + "($seconds s)") -join "`n") }
    }

    function Invoke-ModelsVolume {
        # Runs Command with sh in a one-off container that mounts the test models volume at /models as the service
        # user, with no network, and returns its output lines.
        param([string]$Command)
        return @(Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '10001:10001', '--entrypoint', 'sh',
                '--volume', "${script:ModelsVolume}:/models", $script:BackendImage, '-c', $Command))
    }

    function Get-VolumeFile {
        # Returns the relative paths of the files in the test models volume, sorted.
        # No double quote in the command: Windows PowerShell 5.1 drops them inside a native argument.
        return [string[]]@(Invoke-ModelsVolume -Command "cd /models && find . -type f | sed 's|^[.]/||' | LC_ALL=C sort" | Where-Object { $_ })
    }

    function Get-VolumeListing {
        # Returns the files of the test models volume as one comma-separated line, for exact comparisons.
        return (@(Get-VolumeFile) -join ',')
    }

    function Get-ServerRequest {
        # Returns the REQUEST lines models-server printed so far, "REQUEST <path> range=<Range or ->".
        return [string[]]@(Invoke-Docker ((Get-TestComposeArgument) + @('logs', '--no-color', '--no-log-prefix', 'models-server')) |
                Where-Object { $_ -match '^REQUEST ' })
    }

    function Get-VerifiedPath {
        # Returns the relative paths that /models/.verified.json records.
        $text = (Invoke-ModelsVolume -Command 'cat /models/.verified.json') -join "`n"
        return [string[]]@(($text | ConvertFrom-Json).files.PSObject.Properties.Name)
    }

    function Get-Sha256 {
        # Returns the SHA-256 of the file at the relative path Path in the test models volume.
        param([string]$Path)
        return ((Invoke-ModelsVolume -Command "sha256sum '/models/$Path'") -join '').Split(' ')[0]
    }
}

Describe 'The models command against a local HTTPS server (T-DCK-11)' {
    BeforeAll {
        # Refuse to run unless the resolved configuration names only the test project and volumes, and the download
        # tool reads the test catalog and CA.
        $config = (Invoke-Docker ((Get-TestComposeArgument) + @('config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
        $config.name | Should -Be $script:ProjectName
        foreach ($key in @('lyrveil-data', 'lyrveil-models')) {
            @($script:VolumeLabels.Keys) | Should -Contain $config.volumes.$key.name
        }
        foreach ($service in $config.services.PSObject.Properties) {
            $service.Value.PSObject.Properties.Name | Should -Not -Contain 'container_name' -Because "$($service.Name) must not take the name of an installation's container"
        }
        $config.services.'lyrveil-models'.environment.LYRVEIL_MODEL_CATALOG | Should -Be '/test/catalog.json'
        $config.services.'lyrveil-models'.environment.LYRVEIL_MODELS_CA_FILE | Should -Be '/test/ca.pem'

        # As in an unattended run: Read-Confirmation prints its question and answers no at once.
        Mock -ModuleName ConsolePrompt Test-ConsoleInput { $false }

        $installation = Join-Path $TestDrive 'installation'
        $null = New-Item -ItemType Directory -Force -Path $installation
        # Never created: Compose interpolates from .env.example, and no container reads a .env.
        $script:EnvPath = Join-Path $installation '.env'

        Clear-ModelsTestProject
        $null = Invoke-Docker ((Get-TestComposeArgument) + @('build', '--quiet', 'lyrveil-backend', 'models-server'))
        foreach ($name in $script:VolumeLabels.Keys) {
            $null = Invoke-Docker @('volume', 'create', '--label', "io.lyrveil.volume=$($script:VolumeLabels[$name])", $name)
        }
        # The owner setup.cmd gives the volume root, set the same way.
        $null = Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
            '--user', '0:0', '--entrypoint', 'chown', '--volume', "${script:ModelsVolume}:/volume", $script:BackendImage, '10001:10001', '/volume')
        $null = Invoke-Docker ((Get-TestComposeArgument) + @('up', '--detach', '--wait', '--wait-timeout', '60', 'models-server'))
        # A plain "docker run" attaches no standard input, unlike "docker compose exec".
        $catalogText = (Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--entrypoint', 'cat',
                '--volume', "${script:ProjectName}_test-shared:/shared:ro", $script:BackendImage, '/shared/catalog.json')) -join "`n"
        $script:Catalog = @{}
        foreach ($item in @(($catalogText | ConvertFrom-Json).text) + @(($catalogText | ConvertFrom-Json).image)) { $script:Catalog[$item.id] = $item }
    }

    AfterAll {
        Clear-ModelsTestProject
        foreach ($name in $script:VolumeLabels.Keys) {
            Test-TestVolume -Name $name | Should -BeFalse -Because "the test volume $name must be removed"
        }
    }

    It 'downloads nothing and exits 5 without console input or -AcceptModelLicenses' {
        $run = Invoke-ModelsCommand
        $run.Status | Should -Be 5 -Because $run.Text
        $run.Text | Should -Match 'resume-model: Test resume-model'
        $run.Text | Should -Match 'License: Test-License-1\.0'
        $run.Text | Should -Match 'No console input is available, so the answer is no'
        $run.Text | Should -Match 'No model was downloaded \(exit status 5\)'
        @(Get-ServerRequest).Length | Should -Be 0
        Get-VolumeListing | Should -BeExactly ''
    }

    It 'downloads the defaults with -AcceptModelLicenses, resuming the interrupted file with a range request' {
        $run = Invoke-ModelsCommand -Arguments @('-AcceptModelLicenses')
        $run.Status | Should -Be 0 -Because $run.Text
        # The cut connection ends with an early end of stream or a TLS EOF error, depending on timing.
        $run.Text | Should -Match 'resume-model: .+\. Retry 1 of 5 in 5 s, resuming at byte [1-9]\d*\.'
        $requests = Get-ServerRequest
        $requests | Should -Contain 'REQUEST /files/resume.gguf range=-'
        @($requests | Where-Object { $_ -match '^REQUEST /files/resume\.gguf range=bytes=[1-9]\d*-$' }).Length | Should -Be 1
        $requests | Should -Contain 'REQUEST /files/image.safetensors range=-'
        Get-VolumeListing | Should -BeExactly $script:DefaultListing
        Get-Sha256 -Path 'llm/test-resume.gguf' | Should -Be $script:Catalog['resume-model'].sha256
        Get-Sha256 -Path 'image/checkpoints/test-image.safetensors' | Should -Be $script:Catalog['image-model'].sha256
        (@(Get-VerifiedPath | Sort-Object) -join ',') | Should -BeExactly 'image/checkpoints/test-image.safetensors,llm/test-resume.gguf'
    }

    It 'skips the files recorded in .verified.json without a request' {
        $before = @(Get-ServerRequest).Length
        $run = Invoke-ModelsCommand -Arguments @('-AcceptModelLicenses')
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'Every chosen model file is present and verified'
        @(Get-ServerRequest).Length | Should -Be $before
    }

    It 'keeps nothing and exits 4 when the SHA-256 differs from the catalog' {
        $run = Invoke-ModelsCommand -Arguments @('-Model', 'bad-hash-model', '-AcceptModelLicenses')
        $run.Status | Should -Be 4 -Because $run.Text
        $run.Text | Should -Match 'bad-hash-model: SHA-256 [0-9a-f]{64} differs from the catalog'
        Get-VolumeListing | Should -BeExactly $script:DefaultListing
    }

    It 'keeps nothing and exits 4 when the size differs from the catalog' {
        $run = Invoke-ModelsCommand -Arguments @('-Model', 'bad-size-model', '-AcceptModelLicenses')
        $run.Status | Should -Be 4 -Because $run.Text
        $run.Text | Should -Match 'bad-size-model: the file has \d+ bytes, the catalog says \d+'
        Get-VolumeListing | Should -BeExactly $script:DefaultListing
    }

    It 'refuses a redirect to HTTP, keeps nothing and exits 1' {
        $run = Invoke-ModelsCommand -Arguments @('-Model', 'http-redirect-model', '-AcceptModelLicenses')
        $run.Status | Should -Be 1 -Because $run.Text
        $run.Text | Should -Match 'Refused http://models-server:8080/files/redirected\.gguf: only HTTPS is allowed'
        Get-VolumeListing | Should -BeExactly $script:DefaultListing
    }

    It 'follows a redirect to HTTPS' {
        $run = Invoke-ModelsCommand -Arguments @('-Model', 'redirect-model', '-AcceptModelLicenses')
        $run.Status | Should -Be 0 -Because $run.Text
        Get-ServerRequest | Should -Contain 'REQUEST /files/redirected.gguf range=-'
        Get-Sha256 -Path 'llm/test-redirect.gguf' | Should -Be $script:Catalog['redirect-model'].sha256
    }

    It 'lists the catalog with sizes, licenses and local state with -List, and downloads nothing' {
        $before = @(Get-ServerRequest).Length
        $run = Invoke-ModelsCommand -Arguments @('-List')
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'resume-model\s+text\s+[\d.]+ GB\s+present, verified\s+Test-License-1\.0 \(default\)'
        $run.Text | Should -Match 'bad-hash-model\s+text\s+[\d.]+ GB\s+missing'
        $run.Text | Should -Match 'image-model\s+image\s+[\d.]+ GB\s+present, verified'
        @(Get-ServerRequest).Length | Should -Be $before
    }

    It 'recomputes the hashes with -Verify, and exits 4 for a changed file that it no longer records' {
        $run = Invoke-ModelsCommand -Arguments @('-Verify')
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'redirect-model: verified'

        # One byte more, so the content certainly changes.
        $null = Invoke-ModelsVolume -Command "printf 'x' >> /models/llm/test-redirect.gguf"
        $run = Invoke-ModelsCommand -Arguments @('-Verify')
        $run.Status | Should -Be 4 -Because $run.Text
        $run.Text | Should -Match 'redirect-model: llm/test-redirect\.gguf differs from the catalog'
        Get-VerifiedPath | Should -Not -Contain 'llm/test-redirect.gguf'
        Get-VolumeFile | Should -Contain 'llm/test-redirect.gguf'
    }

    It 'refuses to remove an active profile with -Remove' {
        $run = Invoke-ModelsCommand -Arguments @('-Remove', 'resume-model')
        $run.Status | Should -Be 1 -Because $run.Text
        $run.Text | Should -Match 'resume-model is an active profile, so its file is kept'
        Get-VolumeFile | Should -Contain 'llm/test-resume.gguf'
    }

    It 'asks before removing, and deletes nothing without console input' {
        $run = Invoke-ModelsCommand -Arguments @('-Remove', 'redirect-model')
        $run.Status | Should -Be 5 -Because $run.Text
        $run.Text | Should -Match 'Delete llm/test-redirect\.gguf \([\d.]+ GB\) of the profile redirect-model'
        Get-VolumeFile | Should -Contain 'llm/test-redirect.gguf'
    }

    It 'removes the file after a yes' {
        Mock -ModuleName Models Read-Confirmation { $true }
        $parameters = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:EnvPath; ExamplePath = $script:Example; Tier = 'target'; Argument = @('-Remove', 'redirect-model')
        }
        $status = @(Invoke-LyrveilModel @parameters 6>&1 | Where-Object { $_ -is [int] })
        $status[-1] | Should -Be 0
        Should -Invoke -ModuleName Models Read-Confirmation -Times 1 -Exactly
        Get-VolumeFile | Should -Not -Contain 'llm/test-redirect.gguf'
    }

    It 'refuses conflicting options with exit status 1 before any Docker call' {
        (Invoke-ModelsCommand -Arguments @('-List', '-Verify')).Status | Should -Be 1
        (Invoke-ModelsCommand -Arguments @('-All', '-Model', 'resume-model')).Status | Should -Be 1
        (Invoke-ModelsCommand -Arguments @('-NoSuchOption')).Status | Should -Be 1
    }
}
