# Judges the mode switches of T-GPU-07 from the NVML and Docker samples of a run, and times them from the orchestrator's events.

Set-StrictMode -Version Latest

function Get-SwitchField {
    # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
    # Hashtables and parsed JSON objects both work. Not exported.
    param($Value, [string]$Name)

    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        if ($Value.Contains($Name)) { return $Value[$Name] }
        return $null
    }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-GpuMemoryPhase {
    <#
    Splits Sample, NVML readings with At (Unix milliseconds) and UsedMiB in time order, into phases with hysteresis: a
    'resident' phase starts at a reading at or above ResidentMiB and lasts until the next reading at or below
    ReleasedMiB, which starts a 'released' phase; readings in between continue the current phase. The first phase is
    resident only when the first reading is at or above ResidentMiB. Returns, in order, one @{ Kind; StartAt; EndAt;
    PeakMiB; LowMiB; Samples } per phase, EndAt being the time of its last reading, and none for no reading. Throws
    unless ReleasedMiB is below ResidentMiB.
    #>
    [CmdletBinding()]
    [OutputType([hashtable[]])]
    param(
        [AllowEmptyCollection()] [object[]]$Sample = @(),
        [Parameter(Mandatory = $true)] [int]$ResidentMiB,
        [Parameter(Mandatory = $true)] [int]$ReleasedMiB
    )

    if ($ReleasedMiB -ge $ResidentMiB) { throw 'ReleasedMiB must be below ResidentMiB.' }
    $phases = New-Object System.Collections.Generic.List[hashtable]
    $current = $null
    foreach ($reading in @($Sample)) {
        $used = [int](Get-SwitchField $reading 'UsedMiB')
        $at = [double](Get-SwitchField $reading 'At')
        $kind = $null
        if ($null -eq $current) { $kind = $(if ($used -ge $ResidentMiB) { 'resident' } else { 'released' }) }
        elseif ($current.Kind -eq 'released' -and $used -ge $ResidentMiB) { $kind = 'resident' }
        elseif ($current.Kind -eq 'resident' -and $used -le $ReleasedMiB) { $kind = 'released' }
        if ($null -ne $kind) {
            $current = @{ Kind = $kind; StartAt = $at; EndAt = $at; PeakMiB = $used; LowMiB = $used; Samples = 0 }
            $phases.Add($current)
        }
        $current.EndAt = $at
        $current.Samples++
        if ($used -gt $current.PeakMiB) { $current.PeakMiB = $used }
        if ($used -lt $current.LowMiB) { $current.LowMiB = $used }
    }
    return [hashtable[]]$phases.ToArray()
}

function Test-GpuExclusiveTimeline {
    <#
    Checks that the samples of a run of mode switches never show both models (T-GPU-07) and returns @{ Problems; Phases }.
    Memory holds NVML readings @{ At; UsedMiB } and Container Docker readings @{ At; Running }, Running holding the
    flags of the text and the image runtime in that order, all on one clock. Expected names the runtime of each model
    phase in order, such as llm, image, llm. A problem is reported for:
      - a container reading with both runtimes running;
      - a number of resident phases (Get-GpuMemoryPhase) other than Expected's. Two models resident together leave no
        released reading between them, so their phases would merge into one;
      - a resident phase during which the container readings show both runtimes running, neither, or another runtime
        than Expected names at its place.
    Phases lists every phase of Get-GpuMemoryPhase with Runtime added for a resident one: 'llm', 'image', 'llm+image'
    for both, or '' when no container reading falls in it.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [AllowEmptyCollection()] [object[]]$Memory = @(),
        [AllowEmptyCollection()] [object[]]$Container = @(),
        [Parameter(Mandatory = $true)] [string[]]$Expected,
        [Parameter(Mandatory = $true)] [int]$ResidentMiB,
        [Parameter(Mandatory = $true)] [int]$ReleasedMiB
    )

    $problems = New-Object System.Collections.Generic.List[string]
    $names = @('llm', 'image')
    $both = @(@($Container) | Where-Object { @(@(Get-SwitchField $_ 'Running') | Where-Object { $_ }).Length -ge 2 })
    if ($both.Length -gt 0) {
        $problems.Add("$($both.Length) container samples show both runtimes running, the first at $([long](Get-SwitchField $both[0] 'At')).")
    }
    if (@($Memory).Length -eq 0) { $problems.Add('No NVML sample was read.') }
    if (@($Container).Length -eq 0) { $problems.Add('No container sample was read.') }

    $phases = @(Get-GpuMemoryPhase -Sample $Memory -ResidentMiB $ResidentMiB -ReleasedMiB $ReleasedMiB)
    $resident = @($phases | Where-Object { $_.Kind -eq 'resident' })
    if ($resident.Length -ne $Expected.Length) {
        $problems.Add("NVML shows $($resident.Length) model phases separated by a release, not $($Expected.Length).")
    }
    for ($index = 0; $index -lt $resident.Length; $index++) {
        $phase = $resident[$index]
        $seen = New-Object System.Collections.Generic.List[string]
        foreach ($reading in @($Container)) {
            $at = [double](Get-SwitchField $reading 'At')
            if ($at -lt $phase.StartAt -or $at -gt $phase.EndAt) { continue }
            $flags = @(Get-SwitchField $reading 'Running')
            for ($slot = 0; $slot -lt [math]::Min($flags.Length, $names.Length); $slot++) {
                if ($flags[$slot] -and -not $seen.Contains($names[$slot])) { $seen.Add($names[$slot]) }
            }
        }
        $phase.Runtime = (@($names | Where-Object { $seen.Contains($_) }) -join '+')
        $place = $index + 1
        if ($phase.Runtime -eq '') { $problems.Add("Model phase $place shows no runtime running.") }
        elseif ($seen.Count -gt 1) { $problems.Add("Model phase $place shows both runtimes running.") }
        elseif ($index -lt $Expected.Length -and $phase.Runtime -ne $Expected[$index]) {
            $problems.Add("Model phase $place is the $($phase.Runtime) runtime, not $($Expected[$index]).")
        }
    }
    return @{ Problems = [string[]]$problems.ToArray(); Phases = [hashtable[]]$phases }
}

function Get-GpuSwitchTime {
    <#
    Times one switch from Transition, the orchestrator's events as GET /v1/events gives them (seq, at, from, to,
    job_id, details): the switch toward Runtime ('llm' or 'image') that was requested on behalf of JobId. Returns @{ Complete;
    UnloadSeconds; LoadSeconds; Seconds; Release; Load }: UnloadSeconds runs from the event that leaves the other
    runtime's ready state for its unloading state to the verified release (IDLE), LoadSeconds from that release to the
    ready state of Runtime, and Seconds is their sum. Release and Load are the details of the release and of the ready
    event. Complete is $false, with the times $null, when one of the three events is missing or they are out of order.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [AllowEmptyCollection()] [object[]]$Transition = @(),
        [Parameter(Mandatory = $true)] [string]$JobId,
        [Parameter(Mandatory = $true)] [ValidateSet('llm', 'image')] [string]$Runtime
    )

    $other = if ($Runtime -eq 'llm') { 'IMAGE' } else { 'LLM' }
    $target = $Runtime.ToUpperInvariant()
    $mine = @(@($Transition) | Where-Object { [string](Get-SwitchField $_ 'job_id') -ceq $JobId } | Sort-Object { [long](Get-SwitchField $_ 'seq') })
    $unload = @($mine | Where-Object { (Get-SwitchField $_ 'from') -eq "${other}_READY" -and (Get-SwitchField $_ 'to') -eq "UNLOADING_$other" } | Select-Object -First 1)
    $release = @($mine | Where-Object { (Get-SwitchField $_ 'from') -eq "UNLOADING_$other" -and (Get-SwitchField $_ 'to') -eq 'IDLE' } | Select-Object -First 1)
    $ready = @($mine | Where-Object { (Get-SwitchField $_ 'from') -eq "LOADING_$target" -and (Get-SwitchField $_ 'to') -eq "${target}_READY" } | Select-Object -First 1)
    $result = @{ Complete = $false; UnloadSeconds = $null; LoadSeconds = $null; Seconds = $null; Release = $null; Load = $null }
    if ($unload.Length -ne 1 -or $release.Length -ne 1 -or $ready.Length -ne 1) { return $result }
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $times = @($unload[0], $release[0], $ready[0] | ForEach-Object { [DateTimeOffset]::Parse([string](Get-SwitchField $_ 'at'), $culture) })
    if ($times[1] -lt $times[0] -or $times[2] -lt $times[1]) { return $result }
    $result.Complete = $true
    $result.UnloadSeconds = [math]::Round(($times[1] - $times[0]).TotalSeconds, 2)
    $result.LoadSeconds = [math]::Round(($times[2] - $times[1]).TotalSeconds, 2)
    $result.Seconds = [math]::Round(($times[2] - $times[0]).TotalSeconds, 2)
    $result.Release = Get-SwitchField $release[0] 'details'
    $result.Load = Get-SwitchField $ready[0] 'details'
    return $result
}

Export-ModuleMember -Function Get-GpuMemoryPhase, Test-GpuExclusiveTimeline, Get-GpuSwitchTime
