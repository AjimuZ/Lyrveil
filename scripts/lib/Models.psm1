# Runs "scripts\lyrveil.cmd models": license acceptance, then downloads, lists, verifies or removes model files with the one-shot lyrveil-models service.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Lifecycle.psm1') -Force
# Lifecycle.psm1 has just loaded Setup.psm1. Importing it again without -Force reuses that instance and only makes its
# helpers visible here; -Force would replace the instance that Lifecycle.psm1 uses.
Import-Module (Join-Path $PSScriptRoot 'Setup.psm1')
Import-Module (Join-Path $PSScriptRoot 'ConsolePrompt.psm1') -Force

# The service of compose.yaml that runs python -m lyrveil.tools.models, and the volume key it mounts.
$script:Service = 'lyrveil-models'
$script:VolumeKey = 'lyrveil-models'
$script:VolumeLabel = 'io.lyrveil.volume'
# Memory seen by Docker from which the machine is on the target tier (ADR 0027, docs/models.md).
$script:TargetTierBytes = 20GB
# Exit statuses of the tool, which the command returns unchanged (docs/operations.md, Scripts).
$script:ToolStatuses = @(0, 1, 2, 4, 5)
# Why the active profiles are the defaults of the tier for now, printed when -Remove refuses one.
$script:ActivePending = 'Until the launch file (P02) and the image setting (P05) exist, the active profiles are the defaults of the tier.'
$script:Invariant = [System.Globalization.CultureInfo]::InvariantCulture

function Format-Gigabyte {
    # Returns Bytes in decimal gigabytes with two decimals and a dot, as the catalog states sizes. Pure.
    [CmdletBinding()]
    [OutputType([string])]
    param([double]$Bytes)

    return [string]::Format($script:Invariant, '{0:0.00} GB', ($Bytes / 1e9))
}

function Get-ModelTier {
    # Returns 'target' when Docker sees at least 20 GiB of memory and 'reduced' otherwise, also when the memory cannot
    # be read, because a smaller default never fails to load. Reads only.
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $memory = Invoke-NativeCapture -Command 'docker' -Argument @('info', '--format', '{{.MemTotal}}') -IncludeErrorStream
    $bytes = [long]0
    if ($memory.Status -eq 0 -and [long]::TryParse((($memory.Output -join '').Trim()), [ref]$bytes)) {
        if ($bytes -ge $script:TargetTierBytes) { return 'target' }
        return 'reduced'
    }
    Write-Host 'WARNING  Memory seen by Docker: unreadable, so the reduced tier is assumed'
    return 'reduced'
}

function Test-ModelPrerequisite {
    <#
    Returns $true when the models volume of Project exists with the label io.lyrveil.volume and the image of the
    lyrveil-models service is present. Otherwise prints which one is missing and that setup.cmd creates it, and
    returns $false. An unlabelled volume is never used, because setup did not create it. Reads only.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] $Project)

    $volume = @(Get-ProjectVolume -Project $Project | Where-Object { $_.Key -eq $script:VolumeKey })[0]
    # The labels come as JSON: Windows PowerShell 5.1 drops the quotes inside a native argument.
    $inspect = Invoke-NativeCapture -Command 'docker' -Argument @('volume', 'inspect', '--format', '{{json .Labels}}', $volume.Name) -IncludeErrorStream
    if ($inspect.Status -ne 0) {
        Write-Host "FAILURE  Models volume: $($volume.Name) does not exist. Run setup.cmd first."
        return $false
    }
    $labels = ($inspect.Output -join '') | ConvertFrom-Json
    if (-not (Get-ObjectProperty -Object $labels -Name $script:VolumeLabel)) {
        Write-Host "FAILURE  Models volume: $($volume.Name) exists without the label $script:VolumeLabel, so it is left untouched (docs/operations.md, Uninstall)."
        return $false
    }
    $service = Get-ObjectProperty -Object (Get-ObjectProperty -Object $Project -Name 'services') -Name $script:Service
    $image = [string](Get-ObjectProperty -Object $service -Name 'image')
    if (-not $image) { throw "The Compose configuration has no image for $script:Service." }
    if ((Invoke-NativeCapture -Command 'docker' -Argument @('image', 'inspect', '--format', '{{.Id}}', $image) -IncludeErrorStream).Status -ne 0) {
        Write-Host "FAILURE  Image $image is missing. Run setup.cmd first."
        return $false
    }
    return $true
}

function Invoke-ModelTool {
    <#
    Runs python -m lyrveil.tools.models with ToolArgument in a one-off lyrveil-models container and returns
    @{ Status; Output }. With Capture, Output holds the standard output lines and the console shows only standard
    error; otherwise every line, standard error included, is printed with Write-Host as it arrives, so progress shows
    live and a caller can capture it, and Output is empty. Status is the tool's exit status when it is one of
    docs/operations.md, and 1 after a message when Docker could not run the container.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Context,
        [Parameter(Mandatory = $true)] [string[]]$ToolArgument,
        [switch]$Capture
    )

    # The tool reads no input. Without --interactive=false, Compose would attach a standard input that never closes
    # when the caller's is a pipe, and could wait on it after the container has exited.
    $arguments = (Get-ComposeArgument -Context $Context) + @('--profile', 'tools', 'run', '--rm', '--no-deps', '-T', '--interactive=false', $script:Service) + $ToolArgument
    if ($Capture) {
        $result = Invoke-NativeCapture -Command 'docker' -Argument $arguments
    }
    else {
        # Windows PowerShell 5.1 turns native standard error lines into error records, which 'Stop' would throw.
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & docker @arguments 2>&1 | ForEach-Object { Write-Host ([string]$_) }
            $result = @{ Status = [int]$LASTEXITCODE; Output = [string[]]@() }
        }
        finally {
            $ErrorActionPreference = $previous
        }
    }
    if ($script:ToolStatuses -notcontains $result.Status) {
        Write-Host "FAILURE  docker compose run $script:Service exited with $($result.Status) (see the output above)."
        $result.Status = 1
    }
    return $result
}

function Get-ModelPlan {
    # Returns @{ Status; Plan }: the tool's "plan" JSON for ToolArgument as an object, or $null with the tool's status
    # when it failed. The plan lists the tier's defaults and the chosen profiles with licenses and states. Reads only.
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] [hashtable]$Context, [Parameter(Mandatory = $true)] [string[]]$ToolArgument)

    $result = Invoke-ModelTool -Context $Context -ToolArgument (@('plan') + $ToolArgument) -Capture
    if ($result.Status -ne 0) { return @{ Status = $result.Status; Plan = $null } }
    return @{ Status = 0; Plan = (($result.Output -join "`n") | ConvertFrom-Json) }
}

function Install-ModelFile {
    <#
    Downloads the chosen profiles: the tier's defaults, the -Model profiles, or every profile allowed on the tier with
    -All. Profiles already verified need nothing. For the others, prints each license and asks for acceptance unless
    AcceptLicense is set, and returns 5 without starting any download when the answer is no or nobody can answer.
    Otherwise returns the status of the download: 0, 2 for too little space, 4 for a size or hash mismatch, 1 for any
    other failure.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Context,
        [Parameter(Mandatory = $true)] [string]$Tier,
        [AllowEmptyCollection()] [string[]]$Model = @(),
        [switch]$All,
        [switch]$AcceptLicense
    )

    $selection = @()
    foreach ($id in $Model) { $selection += @('--model', $id) }
    if ($All) { $selection += '--all' }
    $planned = Get-ModelPlan -Context $Context -ToolArgument (@('--tier', $Tier) + $selection)
    if ($planned.Status -ne 0) { return $planned.Status }

    $pending = @($planned.Plan.profiles | Where-Object { $_.state -ne 'verified' })
    if ($pending.Length -eq 0) {
        Write-Host 'Every chosen model file is present and verified. Nothing to download.'
        return 0
    }
    Write-Host 'Profiles to download, with their licenses (docs/licensing.md):'
    $total = [double]0
    foreach ($item in $pending) {
        $total += [double]$item.bytes
        Write-Host "  $($item.id): $($item.display_name), $(Format-Gigabyte -Bytes $item.bytes), to $($item.file)"
        Write-Host "    License: $($item.license), $($item.license_url)"
        if ($item.above_tier) { Write-Host "    Needs the $($item.min_tier) tier: it can be downloaded, but not selected on this machine." }
    }
    Write-Host "  Total: $(Format-Gigabyte -Bytes $total)"
    if ($AcceptLicense) {
        Write-Host '  Licenses accepted with -AcceptModelLicenses.'
    }
    elseif (-not (Read-Confirmation -Question 'Do you accept these licenses and download the files?')) {
        Write-Host 'No model was downloaded (exit status 5).'
        return 5
    }
    return (Invoke-ModelTool -Context $Context -ToolArgument (@('download', '--tier', $Tier, '--accept-licenses') + $selection)).Status
}

function Remove-ModelFile {
    <#
    Deletes the file of profile Id after asking, and returns 0 when it was deleted or was not present, 1 when the
    profile is active or unknown, and 5 when the answer is no or nobody can answer. The active profiles are the
    defaults of Tier until the launch file and the image setting exist ($script:ActivePending).
    #>
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'None')]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Context,
        [Parameter(Mandatory = $true)] [string]$Tier,
        [Parameter(Mandatory = $true)] [string]$Id
    )

    $planned = Get-ModelPlan -Context $Context -ToolArgument @('--tier', $Tier, '--model', $Id)
    if ($planned.Status -ne 0) { return $planned.Status }
    $item = @($planned.Plan.profiles)[0]
    if (@($planned.Plan.defaults) -contains $item.id) {
        Write-Host "FAILURE  $($item.id) is an active profile, so its file is kept. $script:ActivePending"
        return 1
    }
    if ($item.state -eq 'missing') {
        Write-Host "$($item.id): no file to delete."
        return 0
    }
    if (-not (Read-Confirmation -Question "Delete $($item.file) ($(Format-Gigabyte -Bytes $item.bytes)) of the profile $($item.id) from the models volume?")) {
        Write-Host 'Nothing was deleted (exit status 5).'
        return 5
    }
    if (-not $PSCmdlet.ShouldProcess($item.file, 'Delete the model file')) { return 5 }
    return (Invoke-ModelTool -Context $Context -ToolArgument @('remove', $item.id)).Status
}

function Invoke-LyrveilModel {
    <#
    Runs "scripts\lyrveil.cmd models [options]" for the repository at Root and returns its exit status
    (docs/operations.md, Model downloads and Scripts): 0 on success, 1 on an unknown or conflicting option, an unknown
    or active profile or another failure, 2 when Docker is not running or the models volume or the image is missing,
    4 when a file's size or SHA-256 differs from the catalog, and 5 when licenses or a removal were not accepted.

    Options: none (the defaults of the machine's tier), -Model <id> (repeatable), -All, -List, -Verify, -Remove <id>
    and -AcceptModelLicenses. -List, -Verify, -Remove and a download exclude each other. The tier comes from the memory
    Docker sees. ProjectName, ComposeFile, EnvPath and ExamplePath default to the installation's; tests pass their own,
    and Tier to fix the machine's tier.
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
        [string]$Tier = ''
    )

    $options = Read-CommandOption -Command 'models' -Argument $Argument -Flag @('-All', '-List', '-Verify', '-AcceptModelLicenses') -Valued @('-Remove') -Repeated @('-Model')
    if ($null -eq $options) { return 1 }
    $download = $options.All -or $options.Model.Length -gt 0
    $actions = @($options.List, $options.Verify, [bool]$options.Remove, $download) | Where-Object { $_ }
    if (@($actions).Length -gt 1 -or ($options.All -and $options.Model.Length -gt 0)) {
        Write-Host 'models: -List, -Verify, -Remove, -Model and -All exclude each other, except that -Model can be repeated.'
        return 1
    }
    if ($Tier -and @('reduced', 'target') -notcontains $Tier) { throw "Unknown tier '$Tier'." }

    $context = Resolve-LifecycleContext -Root $Root -ProjectName $ProjectName -ComposeFile $ComposeFile -EnvPath $EnvPath -ExamplePath $ExamplePath
    if (-not (Test-DockerReachable)) {
        Write-Host 'FAILURE  Docker daemon: unreachable. Start Docker Desktop, then run the command again.'
        return 2
    }
    $project = Get-ComposeProject -Context $context
    if (-not (Test-ModelPrerequisite -Project $project)) { return 2 }
    if (-not $Tier) { $Tier = Get-ModelTier }
    Write-Host "Hardware tier: $Tier"

    if ($options.List) { return (Invoke-ModelTool -Context $context -ToolArgument @('list', '--tier', $Tier)).Status }
    if ($options.Verify) { return (Invoke-ModelTool -Context $context -ToolArgument @('verify')).Status }
    if ($options.Remove) { return (Remove-ModelFile -Context $context -Tier $Tier -Id $options.Remove) }
    return (Install-ModelFile -Context $context -Tier $Tier -Model $options.Model -All:$options.All -AcceptLicense:$options.AcceptModelLicenses)
}

Export-ModuleMember -Function Invoke-LyrveilModel
