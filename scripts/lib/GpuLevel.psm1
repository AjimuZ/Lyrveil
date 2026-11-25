# Runs the gpu level against the real runtimes of compose.yaml in the project "lyrveil-gpu-test", and gives its tests NVML samples and the runtime APIs.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# The project, volume and files of the level. Every command names the project with -p, so no COMPOSE_PROJECT_NAME can
# point it at "lyrveil", and the only volumes it may mount are its own data volume and the models, read-only.
$script:ProjectName = 'lyrveil-gpu-test'
$script:DataVolume = 'lyrveil-gpu-test-data'
$script:ModelsVolume = 'lyrveil-models'
$script:ComposeFiles = @('-f', 'compose.yaml', '-f', 'tests/gpu/compose.gpu-test.yaml')
$script:Services = @('lyrveil-llm', 'lyrveil-image')

# The container names of an installation's runtimes. The level refuses to start while one of them runs.
$script:InstallationRuntimes = @('lyrveil-llm', 'lyrveil-image')

# What Invoke-GpuLevel returns when the machine has no usable GPU: not 0, so a skip never reads as a pass.
$script:SkippedStatus = 2

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
    Returns the names the level uses: Project, DataVolume, ModelsVolume and Services. The tests compare mounts and
    containers with them.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    return @{
        Project      = $script:ProjectName
        DataVolume   = $script:DataVolume
        ModelsVolume = $script:ModelsVolume
        Services     = [string[]]$script:Services
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
    hold exactly lyrveil-llm and lyrveil-image, with ":gpu-test" images, no container_name and no published port; every
    mount must be a tmpfs or a named volume, and the only volumes may be lyrveil-gpu-test-data and lyrveil-models,
    both mounted read-only.
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
    $expected = @($script:Services | Sort-Object)
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
            if ($type -ne 'volume') {
                $failures.Add("${serviceName} mounts a $type at $target.")
                continue
            }
            $source = [string](Get-JsonProperty $mount 'source')
            $volumeName = if ($volumes.ContainsKey($source)) { $volumes[$source] } else { $source }
            if ($allowed -notcontains $volumeName) { $failures.Add("${serviceName} mounts the volume '$volumeName' at $target.") }
            elseif ((Get-JsonProperty $mount 'read_only') -ne $true) {
                $failures.Add("${serviceName} mounts '$volumeName' writable at $target.")
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

function Get-RunningInstallationRuntime {
    <#
    Returns @{ Known; Names }: Known is $false when Docker cannot be asked, and Names lists the installation's runtime
    containers that are running, empty when none is. Not exported.
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
    # caller sees the events and the body. Not exported.
    param([string]$Path, [int]$TimeoutSecond, [switch]$Stream)

    $mode = if ($Stream) { @('-sS', '-N') } else { @('-fsS') }
    return [string[]](@('exec', '-T', 'lyrveil-llm', 'curl') + $mode + @('--max-time', "$TimeoutSecond",
            '-H', 'Content-Type: application/json', '--data-binary', '@-', "http://127.0.0.1:5001$Path"))
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

function Invoke-GpuLevel {
    <#
    Runs the gpu level for the Pester files File (repository-relative) through Runner, a script block called with the
    root and the files that returns 0 when the tests ran and passed. In order: it returns 1 when File is empty; returns
    2, the skip status, with a message when nvidia-smi finds no GPU, before any Docker call; returns 1 when an
    installation's runtime is running or Docker cannot be asked, touching nothing. Otherwise it removes any earlier
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
        [Parameter(Mandatory = $true)] [scriptblock]$Runner
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
        Write-Host "  [FAIL] gpu: the runtime $($running.Names -join ' and ') of an installation is running. Stop Lyrveil first; this level never touches it."
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
            $config = Invoke-GpuCompose -Root $Root -Capture -Argument (@('config', '--format', 'json') + $script:Services)
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
            $status = [int](& $Runner $Root $files)
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
    Get-GpuTestProfile, Get-GpuTestTextProfile, Get-GpuLaunchLine, Set-GpuTestTextLaunch, Test-GpuTestConfiguration,
    Start-GpuTestRuntime, Remove-GpuTestRuntime, Get-GpuTestContainerFact, Invoke-GpuRuntimeRequest,
    ConvertTo-GpuRequestJson, Invoke-GpuTextPost, Invoke-GpuTextStream, Get-GpuTestContainerMemory,
    ConvertFrom-DockerMemoryText, Start-GpuMemorySampler, Stop-GpuMemorySampler
