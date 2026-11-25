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
    and Output is empty. Never throws for a non-zero status; a command that is not on PATH returns status 127.
    Not exported.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [string]$Directory = '',
        [Parameter(Mandatory = $true)] [string]$Command,
        [string[]]$Argument = @(),
        [switch]$Capture
    )

    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return @{ Status = 127; Output = [string[]]@("The command '$Command' is not available.") }
    }
    # Windows PowerShell 5.1 may turn native standard error lines into error records, which 'Stop' would throw.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($Directory) { Push-Location -LiteralPath $Directory }
    try {
        $lines = @()
        if ($Capture) { $lines = @(& $Command @Argument | ForEach-Object { [string]$_ }) }
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
    "ai" profile, and returns @{ Status; Output } as Invoke-GpuNative does.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Argument = @(),
        [switch]$Capture
    )

    $arguments = @('compose', '-p', $script:ProjectName) + $script:ComposeFiles + @('--profile', 'ai') + @($Argument)
    return (Invoke-GpuNative -Directory $Root -Command 'docker' -Argument $arguments -Capture:$Capture)
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
    $chosen = $smallest[0]
    if ($chosen.id -notmatch '^[a-z0-9][a-z0-9.-]{1,47}$' -or $chosen.file -notmatch '^[A-Za-z0-9._-]{1,128}\.gguf$') {
        throw "The catalog text profile '$($chosen.id)' has an identifier or file name the launch file refuses."
    }
    return $chosen
}

function Get-GpuLaunchLine {
    <#
    Returns the lines of the text runtime's launch file for TextProfile: every key of docs/configuration.md, a small
    4096-token context and every layer on the GPU (GPU_LAYERS 99), so the model's weights must show up in VRAM. Not
    exported.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] $TextProfile)

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
    # The lines reach printf as separate arguments; they hold no space or shell character, which Get-GpuTestProfile
    # checks for the catalog values. The script has no double quote, which Windows PowerShell 5.1 would pass unescaped.
    $shellScript = "set -eu; umask 022; mkdir -p /data/runtime; printf '%s\n' `$@ > /data/runtime/llm-launch.env; " +
        "printf '%s\n' RESERVE_VRAM_GB=1.5 > /data/runtime/image-launch.env"
    $write = Invoke-GpuNative -Directory $Root -Command 'docker' -Argument (@(
            'run', '--rm', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--volume', "$($script:DataVolume):/data",
            '--entrypoint', '/bin/sh', 'lyrveil/llm:gpu-test', '-c', $shellScript, 'sh') + (Get-GpuLaunchLine -TextProfile $TextProfile))
    return ($write.Status -eq 0)
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
    Starts Service of the test project from its built image, without dependencies, and waits up to ten minutes for its
    health check. Returns the exit status of "up --wait": 0 when the runtime is healthy. On failure it prints the last
    60 log lines, which hold no prompt because the runtimes run quiet.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [Parameter(Mandatory = $true)] [string]$Service
    )

    if (-not $PSCmdlet.ShouldProcess($Service, 'Start the test runtime')) { return 0 }
    $up = Invoke-GpuCompose -Root $Root -Argument @('up', '--detach', '--wait', '--wait-timeout', '600', '--no-deps', '--no-build', $Service)
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
    Get-GpuTestProfile, Test-GpuTestConfiguration, Start-GpuTestRuntime, Remove-GpuTestRuntime, Get-GpuTestContainerFact,
    Invoke-GpuRuntimeRequest
