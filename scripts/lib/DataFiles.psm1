# Runs "scripts\lyrveil.cmd import-model", "backup" and "restore": the commands that move files between Windows folders and the Docker volumes.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Lifecycle.psm1') -Force
# Lifecycle.psm1 has just loaded Setup.psm1. Importing it again without -Force reuses that instance and only makes its
# helpers visible here; -Force would replace the instance that Lifecycle.psm1 uses.
Import-Module (Join-Path $PSScriptRoot 'Setup.psm1')
# Lifecycle.psm1 has loaded ToolContainer.psm1 too; the same rule applies.
Import-Module (Join-Path $PSScriptRoot 'ToolContainer.psm1')
Import-Module (Join-Path $PSScriptRoot 'ConsolePrompt.psm1') -Force

$script:DataVolumeKey = 'lyrveil-data'
$script:ModelsVolumeKey = 'lyrveil-models'
# docs/operations.md (scripts\lyrveil.cmd import-model), the same rule as the download tool's. Matched with case.
$script:ModelNamePattern = '^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$'
$script:ModelFolders = @{ '.gguf' = 'llm'; '.safetensors' = 'image/checkpoints' }
$script:HealthWaitSeconds = 120

function Resolve-SourceFile {
    # Returns the full path of the file Path, relative to the current folder, or $null after printing why when it is
    # not an existing file or its folder cannot be mounted. Reads only.
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Path, [Parameter(Mandatory = $true)] [string]$Command)

    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        Write-Host "${Command}: $full is not a file."
        return $null
    }
    $folder = Split-Path -Parent $full
    if (-not (Test-MountableFolder -Folder $folder)) {
        Write-Host "${Command}: the folder $folder cannot be mounted into a container, because it is not on a drive or holds a comma or a double quote. Move the file to another folder."
        return $null
    }
    return $full
}

function Get-ModelImportFolder {
    # Returns the folder of the models volume that receives a model file called Name, llm or image/checkpoints, or
    # $null after printing why when its extension is not .gguf or .safetensors or the name breaks the pattern. Pure.
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Name)

    $extension = [System.IO.Path]::GetExtension($Name)
    $folder = $null
    foreach ($key in $script:ModelFolders.Keys) {
        if ($extension -ceq $key) { $folder = $script:ModelFolders[$key] }
    }
    if (-not $folder) {
        Write-Host "import-model: $Name is refused: only .gguf and .safetensors files can be imported."
        return $null
    }
    if ($Name -cnotmatch $script:ModelNamePattern) {
        Write-Host "import-model: $Name is refused: the name must match $script:ModelNamePattern. Rename the file, then import it."
        return $null
    }
    return $folder
}

function Read-SingleFileArgument {
    # Returns the full path of the single file argument of Command, or $null after printing the usage or why the file
    # is refused. Reads only.
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Command, [string[]]$Argument = @(), [Parameter(Mandatory = $true)] [string]$Usage)

    $options = Read-CommandOption -Command $Command -Argument $Argument -AllowPositional
    if ($null -eq $options) { return $null }
    if ($options.Positional.Length -ne 1) {
        Write-Host "Usage: $Usage"
        return $null
    }
    return (Resolve-SourceFile -Path $options.Positional[0] -Command $Command)
}

function Invoke-LyrveilImportModel {
    <#
    Runs "scripts\lyrveil.cmd import-model <file>" and returns its exit status (docs/operations.md, Scripts): 0 when
    the file was copied, 1 when it is refused (another extension, a name outside the pattern, a name already in the
    models volume, a missing file) or the copy fails, and 2 when Docker is not running, the models volume is missing
    or unlabelled, the image is missing, or the volume lacks space. A .gguf goes to llm/, a .safetensors to
    image/checkpoints/. The name is checked before any Docker call; the copy runs "python -m
    lyrveil.tools.import_model" in a container with no network, the file's folder mounted read-only and the models
    volume as its only writable mount. The parameters after Argument are for tests (see Invoke-LyrveilStart).
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @(),
        [string]$ProjectName = 'lyrveil',
        [string[]]$ComposeFile = @('compose.yaml'),
        [string]$EnvPath = '',
        [string]$ExamplePath = ''
    )

    $file = Read-SingleFileArgument -Command 'import-model' -Argument $Argument -Usage 'scripts\lyrveil.cmd import-model <file>, with a .gguf or .safetensors file.'
    if (-not $file) { return 1 }
    $name = Split-Path -Leaf $file
    $folder = Get-ModelImportFolder -Name $name
    if (-not $folder) { return 1 }

    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    if (-not (Test-DockerReachable)) {
        Write-Host 'FAILURE  Docker daemon: unreachable. Start Docker Desktop, then run the command again.'
        return 2
    }
    $target = Get-ToolTarget -Project (Get-ComposeProject -Context $context) -VolumeKey $script:ModelsVolumeKey
    if ($null -eq $target) { return 2 }

    Write-Host "Copying $name to $folder/ in the models volume $($target.Volume)"
    $mounts = @(
        @{ Type = 'bind'; Source = (Split-Path -Parent $file); Target = '/import'; ReadOnly = $true },
        @{ Type = 'volume'; Source = $target.Volume; Target = '/models'; ReadOnly = $false }
    )
    $status = Invoke-ToolContainer -Argument (Get-ToolContainerArgument -Image $target.Image -Mount $mounts -Module 'lyrveil.tools.import_model' -ToolArgument @("/import/$name"))
    if ($status -eq 0) {
        Write-Host 'Register the file as a custom profile in Impostazioni > Modelli (docs/models.md, Custom profiles).'
        return 0
    }
    if (@(1, 2) -notcontains $status) {
        Write-Host "FAILURE  The import container exited with $status (see the output above)."
        return 1
    }
    return $status
}

function Invoke-LyrveilBackup {
    <#
    Runs "scripts\lyrveil.cmd backup" and returns its exit status: 0 when a new backup is in the data volume and
    every backup of the volume is in the Windows backup folder, 1 on an argument, an invalid LYRVEIL_BACKUP_DIR or a
    failed step, and 2 when Docker is not running, or the data volume or the image is missing. The backup runs
    "python -m lyrveil.cli backup" in a container with no network and only the data volume mounted, then
    Export-LyrveilBackup copies it to the folder of Get-BackupFolder. The backend may keep running: the database is
    copied with the SQLite backup API. The parameters after Argument are for tests (see Invoke-LyrveilStart); the
    folder comes from the .env at EnvPath, so a test sets its own.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @(),
        [string]$ProjectName = 'lyrveil',
        [string[]]$ComposeFile = @('compose.yaml'),
        [string]$EnvPath = '',
        [string]$ExamplePath = ''
    )

    $options = Read-CommandOption -Command 'backup' -Argument $Argument
    if ($null -eq $options) { return 1 }
    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    try {
        $folder = Get-BackupFolder -EnvPath $context.InstallationEnvPath
    }
    catch {
        Write-Host "FAILURE  Backup folder: $($_.Exception.Message)"
        return 1
    }
    if (-not (Test-DockerReachable)) {
        Write-Host 'FAILURE  Docker daemon: unreachable. Start Docker Desktop, then run the command again.'
        return 2
    }
    $target = Get-ToolTarget -Project (Get-ComposeProject -Context $context) -VolumeKey $script:DataVolumeKey
    if ($null -eq $target) { return 2 }

    Write-Host "Backup of the data volume $($target.Volume)"
    $mounts = @(@{ Type = 'volume'; Source = $target.Volume; Target = '/data'; ReadOnly = $false })
    $status = Invoke-ToolContainer -Argument (Get-ToolContainerArgument -Image $target.Image -Mount $mounts -Module 'lyrveil.cli' -ToolArgument @('backup'))
    if ($status -ne 0) {
        Write-Host "FAILURE  Backup: not written (exit $status, see the output above)."
        return 1
    }
    Write-Host "Copy to $folder"
    $exported = Export-LyrveilBackup -Image $target.Image -DataVolume $target.Volume -Folder $folder
    if ($exported -ne 0) {
        Write-Host "FAILURE  Backups: not copied to $folder (exit $exported, see the output above)."
        return 1
    }
    Write-Host "Backup complete. The backups are in $folder; they hold private content and are not encrypted."
    return 0
}

function Invoke-LyrveilRestore {
    <#
    Runs "scripts\lyrveil.cmd restore <file>" and returns its exit status: the status of start when the backup was
    restored, 1 when the backup fails restore-check, an argument is wrong or the restore fails, 2 when Docker is not
    running or the data volume or the image is missing, 5 when the user declined, and the status of stop when stop
    fails. Steps (docs/operations.md, Backup and restore):
      1. "python -m lyrveil.cli restore-check" in a container with no network and the file's folder mounted
         read-only, which refuses a damaged backup or one with a schema newer than the installed app;
      2. a confirmation, since Lyrveil stops and the data is replaced;
      3. stop.cmd, which copies the backups missing from the Windows backup folder first;
      4. "python -m lyrveil.cli restore" in a container with no network, the folder read-only and the data volume:
         it checks again, extracts with the hashes checked, moves db, media and runtime to pre-restore-<UTC time>/
         and puts the backup's in their place, or puts everything back on a failure;
      5. start.cmd, which applies any pending migration.
    Nothing is changed before step 4. The parameters after Argument are for tests: BackupFolder goes to stop and
    start, and the start ones to Invoke-LyrveilStart.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @(),
        [string]$ProjectName = 'lyrveil',
        [string[]]$ComposeFile = @('compose.yaml'),
        [string]$EnvPath = '',
        [string]$ExamplePath = '',
        [hashtable]$PreflightFact = $null,
        [switch]$SkipGpuPreflight,
        [int]$WaitSeconds = $script:HealthWaitSeconds,
        [string]$BackupFolder = ''
    )

    $file = Read-SingleFileArgument -Command 'restore' -Argument $Argument -Usage 'scripts\lyrveil.cmd restore <file>, with a lyrveil-<time>.tar backup.'
    if (-not $file) { return 1 }
    $name = Split-Path -Leaf $file
    $common = @{ Root = $Root; ProjectName = $ProjectName; ComposeFile = $ComposeFile; EnvPath = $EnvPath; ExamplePath = $ExamplePath }
    $context = Resolve-LifecycleContext @common
    if (-not (Test-DockerReachable)) {
        Write-Host 'FAILURE  Docker daemon: unreachable. Start Docker Desktop, then run the command again.'
        return 2
    }
    $target = Get-ToolTarget -Project (Get-ComposeProject -Context $context) -VolumeKey $script:DataVolumeKey
    if ($null -eq $target) { return 2 }
    $backupMount = @{ Type = 'bind'; Source = (Split-Path -Parent $file); Target = '/backup'; ReadOnly = $true }

    Write-Host "Check of $name"
    $check = Invoke-ToolContainer -Argument (Get-ToolContainerArgument -Image $target.Image -Mount @($backupMount) -Module 'lyrveil.cli' -ToolArgument @('restore-check', "/backup/$name"))
    if ($check -ne 0) {
        Write-Host "FAILURE  Restore refused: $name failed restore-check (see the output above). Nothing was stopped or changed."
        return 1
    }
    if (-not (Read-Confirmation -Question "Restore ${name}? Lyrveil stops, the database, media and launch files of the data volume move to pre-restore-<time>, and the backup's take their place.")) {
        Write-Host 'Nothing was restored (exit status 5).'
        return 5
    }

    Write-Host 'Stop'
    $stopStatus = [int](Invoke-LyrveilStop @common -BackupFolder $BackupFolder)
    if ($stopStatus -ne 0) {
        Write-Host "Restore stopped, because stop exited with status $stopStatus. Nothing was changed."
        return $stopStatus
    }

    Write-Host "Restore into the data volume $($target.Volume)"
    $mounts = @($backupMount, @{ Type = 'volume'; Source = $target.Volume; Target = '/data'; ReadOnly = $false })
    $apply = Invoke-ToolContainer -Argument (Get-ToolContainerArgument -Image $target.Image -Mount $mounts -Module 'lyrveil.cli' -ToolArgument @('restore', "/backup/$name"))
    if ($apply -ne 0) {
        Write-Host "FAILURE  Restore: failed (exit $apply, see the output above). Lyrveil is stopped; run start.cmd to start it again."
        return 1
    }

    Write-Host 'Start'
    return [int](Invoke-LyrveilStart @common -PreflightFact $PreflightFact -SkipGpuPreflight:$SkipGpuPreflight -WaitSeconds $WaitSeconds -BackupFolder $BackupFolder)
}

Export-ModuleMember -Function Invoke-LyrveilImportModel, Invoke-LyrveilBackup, Invoke-LyrveilRestore,
Get-ModelImportFolder
