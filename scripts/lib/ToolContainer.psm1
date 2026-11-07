# Runs one-off containers of the backend image on the Lyrveil volumes, and copies the data volume's backups to the Windows backup folder.

Set-StrictMode -Version Latest

# Lifecycle.psm1 and DataFiles.psm1 load Setup.psm1 with -Force before this module. Importing it again without -Force
# reuses that instance and only makes its helpers visible here.
Import-Module (Join-Path $PSScriptRoot 'Setup.psm1')

$script:VolumeLabel = 'io.lyrveil.volume'
$script:DataVolumeKey = 'lyrveil-data'
# The service whose local image runs the one-off containers: it holds lyrveil.cli and lyrveil.tools.
$script:ImageService = 'lyrveil-backend'
$script:ServiceUser = '10001:10001'
$script:BackupFolderVariable = 'LYRVEIL_BACKUP_DIR'
$script:DefaultBackupFolder = 'Documents\Lyrveil\backups'

function Get-ToolContainerArgument {
    <#
    Returns the arguments of a "docker run" that runs "python -m Module ToolArgument" in Image as 10001:10001, removed
    afterwards, never pulled, with no network, a read-only root with a /tmp tmpfs, no capability and no new
    privileges. Mount lists hashtables @{ Type = 'volume' or 'bind'; Source; Target; ReadOnly }; a bind source is a
    Windows folder. Pure.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string]$Image,
        [Parameter(Mandatory = $true)] [object[]]$Mount,
        [Parameter(Mandatory = $true)] [string]$Module,
        [string[]]$ToolArgument = @()
    )

    $arguments = @('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--user', $script:ServiceUser)
    foreach ($item in $Mount) {
        # --mount instead of --volume: a Windows source such as C:\ would make the colon-separated form ambiguous.
        $spec = "type=$($item.Type),source=$($item.Source),target=$($item.Target)"
        if ($item.ReadOnly) { $spec += ',readonly' }
        $arguments += @('--mount', $spec)
    }
    $arguments += @('--entrypoint', 'python', $Image, '-m', $Module) + $ToolArgument
    return [string[]]$arguments
}

function Test-MountableFolder {
    # Returns $true when Folder can be the source of a --mount: an absolute path on a drive, with no comma and no
    # double quote, which the mount syntax would split or Windows PowerShell 5.1 would drop. Pure.
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Folder)

    return ($Folder -match '^[A-Za-z]:\\' -and $Folder -notmatch '[,"]')
}

function Get-BackupFolder {
    <#
    Returns the Windows backup folder: LYRVEIL_BACKUP_DIR of the .env at EnvPath with %VARIABLES% expanded, or
    %USERPROFILE%\Documents\Lyrveil\backups when the file, the line or the value is missing. Throws when the value is
    not an absolute folder on a drive or cannot be mounted (Test-MountableFolder). Reads only.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$EnvPath)

    $value = Get-EnvFileValue -Path $EnvPath -Name $script:BackupFolderVariable
    if ($value) { $value = $value.Trim().Trim('"').Trim("'") }
    if (-not $value) { return (Join-Path $env:USERPROFILE $script:DefaultBackupFolder) }
    $folder = [Environment]::ExpandEnvironmentVariables($value)
    if (-not (Test-MountableFolder -Folder $folder)) {
        throw "$script:BackupFolderVariable in $EnvPath must be an absolute folder on a drive, such as D:\Lyrveil\backups, without a comma or a double quote."
    }
    $folder = [System.IO.Path]::GetFullPath($folder)
    # Keep the separator of a drive root, such as D:\, which --mount needs.
    if ($folder.Length -gt 3) { $folder = $folder.TrimEnd('\') }
    return $folder
}

function Get-ToolTarget {
    <#
    Returns @{ Volume; Image }: the Docker name of the volume VolumeKey of Project and the image of the backend
    service, when the volume exists with the label io.lyrveil.volume and the image is present. Otherwise prints which
    one is missing and returns $null. An unlabelled volume is never used, because setup did not create it. Reads only.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] $Project, [Parameter(Mandatory = $true)] [string]$VolumeKey)

    $volume = @(Get-ProjectVolume -Project $Project | Where-Object { $_.Key -eq $VolumeKey })[0]
    # The labels come as JSON: Windows PowerShell 5.1 drops the quotes inside a native argument.
    $inspect = Invoke-NativeCapture -Command 'docker' -Argument @('volume', 'inspect', '--format', '{{json .Labels}}', $volume.Name) -IncludeErrorStream
    if ($inspect.Status -ne 0) {
        Write-Host "FAILURE  Volume $($volume.Name) does not exist. Run setup.cmd first."
        return $null
    }
    $labels = ($inspect.Output -join '') | ConvertFrom-Json
    if (-not (Get-ObjectProperty -Object $labels -Name $script:VolumeLabel)) {
        Write-Host "FAILURE  Volume $($volume.Name) exists without the label $script:VolumeLabel, so it is left untouched (docs/operations.md, Uninstall)."
        return $null
    }
    $service = Get-ObjectProperty -Object (Get-ObjectProperty -Object $Project -Name 'services') -Name $script:ImageService
    $image = [string](Get-ObjectProperty -Object $service -Name 'image')
    if (-not $image) { throw "The Compose configuration has no image for $script:ImageService." }
    if ((Invoke-NativeCapture -Command 'docker' -Argument @('image', 'inspect', '--format', '{{.Id}}', $image) -IncludeErrorStream).Status -ne 0) {
        Write-Host "FAILURE  Image $image is missing. Run setup.cmd first."
        return $null
    }
    return @{ Volume = $volume.Name; Image = $image }
}

function Invoke-ToolContainer {
    # Runs "docker" with Argument, prints every line, standard error included, with Write-Host as it arrives, so a
    # caller can capture it, and returns the exit status.
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string[]]$Argument)

    # Windows PowerShell 5.1 turns native standard error lines into error records, which 'Stop' would throw.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @Argument 2>&1 | ForEach-Object { Write-Host ([string]$_) }
        return [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Export-LyrveilBackup {
    <#
    Copies every backup of the data volume DataVolume that the Windows folder Folder lacks, by name, creating Folder
    when missing, and returns the exit status of the copy, 0 when it succeeded. Nothing in Folder is overwritten or
    deleted. The copy runs "python -m lyrveil.cli export-backups" in a container with no network, the data volume
    read-only and Folder as its only writable mount; its output is printed.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Image,
        [Parameter(Mandatory = $true)] [string]$DataVolume,
        [Parameter(Mandatory = $true)] [string]$Folder
    )

    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
        $null = New-Item -ItemType Directory -Force -Path $Folder
    }
    $mounts = @(
        @{ Type = 'volume'; Source = $DataVolume; Target = '/data'; ReadOnly = $true },
        @{ Type = 'bind'; Source = $Folder; Target = '/export'; ReadOnly = $false }
    )
    return [int](Invoke-ToolContainer -Argument (Get-ToolContainerArgument -Image $Image -Mount $mounts -Module 'lyrveil.cli' -ToolArgument @('export-backups', '/export')))
}

function Copy-BackupToWindows {
    <#
    Copies every backup of the data volume of the Compose project of Context that the Windows backup folder lacks:
    step 10 of start.cmd and step 3 of stop.cmd (docs/operations.md, Backup and restore). Folder is the backup folder;
    when empty, it is Get-BackupFolder of the installation's .env. Returns $null after printing a PASS line when the
    copy succeeded, and otherwise the reason as one line, so the caller prints it as a warning: an invalid
    LYRVEIL_BACKUP_DIR, a missing or unlabelled data volume, a missing image, a failed copy or a Compose error. Never
    throws.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context, [string]$Folder = '')

    try {
        if (-not $Folder) { $Folder = Get-BackupFolder -EnvPath $Context.InstallationEnvPath }
        $project = Get-ComposeProject -Context $Context
        # Get-ToolTarget prints why it found no target; that text becomes the reason instead of a FAILURE line.
        $found = @(Get-ToolTarget -Project $project -VolumeKey $script:DataVolumeKey 6>&1)
        $target = @($found | Where-Object { $_ -is [hashtable] })
        if ($target.Length -eq 0) {
            $reason = @($found | Where-Object { $null -ne $_ -and $_ -isnot [hashtable] } | ForEach-Object { ([string]$_) -replace '^FAILURE\s+', '' }) -join ' '
            return "not copied to $Folder. $reason"
        }
        $status = Export-LyrveilBackup -Image $target[0].Image -DataVolume $target[0].Volume -Folder $Folder
        if ($status -ne 0) { return "not copied to $Folder (exit $status, see the output above). scripts\lyrveil.cmd backup copies them too" }
    }
    catch {
        return "not copied: $($_.Exception.Message)"
    }
    Write-Host "PASS     Backups: every backup of the data volume is in $Folder"
    return $null
}

Export-ModuleMember -Function Get-ToolContainerArgument, Test-MountableFolder, Get-BackupFolder, Get-ToolTarget,
Invoke-ToolContainer, Export-LyrveilBackup, Copy-BackupToWindows
