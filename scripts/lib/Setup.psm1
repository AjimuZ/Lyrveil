# Runs "setup.cmd": the preflight checks, .env, the two labelled volumes and their owner, the image builds and the runtime containers.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# Thresholds of docs/operations.md (Preflight checks).
$script:MinimumCompose = [version]'2.30'
$script:MinimumApi = [version]'1.45'
$script:MemoryPassGiB = 20
$script:MemoryFailGiB = 11
$script:DiskPassGiB = 20
$script:DiskFailGiB = 5
# Windows RAM from which the target tier's WSL2 values are advised; a "32 GB" machine reports a little less.
$script:TargetTierWindowsGiB = 30

# The label every Lyrveil volume carries, and its value for each Compose volume key. The Docker names of the volumes
# come from the resolved Compose configuration, so a test project can point the same keys at its own volumes.
$script:VolumeLabel = 'io.lyrveil.volume'
$script:Volumes = [ordered]@{ 'lyrveil-data' = 'data'; 'lyrveil-models' = 'models' }
$script:ServiceUser = '10001:10001'
# The runtime services, created and never started by setup. lyrveil-llm is in compose.yaml since P02.1; lyrveil-image
# enters it with P05.
$script:RuntimeServices = @('lyrveil-llm', 'lyrveil-image')
# The service whose image runs the one-off ownership containers. It is local and built by setup, so no pull is needed.
$script:OwnerImageService = 'lyrveil-backend'

$script:Options = @('-Rebuild', '-SkipModels')
# Documented options whose work is not delivered yet. They are refused, so no run claims to have done it.
$script:PlannedOptions = @{
    '-AcceptModelLicenses' = 'Setup downloads models once the model catalog is in the image (P02.1a). scripts\lyrveil.cmd models -AcceptModelLicenses downloads them with a catalog.'
    '-VerifyModels'        = 'Setup verifies models once the model catalog is in the image (P02.1a). scripts\lyrveil.cmd models -Verify verifies them with a catalog.'
}

$script:Invariant = [System.Globalization.CultureInfo]::InvariantCulture

function Invoke-NativeCapture {
    <#
    Runs Command with Argument and returns @{ Status; Output }, where Output holds the standard output lines as text,
    and the standard error lines too with IncludeErrorStream; otherwise standard error goes to the console. Never
    throws for a non-zero status. A command that is not on PATH returns status 127.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @(),
        [switch]$IncludeErrorStream
    )

    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return @{ Status = 127; Output = [string[]]@("The command '$Command' is not available.") }
    }
    # Windows PowerShell 5.1 may turn native standard error lines into error records, which 'Stop' would throw.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($IncludeErrorStream) {
            $lines = @(& $Command @Argument 2>&1 | ForEach-Object { [string]$_ })
        }
        else {
            $lines = @(& $Command @Argument | ForEach-Object { [string]$_ })
        }
        $status = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return @{ Status = $status; Output = [string[]]$lines }
}

function Invoke-NativeShown {
    # Runs Command with Argument with its output on the console, and returns its exit status, or 127 when it is not
    # on PATH.
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @()
    )

    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        Write-Host "  The command '$Command' is not available."
        return 127
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command @Argument | Out-Host
        return [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function ConvertTo-Version {
    # Returns the first "major.minor[.patch]" in Text as a [version], or $null when there is none. Not exported.
    [CmdletBinding()]
    [OutputType([version])]
    param([string]$Text)

    if ($Text -match '(\d+)\.(\d+)(\.(\d+))?') {
        if ($Matches[4]) { return [version]"$($Matches[1]).$($Matches[2]).$($Matches[4])" }
        return [version]"$($Matches[1]).$($Matches[2])"
    }
    return $null
}

function Get-EnvFileValue {
    # Returns the value of Name in the dotenv file at Path, or $null when the file or the line is missing. Reads only.
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Path, [string]$Name)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    foreach ($line in ((Read-Utf8Text -Path $Path) -split "`r?`n")) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

function Get-PreflightFact {
    <#
    Returns the facts that Get-PreflightResult judges, read from this machine: Git's version line, whether the docker
    command exists and its daemon answers, the Compose and Engine API versions, the memory Docker sees, the Windows
    RAM, and the free space on the drive of %LOCALAPPDATA%, which holds Docker Desktop's data. A fact that cannot be
    read is $null, or $false for the two booleans. AllowLowMemory is true when LYRVEIL_ALLOW_LOW_MEMORY is "true" in
    the environment or in the .env at EnvPath. Reads only.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([string]$EnvPath)

    $fact = @{
        GitVersion = $null; DockerCli = $false; DockerReachable = $false; ComposeVersion = $null; ApiVersion = $null
        MemoryBytes = $null; WindowsMemoryBytes = $null; DiskDrive = $null; FreeDiskBytes = $null; AllowLowMemory = $false
    }

    $git = Invoke-NativeCapture -Command 'git' -Argument @('--version') -IncludeErrorStream
    if ($git.Status -eq 0 -and @($git.Output).Length -gt 0) { $fact.GitVersion = [string]$git.Output[0] }

    $fact.DockerCli = [bool](Get-Command 'docker' -ErrorAction SilentlyContinue)
    if ($fact.DockerCli) {
        $compose = Invoke-NativeCapture -Command 'docker' -Argument @('compose', 'version', '--short') -IncludeErrorStream
        if ($compose.Status -eq 0) { $fact.ComposeVersion = ConvertTo-Version -Text ($compose.Output -join ' ') }
        $api = Invoke-NativeCapture -Command 'docker' -Argument @('version', '--format', '{{.Server.APIVersion}}') -IncludeErrorStream
        if ($api.Status -eq 0) {
            $fact.DockerReachable = $true
            $fact.ApiVersion = ConvertTo-Version -Text ($api.Output -join ' ')
            $memory = Invoke-NativeCapture -Command 'docker' -Argument @('info', '--format', '{{.MemTotal}}') -IncludeErrorStream
            $bytes = [long]0
            if ($memory.Status -eq 0 -and [long]::TryParse((($memory.Output -join '').Trim()), [ref]$bytes)) {
                $fact.MemoryBytes = $bytes
            }
        }
    }

    try {
        $fact.WindowsMemoryBytes = [long](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
    }
    catch {
        $fact.WindowsMemoryBytes = $null
    }

    $dataFolder = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:SystemDrive + '\' }
    try {
        $drive = [System.IO.Path]::GetPathRoot($dataFolder)
        $fact.DiskDrive = $drive.TrimEnd('\')
        $fact.FreeDiskBytes = [long](New-Object System.IO.DriveInfo $drive).AvailableFreeSpace
    }
    catch {
        $fact.FreeDiskBytes = $null
    }

    $allow = $env:LYRVEIL_ALLOW_LOW_MEMORY
    if (-not $allow) { $allow = Get-EnvFileValue -Path $EnvPath -Name 'LYRVEIL_ALLOW_LOW_MEMORY' }
    $fact.AllowLowMemory = ([string]$allow).Trim() -eq 'true'
    return $fact
}

function New-PreflightRow {
    # Returns one judged row. Pure: it builds an object and changes nothing. Not exported.
    [CmdletBinding()]
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'Builds an object and changes no state.')]
    [OutputType([pscustomobject])]
    param([string]$Name, [string]$Level, [string]$Detail, [string]$Advice = '')

    return [pscustomobject]@{ Name = $Name; Level = $Level; Detail = $Detail; Advice = $Advice }
}

function Format-GiB {
    # Returns Bytes in GiB with one decimal and a dot, whatever the Windows language is. Not exported.
    [CmdletBinding()]
    [OutputType([string])]
    param([long]$Bytes)

    return [string]::Format($script:Invariant, '{0:0.0} GiB', ($Bytes / 1GB))
}

function Get-MemoryAdvice {
    # Returns the documented .wslconfig advice for a machine with WindowsBytes of RAM ($null when unknown). Not exported.
    [CmdletBinding()]
    [OutputType([string])]
    param($WindowsBytes)

    $values = 'memory=12GB and swap=16GB, the values for 16 GB of RAM'
    if ($null -ne $WindowsBytes -and ($WindowsBytes / 1GB) -ge $script:TargetTierWindowsGiB) {
        $values = 'memory=24GB and swap=8GB, the values for 32 GB of RAM'
    }
    $advice = "Create %UserProfile%\.wslconfig with a [wsl2] section holding $values (docs/operations.md, WSL2 memory). " +
        'Then run "wsl --shutdown", start Docker Desktop again, and run setup.cmd again.'
    if ($null -ne $WindowsBytes -and ($WindowsBytes / 1GB) -lt 15) {
        $advice += ' Machines with under 16 GB of RAM are not supported.'
    }
    return $advice
}

function Get-PreflightResult {
    <#
    Returns the rows of the preflight checks that setup runs, in order: Git, Docker daemon, Compose, Engine API,
    memory seen by Docker and free disk, judged from Fact (see Get-PreflightFact) at the thresholds of
    docs/operations.md (Preflight checks). Each row has Name, Level (PASS, WARNING or FAILURE), Detail and Advice;
    every FAILURE has advice. A fact that was never read never passes: when the daemon is unreachable, the rows that
    need it fail as not checked. A memory failure becomes a warning when Fact.AllowLowMemory is true. Pure.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param([Parameter(Mandatory = $true)] [hashtable]$Fact)

    $rows = New-Object System.Collections.Generic.List[object]
    $unreachable = 'not checked, because the Docker daemon is unreachable'
    $dockerAdvice = 'Fix the Docker daemon row first.'

    if ($Fact.GitVersion) {
        $rows.Add((New-PreflightRow 'Git' 'PASS' $Fact.GitVersion))
    }
    else {
        $rows.Add((New-PreflightRow 'Git' 'FAILURE' 'the git command is not found' 'Install Git for Windows from https://git-scm.com/download/win, then open a new terminal and run setup.cmd again.'))
    }

    if (-not $Fact.DockerCli) {
        $rows.Add((New-PreflightRow 'Docker daemon' 'FAILURE' 'the docker command is not found' 'Install Docker Desktop with the WSL2 backend (docs/operations.md, Requirements), start it, then run setup.cmd again.'))
    }
    elseif (-not $Fact.DockerReachable) {
        $rows.Add((New-PreflightRow 'Docker daemon' 'FAILURE' 'unreachable' 'Start Docker Desktop and wait until it reports that the engine is running, then run setup.cmd again.'))
    }
    else {
        $rows.Add((New-PreflightRow 'Docker daemon' 'PASS' 'reachable'))
    }

    $composeAdvice = "Update Docker Desktop: Lyrveil needs Docker Compose v$script:MinimumCompose or later."
    if (-not $Fact.DockerCli) {
        $rows.Add((New-PreflightRow 'Compose' 'FAILURE' 'not checked, because the docker command is not found' $dockerAdvice))
    }
    elseif ($null -eq $Fact.ComposeVersion) {
        $rows.Add((New-PreflightRow 'Compose' 'FAILURE' 'docker compose is not available' $composeAdvice))
    }
    elseif ([version]$Fact.ComposeVersion -lt $script:MinimumCompose) {
        $rows.Add((New-PreflightRow 'Compose' 'FAILURE' "v$($Fact.ComposeVersion), older than v$script:MinimumCompose" $composeAdvice))
    }
    else {
        $rows.Add((New-PreflightRow 'Compose' 'PASS' "v$($Fact.ComposeVersion)"))
    }

    $apiAdvice = "Update Docker Desktop: Lyrveil needs Engine API $script:MinimumApi or later, which adds volume subpaths."
    if (-not $Fact.DockerReachable) {
        $rows.Add((New-PreflightRow 'Engine API' 'FAILURE' $unreachable $dockerAdvice))
    }
    elseif ($null -eq $Fact.ApiVersion) {
        $rows.Add((New-PreflightRow 'Engine API' 'FAILURE' 'the version could not be read' $apiAdvice))
    }
    elseif ([version]$Fact.ApiVersion -lt $script:MinimumApi) {
        $rows.Add((New-PreflightRow 'Engine API' 'FAILURE' "$($Fact.ApiVersion), older than $script:MinimumApi" $apiAdvice))
    }
    else {
        $rows.Add((New-PreflightRow 'Engine API' 'PASS' "$($Fact.ApiVersion)"))
    }

    $memoryName = 'Memory seen by Docker'
    if (-not $Fact.DockerReachable) {
        $rows.Add((New-PreflightRow $memoryName 'FAILURE' $unreachable $dockerAdvice))
    }
    elseif ($null -eq $Fact.MemoryBytes) {
        $rows.Add((New-PreflightRow $memoryName 'FAILURE' 'the value could not be read' 'Restart Docker Desktop, then run setup.cmd again.'))
    }
    else {
        $gib = $Fact.MemoryBytes / 1GB
        $seen = Format-GiB -Bytes $Fact.MemoryBytes
        $advice = Get-MemoryAdvice -WindowsBytes $Fact.WindowsMemoryBytes
        if ($gib -ge $script:MemoryPassGiB) {
            $rows.Add((New-PreflightRow $memoryName 'PASS' $seen))
        }
        elseif ($gib -ge $script:MemoryFailGiB) {
            $rows.Add((New-PreflightRow $memoryName 'WARNING' "$seen, under $script:MemoryPassGiB GiB: the reduced tier applies" $advice))
        }
        elseif ($Fact.AllowLowMemory) {
            $rows.Add((New-PreflightRow $memoryName 'WARNING' "$seen, under $script:MemoryFailGiB GiB, allowed by LYRVEIL_ALLOW_LOW_MEMORY=true: unsupported, and image generation may fail" $advice))
        }
        else {
            $rows.Add((New-PreflightRow $memoryName 'FAILURE' "$seen, under $script:MemoryFailGiB GiB" $advice))
        }
    }

    $diskName = 'Free disk'
    $drive = if ($Fact.DiskDrive) { $Fact.DiskDrive } else { 'the drive of %LOCALAPPDATA%' }
    $diskAdvice = "Free space on $drive, which holds Docker Desktop's data in %LOCALAPPDATA%\Docker\wsl. A first setup with the default models needs about 50 GB (docs/operations.md, Disk use)."
    if ($null -eq $Fact.FreeDiskBytes) {
        $rows.Add((New-PreflightRow $diskName 'FAILURE' "the free space of $drive could not be read" $diskAdvice))
    }
    else {
        $gib = $Fact.FreeDiskBytes / 1GB
        $free = "$(Format-GiB -Bytes $Fact.FreeDiskBytes) free on $drive"
        if ($gib -ge $script:DiskPassGiB) {
            $rows.Add((New-PreflightRow $diskName 'PASS' $free))
        }
        elseif ($gib -ge $script:DiskFailGiB) {
            $rows.Add((New-PreflightRow $diskName 'WARNING' "$free, under $script:DiskPassGiB GiB" $diskAdvice))
        }
        else {
            $rows.Add((New-PreflightRow $diskName 'FAILURE' "$free, under $script:DiskFailGiB GiB" "$diskAdvice Then run setup.cmd again."))
        }
    }
    return [object[]]$rows.ToArray()
}

function Write-PreflightReport {
    # Prints one line per row, as "LEVEL    Name: detail", and its advice on the next line.
    [CmdletBinding()]
    param([object[]]$Row)

    foreach ($item in $Row) {
        Write-Host ('{0,-8} {1}: {2}' -f $item.Level, $item.Name, $item.Detail)
        if ($item.Advice) { Write-Host "         $($item.Advice)" }
    }
}

function Get-RandomToken {
    # Returns 32 bytes from the system's cryptographic generator as 64 lowercase hexadecimal characters. Not exported.
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Initialize-EnvironmentFile {
    <#
    Creates the .env at Path from the template at Example when Path does not exist, and returns 'created'. Returns
    'kept' when Path exists, without reading or changing it. The new file holds the template's lines, as UTF-8
    without BOM, with LYRVEIL_CONTROL_TOKEN set to a new token of 64 hexadecimal characters (added when the
    template lacks the line). Its ACL has no inherited entry and grants only the current Windows user, by SID, with
    icacls. It is written under a temporary name, restricted before any content is written, then renamed, so a
    partial or readable .env never exists and a .env that appears meanwhile is never replaced.

    Throws when the template is missing or icacls fails, and removes the temporary file first.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string]$Example
    )

    if (Test-Path -LiteralPath $Path) { return 'kept' }
    if (-not (Test-Path -LiteralPath $Example -PathType Leaf)) {
        throw "The template $Example is missing, so .env cannot be created. Restore it with git checkout."
    }

    $template = Read-Utf8Text -Path $Example
    $line = 'LYRVEIL_CONTROL_TOKEN=' + (Get-RandomToken)
    $pattern = '(?m)^LYRVEIL_CONTROL_TOKEN=.*?(\r?)$'
    if ([regex]::IsMatch($template, $pattern)) {
        $content = [regex]::Replace($template, $pattern, $line + '$1')
    }
    else {
        $separator = if ($template.Length -gt 0 -and -not $template.EndsWith("`n")) { "`n" } else { '' }
        $content = $template + $separator + $line + "`n"
    }

    # ".env.*" is ignored by Git and Docker, so the temporary file never reaches a commit or an image.
    $temporary = "$Path.setup-tmp"
    try {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        [System.IO.File]::WriteAllText($temporary, '')
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $icacls = Invoke-NativeCapture -Command 'icacls' -Argument @($temporary, '/inheritance:r', '/grant:r', "*${sid}:(F)") -IncludeErrorStream
        if ($icacls.Status -ne 0) {
            throw "icacls could not limit access to $temporary (exit $($icacls.Status)): $($icacls.Output -join ' ')"
        }
        [System.IO.File]::WriteAllText($temporary, $content, (New-Object System.Text.UTF8Encoding($false)))
        # File.Move never overwrites: it throws when Path exists by now.
        [System.IO.File]::Move($temporary, $Path)
    }
    finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
    return 'created'
}

function Get-ComposeArgument {
    # Returns the leading arguments of every "docker compose" call of Context: project name, env file and files.
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context)

    $arguments = @('compose', '-p', $Context.ProjectName, '--env-file', $Context.EnvPath)
    foreach ($file in $Context.ComposeFile) { $arguments += @('-f', $file) }
    return [string[]]$arguments
}

function Get-ComposeProject {
    # Returns the resolved Compose configuration of Context with the ai and tools profiles, as an object. Throws when
    # Compose refuses it.
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context)

    $arguments = (Get-ComposeArgument -Context $Context) + @('--profile', 'ai', '--profile', 'tools', 'config', '--format', 'json')
    $result = Invoke-NativeCapture -Command 'docker' -Argument $arguments
    if ($result.Status -ne 0) { throw "docker compose config failed with exit status $($result.Status)." }
    return (($result.Output -join "`n") | ConvertFrom-Json)
}

function Get-ObjectProperty {
    # Returns the value of property Name of Object, or $null when it has none, which strict mode would otherwise
    # refuse.
    [CmdletBinding()]
    param($Object, [string]$Name)

    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-ProjectVolume {
    <#
    Returns, for each Compose volume key of $script:Volumes, an object with Key, Name (the Docker name) and Label
    (the value of io.lyrveil.volume). Throws when the configuration lacks a key or declares it not external, because
    Compose would then own the volume and "down -v" could remove it.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param([Parameter(Mandatory = $true)] $Project)

    $volumes = New-Object System.Collections.Generic.List[object]
    foreach ($key in $script:Volumes.Keys) {
        $volume = Get-ObjectProperty -Object (Get-ObjectProperty -Object $Project -Name 'volumes') -Name $key
        if ($null -eq $volume -or -not (Get-ObjectProperty -Object $volume -Name 'external')) {
            throw "The Compose configuration must declare the volume '$key' as external."
        }
        $name = Get-ObjectProperty -Object $volume -Name 'name'
        if (-not $name) { $name = $key }
        $volumes.Add([pscustomobject]@{ Key = $key; Name = [string]$name; Label = $script:Volumes[$key] })
    }
    return [object[]]$volumes.ToArray()
}

function Install-ProjectVolume {
    <#
    Creates each volume of Volume that does not exist, with the label io.lyrveil.volume=<Label>, and returns $true.
    An existing volume is kept when it carries the label. Returns $false with advice when one exists without the
    label, because it was not created by setup and may hold someone else's data: setup never deletes or adopts it.
    Throws when Docker fails. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [object[]]$Volume)

    foreach ($item in $Volume) {
        # The labels come as JSON: Windows PowerShell 5.1 drops the quotes inside a native argument, so a template
        # naming the label in quotes would reach Docker broken.
        $inspect = Invoke-NativeCapture -Command 'docker' -Argument @('volume', 'inspect', '--format', '{{json .Labels}}', $item.Name) -IncludeErrorStream
        if ($inspect.Status -eq 0) {
            $labels = ($inspect.Output -join '') | ConvertFrom-Json
            $value = Get-ObjectProperty -Object $labels -Name $script:VolumeLabel
            if (-not $value) {
                Write-Host "FAILURE  Volume $($item.Name): exists without the label $script:VolumeLabel"
                Write-Host '         Setup did not create it, so it is left untouched. Rename or remove it on purpose (docs/operations.md, Uninstall), then run setup.cmd again.'
                return $false
            }
            Write-Host "  Volume $($item.Name): present"
            continue
        }
        $create = Invoke-NativeCapture -Command 'docker' -Argument @('volume', 'create', '--label', "$script:VolumeLabel=$($item.Label)", $item.Name) -IncludeErrorStream
        if ($create.Status -ne 0) { throw "docker volume create $($item.Name) failed: $($create.Output -join ' ')" }
        Write-Host "  Volume $($item.Name): created"
    }
    return $true
}

function Get-ServiceImage {
    # Returns the image of every service of Project that has a build section, sorted and unique.
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] $Project)

    $images = New-Object 'System.Collections.Generic.SortedSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($service in @((Get-ObjectProperty -Object $Project -Name 'services').PSObject.Properties)) {
        $image = Get-ObjectProperty -Object $service.Value -Name 'image'
        if ($image -and $null -ne (Get-ObjectProperty -Object $service.Value -Name 'build')) { [void]$images.Add([string]$image) }
    }
    return [string[]]@($images)
}

function Invoke-ProjectBuild {
    <#
    Builds the images of the ai and tools profiles with "docker compose build" when Rebuild is set or any image of a
    service with a build section is missing, and returns $true when the build succeeded or nothing was missing.
    Returns $false when the build fails. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context, [Parameter(Mandatory = $true)] $Project, [switch]$Rebuild)

    $missing = @(Get-ServiceImage -Project $Project | Where-Object {
            (Invoke-NativeCapture -Command 'docker' -Argument @('image', 'inspect', '--format', '{{.Id}}', $_) -IncludeErrorStream).Status -ne 0
        })
    if (-not $Rebuild -and $missing.Length -eq 0) {
        Write-Host '  Images: all present (setup.cmd -Rebuild builds them again)'
        return $true
    }
    if ($missing.Length -gt 0) { Write-Host "  Images missing: $($missing -join ', ')" }
    $status = Invoke-NativeShown -Command 'docker' -Argument ((Get-ComposeArgument -Context $Context) + @('--profile', 'ai', '--profile', 'tools', 'build'))
    if ($status -ne 0) {
        Write-Host "FAILURE  Image build: docker compose build exited with $status (see the output above)"
        return $false
    }
    Write-Host '  Images: built'
    return $true
}

function Get-OwnerContainerArgument {
    # Returns the arguments of a one-off "docker run" that mounts VolumeName at /volume as root, with no network, no
    # pull, a read-only root and no capability except Capability. Not exported.
    [CmdletBinding()]
    [OutputType([string[]])]
    param([string]$VolumeName, [string]$Image, [string]$Entrypoint, [string[]]$Capability = @(), [switch]$ReadOnlyVolume)

    $mount = "${VolumeName}:/volume"
    if ($ReadOnlyVolume) { $mount += ':ro' }
    $arguments = @('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only', '--cap-drop', 'ALL')
    foreach ($item in $Capability) { $arguments += @('--cap-add', $item) }
    $arguments += @('--security-opt', 'no-new-privileges:true', '--user', '0:0', '--entrypoint', $Entrypoint, '--volume', $mount, $Image)
    return [string[]]$arguments
}

function Set-VolumeOwner {
    <#
    Makes UID and GID 10001 own the root of each volume of Volume, and returns $true when all of them do. The owner
    is read first in a container with no capability, and changed only when it differs, in a container with no
    network and only CAP_CHOWN, so a second run changes nothing. Only the root folder is changed, never the files
    inside. Returns $false when a container fails. Not exported.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [object[]]$Volume, [Parameter(Mandatory = $true)] [string]$Image)

    foreach ($item in $Volume) {
        $read = Invoke-NativeCapture -Command 'docker' -Argument ((Get-OwnerContainerArgument -VolumeName $item.Name -Image $Image -Entrypoint 'stat' -ReadOnlyVolume) + @('-c', '%u:%g', '/volume')) -IncludeErrorStream
        if ($read.Status -ne 0) {
            Write-Host "FAILURE  Volume $($item.Name): its owner could not be read: $($read.Output -join ' ')"
            return $false
        }
        $owner = ($read.Output -join '').Trim()
        if ($owner -eq $script:ServiceUser) {
            Write-Host "  Volume $($item.Name): owned by $script:ServiceUser"
            continue
        }
        if (-not $PSCmdlet.ShouldProcess($item.Name, "Change the owner of the volume root from $owner to $script:ServiceUser")) { continue }
        $change = Invoke-NativeCapture -Command 'docker' -Argument ((Get-OwnerContainerArgument -VolumeName $item.Name -Image $Image -Entrypoint 'chown' -Capability @('CHOWN')) + @($script:ServiceUser, '/volume')) -IncludeErrorStream
        if ($change.Status -ne 0) {
            Write-Host "FAILURE  Volume $($item.Name): its owner could not be set: $($change.Output -join ' ')"
            return $false
        }
        Write-Host "  Volume $($item.Name): owner set to $script:ServiceUser (was $owner)"
    }
    return $true
}

function Install-RuntimeContainer {
    <#
    Creates the runtime containers of the services lyrveil-llm and lyrveil-image that Project defines, with
    "docker compose --profile ai create", which starts nothing and leaves an up-to-date container as it is. Returns
    $true when that succeeded or Project defines neither service yet, and $false when Compose fails. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context, [Parameter(Mandatory = $true)] $Project)

    $services = Get-ObjectProperty -Object $Project -Name 'services'
    $present = @($script:RuntimeServices | Where-Object { $null -ne (Get-ObjectProperty -Object $services -Name $_) })
    if ($present.Length -eq 0) {
        Write-Host "  Runtime containers: $($script:RuntimeServices -join ' and ') are not in the Compose file yet (P02, P05), so none was created"
        return $true
    }
    $status = Invoke-NativeShown -Command 'docker' -Argument ((Get-ComposeArgument -Context $Context) + @('--profile', 'ai', 'create') + $present)
    if ($status -ne 0) {
        Write-Host "FAILURE  Runtime containers: docker compose create exited with $status (see the output above)"
        return $false
    }
    Write-Host "  Runtime containers: $($present -join ', ') created, not started"
    return $true
}

function Read-SetupOption {
    # Returns @{ Rebuild; SkipModels } from Argument, matched without case, or $null after printing why when an
    # argument is unknown or names a planned option. Not exported.
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([string[]]$Argument = @())

    $options = @{ Rebuild = $false; SkipModels = $false }
    foreach ($item in @($Argument | Where-Object { $_ })) {
        $known = @($script:Options | Where-Object { $_ -ieq $item })
        if ($known.Length -gt 0) {
            $options[$known[0].TrimStart('-')] = $true
            continue
        }
        $planned = @($script:PlannedOptions.Keys | Where-Object { $_ -ieq $item })
        if ($planned.Length -gt 0) {
            Write-Host "setup: $($planned[0]) is not available yet. $($script:PlannedOptions[$planned[0]])"
            return $null
        }
        Write-Host "setup: unknown option '$item'. Options: $(($script:Options + @($script:PlannedOptions.Keys | Sort-Object)) -join ', ')."
        return $null
    }
    return $options
}

function Invoke-LyrveilSetup {
    <#
    Runs setup for the repository at Root and returns its exit status (docs/operations.md, Scripts): 0 on success,
    1 on an unknown option, a planned option or a failed step, and 2 when a preflight check fails or a volume exists
    without its label. Steps, each skipped when its result is already there, so a second run changes nothing:
      1. preflight: Git, Docker daemon, Compose, Engine API, memory seen by Docker, free disk;
      2. .env from .env.example with a new token and access for the current user only, never overwritten;
      3. the two external volumes, created with the label io.lyrveil.volume;
      4. the images of the ai and tools profiles, built when one is missing or with -Rebuild;
      5. the owner 10001:10001 of each volume root, set by a container with no network and only CAP_CHOWN;
      6. the runtime containers, created and not started.
    Model licenses, downloads and launch files are not part of it yet: they need the model catalog of P02.1a.

    ProjectName, ComposeFile (relative to Root), EnvPath and ExamplePath default to the installation's own; tests
    pass their own so that no command reaches "lyrveil", its volumes or its .env. PreflightFact, for tests, replaces
    the facts read from the machine.
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
        [hashtable]$PreflightFact = $null
    )

    $options = Read-SetupOption -Argument $Argument
    if ($null -eq $options) { return 1 }
    if (-not $EnvPath) { $EnvPath = Join-Path $Root '.env' }
    if (-not $ExamplePath) { $ExamplePath = Join-Path $Root '.env.example' }
    $context = @{
        ProjectName = $ProjectName
        EnvPath     = $EnvPath
        ComposeFile = [string[]]@($ComposeFile | ForEach-Object { Join-Path $Root $_ })
    }

    Write-Host 'Preflight'
    $fact = if ($null -ne $PreflightFact) { $PreflightFact } else { Get-PreflightFact -EnvPath $EnvPath }
    $rows = @(Get-PreflightResult -Fact $fact)
    Write-PreflightReport -Row $rows
    if (@($rows | Where-Object { $_.Level -eq 'FAILURE' }).Length -gt 0) {
        Write-Host 'Preflight failed (exit status 2). Follow the advice above, then run setup.cmd again. Nothing was changed.'
        return 2
    }

    Write-Host 'Configuration'
    $envResult = Initialize-EnvironmentFile -Path $EnvPath -Example $ExamplePath
    if ($envResult -eq 'created') {
        Write-Host "  $EnvPath created with a new control token, readable by the current user only"
    }
    else {
        Write-Host "  $EnvPath present, left unchanged"
    }

    $project = Get-ComposeProject -Context $context
    $volumes = @(Get-ProjectVolume -Project $project)
    Write-Host 'Volumes'
    if (-not (Install-ProjectVolume -Volume $volumes)) {
        Write-Host 'Setup stopped (exit status 2).'
        return 2
    }

    Write-Host 'Images'
    if (-not (Invoke-ProjectBuild -Context $context -Project $project -Rebuild:$options.Rebuild)) { return 1 }

    $ownerService = Get-ObjectProperty -Object (Get-ObjectProperty -Object $project -Name 'services') -Name $script:OwnerImageService
    $ownerImage = Get-ObjectProperty -Object $ownerService -Name 'image'
    if (-not $ownerImage) { throw "The Compose configuration has no image for $script:OwnerImageService." }
    Write-Host 'Volume owners'
    if (-not (Set-VolumeOwner -Volume $volumes -Image ([string]$ownerImage))) { return 1 }

    Write-Host 'Models'
    Write-Host '  License acceptance, downloads and launch files need the model catalog, which is not in the image yet (P02.1a); nothing was downloaded'

    Write-Host 'Runtime containers'
    if (-not (Install-RuntimeContainer -Context $context -Project $project)) { return 1 }

    Write-Host 'Setup complete.'
    return 0
}

# The helpers after the first line are shared with Lifecycle.psm1, which runs start, stop, status and logs on the same
# project, with the same Compose arguments, volumes and images as setup, and with DataFiles.psm1.
Export-ModuleMember -Function Invoke-LyrveilSetup, Get-PreflightResult, Initialize-EnvironmentFile,
Invoke-NativeCapture, Invoke-NativeShown, Get-PreflightFact, Write-PreflightReport, Get-ComposeArgument,
Get-ComposeProject, Get-ObjectProperty, Get-ProjectVolume, Get-ServiceImage, Get-EnvFileValue
