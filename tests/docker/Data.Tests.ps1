# Docker-level tests of import-model, backup and restore (docs/operations.md): import-model copies a .gguf to llm/ and
# a .safetensors to image/checkpoints/ and refuses an existing name; backup writes a backup into the data volume and
# copies it to the Windows backup folder of LYRVEIL_BACKUP_DIR; restore refuses a backup that fails restore-check and
# changes nothing, asks first, and otherwise replaces the data and keeps the previous data in pre-restore-<time>/; and
# T-DCK-12, a round trip of characters, conversations, assets and settings through the copy in the Windows folder,
# which tests/docker/round_trip.py seeds and reads inside the backend container. T-DCK-14 is completed in P06.
# The backend's daily backup may add a backup to the test volume at any idle moment, so no test counts backups.
# The commands run against the project "lyrveil-data-test" of tests/docker/compose.data-test.yaml, with its own volumes,
# image tags, port 14212, and a .env and a backup folder in Pester's temporary drive, so a real installation, its
# volumes "lyrveil-data" and "lyrveil-models", its .env and %USERPROFILE%\Documents\Lyrveil\backups are never touched.
# The project and its two volumes are removed afterwards. The GPU rows are skipped, so the tests need no GPU.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\DataFiles.psm1') -Force
    # DataFiles.psm1 has loaded Lifecycle.psm1; without -Force its instance is reused and only made visible here.
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\Lifecycle.psm1')
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\ToolContainer.psm1')
    $script:RoundTripScript = Join-Path $PSScriptRoot 'round_trip.py'

    $script:ProjectName = 'lyrveil-data-test'
    $script:ComposeFile = @('compose.yaml', 'tests/docker/compose.data-test.yaml')
    $script:VolumeNames = @('lyrveil-data-test-data', 'lyrveil-data-test-models')
    $script:DataVolume = 'lyrveil-data-test-data'
    $script:ModelsVolume = 'lyrveil-data-test-models'
    $script:BackendImage = 'lyrveil/backend:data-test'
    $script:Example = Join-Path $script:RepositoryRoot '.env.example'
    $script:ReadyLine = "Lyrveil $([char]0x00E8) pronto: http://localhost:14212"

    # A machine on which every preflight row passes, as in Lifecycle.Tests.ps1. The real Docker still runs every command.
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
        # Runs the command Name with Parameters and returns its Status, the Text it printed with Write-Host, and the
        # last line of that text that is not empty.
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

    function Clear-DataTestProject {
        # Removes the containers and networks of the test project, then its two volumes. Only test names are used.
        $ErrorActionPreference = 'Continue'
        $null = & docker @((Get-TestComposeArgument) + @('--profile', 'ai', '--profile', 'tools', 'down', '--remove-orphans', '--timeout', '5')) 2>&1
        foreach ($name in $script:VolumeNames) {
            if (Test-TestVolume -Name $name) { $null = & docker volume rm $name 2>&1 }
        }
    }

    function Invoke-Volume {
        # Runs Command with sh in a one-off container that mounts the test volume Volume at /volume as the service
        # user, with no network, and returns its output lines. No double quote in Command: Windows PowerShell 5.1
        # drops them inside a native argument.
        param([string]$Volume, [string]$Command)
        return @(Invoke-Docker @('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '10001:10001', '--entrypoint', 'sh',
                '--volume', "${Volume}:/volume", $script:BackendImage, '-c', $Command))
    }

    function Get-VolumeFile {
        # Returns the relative paths of the files in the test volume Volume, sorted.
        param([string]$Volume)
        return [string[]]@(Invoke-Volume -Volume $Volume -Command "cd /volume && find . -type f | sed 's|^[.]/||' | LC_ALL=C sort" | Where-Object { $_ })
    }

    function Get-VolumeSha256 {
        # Returns the SHA-256 of the file at the relative path Path in the test volume Volume.
        param([string]$Volume, [string]$Path)
        return ((Invoke-Volume -Volume $Volume -Command "sha256sum '/volume/$Path'") -join '').Split(' ')[0]
    }

    function Invoke-RoundTrip {
        # Runs tests/docker/round_trip.py with Mode (seed, change or read) in the running backend container, the
        # script on standard input so that no double quote crosses a native argument, and returns its last line.
        param([string]$Mode)
        $code = Get-Content -Raw -LiteralPath $script:RoundTripScript
        $output = @($code | & docker @((Get-TestComposeArgument) + @('exec', '-T', 'lyrveil-backend', 'python', '-', $Mode)))
        if ($LASTEXITCODE -ne 0) { throw "round_trip.py $Mode failed with exit status ${LASTEXITCODE}: $($output -join ' ')" }
        return [string]$output[-1]
    }

    function Get-WrittenBackup {
        # Returns the name of the backup that the backup command reports in Text.
        param([string]$Text)
        $Text | Should -Match 'Backup written: backups/(lyrveil-\d{8}T\d{6}Z\.tar), '
        return $Matches[1]
    }

    function Write-TestFile {
        # Writes Size pseudo-random bytes to Path and returns their SHA-256 in lowercase.
        param([string]$Path, [int]$Size, [int]$Seed)
        $bytes = New-Object byte[] $Size
        (New-Object System.Random $Seed).NextBytes($bytes)
        [System.IO.File]::WriteAllBytes($Path, $bytes)
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

Describe 'import-model, backup and restore against a test project' {
    BeforeAll {
        # Refuse to run unless the resolved configuration names only the test project, its volumes and its port.
        $config = (Invoke-Docker ((Get-TestComposeArgument) + @('--profile', 'tools', 'config', '--format', 'json'))) -join "`n" | ConvertFrom-Json
        $config.name | Should -Be $script:ProjectName
        foreach ($key in @('lyrveil-data', 'lyrveil-models')) {
            $script:VolumeNames | Should -Contain $config.volumes.$key.name
        }
        foreach ($service in $config.services.PSObject.Properties) {
            $service.Value.PSObject.Properties.Name | Should -Not -Contain 'container_name' -Because "$($service.Name) must not take the name of an installation's container"
        }
        @($config.services.'lyrveil-frontend'.ports)[0].published | Should -Be '14212'
        $config.services.'lyrveil-backend'.image | Should -Be $script:BackendImage

        # The test's own .env: the template with the backup folder in the temporary drive. No container reads it.
        $installation = Join-Path $TestDrive 'installation'
        $null = New-Item -ItemType Directory -Force -Path $installation
        $script:BackupFolder = Join-Path $TestDrive 'backups'
        $script:EnvPath = Join-Path $installation '.env'
        $lines = @(Get-Content -LiteralPath $script:Example | Where-Object { $_ -notmatch '^\s*LYRVEIL_BACKUP_DIR\s*=' })
        Set-Content -LiteralPath $script:EnvPath -Value ($lines + "LYRVEIL_BACKUP_DIR=$script:BackupFolder") -Encoding Ascii
        Get-BackupFolder -EnvPath $script:EnvPath | Should -Be $script:BackupFolder

        $script:Common = @{
            Root = $script:RepositoryRoot; ProjectName = $script:ProjectName; ComposeFile = $script:ComposeFile
            EnvPath = $script:EnvPath; ExamplePath = $script:Example
        }
        $script:StartParameters = $script:Common + @{ PreflightFact = $script:PassingFact; SkipGpuPreflight = $true }
        $script:ImportFolder = Join-Path $TestDrive 'import'
        $null = New-Item -ItemType Directory -Force -Path $script:ImportFolder

        Clear-DataTestProject
        # Start runs setup: volumes with their label and owner, images, then the backend migrates the new database.
        $run = Invoke-Captured -Name 'Invoke-LyrveilStart' -Parameters $script:StartParameters
        $run.Status | Should -Be 0 -Because $run.Text
    }

    AfterAll {
        Clear-DataTestProject
        foreach ($name in $script:VolumeNames) {
            Test-TestVolume -Name $name | Should -BeFalse -Because "the test volume $name must be removed"
        }
    }

    It 'import-model copies a .gguf to llm/ and a .safetensors to image/checkpoints/' {
        $gguf = Join-Path $script:ImportFolder 'custom-test.Q4_K_M.gguf'
        $safetensors = Join-Path $script:ImportFolder 'custom-test.safetensors'
        $ggufHash = Write-TestFile -Path $gguf -Size 3000000 -Seed 1
        $safetensorsHash = Write-TestFile -Path $safetensors -Size 1500000 -Seed 2

        $run = Invoke-Captured -Name 'Invoke-LyrveilImportModel' -Parameters ($script:Common + @{ Argument = @($gguf) })
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match "Imported llm/custom-test\.Q4_K_M\.gguf: 3000000 bytes, SHA-256 $ggufHash"
        $run = Invoke-Captured -Name 'Invoke-LyrveilImportModel' -Parameters ($script:Common + @{ Argument = @($safetensors) })
        $run.Status | Should -Be 0 -Because $run.Text

        (@(Get-VolumeFile -Volume $script:ModelsVolume) -join ',') | Should -BeExactly 'image/checkpoints/custom-test.safetensors,llm/custom-test.Q4_K_M.gguf'
        Get-VolumeSha256 -Volume $script:ModelsVolume -Path 'llm/custom-test.Q4_K_M.gguf' | Should -Be $ggufHash
        Get-VolumeSha256 -Volume $script:ModelsVolume -Path 'image/checkpoints/custom-test.safetensors' | Should -Be $safetensorsHash
    }

    It 'import-model refuses a name already in the models volume and keeps the file' {
        $other = Join-Path $TestDrive 'other'
        $null = New-Item -ItemType Directory -Force -Path $other
        $same = Join-Path $other 'custom-test.Q4_K_M.gguf'
        $null = Write-TestFile -Path $same -Size 1000 -Seed 3
        $before = Get-VolumeSha256 -Volume $script:ModelsVolume -Path 'llm/custom-test.Q4_K_M.gguf'

        $run = Invoke-Captured -Name 'Invoke-LyrveilImportModel' -Parameters ($script:Common + @{ Argument = @($same) })
        $run.Status | Should -Be 1 -Because $run.Text
        $run.Text | Should -Match 'llm/custom-test\.Q4_K_M\.gguf exists already in the models volume'
        Get-VolumeSha256 -Volume $script:ModelsVolume -Path 'llm/custom-test.Q4_K_M.gguf' | Should -Be $before
        (@(Get-VolumeFile -Volume $script:ModelsVolume) -join ',') | Should -BeExactly 'image/checkpoints/custom-test.safetensors,llm/custom-test.Q4_K_M.gguf'
    }

    It 'import-model refuses another extension and a name outside the pattern' {
        foreach ($name in @('custom-test.bin', 'custom test.gguf')) {
            $path = Join-Path $script:ImportFolder $name
            $null = Write-TestFile -Path $path -Size 10 -Seed 4
            $run = Invoke-Captured -Name 'Invoke-LyrveilImportModel' -Parameters ($script:Common + @{ Argument = @($path) })
            $run.Status | Should -Be 1 -Because $run.Text
        }
        (@(Get-VolumeFile -Volume $script:ModelsVolume) -join ',') | Should -BeExactly 'image/checkpoints/custom-test.safetensors,llm/custom-test.Q4_K_M.gguf'
    }

    It 'backup writes a backup into the data volume and copies it to the configured Windows folder' {
        $null = Invoke-Volume -Volume $script:DataVolume -Command 'mkdir -p /volume/media && printf backed-up > /volume/media/before-backup.txt'

        $run = Invoke-Captured -Name 'Invoke-LyrveilBackup' -Parameters $script:Common
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Match 'Backup written: backups/lyrveil-\d{8}T\d{6}Z\.tar, 2 files'
        $run.Text | Should -Match ([regex]::Escape("The backups are in $script:BackupFolder"))
        $name = Get-WrittenBackup -Text $run.Text

        $copies = @(Get-ChildItem -LiteralPath $script:BackupFolder -File)
        @($copies | Where-Object { $_.Name -notmatch '^lyrveil-\d{8}T\d{6}Z\.tar$' }).Length | Should -Be 0
        $script:BackupFile = Join-Path $script:BackupFolder $name
        Test-Path -LiteralPath $script:BackupFile -PathType Leaf | Should -BeTrue
        @(Get-VolumeFile -Volume $script:DataVolume) | Should -Contain "backups/$name"
        Get-VolumeSha256 -Volume $script:DataVolume -Path "backups/$name" |
            Should -Be (Get-FileHash -LiteralPath $script:BackupFile -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    It 'restore refuses a backup that fails restore-check, and stops and changes nothing' {
        Mock -ModuleName DataFiles Read-Confirmation { throw 'No question was expected.' }
        $damagedFolder = Join-Path $TestDrive 'damaged'
        $null = New-Item -ItemType Directory -Force -Path $damagedFolder
        $damaged = Join-Path $damagedFolder 'lyrveil-damaged.tar'
        # One hexadecimal digit of the first hash in manifest.json, which the archive holds as plain text, so the
        # manifest stays valid JSON and the database no longer matches it. One character per byte keeps the offsets.
        $bytes = [System.IO.File]::ReadAllBytes($script:BackupFile)
        $marker = '"sha256": "'
        $index = [System.Text.Encoding]::ASCII.GetString($bytes).IndexOf($marker) + $marker.Length
        $index | Should -BeGreaterOrEqual $marker.Length
        $bytes[$index] = if ($bytes[$index] -eq [byte][char]'0') { [byte][char]'1' } else { [byte][char]'0' }
        [System.IO.File]::WriteAllBytes($damaged, $bytes)
        $before = (@(Get-VolumeFile -Volume $script:DataVolume) -join ',')

        $run = Invoke-Captured -Name 'Invoke-LyrveilRestore' -Parameters ($script:StartParameters + @{ Argument = @($damaged) })
        $run.Status | Should -Be 1 -Because $run.Text
        $run.Text | Should -Match 'db/lyrveil\.sqlite3: its SHA-256 differs from the manifest'
        $run.Text | Should -Match 'FAILURE  Restore refused: lyrveil-damaged\.tar failed restore-check'
        $run.Text | Should -Not -Match 'Lyrveil is stopped'
        (@(Get-VolumeFile -Volume $script:DataVolume) -join ',') | Should -BeExactly $before
        (Get-LyrveilStatus -Context (Resolve-LifecycleContext @script:Common)).ready | Should -BeTrue
    }

    It 'restore asks first, and changes nothing without console input' {
        Mock -ModuleName ConsolePrompt Test-ConsoleInput { $false }
        $before = (@(Get-VolumeFile -Volume $script:DataVolume) -join ',')
        $run = Invoke-Captured -Name 'Invoke-LyrveilRestore' -Parameters ($script:StartParameters + @{ Argument = @($script:BackupFile) })
        $run.Status | Should -Be 5 -Because $run.Text
        $run.Text | Should -Match 'PASS     lyrveil-\d{8}T\d{6}Z\.tar: 2 files match the manifest'
        $run.Text | Should -Match 'Nothing was restored \(exit status 5\)'
        (@(Get-VolumeFile -Volume $script:DataVolume) -join ',') | Should -BeExactly $before
    }

    It 'restore replaces the data with the backup, keeps the previous data in a pre-restore folder, and starts' {
        Mock -ModuleName DataFiles Read-Confirmation { $true }
        $null = Invoke-Volume -Volume $script:DataVolume -Command 'printf after > /volume/media/after-backup.txt'

        $run = Invoke-Captured -Name 'Invoke-LyrveilRestore' -Parameters ($script:StartParameters + @{ Argument = @($script:BackupFile) })
        $run.Status | Should -Be 0 -Because $run.Text
        Should -Invoke -ModuleName DataFiles Read-Confirmation -Times 1 -Exactly
        $run.Text | Should -Match 'Lyrveil is stopped\. Containers, volumes and data are kept\.'
        $run.Text | Should -Match 'Restored 2 files from lyrveil-\d{8}T\d{6}Z\.tar\. The previous data is in pre-restore-\d{8}T\d{6}Z/\.'
        $run.Last | Should -BeExactly $script:ReadyLine

        $files = @(Get-VolumeFile -Volume $script:DataVolume)
        $files | Should -Contain 'media/before-backup.txt'
        $files | Should -Not -Contain 'media/after-backup.txt'
        @($files | Where-Object { $_ -match '^pre-restore-\d{8}T\d{6}Z/media/after-backup\.txt$' }).Length | Should -Be 1
        @($files | Where-Object { $_ -match '^pre-restore-\d{8}T\d{6}Z/db/lyrveil\.sqlite3$' }).Length | Should -Be 1
        @($files | Where-Object { $_ -match '^\.restore-' }).Length | Should -Be 0
        $files | Should -Contain "backups/$(Split-Path -Leaf $script:BackupFile)"
    }

    It 'a backup and restore round trip through the Windows folder keeps characters, conversations, assets and settings (T-DCK-12)' {
        Mock -ModuleName DataFiles Read-Confirmation { $true }
        $null = Invoke-RoundTrip -Mode 'seed'
        $null = Invoke-Volume -Volume $script:DataVolume -Command 'mkdir -p /volume/runtime && printf MODEL_FILE=t-dck-12.gguf > /volume/runtime/llm-launch.env'
        $before = Invoke-RoundTrip -Mode 'read'
        $state = $before | ConvertFrom-Json
        @($state.characters).Count | Should -BeGreaterThan 0
        @($state.conversations).Count | Should -BeGreaterThan 0
        @($state.assets).Count | Should -BeGreaterThan 0
        $state.setting | Should -Not -BeNullOrEmpty

        $run = Invoke-Captured -Name 'Invoke-LyrveilBackup' -Parameters $script:Common
        $run.Status | Should -Be 0 -Because $run.Text
        $name = Get-WrittenBackup -Text $run.Text
        $copy = Join-Path $script:BackupFolder $name
        Test-Path -LiteralPath $copy -PathType Leaf | Should -BeTrue
        $copyHash = (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash

        # Everything changes after the backup, and the volume loses its own copy, so only the Windows copy can bring
        # the content back.
        $null = Invoke-RoundTrip -Mode 'change'
        $null = Invoke-Volume -Volume $script:DataVolume -Command "rm /volume/backups/$name && printf changed > /volume/runtime/llm-launch.env"
        Invoke-RoundTrip -Mode 'read' | Should -Not -BeExactly $before

        $restore = Invoke-Captured -Name 'Invoke-LyrveilRestore' -Parameters ($script:StartParameters + @{ Argument = @($copy) })
        $restore.Status | Should -Be 0 -Because $restore.Text
        $restore.Text | Should -Match ([regex]::Escape("PASS     $name"))
        $restore.Last | Should -BeExactly $script:ReadyLine

        Invoke-RoundTrip -Mode 'read' | Should -BeExactly $before
        (Invoke-Volume -Volume $script:DataVolume -Command 'cat /volume/runtime/llm-launch.env') -join '' | Should -BeExactly 'MODEL_FILE=t-dck-12.gguf'
        # Stop and start copied the volume's backups to the Windows folder again, which never overwrites a copy.
        $restore.Text | Should -Match 'PASS     Backups: every backup of the data volume is in'
        (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash | Should -Be $copyHash
    }
}
