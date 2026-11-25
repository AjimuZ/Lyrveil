# Runs the gpu level against the real runtimes of compose.yaml in the project "lyrveil-gpu-test", and gives its tests NVML samples, the runtime APIs and, for the mode switch tests, the control and backend APIs.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# The project, volume and files of the level. Every command names the project with -p, so no COMPOSE_PROJECT_NAME can
# point it at "lyrveil", and the only volumes it may mount are its own data volume and the models, read-only.
$script:ProjectName = 'lyrveil-gpu-test'
$script:DataVolume = 'lyrveil-gpu-test-data'
$script:ModelsVolume = 'lyrveil-models'
$script:ComposeFiles = @('-f', 'compose.yaml', '-f', 'tests/gpu/compose.gpu-test.yaml')
$script:Services = @('lyrveil-llm', 'lyrveil-image')

# The services that only the mode switch tests build and start (tests/gpu/ModeSwitch.Tests.ps1): the test-only
# orchestrator, whose gate acts on this project's runtimes alone, and the backend. The override gives both the control
# token below and no env_file, so the installation's .env and its token never reach the test project.
$script:StackServices = @('lyrveil-orchestrator', 'lyrveil-backend')
$script:ControlToken = '7e57a11c' * 8
$script:StackEnvironment = @{
    'lyrveil-orchestrator' = @('LYRVEIL_CONTROL_TOKEN', 'LYRVEIL_LOG_LEVEL')
    'lyrveil-backend'      = @('LYRVEIL_CONTROL_TOKEN', 'LYRVEIL_LOG_LEVEL', 'LYRVEIL_TZ')
}
# The only host path any service may mount: the Docker socket, for the orchestrator's gate.
$script:DockerSocket = '/var/run/docker.sock'

# The one-off container of T-GPU-09 that holds VRAM as another program would (Start-GpuVramLoad). It carries the
# project's label, so the leftover check sees it.
$script:VramLoadName = 'lyrveil-gpu-test-vram-load'

# The backend's test stage, under a tag of this level only, where T-GPU-05 runs the backend's prompt, grammar and
# validation, and T-GPU-06 its render graphs and preset rule (Initialize-GpuBackendProbe, Invoke-GpuBackendProbe).
$script:BackendProbeImage = 'lyrveil/backend-test:gpu-test'

# The container names of an installation that could use the GPU: its runtimes, and its orchestrator, which starts a
# runtime by itself as soon as the card has room. The level refuses to start while one of them runs.
$script:InstallationRuntimes = @('lyrveil-llm', 'lyrveil-image', 'lyrveil-orchestrator')

# What Invoke-GpuLevel returns when the machine has no usable GPU: not 0, so a skip never reads as a pass.
$script:SkippedStatus = 2

# The record of Open-GpuCommandLog: $null while closed, otherwise every native command since it opened, in order, as
# @{ Line; Count }, a run of the same command being one entry with its count.
$script:CommandLog = $null

function Open-GpuCommandLog {
    <#
    Starts recording every native command that this module runs (docker, nvidia-smi), replacing any earlier record,
    so a test can store the commands behind its numbers. Close-GpuCommandLog returns the record.
    #>
    [CmdletBinding()]
    param()

    $script:CommandLog = New-Object System.Collections.Generic.List[object]
}

function Close-GpuCommandLog {
    <#
    Returns the commands recorded since Open-GpuCommandLog, one line each in order, a run of the same command written
    once with "(N times)", and stops recording. Returns none when no record was open.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    if ($null -eq $script:CommandLog) { return [string[]]@() }
    $lines = @($script:CommandLog | ForEach-Object { if ($_.Count -gt 1) { "$($_.Line) ($($_.Count) times)" } else { $_.Line } })
    $script:CommandLog = $null
    return [string[]]$lines
}

function Add-GpuCommandLog {
    # Appends Line to the open command record, or counts it when it repeats the last entry. Does nothing while the
    # record is closed. Not exported.
    param([string]$Line)

    if ($null -eq $script:CommandLog) { return }
    if ($script:CommandLog.Count -gt 0 -and $script:CommandLog[$script:CommandLog.Count - 1].Line -ceq $Line) {
        $script:CommandLog[$script:CommandLog.Count - 1].Count++
        return
    }
    $script:CommandLog.Add(@{ Line = $Line; Count = 1 })
}

function Format-GpuCommandLine {
    # Returns Command and Argument as one line, with an argument that holds a space or a quote in single quotes, and
    # the size of InputText when there is one. Not exported.
    param([string]$Command, [string[]]$Argument = @(), $InputText = $null)

    $parts = @($Command) + @($Argument | ForEach-Object { if ("$_" -match "[\s'`"]") { "'" + ("$_" -replace "'", "''") + "'" } else { "$_" } })
    $line = $parts -join ' '
    if ($null -ne $InputText) { $line += " < standard input of $(([string]$InputText).Length) characters" }
    return $line
}

function Invoke-GpuNative {
    <#
    Runs Command with Argument, in Directory when one is given, and returns @{ Status; Output }. With Capture, Output
    holds the standard output lines and standard error goes to the console; otherwise everything goes to the console
    and Output is empty. InputText, when given, is written to the command's standard input; it must be ASCII, because
    Windows PowerShell 5.1 encodes piped text with $OutputEncoding. Never throws for a non-zero status; a command that
    is not on PATH returns status 127. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [string]$Directory = '',
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @(),
        [string]$InputText = $null,
        [switch]$Capture
    )

    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return @{ Status = 127; Output = [string[]]@("The command '$Command' is not available.") }
    }
    $hasInput = $PSBoundParameters.ContainsKey('InputText')
    $logged = if ($hasInput) { $InputText } else { $null }
    Add-GpuCommandLog -Line (Format-GpuCommandLine -Command $Command -Argument $Argument -InputText $logged)
    # Windows PowerShell 5.1 may turn native standard error lines into error records, which 'Stop' would throw.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($Directory) { Push-Location -LiteralPath $Directory }
    try {
        $lines = @()
        if ($Capture -and $hasInput) { $lines = @($InputText | & $Command @Argument | ForEach-Object { [string]$_ }) }
        elseif ($Capture) { $lines = @(& $Command @Argument | ForEach-Object { [string]$_ }) }
        elseif ($hasInput) { $InputText | & $Command @Argument | Out-Host }
        else { & $Command @Argument | Out-Host }
        $status = [int]$LASTEXITCODE
    }
    finally {
        if ($Directory) { Pop-Location }
        $ErrorActionPreference = $previous
    }
    return @{ Status = $status; Output = [string[]]$lines }
}

function Get-GpuTestSetting {
    <#
    Returns the names the level uses: Project, DataVolume, ModelsVolume, Services (the two runtimes) and
    StackServices (the orchestrator and the backend of the mode switch tests). The tests compare mounts and
    containers with them.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    return @{
        Project       = $script:ProjectName
        DataVolume    = $script:DataVolume
        ModelsVolume  = $script:ModelsVolume
        Services      = [string[]]$script:Services
        StackServices = [string[]]$script:StackServices
    }
}

function Get-GpuSample {
    <#
    Reads GPU 0 through NVML with nvidia-smi and returns Available, Reason, Name, TotalMiB and UsedMiB. Available is
    $false, with the reason, when nvidia-smi is missing, fails (as it does without an NVIDIA driver) or lists no GPU;
    the numbers are then 0. Never throws. memory.used counts every process on the card, not only the test's.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param()

    $sample = [pscustomobject]@{ Available = $false; Reason = ''; Name = ''; TotalMiB = 0; UsedMiB = 0 }
    $result = Invoke-GpuNative -Command 'nvidia-smi' -Capture -Argument @(
        '--query-gpu=index,name,memory.total,memory.used', '--format=csv,noheader,nounits')
    if ($result.Status -eq 127) {
        $sample.Reason = 'nvidia-smi is not on PATH'
        return $sample
    }
    if ($result.Status -ne 0) {
        $first = @($result.Output | Where-Object { $_.Trim() } | Select-Object -First 1)
        $detail = if ($first.Length -gt 0) { ": $($first[0].Trim())" } else { '' }
        $sample.Reason = "nvidia-smi exited with status $($result.Status)$detail"
        return $sample
    }
    foreach ($line in $result.Output) {
        if ($line -match '^\s*0\s*,\s*(.+?)\s*,\s*(\d+)\s*,\s*(\d+)\s*$') {
            $sample.Available = $true
            $sample.Name = $Matches[1]
            $sample.TotalMiB = [int]$Matches[2]
            $sample.UsedMiB = [int]$Matches[3]
            return $sample
        }
    }
    $sample.Reason = 'nvidia-smi listed no GPU'
    return $sample
}

function Wait-GpuMemoryBelow {
    <#
    Samples NVML every two seconds until the used memory of GPU 0 is below LimitMiB, and returns $true then, or
    $false when TimeoutSecond passes first or no sample can be read.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [int]$LimitMiB,
        [int]$TimeoutSecond = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSecond)
    while ($true) {
        $sample = Get-GpuSample
        if (-not $sample.Available) { return $false }
        if ($sample.UsedMiB -lt $LimitMiB) { return $true }
        if ((Get-Date) -ge $deadline) {
            Write-Host "  GPU memory still at $($sample.UsedMiB) MiB after $TimeoutSecond s (limit $LimitMiB MiB)."
            return $false
        }
        Start-Sleep -Seconds 2
    }
}

function Invoke-GpuCompose {
    <#
    Runs "docker compose" for the test project in Root, with compose.yaml, tests/gpu/compose.gpu-test.yaml and the
    "ai" profile, and returns @{ Status; Output } as Invoke-GpuNative does, with InputText, when given, on its
    standard input.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @(),
        [string]$InputText = $null,
        [switch]$Capture
    )

    $arguments = Get-GpuComposeArgument -Argument $Argument
    if ($PSBoundParameters.ContainsKey('InputText')) {
        return (Invoke-GpuNative -Directory $Root -Command 'docker' -Argument $arguments -InputText $InputText -Capture:$Capture)
    }
    return (Invoke-GpuNative -Directory $Root -Command 'docker' -Argument $arguments -Capture:$Capture)
}

function Get-GpuComposeArgument {
    # Returns the docker arguments of "docker compose" for the test project with Argument appended. Not exported.
    param([string[]]$Argument = @())

    return [string[]](@('compose', '-p', $script:ProjectName) + $script:ComposeFiles + @('--profile', 'ai') + @($Argument))
}

function Get-GpuTestProfile {
    <#
    Returns the text profile of config/model-catalog.json with the smallest file, the one T-GPU-01 loads, after
    checking that its identifier and file name follow the patterns of the launch file. Throws when the catalog is
    missing, holds no text profile or names an invalid one.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $catalog = (Read-Utf8Text -Path (Join-Path $Root 'config\model-catalog.json')) | ConvertFrom-Json
    $smallest = @($catalog.text | Sort-Object -Property { [long]$_.bytes } | Select-Object -First 1)
    if ($smallest.Length -eq 0) { throw 'config/model-catalog.json has no text profile.' }
    return (Assert-GpuTestProfile -TextProfile $smallest[0])
}

function Get-GpuTestTextProfile {
    <#
    Returns the text profile Id of config/model-catalog.json, after the same checks as Get-GpuTestProfile. Throws when
    the catalog is missing, has no such profile or names an invalid one.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Id
    )

    $catalog = (Read-Utf8Text -Path (Join-Path $Root 'config\model-catalog.json')) | ConvertFrom-Json
    $found = @($catalog.text | Where-Object { $_.id -eq $Id })
    if ($found.Length -ne 1) { throw "config/model-catalog.json has no text profile '$Id'." }
    return (Assert-GpuTestProfile -TextProfile $found[0])
}

function Assert-GpuTestProfile {
    # Returns TextProfile when its identifier and file name follow the patterns of the launch file, and throws
    # otherwise, so no catalog value with a space or shell character reaches the shell of Write-GpuTestLaunchFile.
    # Not exported.
    param([Parameter(Mandatory = $true)] $TextProfile)

    if ($TextProfile.id -notmatch '^[a-z0-9][a-z0-9.-]{1,47}$' -or $TextProfile.file -notmatch '^[A-Za-z0-9._-]{1,128}\.gguf$') {
        throw "The catalog text profile '$($TextProfile.id)' has an identifier or file name the launch file refuses."
    }
    return $TextProfile
}

function Get-GpuLaunchLine {
    <#
    Returns the lines of the text runtime's launch file for TextProfile, with every key of docs/configuration.md.
    Level Custom, the default, gives a small 4096-token context and every layer on the GPU (GPU_LAYERS 99), so the
    model's weights must show up in VRAM (T-GPU-01). Level A gives what the backend writes for a catalog profile at
    level A (lyrveil/orchestration/launch_file.py): the profile's context, batch, mmap, KV cache type, SWA padding and
    SmartCache, AutoFit (GPU_LAYERS -1) and no expert layer in RAM (T-GPU-02).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] $TextProfile,
        [ValidateSet('Custom', 'A')] [string]$Level = 'Custom'
    )

    if ($Level -eq 'A') {
        $mmap = if ($TextProfile.use_mmap) { '1' } else { '0' }
        return [string[]]@(
            "MODEL_PROFILE=$($TextProfile.id)", "MODEL_FILE=$($TextProfile.file)", "CONTEXT_SIZE=$($TextProfile.context_size)",
            'GPU_LAYERS=-1', "BATCH_SIZE=$($TextProfile.batch_size)", 'MOE_CPU_LAYERS=0', "USE_MMAP=$mmap",
            "KV_QUANT=$($TextProfile.kv_quant)", "SWA_PADDING=$($TextProfile.swa_padding)",
            "SMART_CACHE=$($TextProfile.smart_cache)", 'LEVEL=A')
    }
    return [string[]]@(
        "MODEL_PROFILE=$($TextProfile.id)", "MODEL_FILE=$($TextProfile.file)", 'CONTEXT_SIZE=4096', 'GPU_LAYERS=99',
        'BATCH_SIZE=512', 'MOE_CPU_LAYERS=0', 'USE_MMAP=0', 'KV_QUANT=f16', 'SWA_PADDING=0', 'SMART_CACHE=0',
        'LEVEL=CUSTOM')
}

function Get-JsonProperty {
    # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
    # Not exported.
    param($Value, [string]$Name)

    if ($null -eq $Value) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-GpuTestConfiguration {
    <#
    Checks the JSON of "docker compose config --format json" for the test project and returns one message for every
    way it could reach a real installation, or none when it is safe to start: the project must be lyrveil-gpu-test and
    hold exactly lyrveil-llm, lyrveil-image, lyrveil-orchestrator and lyrveil-backend, with ":gpu-test" images, no
    container_name and no published port; every mount must be a tmpfs or a named volume, and the only volumes may be
    lyrveil-gpu-test-data and lyrveil-models, both mounted read-only, except the backend's /data, which is the test
    data volume. The only other mount allowed is the Docker socket, in the orchestrator alone. The orchestrator and the
    backend must read no env_file, hold only the environment variables of the override, and carry the level's own
    control token, so the installation's .env never reaches them.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] [AllowEmptyString()] [string]$Text)

    try { $config = $Text | ConvertFrom-Json }
    catch { return [string[]]@("The resolved configuration is not JSON: $($_.Exception.Message)") }
    if ($null -eq $config) { return [string[]]@('The resolved configuration is empty.') }

    $failures = New-Object System.Collections.Generic.List[string]
    $allowed = @($script:DataVolume, $script:ModelsVolume)
    $name = Get-JsonProperty $config 'name'
    if ($name -ne $script:ProjectName) { $failures.Add("The project is '$name', not '$script:ProjectName'.") }

    # Compose keys of the volumes, mapped to the Docker volume each one names.
    $volumes = @{}
    $declared = Get-JsonProperty $config 'volumes'
    if ($null -ne $declared) {
        foreach ($entry in $declared.PSObject.Properties) {
            $volumeName = Get-JsonProperty $entry.Value 'name'
            if (-not $volumeName) { $volumeName = $entry.Name }
            $volumes[$entry.Name] = $volumeName
            if ($allowed -notcontains $volumeName) { $failures.Add("The configuration names the volume '$volumeName'.") }
        }
    }

    $services = Get-JsonProperty $config 'services'
    $serviceNames = @()
    if ($null -ne $services) { $serviceNames = @($services.PSObject.Properties | ForEach-Object { $_.Name } | Sort-Object) }
    $expected = @(@($script:Services) + @($script:StackServices) | Sort-Object)
    if (($serviceNames -join ',') -ne ($expected -join ',')) {
        $failures.Add("The services are '$($serviceNames -join ', ')', not '$($expected -join ', ')'.")
    }

    foreach ($serviceName in $serviceNames) {
        $service = Get-JsonProperty $services $serviceName
        if (Get-JsonProperty $service 'container_name') { $failures.Add("${serviceName} sets container_name.") }
        $image = [string](Get-JsonProperty $service 'image')
        if ($image -notlike '*:gpu-test') { $failures.Add("${serviceName} uses the image '$image', not a ':gpu-test' tag.") }
        if (@(Get-JsonProperty $service 'ports').Where({ $null -ne $_ }).Count -gt 0) { $failures.Add("${serviceName} publishes a port.") }
        foreach ($mount in @(Get-JsonProperty $service 'volumes')) {
            if ($null -eq $mount) { continue }
            $type = Get-JsonProperty $mount 'type'
            $target = Get-JsonProperty $mount 'target'
            if ($type -eq 'tmpfs') { continue }
            if ($type -eq 'bind' -and $serviceName -eq 'lyrveil-orchestrator' -and $target -eq $script:DockerSocket -and
                [string](Get-JsonProperty $mount 'source') -match '^(?:[A-Za-z]:)?[\\/]+var[\\/]+run[\\/]+docker\.sock$') { continue }
            if ($type -ne 'volume') {
                $failures.Add("${serviceName} mounts a $type at $target.")
                continue
            }
            $source = [string](Get-JsonProperty $mount 'source')
            $volumeName = if ($volumes.ContainsKey($source)) { $volumes[$source] } else { $source }
            $writableData = ($serviceName -eq 'lyrveil-backend' -and $volumeName -eq $script:DataVolume -and $target -eq '/data')
            if ($allowed -notcontains $volumeName) { $failures.Add("${serviceName} mounts the volume '$volumeName' at $target.") }
            elseif ((Get-JsonProperty $mount 'read_only') -ne $true -and -not $writableData) {
                $failures.Add("${serviceName} mounts '$volumeName' writable at $target.")
            }
        }
        if ($script:StackEnvironment.ContainsKey($serviceName)) {
            if (@(Get-JsonProperty $service 'env_file').Where({ $null -ne $_ }).Count -gt 0) { $failures.Add("${serviceName} reads an env_file.") }
            $environment = Get-JsonProperty $service 'environment'
            $keys = @()
            if ($null -ne $environment) { $keys = @($environment.PSObject.Properties | ForEach-Object { $_.Name }) }
            foreach ($key in $keys) {
                if ($script:StackEnvironment[$serviceName] -notcontains $key) { $failures.Add("${serviceName} sets $key, which the override does not.") }
            }
            if ([string](Get-JsonProperty $environment 'LYRVEIL_CONTROL_TOKEN') -cne $script:ControlToken) {
                $failures.Add("${serviceName} does not carry the level's own control token.")
            }
        }
    }
    return [string[]]$failures.ToArray()
}

function Get-GpuTestLeftover {
    <#
    Returns a line for every container that carries the test project's label and every volume whose name starts with
    lyrveil-gpu-test, and none when both are gone. Returns a line saying so when Docker cannot be asked, so a failed
    check never reads as a clean one. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    $leftovers = New-Object System.Collections.Generic.List[string]
    $containers = Invoke-GpuNative -Command 'docker' -Capture -Argument @(
        'ps', '--all', '--quiet', '--filter', "label=com.docker.compose.project=$script:ProjectName")
    if ($containers.Status -ne 0) { return @('docker ps failed, so the removal of the containers is unverified.') }
    foreach ($id in $containers.Output) { if ($id.Trim()) { $leftovers.Add("container $($id.Trim())") } }
    $volumes = Invoke-GpuNative -Command 'docker' -Capture -Argument @('volume', 'ls', '--quiet', '--filter', "name=$script:ProjectName")
    if ($volumes.Status -ne 0) { return @('docker volume ls failed, so the removal of the volumes is unverified.') }
    foreach ($volume in $volumes.Output) {
        if ($volume.Trim() -like "$script:ProjectName*") { $leftovers.Add("volume $($volume.Trim())") }
    }
    return [string[]]$leftovers.ToArray()
}

function Remove-GpuTestProject {
    <#
    Removes the test project's containers and networks, orphans included, then its data volume, which is external and
    so survives "down --volumes". Images stay for the next build's cache. Returns $true only when both commands
    succeeded and no container or volume of the project is left. Only lyrveil-gpu-test and lyrveil-gpu-test-data are
    named, so a running Lyrveil, lyrveil-data and lyrveil-models are never touched. Not exported.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not $PSCmdlet.ShouldProcess($script:ProjectName, 'Remove the test project and its data volume')) { return $true }
    $down = Invoke-GpuCompose -Root $Root -Argument @('down', '--volumes', '--remove-orphans', '--timeout', '30')
    $volume = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument @('volume', 'rm', '--force', $script:DataVolume)
    $leftovers = @(Get-GpuTestLeftover)
    foreach ($leftover in $leftovers) { Write-Host "  Left behind: $leftover" }
    return ($down.Status -eq 0 -and $volume.Status -eq 0 -and $leftovers.Length -eq 0)
}

function Initialize-GpuTestData {
    <#
    Creates the test data volume and writes runtime/llm-launch.env for TextProfile and runtime/image-launch.env with
    the default RESERVE_VRAM_GB into it, mode 0644 under a 0755 folder, from a one-off container of the built text
    runtime image with no network. The runtimes mount that folder read-only. Returns $true when both steps succeeded.
    Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] $TextProfile
    )

    $create = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument @(
        'volume', 'create', '--label', 'com.lyrveil.test=gpu', $script:DataVolume)
    if ($create.Status -ne 0) { return $false }
    return (Write-GpuTestLaunchFile -Root $Root -Line (Get-GpuLaunchLine -TextProfile $TextProfile) -WithImage)
}

function Write-GpuTestLaunchFile {
    <#
    Replaces runtime/llm-launch.env in the test data volume with Line, and with WithImage also writes
    runtime/image-launch.env with the default RESERVE_VRAM_GB, mode 0644 under a 0755 folder, from a one-off container
    of the built text runtime image with no network. Returns $true when the container succeeded, and $false, writing
    nothing, when a line is not KEY=VALUE with a value of letters, digits, ".", "_" and "-" only. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string[]]$Line,
        [switch]$WithImage
    )

    # The lines reach printf as separate arguments, so none may hold a space or a shell character. The script has no
    # double quote, which Windows PowerShell 5.1 would pass unescaped.
    foreach ($entry in $Line) {
        if ($entry -cnotmatch '^[A-Z_]+=[A-Za-z0-9._-]+$') {
            Write-Host "  The launch line '$entry' is not KEY=VALUE with a plain value; nothing was written."
            return $false
        }
    }
    $shellScript = "set -eu; umask 022; mkdir -p /data/runtime; printf '%s\n' `$@ > /data/runtime/llm-launch.env"
    if ($WithImage) { $shellScript += "; printf '%s\n' RESERVE_VRAM_GB=1.5 > /data/runtime/image-launch.env" }
    $write = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument (@(
            'run', '--rm', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--volume', "$($script:DataVolume):/data",
            '--entrypoint', '/bin/sh', 'lyrveil/llm:gpu-test', '-c', $shellScript, 'sh') + $Line)
    return ($write.Status -eq 0)
}

function Set-GpuTestTextLaunch {
    <#
    Replaces the text runtime's launch file in the test data volume with the lines of Get-GpuLaunchLine for
    TextProfile at Level, and returns $true when it was written. The text runtime reads it at its next start, so call
    it while lyrveil-llm is removed.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] $TextProfile,
        [ValidateSet('Custom', 'A')] [string]$Level = 'A'
    )

    if (-not $PSCmdlet.ShouldProcess($TextProfile.id, "Write the level $Level launch file")) { return $true }
    $checked = Assert-GpuTestProfile -TextProfile $TextProfile
    return (Write-GpuTestLaunchFile -Root $Root -Line (Get-GpuLaunchLine -TextProfile $checked -Level $Level))
}

function Set-GpuTestLaunchLine {
    <#
    Replaces the text runtime's launch file in the test data volume with Line, the lines of a tuning trial, and returns
    $true when it was written, or $false, writing nothing, when a line is not KEY=VALUE with a plain value. The
    runtime's entrypoint still checks every key and value at its next start. Call it while lyrveil-llm is removed.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string[]]$Line
    )

    if (-not $PSCmdlet.ShouldProcess('runtime/llm-launch.env', 'Write the launch lines of a trial')) { return $true }
    return (Write-GpuTestLaunchFile -Root $Root -Line $Line)
}

function Get-RunningInstallationRuntime {
    <#
    Returns @{ Known; Names }: Known is $false when Docker cannot be asked, and Names lists the installation's runtime
    and orchestrator containers that are running, empty when none is. Not exported.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    $result = Invoke-GpuNative -Command 'docker' -Capture -Argument @('ps', '--format', '{{.Names}}')
    $names = [string[]]@($result.Output | ForEach-Object { $_.Trim() } | Where-Object { $script:InstallationRuntimes -contains $_ })
    return @{ Known = ($result.Status -eq 0); Names = $names }
}

function Start-GpuTestRuntime {
    <#
    Starts Service of the test project from its built image, without dependencies, and waits up to TimeoutSecond, ten
    minutes by default, for its health check. Returns the exit status of "up --wait": 0 when the runtime is healthy. On
    failure it prints the last 60 log lines, which hold no prompt because the runtimes run quiet.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service,
        [ValidateRange(10, 600)] [int]$TimeoutSecond = 600
    )

    if (-not $PSCmdlet.ShouldProcess($Service, 'Start the test runtime')) { return 0 }
    $up = Invoke-GpuCompose -Root $Root -Argument @('up', '--detach', '--wait', '--wait-timeout', "$TimeoutSecond", '--no-deps', '--no-build', $Service)
    if ($up.Status -ne 0) {
        Write-Host "  $Service did not become healthy (exit $($up.Status)). Its last log lines:"
        $null = Invoke-GpuCompose -Root $Root -Argument @('logs', '--no-color', '--tail', '60', $Service)
    }
    return [int]$up.Status
}

function Get-GpuTestServiceLog {
    <#
    Returns the last Tail lines of the log of Service in the test project, across every start of its container, as
    "docker compose logs" gives them, or none when it has no container. For the record of a failed start: the runtimes
    run quiet and the tests send only fixed test prompts.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service,
        [ValidateRange(1, 500)] [int]$Tail = 80
    )

    $logs = Invoke-GpuCompose -Root $Root -Capture -Argument @('logs', '--no-color', '--timestamps', '--tail', "$Tail", $Service)
    return [string[]]@($logs.Output)
}

function Remove-GpuTestRuntime {
    <#
    Stops and removes the container of Service in the test project and returns the exit status of "rm", 0 also when
    there was no container.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service
    )

    if (-not $PSCmdlet.ShouldProcess($Service, 'Remove the test runtime')) { return 0 }
    return [int](Invoke-GpuCompose -Root $Root -Argument @('rm', '--stop', '--force', $Service)).Status
}

function Get-GpuTestContainerFact {
    <#
    Returns the value of the Go template Template, which must render JSON (as "{{json .Mounts}}" does), for the
    container of Service in the test project, parsed. Returns $null when the service has no container or Docker fails.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service,
        [Parameter(Mandatory = $true)] [string]$Template
    )

    $id = @((Invoke-GpuCompose -Root $Root -Argument @('ps', '--all', '--quiet', $Service) -Capture).Output | Where-Object { $_.Trim() })
    if ($id.Length -eq 0) { return $null }
    $inspect = Invoke-GpuNative -Command 'docker' -Capture -Argument @('inspect', '--format', $Template, $id[0].Trim())
    if ($inspect.Status -ne 0) { return $null }
    # Windows PowerShell 5.1 writes a parsed JSON array as one object; returning the variable enumerates it.
    $parsed = ($inspect.Output -join "`n") | ConvertFrom-Json
    return $parsed
}

function Invoke-GpuRuntimeRequest {
    <#
    Sends GET Path to the HTTP API of Service from inside its container, because the runtime network is internal and
    publishes no port: curl for lyrveil-llm on port 5001, Python for lyrveil-image on port 8188. Returns @{ Status;
    Body }, where Body is the parsed JSON answer, or $null when the request failed or the answer is not JSON. Path must
    hold no quote or space.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [ValidateSet('lyrveil-llm', 'lyrveil-image')] [string]$Service,
        [Parameter(Mandatory = $true)] [ValidatePattern('^/[A-Za-z0-9/_.-]*$')] [string]$Path
    )

    $request = if ($Service -eq 'lyrveil-llm') {
        @('curl', '-fsS', '--max-time', '20', "http://127.0.0.1:5001$Path")
    }
    else {
        @('python', '-c', "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8188$Path', timeout=20).read().decode())")
    }
    $result = Invoke-GpuCompose -Root $Root -Capture -Argument (@('exec', '-T', $Service) + $request)
    $body = $null
    if ($result.Status -eq 0) {
        try { $body = ($result.Output -join "`n") | ConvertFrom-Json }
        catch { $body = $null }
    }
    return @{ Status = [int]$result.Status; Body = $body }
}

function ConvertTo-GpuRequestJson {
    <#
    Returns Body as compact JSON with every character above U+007F written as a \u escape, so the text is ASCII and
    survives the standard input of a native command under Windows PowerShell 5.1 byte for byte.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [hashtable]$Body)

    $json = ConvertTo-Json -InputObject $Body -Depth 8 -Compress
    return [regex]::Replace($json, '[^\x00-\x7F]', { param($match) '\u{0:x4}' -f [int][char]$match.Value })
}

function Get-GpuTextRequestArgument {
    # Returns the "exec" arguments that POST the JSON of standard input to Path of lyrveil-llm with curl, waiting at
    # most TimeoutSecond. With Stream, curl writes every line as it arrives and does not fail on an HTTP error, so the
    # caller sees the events and the body. With Timed, curl adds a last line "LYRVEIL_SECONDS=<time_total>", its own
    # time from the start of the request to the end of the answer, measured inside the container. Not exported.
    param([string]$Path, [int]$TimeoutSecond, [switch]$Stream, [switch]$Timed)

    # @() keeps a one-element answer of "if" an array, so += appends instead of joining strings.
    $mode = @(if ($Stream) { '-sS', '-N' } else { '-fsS' })
    if ($Timed) { $mode += @('-w', '\nLYRVEIL_SECONDS=%{time_total}\n') }
    return [string[]](@('exec', '-T', 'lyrveil-llm', 'curl') + $mode + @('--max-time', "$TimeoutSecond",
            '-H', 'Content-Type: application/json', '--data-binary', '@-', "http://127.0.0.1:5001$Path"))
}

function Invoke-GpuTextTimed {
    <#
    Sends POST Path with Body as JSON to the KoboldCpp API of lyrveil-llm, as Invoke-GpuTextPost does, and returns
    @{ Status; Body; Seconds }: Status is curl's exit status (0 for a 2xx answer), Body the parsed JSON answer or $null,
    and Seconds curl's time_total inside the container, from sending the request to the end of the answer, without the
    start of "docker compose exec"; Seconds is 0 when curl reported no time.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [ValidatePattern('^/[A-Za-z0-9/_.-]*$')] [string]$Path,
        [Parameter(Mandatory = $true)] [hashtable]$Body,
        [int]$TimeoutSecond = 180
    )

    $result = Invoke-GpuCompose -Root $Root -Capture -InputText (ConvertTo-GpuRequestJson -Body $Body) `
        -Argument (Get-GpuTextRequestArgument -Path $Path -TimeoutSecond $TimeoutSecond -Timed)
    $seconds = 0.0
    $bodyLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $result.Output) {
        if ($line -match '^LYRVEIL_SECONDS=([0-9]+(?:\.[0-9]+)?)\s*$') {
            $seconds = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
        }
        else { $bodyLines.Add($line) }
    }
    $parsed = $null
    if ($result.Status -eq 0) {
        try { $parsed = ($bodyLines.ToArray() -join "`n") | ConvertFrom-Json }
        catch { $parsed = $null }
    }
    return @{ Status = [int]$result.Status; Body = $parsed; Seconds = $seconds }
}

function Initialize-GpuBackendProbe {
    <#
    Builds the test stage of backend/Dockerfile with the tag lyrveil/backend-test:gpu-test, its own, so the image the
    unit level uses is never replaced, and returns $true when the build succeeded. Docker's cache makes a build without
    changes take seconds. Invoke-GpuBackendProbe runs the backend's code in it.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $build = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument @(
        'build', '--quiet', '--file', 'backend/Dockerfile', '--target', 'test', '--tag', $script:BackendProbeImage, '.')
    return ($build.Status -eq 0)
}

function Invoke-GpuBackendProbe {
    <#
    Runs Action of the probe backend/tests/gpu_probe/<Probe>.py with Body as its JSON request, in a new container of
    the image of Initialize-GpuBackendProbe, and returns @{ Status; Body }: the container's exit status and the parsed
    JSON line it printed, or $null. structured_task (T-GPU-05) takes "prompt" and "check"; image_presets (T-GPU-06,
    T-IMG-14) takes "workflow" and "rule". The container has no network, a read-only root and no capability, carries
    the test project's label, so the level's leftover check would see it, and is removed when it exits.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [ValidateSet('structured_task', 'image_presets')] [string]$Probe = 'structured_task',
        [Parameter(Mandatory = $true)] [ValidateSet('prompt', 'check', 'workflow', 'rule')] [string]$Action,
        [Parameter(Mandatory = $true)] [hashtable]$Body
    )

    $run = Invoke-GpuNative -Directory $Root -Command 'docker' -Capture -InputText (ConvertTo-GpuRequestJson -Body $Body) -Argument @(
        'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--label', "com.docker.compose.project=$script:ProjectName", $script:BackendProbeImage,
        'python', "tests/gpu_probe/$Probe.py", $Action)
    $parsed = $null
    $last = @($run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($run.Status -eq 0 -and $last.Length -eq 1) {
        try { $parsed = $last[0] | ConvertFrom-Json }
        catch { $parsed = $null }
    }
    return @{ Status = [int]$run.Status; Body = $parsed }
}

# The client of Invoke-GpuImageGraph, run by the Python of lyrveil-image from its standard input. It submits the
# workflow to ComfyUI, polls its history every half second, reads the PNG header of the first saved image through
# /view, and prints one JSON line. execution_seconds comes from ComfyUI's own execution_start and execution_success
# timestamps (milliseconds), so it holds no polling delay. __WORKFLOW__ and __TIMEOUT__ are replaced.
$script:ImageJobClient = @'
import json, struct, time, urllib.parse, urllib.request as request
workflow = json.loads(r'''__WORKFLOW__''')
base = 'http://127.0.0.1:8188'
start = time.time()
body = json.dumps({'prompt': workflow}).encode()
answer = request.urlopen(request.Request(base + '/prompt', data=body, headers={'Content-Type': 'application/json'}), timeout=30)
prompt_id = json.load(answer)['prompt_id']
status = 'timeout'
entry = None
while time.time() - start < __TIMEOUT__:
    entry = json.load(request.urlopen(base + '/history/' + prompt_id, timeout=10)).get(prompt_id)
    if entry:
        state = entry.get('status') or {}
        status = state.get('status_str') or 'unknown'
        if state.get('completed') or status == 'error':
            break
    time.sleep(0.5)
seconds = round(time.time() - start, 2)
stamps = {}
images = []
if entry:
    for message in (entry.get('status') or {}).get('messages') or []:
        if isinstance(message, list) and len(message) == 2 and isinstance(message[1], dict) and 'timestamp' in message[1]:
            stamps[message[0]] = message[1]['timestamp']
    for output in (entry.get('outputs') or {}).values():
        images.extend(output.get('images') or [])
execution = None
if 'execution_start' in stamps and 'execution_success' in stamps:
    execution = round((stamps['execution_success'] - stamps['execution_start']) / 1000.0, 2)
width = height = 0
if images:
    first = images[0]
    query = urllib.parse.urlencode({'filename': first.get('filename', ''), 'subfolder': first.get('subfolder', ''), 'type': first.get('type', 'output')})
    head = request.urlopen(base + '/view?' + query, timeout=30).read(24)
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        width, height = struct.unpack('>II', head[16:24])
print(json.dumps({'prompt_id': prompt_id, 'status': status, 'seconds': seconds, 'execution_seconds': execution, 'images': len(images), 'width': width, 'height': height}))
'@

function Get-GpuImageWorkflow {
    <#
    Returns the ComfyUI API workflow of one text-to-image job: CheckpointLoaderSimple with Checkpoint, a positive and a
    negative CLIPTextEncode, an empty Width x Height latent, KSampler (euler, normal, cfg 5, Steps steps, Seed),
    VAEDecode and SaveImage, which writes into the runtime's tmpfs.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [ValidatePattern('^[A-Za-z0-9._-]{1,128}\.safetensors$')] [string]$Checkpoint,
        [int]$Width = 768,
        [int]$Height = 768,
        [int]$Steps = 8,
        [long]$Seed = 1
    )

    return @{
        '1' = @{ class_type = 'CheckpointLoaderSimple'; inputs = @{ ckpt_name = $Checkpoint } }
        '2' = @{ class_type = 'CLIPTextEncode'; inputs = @{ text = 'a quiet bookshop at night, warm light, detailed illustration'; clip = @('1', 1) } }
        '3' = @{ class_type = 'CLIPTextEncode'; inputs = @{ text = 'lowres, blurry'; clip = @('1', 1) } }
        '4' = @{ class_type = 'EmptyLatentImage'; inputs = @{ width = $Width; height = $Height; batch_size = 1 } }
        '5' = @{ class_type = 'KSampler'; inputs = @{
                model = @('1', 0); positive = @('2', 0); negative = @('3', 0); latent_image = @('4', 0)
                seed = $Seed; steps = $Steps; cfg = 5.0; sampler_name = 'euler'; scheduler = 'normal'; denoise = 1.0
            }
        }
        '6' = @{ class_type = 'VAEDecode'; inputs = @{ samples = @('5', 0); vae = @('1', 2) } }
        '7' = @{ class_type = 'SaveImage'; inputs = @{ images = @('6', 0); filename_prefix = 'lyrveil-gpu-test' } }
    }
}

function Invoke-GpuImageJob {
    <#
    Runs the workflow of Get-GpuImageWorkflow for Checkpoint on the running lyrveil-image and waits up to
    TimeoutSecond for ComfyUI to finish it. Returns what Invoke-GpuImageGraph returns.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Checkpoint,
        [int]$Width = 768,
        [int]$Height = 768,
        [int]$Steps = 8,
        [ValidateRange(10, 600)] [int]$TimeoutSecond = 240
    )

    $workflow = ConvertTo-GpuRequestJson -Body (Get-GpuImageWorkflow -Checkpoint $Checkpoint -Width $Width -Height $Height -Steps $Steps)
    return (Invoke-GpuImageGraph -Root $Root -WorkflowJson $workflow -TimeoutSecond $TimeoutSecond)
}

function Invoke-GpuImageGraph {
    <#
    Runs WorkflowJson, a ComfyUI API workflow as ASCII JSON text (such as a graph of the image_presets probe), on the
    running lyrveil-image and waits up to TimeoutSecond for ComfyUI to finish it. Returns @{ Status; Result }: Status
    is the exit status of the Python client in the container, 0 when it ran to the end; Result the parsed last line, or
    $null, with prompt_id, status (ComfyUI's status_str, such as "success" or "error", or "timeout"), seconds from the
    submission to the end as the client saw it (polled every half second), execution_seconds from ComfyUI's own
    timestamps ($null when it reported none), images (the number saved) and width and height of the first saved PNG
    (0 when none was read). The image stays in the runtime's tmpfs and is never copied out.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [ValidatePattern('^[\x20-\x7E]+$')] [string]$WorkflowJson,
        [ValidateRange(10, 600)] [int]$TimeoutSecond = 240
    )

    # The text goes into a raw triple-quoted Python string, which three quotes in a row would end.
    if ($WorkflowJson.Contains("'''")) { throw 'The workflow JSON holds three single quotes in a row.' }
    $client = $script:ImageJobClient.Replace('__WORKFLOW__', $WorkflowJson).Replace('__TIMEOUT__', "$TimeoutSecond")
    $run = Invoke-GpuCompose -Root $Root -Capture -InputText $client -Argument @('exec', '-T', 'lyrveil-image', 'python', '-')
    $parsed = $null
    $last = @($run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($run.Status -eq 0 -and $last.Length -eq 1) {
        try { $parsed = $last[0] | ConvertFrom-Json }
        catch { $parsed = $null }
    }
    return @{ Status = [int]$run.Status; Result = $parsed }
}

function Invoke-GpuTextPost {
    <#
    Sends POST Path with Body as JSON to the KoboldCpp API of lyrveil-llm from inside its container and returns
    @{ Status; Body }, where Body is the parsed JSON answer, or $null when the request failed or the answer is not JSON.
    Status is curl's exit status: 0 for a 2xx answer, 22 for an HTTP error.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [ValidatePattern('^/[A-Za-z0-9/_.-]*$')] [string]$Path,
        [Parameter(Mandatory = $true)] [hashtable]$Body,
        [int]$TimeoutSecond = 120
    )

    $result = Invoke-GpuCompose -Root $Root -Capture -InputText (ConvertTo-GpuRequestJson -Body $Body) `
        -Argument (Get-GpuTextRequestArgument -Path $Path -TimeoutSecond $TimeoutSecond)
    $parsed = $null
    if ($result.Status -eq 0) {
        try { $parsed = ($result.Output -join "`n") | ConvertFrom-Json }
        catch { $parsed = $null }
    }
    return @{ Status = [int]$result.Status; Body = $parsed }
}

function Invoke-GpuTextStream {
    <#
    Sends Body to POST /api/extra/generate/stream of lyrveil-llm and reads the server-sent events as they arrive.
    Returns @{ Status; Events; Abort; AbortSecond; Lines }: Status is curl's exit status; Events lists every "data:"
    event in order as @{ Second; Token; FinishReason }, Second being the time since the request started, as this
    process saw the line arrive; Lines counts every line read. With AbortAfter above 0, once that many token events
    have arrived it sends POST /api/extra/abort with Body.genkey and keeps reading; Abort then holds that answer as
    Invoke-GpuTextPost returns it, and AbortSecond the time it was sent. Abort is $null when no abort was sent.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [hashtable]$Body,
        [int]$AbortAfter = 0,
        [int]$TimeoutSecond = 180
    )

    $arguments = Get-GpuComposeArgument -Argument (Get-GpuTextRequestArgument -Path '/api/extra/generate/stream' -TimeoutSecond $TimeoutSecond -Stream)
    $json = ConvertTo-GpuRequestJson -Body $Body
    Add-GpuCommandLog -Line (Format-GpuCommandLine -Command 'docker' -Argument $arguments -InputText $json)
    $events = New-Object System.Collections.Generic.List[object]
    $state = @{ Tokens = 0; Abort = $null; AbortSecond = 0.0; Lines = 0 }
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    # KoboldCpp may send the tokens as UTF-8 text; decode them as such where the console allows it.
    $previousEncoding = $null
    try {
        $previousEncoding = [Console]::OutputEncoding
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    }
    catch { $previousEncoding = $null }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $Root
    try {
        # ForEach-Object runs each line as it arrives, not after the command ends, so the times are arrival times.
        $json | & docker @arguments | ForEach-Object {
            $arrival = $clock.Elapsed.TotalSeconds
            $state.Lines++
            $line = [string]$_
            if ($line -notmatch '^data:\s*(\{.*\})\s*$') { return }
            try { $data = $Matches[1] | ConvertFrom-Json }
            catch { return }
            $reason = Get-JsonProperty $data 'finish_reason'
            $events.Add([pscustomobject]@{
                    Second       = $arrival
                    Token        = [string](Get-JsonProperty $data 'token')
                    FinishReason = $reason
                })
            if ($null -eq $reason) { $state.Tokens++ }
            if ($AbortAfter -gt 0 -and $null -eq $state.Abort -and $state.Tokens -ge $AbortAfter) {
                $state.AbortSecond = $clock.Elapsed.TotalSeconds
                $state.Abort = Invoke-GpuTextPost -Root $Root -Path '/api/extra/abort' -Body @{ genkey = $Body.genkey } -TimeoutSecond 20
            }
        }
        $status = [int]$LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $previous
        if ($null -ne $previousEncoding) {
            try { [Console]::OutputEncoding = $previousEncoding }
            catch { Write-Verbose 'The console encoding could not be restored.' }
        }
    }
    return @{ Status = $status; Events = $events.ToArray(); Abort = $state.Abort; AbortSecond = $state.AbortSecond; Lines = $state.Lines }
}

function Get-GpuTestContainerMemory {
    <#
    Returns the memory that "docker stats" reports in use by the container of Service in the test project, in MiB,
    or -1 when the service has no container or Docker fails. It is the container's cgroup usage, page cache included.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service
    )

    $id = @((Invoke-GpuCompose -Root $Root -Argument @('ps', '--quiet', $Service) -Capture).Output | Where-Object { $_.Trim() })
    if ($id.Length -eq 0) { return -1.0 }
    $stats = Invoke-GpuNative -Command 'docker' -Capture -Argument @('stats', '--no-stream', '--format', '{{.MemUsage}}', $id[0].Trim())
    if ($stats.Status -ne 0) { return -1.0 }
    return (ConvertFrom-DockerMemoryText -Text ($stats.Output -join ' '))
}

function ConvertFrom-DockerMemoryText {
    <#
    Returns the first amount of a "docker stats" memory text such as "4.5GiB / 23.47GiB" in MiB, or -1 when the text
    does not start with an amount and a unit of B, kB, KiB, MB, MiB, GB or GiB.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param([Parameter(Mandatory = $true)] [AllowEmptyString()] [string]$Text)

    if ($Text -notmatch '^\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KiB|MB|MiB|GB|GiB)\b') { return -1.0 }
    $amount = [double]::Parse($Matches[1], [System.Globalization.CultureInfo]::InvariantCulture)
    $factor = @{ B = 1.0 / 1MB; kB = 1000.0 / 1MB; KiB = 1.0 / 1KB; MB = 1000000.0 / 1MB; MiB = 1.0; GB = 1000000000.0 / 1MB; GiB = 1024.0 }
    return [math]::Round($amount * $factor[$Matches[2]], 1)
}

function Start-GpuMemorySampler {
    <#
    Starts a background job that reads the used memory of GPU 0 through NVML every 250 ms, and returns the job once its
    first sample has arrived, or after 20 s. Pass the job to Stop-GpuMemorySampler, also on failure.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()

    if (-not $PSCmdlet.ShouldProcess('GPU 0', 'Sample the used memory')) { return $null }
    Add-GpuCommandLog -Line 'nvidia-smi --id=0 --query-gpu=memory.used --format=csv,noheader,nounits, every 250 ms in a background job'
    $job = Start-Job -ScriptBlock {
        while ($true) {
            $line = @(& nvidia-smi '--id=0' '--query-gpu=memory.used' '--format=csv,noheader,nounits' 2>$null) | Select-Object -First 1
            if ("$line" -match '^\s*(\d+)\s*$') { [int]$Matches[1] }
            Start-Sleep -Milliseconds 250
        }
    }
    $deadline = (Get-Date).AddSeconds(20)
    while (-not $job.HasMoreData -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    return $job
}

function Stop-GpuMemorySampler {
    <#
    Stops and removes the job of Start-GpuMemorySampler and returns @{ PeakMiB; Samples }: the highest used memory it
    read and the number of samples. PeakMiB is 0 when it read none.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([hashtable])]
    param($Job)

    if ($null -eq $Job) { return @{ PeakMiB = 0; Samples = 0 } }
    if (-not $PSCmdlet.ShouldProcess('GPU 0', 'Stop sampling the used memory')) { return @{ PeakMiB = 0; Samples = 0 } }
    Stop-Job -Job $Job
    $values = @(Receive-Job -Job $Job -ErrorAction SilentlyContinue | ForEach-Object { [int]$_ })
    Remove-Job -Job $Job -Force
    $peak = 0
    foreach ($value in $values) { if ($value -gt $peak) { $peak = $value } }
    return @{ PeakMiB = $peak; Samples = $values.Length }
}

function Initialize-GpuStackImage {
    <#
    Builds the images of the orchestrator and the backend of the test project, both tagged ":gpu-test": the test-only
    gpu-test-runtime stage of orchestrator/Dockerfile and the backend's runtime stage. Returns $true when the build
    succeeded. Only the mode switch tests start these services, so the level itself does not build them; Docker's
    cache makes a build without changes take seconds.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $build = Invoke-GpuCompose -Root $Root -Argument (@('build') + $script:StackServices)
    return ($build.Status -eq 0)
}

function New-GpuTestRuntimeContainer {
    <#
    Creates the containers of lyrveil-llm and lyrveil-image in the test project without starting them, so the test
    orchestrator can start and stop them as an installation's orchestrator does its own. Returns the exit status of
    "up --no-start": 0 when both exist. Nothing starts, so no GPU memory is used.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not $PSCmdlet.ShouldProcess(($script:Services -join ', '), 'Create the runtime containers')) { return 0 }
    return [int](Invoke-GpuCompose -Root $Root -Argument (@('up', '--no-start', '--no-deps', '--no-build') + $script:Services)).Status
}

function Set-GpuTestDataOwner {
    <#
    Makes UID and GID 10001, the backend's user, own the root of the test data volume, as setup.cmd does for an
    installation's volumes, from a one-off container of the built text runtime image with no network and only
    CAP_CHOWN. runtime/ and its launch files keep their owner, and the runtimes still mount them read-only. Returns
    $true when the container succeeded.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if (-not $PSCmdlet.ShouldProcess($script:DataVolume, 'Give the volume root to 10001:10001')) { return $true }
    $change = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument @(
        'run', '--rm', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
        '--security-opt', 'no-new-privileges:true', '--volume', "$($script:DataVolume):/data",
        '--entrypoint', 'chown', 'lyrveil/llm:gpu-test', '10001:10001', '/data')
    return ($change.Status -eq 0)
}

# The client of Invoke-GpuServiceApi, run by the Python of the service's container from its standard input. It sends
# one request and prints one ASCII JSON line with the HTTP status and the parsed answer. __AUTH__, __URL__, __METHOD__,
# __TIMEOUT__ and __BODY__ are replaced, the body last.
$script:ServiceApiClient = @'
import json, os, urllib.error, urllib.request as request
headers = {'Content-Type': 'application/json'}
if __AUTH__:
    headers['Authorization'] = 'Bearer ' + os.environ['LYRVEIL_CONTROL_TOKEN']
body = r'''__BODY__'''
call = request.Request('__URL__', data=body.encode() if body else None, method='__METHOD__', headers=headers)
try:
    answer = request.urlopen(call, timeout=__TIMEOUT__)
    status, text = answer.status, answer.read().decode()
except urllib.error.HTTPError as error:
    status, text = error.code, error.read().decode()
try:
    parsed = json.loads(text) if text else None
except ValueError:
    parsed = None
print(json.dumps({'http': status, 'body': parsed}))
'@

function Invoke-GpuServiceApi {
    <#
    Sends Method Path, with Body as JSON when given, to the HTTP API of Service from inside its container, because the
    project's networks are internal and publish no port: the control API of lyrveil-orchestrator on port 8100, with
    the bearer token that container holds, or the REST API of lyrveil-backend on port 8000, whose loopback Host the
    backend accepts. Returns @{ Status; Http; Body }: Status is the exit status of the client, 0 when an answer came;
    Http the HTTP status, 0 without an answer; Body the parsed JSON answer, or $null. Waits at most TimeoutSecond for
    the answer, so a long poll needs more than its wait.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [ValidateSet('lyrveil-orchestrator', 'lyrveil-backend')] [string]$Service,
        [ValidateSet('GET', 'POST', 'PUT', 'PATCH', 'DELETE')] [string]$Method = 'GET',
        [Parameter(Mandatory = $true)] [ValidatePattern('^/[A-Za-z0-9/_.?=&%-]*$')] [string]$Path,
        [hashtable]$Body = $null,
        [ValidateRange(5, 120)] [int]$TimeoutSecond = 60
    )

    $json = ''
    if ($null -ne $Body) { $json = ConvertTo-GpuRequestJson -Body $Body }
    # The body goes into a raw triple-quoted Python string, which three quotes in a row would end.
    if ($json.Contains("'''")) { throw 'The request body holds three single quotes in a row.' }
    $orchestrator = ($Service -eq 'lyrveil-orchestrator')
    $base = if ($orchestrator) { 'http://127.0.0.1:8100' } else { 'http://127.0.0.1:8000' }
    $auth = if ($orchestrator) { 'True' } else { 'False' }
    $client = $script:ServiceApiClient.Replace('__AUTH__', $auth).Replace('__URL__', "$base$Path").Replace('__METHOD__', $Method)
    $client = $client.Replace('__TIMEOUT__', "$TimeoutSecond").Replace('__BODY__', $json)
    $run = Invoke-GpuCompose -Root $Root -Capture -InputText $client -Argument @('exec', '-T', $Service, 'python', '-')
    $parsed = $null
    $last = @($run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($run.Status -eq 0 -and $last.Length -eq 1) {
        try { $parsed = $last[0] | ConvertFrom-Json }
        catch { $parsed = $null }
    }
    $http = [int](Get-JsonProperty $parsed 'http')
    return @{ Status = [int]$run.Status; Http = $http; Body = (Get-JsonProperty $parsed 'body') }
}

function Start-GpuVramLoad {
    <#
    Starts the one-off container lyrveil-gpu-test-vram-load from the built image runtime image: its PyTorch allocates
    GPU memory 256 MiB at a time until CUDA reports at most FreeMiB free, prints LYRVEIL_VRAM_HELD_MIB=<MiB> and holds
    the memory until the container is removed, as another program on the card would (T-GPU-09). It has no network, a
    read-only root and no capability, and carries the test project's label, so the leftover check sees it. Returns
    @{ Status; Id }: the exit status of "docker run" and the new container's id, '' when it failed. Only
    Stop-GpuVramLoad with that id ends it.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [ValidateRange(256, 65536)] [int]$FreeMiB = 3072
    )

    if (-not $PSCmdlet.ShouldProcess('GPU 0', 'Hold VRAM from another container')) { return @{ Status = 0; Id = '' } }
    # One line with single quotes only, since Windows PowerShell 5.1 passes embedded double quotes unescaped. iter()
    # with a sentinel allocates until the free memory reaches the limit.
    $code = ("import time, torch; free = lambda: torch.cuda.mem_get_info()[0] >> 20; " +
        "held = [torch.empty(268435456, dtype=torch.uint8, device='cuda') for _ in iter(lambda: free() > $FreeMiB, False)]; " +
        "print('LYRVEIL_VRAM_HELD_MIB=%d' % (256 * len(held)), flush=True); time.sleep(86400)")
    $run = Invoke-GpuNative -Directory $Root -Command 'docker' -Capture -Argument @(
        'run', '--detach', '--name', $script:VramLoadName, '--label', "com.docker.compose.project=$script:ProjectName",
        '--label', 'com.lyrveil.test=gpu', '--gpus', 'all', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--env', 'HOME=/tmp',
        '--env', 'TMPDIR=/tmp', '--entrypoint', 'python', 'lyrveil/image:gpu-test', '-c', $code)
    $id = @($run.Output | Where-Object { $_ -match '^[0-9a-f]{64}$' } | Select-Object -Last 1)
    return @{ Status = [int]$run.Status; Id = $(if ($id.Length -eq 1) { [string]$id[0] } else { '' }) }
}

function Get-GpuVramLoad {
    <#
    Returns what Docker reports of the container Id of Start-GpuVramLoad: @{ Known; Running; Pid; StartedAt;
    RestartCount; HeldMiB }, HeldMiB being the amount its log line reports, or 0 before that line. Known is $false,
    with the rest empty, when Docker does not answer for it.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] [ValidatePattern('^[0-9a-f]{12,64}$')] [string]$Id)

    $state = @{ Known = $false; Running = $false; Pid = 0; StartedAt = ''; RestartCount = -1; HeldMiB = 0 }
    $inspect = Invoke-GpuNative -Command 'docker' -Capture -Argument @('inspect', '--format', '{{json .State}} {{.RestartCount}}', $Id)
    $line = @($inspect.Output | Where-Object { $_.Trim() } | Select-Object -First 1)
    if ($inspect.Status -ne 0 -or $line.Length -ne 1 -or $line[0] -notmatch '^(\{.*\})\s+(\d+)\s*$') { return $state }
    $restarts = [int]$Matches[2]
    try { $document = $Matches[1] | ConvertFrom-Json }
    catch { return $state }
    $state.Known = $true
    $state.Running = ((Get-JsonProperty $document 'Running') -eq $true)
    $state.Pid = [int](Get-JsonProperty $document 'Pid')
    $state.StartedAt = [string](Get-JsonProperty $document 'StartedAt')
    $state.RestartCount = $restarts
    $logs = Invoke-GpuNative -Command 'docker' -Capture -Argument @('logs', '--tail', '20', $Id)
    foreach ($entry in $logs.Output) {
        if ($entry -match '^LYRVEIL_VRAM_HELD_MIB=(\d+)\s*$') { $state.HeldMiB = [int]$Matches[1] }
    }
    return $state
}

function Stop-GpuVramLoad {
    <#
    Removes the container Id of Start-GpuVramLoad, which frees its memory, and returns the exit status of "docker rm":
    0 when it is gone. Only that id is named, so no other program is ever stopped.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [ValidatePattern('^[0-9a-f]{12,64}$')] [string]$Id)

    if (-not $PSCmdlet.ShouldProcess($Id, 'Remove the VRAM load container')) { return 0 }
    return [int](Invoke-GpuNative -Command 'docker' -Argument @('rm', '--force', $Id)).Status
}

function Start-GpuTimelineSampler {
    <#
    Starts two background jobs and returns @{ Memory; Containers; Count } for Stop-GpuTimelineSampler: one reads the
    used memory of GPU 0 through NVML every 250 ms, the other the State.Running flag of each container of ContainerId
    with one "docker inspect" about every 300 ms. Every sample carries the host's clock in Unix milliseconds, taken
    after the reading, so the two series share one time line. Returns once both have a first sample, or after 20 s.
    Pass the result to Stop-GpuTimelineSampler, also on failure.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] [ValidatePattern('^[0-9a-f]{12,64}$')] [string[]]$ContainerId)

    if (-not $PSCmdlet.ShouldProcess('GPU 0 and the runtime containers', 'Sample memory and state')) { return $null }
    Add-GpuCommandLog -Line 'nvidia-smi --id=0 --query-gpu=memory.used --format=csv,noheader,nounits, every 250 ms in a background job, with the time'
    Add-GpuCommandLog -Line "docker inspect --format '{{.State.Running}}' $($ContainerId -join ' '), every 300 ms in a background job, with the time"
    $memory = Start-Job -ScriptBlock {
        while ($true) {
            $line = @(& nvidia-smi '--id=0' '--query-gpu=memory.used' '--format=csv,noheader,nounits' 2>$null) | Select-Object -First 1
            $at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
            if ("$line" -match '^\s*(\d+)\s*$') { "$at $($Matches[1])" }
            Start-Sleep -Milliseconds 250
        }
    }
    $containers = Start-Job -ScriptBlock {
        # An array argument reaches a native command as one argument per item, in order.
        $ids = [string[]]$using:ContainerId
        while ($true) {
            $flags = @(& docker inspect --format '{{.State.Running}}' $ids 2>$null | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
            $at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
            "$at $($flags -join ' ')"
            Start-Sleep -Milliseconds 300
        }
    }
    $deadline = (Get-Date).AddSeconds(20)
    while (-not ($memory.HasMoreData -and $containers.HasMoreData) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    return @{ Memory = $memory; Containers = $containers; Count = $ContainerId.Length }
}

function Stop-GpuTimelineSampler {
    <#
    Stops and removes the jobs of Start-GpuTimelineSampler and returns @{ Memory; Containers }, both in time order:
    Memory lists @{ At; UsedMiB } and Containers @{ At; Running }, Running holding one flag per container of the
    sampler's ContainerId, in that order. A container sample in which Docker did not report every container is left
    out. Both lists are empty for $null.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([hashtable])]
    param($Sampler)

    $result = @{ Memory = @(); Containers = @() }
    if ($null -eq $Sampler) { return $result }
    if (-not $PSCmdlet.ShouldProcess('GPU 0 and the runtime containers', 'Stop sampling')) { return $result }
    $memory = New-Object System.Collections.Generic.List[object]
    $containers = New-Object System.Collections.Generic.List[object]
    foreach ($job in @($Sampler.Memory, $Sampler.Containers)) { Stop-Job -Job $job }
    foreach ($line in @(Receive-Job -Job $Sampler.Memory -ErrorAction SilentlyContinue)) {
        if ("$line" -match '^(\d+) (\d+)$') { $memory.Add([pscustomobject]@{ At = [double]$Matches[1]; UsedMiB = [int]$Matches[2] }) }
    }
    foreach ($line in @(Receive-Job -Job $Sampler.Containers -ErrorAction SilentlyContinue)) {
        $parts = @("$line".Trim() -split '\s+')
        if ($parts.Length -ne $Sampler.Count + 1 -or $parts[0] -notmatch '^\d+$') { continue }
        $flags = @($parts | Select-Object -Skip 1)
        if (@($flags | Where-Object { $_ -ne 'true' -and $_ -ne 'false' }).Length -gt 0) { continue }
        $containers.Add([pscustomobject]@{ At = [double]$parts[0]; Running = [bool[]]@($flags | ForEach-Object { $_ -eq 'true' }) })
    }
    foreach ($job in @($Sampler.Memory, $Sampler.Containers)) { Remove-Job -Job $job -Force }
    $result.Memory = $memory.ToArray()
    $result.Containers = $containers.ToArray()
    return $result
}

function Invoke-GpuLevel {
    <#
    Runs the gpu level for the Pester files File (repository-relative) through Runner, a script block called with the
    root, the files and Data (the parameters of the test files, $null for none) that returns 0 when the tests ran and
    passed. In order: it returns 1 when File is empty; returns
    2, the skip status, with a message when nvidia-smi finds no GPU, before any Docker call; returns 1 when an
    installation's runtime or orchestrator is running or Docker cannot be asked, touching nothing. Otherwise it removes any earlier
    test project, refuses a resolved configuration that Test-GpuTestConfiguration rejects, builds the runtime images
    with ":gpu-test" tags, creates the test data volume with the launch files and runs the tests. Whatever happens
    after the first removal, also on failure or interruption, it removes the project and its data volume and returns
    1 if anything is left. Returns 0 only when the tests passed and nothing is left. TestCommand.psm1 calls it.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$File = @(),
        [Parameter(Mandatory = $true)] [scriptblock]$Runner,
        [hashtable]$Data = $null
    )

    $files = [string[]]@($File | Where-Object { $_ })
    if ($files.Length -eq 0) {
        Write-Host "Test level 'gpu' has no tests yet. Nothing ran, so it cannot pass."
        return 1
    }
    Write-Host 'GPU: reading GPU 0 through NVML with nvidia-smi'
    $card = Get-GpuSample
    if (-not $card.Available) {
        Write-Host "Test level 'gpu' skipped: $($card.Reason). It needs an NVIDIA GPU with its driver. No test ran, and a skip is not a pass."
        return $script:SkippedStatus
    }
    Write-Host ("GPU: {0}, {1} MiB, {2} MiB in use" -f $card.Name, $card.TotalMiB, $card.UsedMiB)

    $running = Get-RunningInstallationRuntime
    if (-not $running.Known) {
        Write-Host '  [FAIL] gpu: docker ps failed, so Docker is not available.'
        return 1
    }
    if ($running.Names.Length -gt 0) {
        Write-Host "  [FAIL] gpu: the container $($running.Names -join ' and ') of an installation is running. Stop Lyrveil first; this level never touches it."
        return 1
    }
    try { $textProfile = Get-GpuTestProfile -Root $Root }
    catch {
        Write-Host "  [FAIL] gpu: $($_.Exception.Message)"
        return 1
    }

    $status = 1
    try {
        Write-Host "GPU: clearing any earlier Compose project '$script:ProjectName' and the volume '$script:DataVolume'"
        $ready = Remove-GpuTestProject -Root $Root
        if (-not $ready) { Write-Host '  [FAIL] gpu: an earlier test project or its volume could not be removed' }
        if ($ready) {
            Write-Host 'GPU: checking the resolved configuration'
            $config = Invoke-GpuCompose -Root $Root -Capture -Argument (@('config', '--format', 'json') + $script:Services + $script:StackServices)
            $problems = @("docker compose config exited with status $($config.Status).")
            if ($config.Status -eq 0) { $problems = @(Test-GpuTestConfiguration -Text ($config.Output -join "`n")) }
            foreach ($problem in $problems) { Write-Host "         $problem" }
            $ready = ($problems.Length -eq 0)
            if (-not $ready) { Write-Host '  [FAIL] gpu: the resolved configuration could reach an installation; nothing was started' }
        }
        if ($ready) {
            Write-Host 'GPU: building the runtime images with the tag :gpu-test'
            $build = Invoke-GpuCompose -Root $Root -Argument (@('build') + $script:Services)
            $ready = ($build.Status -eq 0)
            if (-not $ready) { Write-Host "  [FAIL] gpu: the runtime images did not build (exit $($build.Status))" }
        }
        if ($ready) {
            Write-Host "GPU: creating the volume '$script:DataVolume' with the launch files for '$($textProfile.id)'"
            $ready = Initialize-GpuTestData -Root $Root -TextProfile $textProfile
            if (-not $ready) { Write-Host '  [FAIL] gpu: the test data volume could not be prepared' }
        }
        if ($ready) {
            $status = [int](& $Runner $Root $files $Data)
            $label = if ($status -eq 0) { 'ok  ' } else { 'FAIL' }
            Write-Host "  [$label] gpu: tests (exit $status)"
        }
    }
    finally {
        Write-Host "GPU: removing the Compose project '$script:ProjectName' and the volume '$script:DataVolume'"
        if (Remove-GpuTestProject -Root $Root) {
            Write-Host '  [ok  ] gpu: no container or volume of the test project is left'
        }
        else {
            Write-Host '  [FAIL] gpu: the test project was not fully removed'
            $status = 1
        }
    }
    return $status
}

Export-ModuleMember -Function Invoke-GpuLevel, Get-GpuTestSetting, Get-GpuSample, Wait-GpuMemoryBelow, Invoke-GpuCompose,
    Get-GpuTestProfile, Get-GpuTestTextProfile, Get-GpuLaunchLine, Set-GpuTestTextLaunch, Set-GpuTestLaunchLine, Test-GpuTestConfiguration,
    Start-GpuTestRuntime, Remove-GpuTestRuntime, Get-GpuTestContainerFact, Invoke-GpuRuntimeRequest,
    ConvertTo-GpuRequestJson, Invoke-GpuTextPost, Invoke-GpuTextStream, Invoke-GpuTextTimed, Initialize-GpuBackendProbe,
    Invoke-GpuBackendProbe, Get-GpuImageWorkflow,
    Invoke-GpuImageJob, Invoke-GpuImageGraph, Get-GpuTestContainerMemory, ConvertFrom-DockerMemoryText, Start-GpuMemorySampler,
    Stop-GpuMemorySampler, Open-GpuCommandLog, Close-GpuCommandLog, Initialize-GpuStackImage, New-GpuTestRuntimeContainer,
    Set-GpuTestDataOwner, Invoke-GpuServiceApi, Start-GpuVramLoad, Get-GpuVramLoad, Stop-GpuVramLoad, Start-GpuTimelineSampler,
    Stop-GpuTimelineSampler, Get-GpuTestServiceLog
