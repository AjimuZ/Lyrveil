# Runs the lifecycle commands of docs/operations.md on a Compose project: start, stop, restart, status and logs.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Setup.psm1') -Force

# The services start.cmd starts and waits for. The frontend publishes the app; it depends on the backend being healthy.
$script:CoreServices = @('lyrveil-backend', 'lyrveil-frontend')
$script:FrontendService = 'lyrveil-frontend'
$script:FrontendPort = '8080'
# Services of compose.yaml that start.cmd does not start yet, with the reason it prints as a warning.
$script:DeferredServices = [ordered]@{
    'lyrveil-orchestrator' = 'not started, because its program comes with P02 and its container exits at once until then'
}
# The runtime services, which setup creates and the orchestrator starts. They enter compose.yaml with P02 and P05.
$script:RuntimeServices = @('lyrveil-llm', 'lyrveil-image')
$script:VolumeLabel = 'io.lyrveil.volume'
$script:VolumeRows = @{ 'lyrveil-data' = 'Data volume'; 'lyrveil-models' = 'Models volume' }
$script:HealthWaitSeconds = 120
$script:StopTimeoutSeconds = 40
$script:LogTail = 50

# What start.cmd and stop.cmd cannot do yet, printed as warnings so that no run claims it was done.
$script:ModelFilesPending = 'not checked, because the model catalog is not available yet (P02)'
$script:TextModelPending = 'not started, because the model catalog and the runtimes are not available yet (P02). The app works for reading and editing'
$script:BackupsPending = 'not copied to the Windows backup folder, because the backup command is not available yet'
$script:DrainPending = 'skipped, because the job queue and its drain command come with P02'
$script:ReleasePending = 'skipped, because the orchestrator manages no runtime until P02'
# What status.cmd cannot report yet. The keys are the JSON fields, which are null until then.
$script:StatusPending = [ordered]@{
    gpu                = 'GPU state and VRAM: not available yet, because the orchestrator that reads them comes with P02'
    queues             = 'Queue sizes: not available yet, because the job queue comes with P02'
    active_model       = 'Active model and launch level: not available yet, because the model catalog and the runtimes come with P02'
    model_verification = 'Model verification: not available yet, because the model catalog comes with P02'
}

$script:Invariant = [System.Globalization.CultureInfo]::InvariantCulture

function Resolve-LifecycleContext {
    <#
    Returns the context of one command: ProjectName, ComposeFile (absolute paths), EnvPath (the file Compose
    interpolates from), InstallationEnvPath (the installation's .env) and ExamplePath. EnvPath is the .env when it
    exists and .env.example otherwise, so a command run before setup never fails on a missing env file and never reads
    another .env. Pure.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string]$ProjectName,
        [string[]]$ComposeFile,
        [string]$EnvPath,
        [string]$ExamplePath
    )

    if (-not $EnvPath) { $EnvPath = Join-Path $Root '.env' }
    if (-not $ExamplePath) { $ExamplePath = Join-Path $Root '.env.example' }
    $interpolation = if (Test-Path -LiteralPath $EnvPath -PathType Leaf) { $EnvPath } else { $ExamplePath }
    return @{
        Root                = $Root
        ProjectName         = $ProjectName
        ComposeFile         = [string[]]@($ComposeFile | ForEach-Object { Join-Path $Root $_ })
        EnvPath             = $interpolation
        InstallationEnvPath = $EnvPath
        ExamplePath         = $ExamplePath
    }
}

function Read-CommandOption {
    <#
    Returns a hashtable with one entry per name of Flag ($true when given), one per name of Valued (the next argument,
    or $null), and Positional, the arguments that do not start with "-" when AllowPositional is set. Names match
    without case. Returns $null after printing why when an argument is unknown or a value is missing.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @(),
        [string[]]$Flag = @(),
        [string[]]$Valued = @(),
        [switch]$AllowPositional
    )

    $options = @{ Positional = [string[]]@() }
    foreach ($name in $Flag) { $options[$name.TrimStart('-')] = $false }
    foreach ($name in $Valued) { $options[$name.TrimStart('-')] = $null }
    $items = @($Argument | Where-Object { $_ })
    for ($index = 0; $index -lt $items.Length; $index++) {
        $item = [string]$items[$index]
        $flagName = @($Flag | Where-Object { $_ -ieq $item })
        if ($flagName.Length -gt 0) {
            $options[$flagName[0].TrimStart('-')] = $true
            continue
        }
        $valueName = @($Valued | Where-Object { $_ -ieq $item })
        if ($valueName.Length -gt 0) {
            if ($index + 1 -ge $items.Length) {
                Write-Host "${Command}: $($valueName[0]) needs a value."
                return $null
            }
            $index++
            $options[$valueName[0].TrimStart('-')] = [string]$items[$index]
            continue
        }
        if ($AllowPositional -and -not $item.StartsWith('-')) {
            $options['Positional'] = [string[]]($options['Positional'] + $item)
            continue
        }
        $known = @($Flag) + @($Valued)
        $usage = if ($known.Length -gt 0) { "Options: $($known -join ', ')." } else { 'It takes no option.' }
        Write-Host "${Command}: unknown argument '$item'. $usage"
        return $null
    }
    return $options
}

function Test-DockerReachable {
    # Returns $true when the docker command exists and its daemon answers. Reads only.
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    return ((Invoke-NativeCapture -Command 'docker' -Argument @('version', '--format', '{{.Server.APIVersion}}') -IncludeErrorStream).Status -eq 0)
}

function Write-WarningRow {
    # Prints "WARNING  Name: Detail" and adds "Name: Detail" to Collector.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [System.Collections.Generic.List[string]]$Collector,
        [Parameter(Mandatory = $true)] [string]$Name,
        [Parameter(Mandatory = $true)] [string]$Detail
    )

    Write-Host "WARNING  ${Name}: $Detail"
    $Collector.Add("${Name}: $Detail")
}

function Get-AppUrl {
    <#
    Returns @{ Url; ProbeUrl } for the port that Project publishes for the frontend: Url is the address users open,
    http://localhost:<port>, and ProbeUrl the same port on 127.0.0.1, where the port is bound. Throws when the
    frontend publishes no port.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] $Project)

    $frontend = Get-ObjectProperty -Object (Get-ObjectProperty -Object $Project -Name 'services') -Name $script:FrontendService
    foreach ($port in @(Get-ObjectProperty -Object $frontend -Name 'ports')) {
        if ($null -eq $port -or [string](Get-ObjectProperty -Object $port -Name 'target') -ne $script:FrontendPort) { continue }
        $published = [string](Get-ObjectProperty -Object $port -Name 'published')
        if ($published) { return @{ Url = "http://localhost:$published"; ProbeUrl = "http://127.0.0.1:$published/" } }
    }
    throw "The Compose configuration publishes no port for $script:FrontendService."
}

function Test-AppAnswer {
    # Returns $true when a GET of Url answers with a status under 400 within 5 seconds, without a proxy. Never throws.
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Url)

    try {
        $request = [System.Net.WebRequest]::Create($Url)
        $request.Proxy = $null
        $request.Timeout = 5000
        $request.AllowAutoRedirect = $false
        $response = $request.GetResponse()
        try {
            return ([int]$response.StatusCode -lt 400)
        }
        finally {
            $response.Close()
        }
    }
    catch {
        return $false
    }
}

function Get-ServiceContainer {
    <#
    Returns a hashtable from service name to the container Compose reports for it with "ps --all", an object with
    State (such as running or exited) and Health (healthy, unhealthy, starting, or empty without a health check).
    A service without a container is absent. Throws when Compose fails. Reads only.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context)

    $result = Invoke-NativeCapture -Command 'docker' -Argument ((Get-ComposeArgument -Context $Context) + @('--profile', 'ai', 'ps', '--all', '--format', 'json'))
    if ($result.Status -ne 0) { throw "docker compose ps failed with exit status $($result.Status)." }
    $text = ($result.Output -join "`n").Trim()
    # Compose prints one JSON object per line; versions before 2.21 printed one array.
    $items = @()
    if ($text.StartsWith('[')) {
        $parsed = $text | ConvertFrom-Json
        $items = @($parsed | ForEach-Object { $_ })
    }
    elseif ($text) {
        $items = @($result.Output | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    }
    $containers = @{}
    foreach ($item in $items) {
        $service = [string](Get-ObjectProperty -Object $item -Name 'Service')
        if (-not $service) { continue }
        $containers[$service] = [pscustomobject]@{
            State  = [string](Get-ObjectProperty -Object $item -Name 'State')
            Health = [string](Get-ObjectProperty -Object $item -Name 'Health')
        }
    }
    return $containers
}

function Get-InstallationState {
    <#
    Returns what start needs from setup: Missing, the parts that are absent (.env, a volume, an image, a runtime
    container), and Unlabelled, the volumes that exist without the label io.lyrveil.volume and so were not created by
    setup. Volume holds the volumes of Project (see Get-ProjectVolume). Reads only. Throws when Docker fails.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context, [Parameter(Mandatory = $true)] $Project)

    $missing = New-Object System.Collections.Generic.List[string]
    $unlabelled = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Context.InstallationEnvPath -PathType Leaf)) { $missing.Add('.env') }

    $volumes = @(Get-ProjectVolume -Project $Project)
    foreach ($volume in $volumes) {
        # The labels come as JSON: Windows PowerShell 5.1 drops the quotes inside a native argument.
        $inspect = Invoke-NativeCapture -Command 'docker' -Argument @('volume', 'inspect', '--format', '{{json .Labels}}', $volume.Name) -IncludeErrorStream
        if ($inspect.Status -ne 0) {
            $missing.Add("volume $($volume.Name)")
            continue
        }
        $labels = ($inspect.Output -join '') | ConvertFrom-Json
        if (-not (Get-ObjectProperty -Object $labels -Name $script:VolumeLabel)) { $unlabelled.Add($volume) }
    }

    foreach ($image in @(Get-ServiceImage -Project $Project)) {
        if ((Invoke-NativeCapture -Command 'docker' -Argument @('image', 'inspect', '--format', '{{.Id}}', $image) -IncludeErrorStream).Status -ne 0) {
            $missing.Add("image $image")
        }
    }

    $services = Get-ObjectProperty -Object $Project -Name 'services'
    foreach ($service in @($script:RuntimeServices | Where-Object { $null -ne (Get-ObjectProperty -Object $services -Name $_) })) {
        $ps = Invoke-NativeCapture -Command 'docker' -Argument ((Get-ComposeArgument -Context $Context) + @('--profile', 'ai', 'ps', '--all', '--quiet', $service))
        if ($ps.Status -ne 0 -or -not (($ps.Output -join '').Trim())) { $missing.Add("runtime container $service") }
    }

    return @{ Missing = [string[]]$missing.ToArray(); Unlabelled = [object[]]$unlabelled.ToArray(); Volume = $volumes }
}

function Invoke-GpuPreflight {
    <#
    Runs the orchestrator's preflight rows (GPU in container, CUDA driver, total and free VRAM) in a one-off
    orchestrator container, prints its lines and adds its warnings to Collector. Returns $true when no row failed, and
    $false when a row failed or Docker could not run the container, for example without GPU support.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Context,
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [System.Collections.Generic.List[string]]$Collector
    )

    $arguments = (Get-ComposeArgument -Context $Context) + @('run', '--rm', '--no-deps', '-T', 'lyrveil-orchestrator', 'python', '-m', 'lyrveil_orchestrator.preflight')
    $result = Invoke-NativeCapture -Command 'docker' -Argument $arguments -IncludeErrorStream
    foreach ($line in $result.Output) {
        Write-Host $line
        if ($line -match '^WARNING\s+(.+)$') { $Collector.Add($Matches[1]) }
    }
    if ($result.Status -eq 0) { return $true }
    if ($result.Status -ne 1) {
        Write-Host "FAILURE  GPU in container: Docker could not run the check (exit $($result.Status))"
        Write-Host '         See Troubleshooting in docs/operations.md, for example for "could not select device driver", then run start.cmd again.'
    }
    return $false
}

function Write-RecentLog {
    # Prints the last lines of the logs of the core services, for a start that failed its health checks.
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)] [hashtable]$Context)

    Write-Host "Last $script:LogTail log lines of each core service:"
    $null = Invoke-NativeShown -Command 'docker' -Argument ((Get-ComposeArgument -Context $Context) + @('logs', '--no-color', '--tail', "$script:LogTail") + $script:CoreServices)
}

function Invoke-LyrveilStart {
    <#
    Runs start.cmd for the repository at Root and returns its exit status (docs/operations.md, Scripts): 0 when the
    core services are healthy and the app answers, 1 on an unknown argument or an unexpected failure, 2 when a
    preflight check fails or a volume exists without its label, 3 when a health check fails or times out, and the
    status of setup when setup is needed and fails. Steps:
      1. preflight: Docker daemon, Compose, Engine API, memory seen by Docker, free disk;
      2. setup, when .env, a volume, an image or a runtime container is missing;
      3. the data and models volumes, which must carry the label;
      4. the orchestrator's GPU rows, in a one-off container, skipped with a warning by SkipGpuPreflight;
      5. "docker compose up -d --no-build --wait" for the core services, waiting up to WaitSeconds for health, then
         a GET of the app's URL;
      6. the ready line, "Lyrveil <e-grave> pronto: <url>", printed last, after a summary of the warnings.
    Model files, the text model and the backup copy are not available yet: each prints a warning and never fails.
    The orchestrator is not started until P02, also with a warning.

    The parameters after Argument are for tests, as in Invoke-LyrveilSetup: they keep every command away from the
    project "lyrveil", its volumes and its .env. SkipGpuPreflight lets the tests run on a machine without a GPU.
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
        [int]$WaitSeconds = $script:HealthWaitSeconds
    )

    $options = Read-CommandOption -Command 'start' -Argument $Argument
    if ($null -eq $options) { return 1 }
    $contextParameters = @{ Root = $Root; ProjectName = $ProjectName; ComposeFile = $ComposeFile; EnvPath = $EnvPath; ExamplePath = $ExamplePath }
    $context = Resolve-LifecycleContext @contextParameters
    $warnings = New-Object System.Collections.Generic.List[string]

    Write-Host 'Preflight'
    $fact = if ($null -ne $PreflightFact) { $PreflightFact } else { Get-PreflightFact -EnvPath $context.InstallationEnvPath }
    # Git is a setup requirement only: start never runs it.
    $rows = @(Get-PreflightResult -Fact $fact | Where-Object { $_.Name -ne 'Git' })
    Write-PreflightReport -Row $rows
    foreach ($row in @($rows | Where-Object { $_.Level -eq 'WARNING' })) { $warnings.Add("$($row.Name): $($row.Detail)") }
    if (@($rows | Where-Object { $_.Level -eq 'FAILURE' }).Length -gt 0) {
        Write-Host 'Preflight failed (exit status 2). Follow the advice above, then run start.cmd again.'
        return 2
    }

    Write-Host 'Installation'
    $project = Get-ComposeProject -Context $context
    $state = Get-InstallationState -Context $context -Project $project
    if ($state.Missing.Length -gt 0 -and $state.Unlabelled.Length -eq 0) {
        Write-Host "  Missing: $($state.Missing -join ', '), so setup runs"
        $setupStatus = Invoke-LyrveilSetup -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $context.InstallationEnvPath -ExamplePath $context.ExamplePath -PreflightFact $PreflightFact
        if ([int]$setupStatus -ne 0) {
            Write-Host "Start stopped, because setup exited with status $setupStatus."
            return [int]$setupStatus
        }
        $context = Resolve-LifecycleContext @contextParameters
        $project = Get-ComposeProject -Context $context
        $state = Get-InstallationState -Context $context -Project $project
    }
    elseif ($state.Missing.Length -eq 0) {
        Write-Host '  .env, volumes, images and runtime containers: present'
    }

    $failed = $false
    foreach ($volume in $state.Volume) {
        $name = $script:VolumeRows[$volume.Key]
        if (@($state.Unlabelled | Where-Object { $_.Name -eq $volume.Name }).Length -gt 0) {
            Write-Host "FAILURE  ${name}: $($volume.Name) exists without the label $script:VolumeLabel"
            Write-Host '         Setup did not create it, so it is left untouched. Rename or remove it on purpose (docs/operations.md, Uninstall), then run setup.cmd.'
            $failed = $true
        }
        elseif (@($state.Missing | Where-Object { $_ -eq "volume $($volume.Name)" }).Length -gt 0) {
            Write-Host "FAILURE  ${name}: $($volume.Name) is missing"
            Write-Host '         Run setup.cmd, then restore the newest backup from the Windows backup folder (docs/operations.md, Backup and restore).'
            $failed = $true
        }
        else {
            Write-Host "PASS     ${name}: $($volume.Name) exists with the label"
        }
    }
    if ($failed) {
        Write-Host 'Preflight failed (exit status 2). Nothing was started.'
        return 2
    }
    if ($state.Missing.Length -gt 0) { throw "Setup left these parts missing: $($state.Missing -join ', ')." }

    Write-Host 'GPU'
    if ($SkipGpuPreflight) {
        Write-WarningRow -Collector $warnings -Name 'GPU checks' -Detail 'skipped by the caller'
    }
    elseif (-not (Invoke-GpuPreflight -Context $context -Collector $warnings)) {
        Write-Host 'Preflight failed (exit status 2). Follow the advice above, then run start.cmd again.'
        return 2
    }

    Write-Host 'Models'
    Write-WarningRow -Collector $warnings -Name 'Model files' -Detail $script:ModelFilesPending

    Write-Host 'Services'
    foreach ($service in $script:DeferredServices.Keys) {
        Write-WarningRow -Collector $warnings -Name $service -Detail $script:DeferredServices[$service]
    }
    $app = Get-AppUrl -Project $project
    $up = (Get-ComposeArgument -Context $context) + @('up', '--detach', '--no-build', '--wait', '--wait-timeout', "$WaitSeconds") + $script:CoreServices
    $status = Invoke-NativeShown -Command 'docker' -Argument $up
    if ($status -ne 0) {
        Write-Host "FAILURE  Health checks: $($script:CoreServices -join ' and ') did not become healthy within $WaitSeconds s (docker compose up exited with $status)"
        Write-RecentLog -Context $context
        Write-Host 'Start failed (exit status 3). See the logs above and Troubleshooting in docs/operations.md.'
        return 3
    }
    if (-not (Test-AppAnswer -Url $app.ProbeUrl)) {
        Write-Host "FAILURE  App: the services are healthy, but $($app.Url) does not answer"
        Write-RecentLog -Context $context
        Write-Host 'Start failed (exit status 3). See the logs above and Troubleshooting in docs/operations.md.'
        return 3
    }
    Write-Host "PASS     Health checks: $($script:CoreServices -join ' and ') are healthy, and $($app.Url) answers"

    Write-Host 'Text model'
    Write-WarningRow -Collector $warnings -Name 'Text model' -Detail $script:TextModelPending
    Write-Host 'Backups'
    Write-WarningRow -Collector $warnings -Name 'Backups' -Detail $script:BackupsPending

    Write-Host "Started with $($warnings.Count) warnings:"
    foreach ($warning in $warnings) { Write-Host "  - $warning" }
    # The ready line is the last line, in Italian as the interface. [char] keeps this file ASCII for PowerShell 5.1.
    Write-Host "Lyrveil $([char]0x00E8) pronto: $($app.Url)"
    return 0
}

function Invoke-LyrveilStop {
    <#
    Runs stop.cmd and returns its exit status: 0 when every service of the project is stopped, also when they were
    stopped already or Docker is not running, 1 on an unknown argument or when "docker compose stop" fails. It stops
    the containers with "docker compose --profile ai stop --timeout 40" and keeps them, their volumes and their data:
    it never runs "docker compose down", with or without -v. The queue drain, the runtime release and the backup copy
    are not available yet and print warnings. The parameters after Argument are for tests (see Invoke-LyrveilStart).
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

    $options = Read-CommandOption -Command 'stop' -Argument $Argument
    if ($null -eq $options) { return 1 }
    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    if (-not (Test-DockerReachable)) {
        Write-Host 'Docker is not running, so no Lyrveil service is running. Nothing to stop.'
        return 0
    }

    $warnings = New-Object System.Collections.Generic.List[string]
    Write-WarningRow -Collector $warnings -Name 'Queue drain' -Detail $script:DrainPending
    Write-WarningRow -Collector $warnings -Name 'Runtime release' -Detail $script:ReleasePending
    Write-WarningRow -Collector $warnings -Name 'Backups' -Detail $script:BackupsPending

    Write-Host 'Services'
    $status = Invoke-NativeShown -Command 'docker' -Argument ((Get-ComposeArgument -Context $context) + @('--profile', 'ai', 'stop', '--timeout', "$script:StopTimeoutSeconds"))
    if ($status -ne 0) {
        Write-Host "FAILURE  Services: docker compose stop exited with $status (see the output above)"
        return 1
    }
    Write-Host 'Lyrveil is stopped. Containers, volumes and data are kept.'
    return 0
}

function Invoke-LyrveilRestart {
    <#
    Runs restart.cmd: stop, then start. Returns 1 on an unknown argument, the status of stop when it fails, and the
    status of start otherwise. The parameters after Argument are for tests (see Invoke-LyrveilStart).
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
        [int]$WaitSeconds = $script:HealthWaitSeconds
    )

    $options = Read-CommandOption -Command 'restart' -Argument $Argument
    if ($null -eq $options) { return 1 }
    $common = @{ Root = $Root; ProjectName = $ProjectName; ComposeFile = $ComposeFile; EnvPath = $EnvPath; ExamplePath = $ExamplePath }
    $stopStatus = Invoke-LyrveilStop @common
    if ([int]$stopStatus -ne 0) { return [int]$stopStatus }
    return [int](Invoke-LyrveilStart @common -PreflightFact $PreflightFact -SkipGpuPreflight:$SkipGpuPreflight -WaitSeconds $WaitSeconds)
}

function Get-FreeDisk {
    # Returns @{ drive; free_bytes } for the drive of %LOCALAPPDATA%, which holds Docker Desktop's data, with
    # free_bytes $null when it cannot be read. Reads only.
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param()

    $folder = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:SystemDrive + '\' }
    $disk = [ordered]@{ drive = $null; free_bytes = $null }
    try {
        $root = [System.IO.Path]::GetPathRoot($folder)
        $disk.drive = $root.TrimEnd('\')
        $disk.free_bytes = [long](New-Object System.IO.DriveInfo $root).AvailableFreeSpace
    }
    catch {
        $disk.free_bytes = $null
    }
    return $disk
}

function Get-LyrveilStatus {
    <#
    Returns the report of status.cmd as an ordered dictionary, the shape of "status.cmd -Json": project, url,
    app_answers, ready (every core service running and healthy, and the app answering), services (name, state, health,
    and started_by_start, false for a service that start.cmd does not start yet), disk, warnings, and gpu, queues,
    active_model and model_verification, which are null until their components exist. A service without a container
    has the state "missing". Reads only. Throws when Compose fails.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context)

    $project = Get-ComposeProject -Context $Context
    $app = Get-AppUrl -Project $project
    $containers = Get-ServiceContainer -Context $Context
    $services = New-Object System.Collections.Generic.List[object]
    $ready = $true
    foreach ($property in @((Get-ObjectProperty -Object $project -Name 'services').PSObject.Properties)) {
        # One-shot tools such as the model downloader have no lasting state to report.
        if (@(Get-ObjectProperty -Object $property.Value -Name 'profiles') -contains 'tools') { continue }
        $container = $containers[$property.Name]
        $state = if ($null -ne $container) { $container.State } else { 'missing' }
        $health = if ($null -ne $container -and $container.Health) { $container.Health } else { $null }
        $isCore = $script:CoreServices -contains $property.Name
        if ($isCore -and ($state -ne 'running' -or $health -ne 'healthy')) { $ready = $false }
        $services.Add([ordered]@{
                name             = $property.Name
                state            = $state
                health           = $health
                started_by_start = -not $script:DeferredServices.Contains($property.Name)
            })
    }
    $answers = Test-AppAnswer -Url $app.ProbeUrl
    $warnings = New-Object System.Collections.Generic.List[string]
    foreach ($service in $script:DeferredServices.Keys) { $warnings.Add("${service}: $($script:DeferredServices[$service])") }
    foreach ($key in $script:StatusPending.Keys) { $warnings.Add($script:StatusPending[$key]) }

    $report = [ordered]@{
        project     = $Context.ProjectName
        url         = $app.Url
        app_answers = $answers
        ready       = ($ready -and $answers)
        services    = [object[]]$services.ToArray()
    }
    foreach ($key in $script:StatusPending.Keys) { $report[$key] = $null }
    $report['disk'] = Get-FreeDisk
    $report['warnings'] = [string[]]$warnings.ToArray()
    return $report
}

function Invoke-LyrveilStatus {
    <#
    Runs status.cmd and returns its exit status: 0 when the report was printed, whatever the state of the services,
    1 on an unknown argument or an unexpected failure, and 2 when Docker is not running. With -Json it prints only
    the JSON of Get-LyrveilStatus, or {"docker_reachable": false} when Docker is not running. The parameters after
    Argument are for tests (see Invoke-LyrveilStart).
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

    $options = Read-CommandOption -Command 'status' -Argument $Argument -Flag @('-Json')
    if ($null -eq $options) { return 1 }
    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    if (-not (Test-DockerReachable)) {
        if ($options.Json) {
            Write-Host (ConvertTo-Json -InputObject ([ordered]@{ docker_reachable = $false }))
        }
        else {
            Write-Host 'FAILURE  Docker daemon: unreachable'
            Write-Host '         Start Docker Desktop and wait until it reports that the engine is running, then run status.cmd again.'
        }
        return 2
    }

    $report = Get-LyrveilStatus -Context $context
    if ($options.Json) {
        Write-Host (ConvertTo-Json -InputObject $report -Depth 5)
        return 0
    }

    Write-Host 'Services'
    foreach ($service in $report.services) {
        $detail = $service.state
        if ($service.health) { $detail += ", $($service.health)" }
        if (-not $service.started_by_start) { $detail += ' (not started by start.cmd until P02)' }
        Write-Host ('  {0,-22} {1}' -f $service.name, $detail)
    }
    $answer = if ($report.app_answers) { 'answers' } else { 'does not answer' }
    Write-Host "App: $($report.url), $answer"
    $readiness = if ($report.ready) { 'ready' } else { 'not ready: run start.cmd' }
    Write-Host "Lyrveil: $readiness"
    if ($null -ne $report.disk.free_bytes) {
        Write-Host ([string]::Format($script:Invariant, 'Disk: {0:0.0} GiB free on {1}', ($report.disk.free_bytes / 1GB), $report.disk.drive))
    }
    foreach ($warning in $report.warnings) { Write-Host "WARNING  $warning" }
    return 0
}

function Invoke-LyrveilLog {
    <#
    Runs logs.cmd, a thin wrapper around "docker compose logs": "logs.cmd [service ...] [-Follow] [-Since <time>]
    [-Tail <lines>]". Returns 0 when Compose succeeded, 1 on an unknown argument or a Compose failure (such as an
    unknown service), and 2 when Docker is not running. The parameters after Argument are for tests.
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

    $options = Read-CommandOption -Command 'logs' -Argument $Argument -Flag @('-Follow') -Valued @('-Since', '-Tail') -AllowPositional
    if ($null -eq $options) { return 1 }
    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    if (-not (Test-DockerReachable)) {
        Write-Host 'FAILURE  Docker daemon: unreachable. Start Docker Desktop, then run logs.cmd again.'
        return 2
    }

    $arguments = (Get-ComposeArgument -Context $context) + @('--profile', 'ai', 'logs')
    if ($options.Follow) { $arguments += '--follow' }
    if ($options.Since) { $arguments += @('--since', $options.Since) }
    if ($options.Tail) { $arguments += @('--tail', $options.Tail) }
    $arguments += $options.Positional
    $status = Invoke-NativeShown -Command 'docker' -Argument $arguments
    if ($status -ne 0) { return 1 }
    return 0
}

Export-ModuleMember -Function Invoke-LyrveilStart, Invoke-LyrveilStop, Invoke-LyrveilRestart, Invoke-LyrveilStatus,
Invoke-LyrveilLog, Get-LyrveilStatus, Read-CommandOption
