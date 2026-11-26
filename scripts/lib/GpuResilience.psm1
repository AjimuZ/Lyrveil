# Reads the Docker, process, route and log output of T-GPU-10 to T-GPU-12 and judges it: no orphan process and a stopped stack after stop.cmd, no way out of the internal networks, and the CUDA request of a forced load.

Set-StrictMode -Version Latest

# The command lines of the runtime programs that could hold VRAM: KoboldCpp and ComfyUI's main script. The host
# process probe of GpuLevel.psm1 reports every process whose command line holds one of them.
$script:RuntimePattern = @('koboldcpp', 'ComfyUI/main.py')

function Get-ResilienceField {
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

function Get-GpuRuntimePattern {
    <#
    Returns the command line fragments that mark a runtime program, KoboldCpp or ComfyUI, which the host process probe
    reports wherever it runs.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    return [string[]]$script:RuntimePattern
}

function ConvertFrom-DockerTopText {
    <#
    Returns one @{ Pid; Command } per process line of "docker top" in its default form (the columns UID, PID, PPID, C,
    STIME, TTY, TIME and CMD of "ps -ef"), in order. Command is the whole CMD column with its spaces. The header and any
    line without a numeric PID are left out, so an error text gives none.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param([AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Line = @())

    $processes = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($Line)) {
        $parts = @(([string]$entry).Trim() -split '\s+', 8)
        if ($parts.Length -lt 8 -or $parts[1] -notmatch '^\d+$') { continue }
        $processes.Add([pscustomobject]@{ Pid = [int]$parts[1]; Command = $parts[7].Trim() })
    }
    return [pscustomobject[]]$processes.ToArray()
}

function ConvertFrom-GpuHostProcessText {
    <#
    Returns one @{ Pid; Command } per line "<pid> <command line>" of the host process probe, in order, and none for a
    line in another form.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param([AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Line = @())

    $processes = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($Line)) {
        if (([string]$entry) -match '^\s*(\d+) (.+)$') {
            $processes.Add([pscustomobject]@{ Pid = [int]$Matches[1]; Command = $Matches[2].Trim() })
        }
    }
    return [pscustomobject[]]$processes.ToArray()
}

function Test-GpuDefaultRoute {
    <#
    Returns $true when the routing tables have a default route: a line of /proc/net/route (Route) whose destination is
    00000000, or a line of /proc/net/ipv6_route (Route6) with the all-zero destination, prefix length 00 and an
    interface other than "lo", whose reject route every container has. The header of /proc/net/route never matches.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Route = @(),
        [AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Route6 = @()
    )

    foreach ($entry in @($Route)) {
        $fields = @(([string]$entry).Trim() -split '\s+')
        if ($fields.Length -ge 2 -and $fields[1] -eq '00000000') { return $true }
    }
    foreach ($entry in @($Route6)) {
        $fields = @(([string]$entry).Trim() -split '\s+')
        if ($fields.Length -ge 3 -and $fields[0] -eq ('0' * 32) -and $fields[1] -eq '00' -and $fields[-1] -ne 'lo') { return $true }
    }
    return $false
}

function ConvertFrom-GpuRouteProbe {
    <#
    Reads the lines of the route probe of GpuLevel.psm1: "route:<line of /proc/net/route>", "route6:<line of
    /proc/net/ipv6_route>", "connect=connected" or "connect=unreachable" (a TCP connection to 1.1.1.1:443) and
    "resolve=resolved" or "resolve=unresolved" (a public name). Returns @{ DefaultRoute; Connect; Resolve; Complete }:
    Connect and Resolve are '' when their line is missing, and Complete is $true only when the IPv4 table, the connect
    line and the resolve line were all read, so a probe that did not run never reads as isolated.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Line = @())

    $route = New-Object System.Collections.Generic.List[string]
    $route6 = New-Object System.Collections.Generic.List[string]
    $connect = ''
    $resolve = ''
    foreach ($entry in @($Line)) {
        $text = [string]$entry
        if ($text.StartsWith('route6:')) { $route6.Add($text.Substring(7)) }
        elseif ($text.StartsWith('route:')) { $route.Add($text.Substring(6)) }
        elseif ($text -match '^connect=(connected|unreachable)\s*$') { $connect = $Matches[1] }
        elseif ($text -match '^resolve=(resolved|unresolved)\s*$') { $resolve = $Matches[1] }
    }
    return [pscustomobject]@{
        DefaultRoute = (Test-GpuDefaultRoute -Route $route.ToArray() -Route6 $route6.ToArray())
        Connect      = $connect
        Resolve      = $resolve
        Complete     = ($route.Count -gt 0 -and $connect -ne '' -and $resolve -ne '')
    }
}

function Get-GpuOfflineProblem {
    <#
    Judges T-GPU-12's isolation and returns one message per way a service of Network could reach outside, or none.
    Each record holds Service, Known (Docker answered for its container), Networks (@{ Name; Internal } for each
    network it joins), Published (its published ports) and Probe: what ConvertFrom-GpuRouteProbe returned for its
    container, or $null when it was not probed. A record must be known, join at least one network, join only internal
    ones and publish nothing; a probe must be complete and show no default route, no connection out and no resolved
    public name. A record whose Probe is $null is judged on its networks alone, so the caller checks that every
    service it needed was probed.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([AllowEmptyCollection()] [object[]]$Network = @())

    $problems = New-Object System.Collections.Generic.List[string]
    if (@($Network).Length -eq 0) { $problems.Add('No service was checked.') }
    foreach ($record in @($Network)) {
        $service = [string](Get-ResilienceField $record 'Service')
        if ((Get-ResilienceField $record 'Known') -ne $true) {
            $problems.Add("Docker did not answer for the container of $service.")
            continue
        }
        $joined = @(Get-ResilienceField $record 'Networks' | Where-Object { $null -ne $_ })
        if ($joined.Length -eq 0) { $problems.Add("$service joins no network.") }
        foreach ($item in $joined) {
            if ((Get-ResilienceField $item 'Internal') -ne $true) {
                $problems.Add("$service joins $(Get-ResilienceField $item 'Name'), which is not internal.")
            }
        }
        $published = @(Get-ResilienceField $record 'Published' | Where-Object { $_ })
        if ($published.Length -gt 0) { $problems.Add("$service publishes $($published -join ', ').") }
        $probe = Get-ResilienceField $record 'Probe'
        if ($null -eq $probe) { continue }
        if ((Get-ResilienceField $probe 'Complete') -ne $true) {
            $problems.Add("The route probe of $service did not report its routes, its connection and its lookup.")
            continue
        }
        if ((Get-ResilienceField $probe 'DefaultRoute') -eq $true) { $problems.Add("$service has a default route.") }
        if ((Get-ResilienceField $probe 'Connect') -ne 'unreachable') { $problems.Add("$service opened a connection to 1.1.1.1:443.") }
        if ((Get-ResilienceField $probe 'Resolve') -ne 'unresolved') { $problems.Add("$service resolved a public name.") }
    }
    return [string[]]$problems.ToArray()
}

function Get-GpuOrphanProblem {
    <#
    Judges T-GPU-11's process check and returns one message per problem, or none. Listed holds the processes "docker
    top" reported for the running containers before the stop, as @{ Service; Pid; Command }; SeenBefore and SeenAfter
    what the host process probe saw, as @{ Pid; Command }, just before and after the stop. A process is the same when
    both its PID and its command line match, so a PID reused by another program is not one.
      - Every service of Listed needs at least one of its processes in SeenBefore: otherwise the probe could not have
        seen it after either. A short-lived process, such as a health check, may end before the probe and is not
        required.
      - A process of Listed still in SeenAfter is an orphan.
      - Any process of SeenAfter whose command line holds a runtime pattern (Get-GpuRuntimePattern) is a runtime
        process left on the card.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [AllowEmptyCollection()] [object[]]$Listed = @(),
        [AllowEmptyCollection()] [object[]]$SeenBefore = @(),
        [AllowEmptyCollection()] [object[]]$SeenAfter = @()
    )

    $problems = New-Object System.Collections.Generic.List[string]
    if (@($Listed).Length -eq 0) { $problems.Add('docker top listed no process before the stop.') }
    $key = { param($Item) '{0} {1}' -f [int](Get-ResilienceField $Item 'Pid'), ([string](Get-ResilienceField $Item 'Command')).Trim() }
    $before = @(@($SeenBefore) | ForEach-Object { & $key $_ })
    $services = @(@($Listed) | ForEach-Object { [string](Get-ResilienceField $_ 'Service') } | Select-Object -Unique)
    foreach ($service in $services) {
        $own = @(@($Listed) | Where-Object { [string](Get-ResilienceField $_ 'Service') -eq $service })
        if (@($own | Where-Object { $before -ccontains (& $key $_) }).Length -eq 0) {
            $problems.Add("The host probe saw no process of $service before the stop, so it could not show one after.")
        }
    }
    foreach ($process in @($SeenAfter)) {
        $match = @(@($Listed) | Where-Object { (& $key $_) -ceq (& $key $process) } | Select-Object -First 1)
        $command = [string](Get-ResilienceField $process 'Command')
        if ($match.Length -eq 1) {
            $problems.Add("Process $(Get-ResilienceField $process 'Pid') of $(Get-ResilienceField $match[0] 'Service') still runs after the stop: $command")
        }
        elseif (@($script:RuntimePattern | Where-Object { $command.Contains($_) }).Length -gt 0) {
            $problems.Add("A runtime process still runs after the stop: $(Get-ResilienceField $process 'Pid') $command")
        }
    }
    return [string[]]$problems.ToArray()
}

function Get-GpuStoppedProblem {
    <#
    Judges the containers after T-GPU-11's stop and returns one message per problem, or none. Stopped holds one record
    per service right after the stop and Later the same records some seconds after, each with Service, Known, Ran (it
    started at least once), Running, Restarting, Status, ExitCode, OOMKilled and StartedAt. Every container must be
    known and neither running nor restarting; one that ran must be "exited" with an exit code and not killed for
    memory. A Later record must still be stopped, with the same StartedAt, so nothing started it again.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [AllowEmptyCollection()] [object[]]$Stopped = @(),
        [AllowEmptyCollection()] [object[]]$Later = @()
    )

    $problems = New-Object System.Collections.Generic.List[string]
    if (@($Stopped).Length -eq 0) { $problems.Add('No container was checked after the stop.') }
    foreach ($record in @($Stopped)) {
        $service = [string](Get-ResilienceField $record 'Service')
        if ((Get-ResilienceField $record 'Known') -ne $true) {
            $problems.Add("$service has no container after the stop.")
            continue
        }
        if ((Get-ResilienceField $record 'Running') -eq $true -or (Get-ResilienceField $record 'Restarting') -eq $true) {
            $problems.Add("$service still runs after the stop.")
            continue
        }
        if ((Get-ResilienceField $record 'Ran') -eq $true) {
            if ([string](Get-ResilienceField $record 'Status') -ne 'exited') { $problems.Add("$service is '$(Get-ResilienceField $record 'Status')', not exited.") }
            if ($null -eq (Get-ResilienceField $record 'ExitCode')) { $problems.Add("$service reports no exit code.") }
        }
        if ((Get-ResilienceField $record 'OOMKilled') -eq $true) { $problems.Add("$service was killed for memory.") }
    }
    foreach ($record in @($Later)) {
        $service = [string](Get-ResilienceField $record 'Service')
        $first = @(@($Stopped) | Where-Object { [string](Get-ResilienceField $_ 'Service') -eq $service } | Select-Object -First 1)
        if ((Get-ResilienceField $record 'Running') -eq $true -or (Get-ResilienceField $record 'Restarting') -eq $true) {
            $problems.Add("$service started again after the stop.")
        }
        elseif ($first.Length -eq 1 -and [string](Get-ResilienceField $record 'StartedAt') -ne [string](Get-ResilienceField $first[0] 'StartedAt')) {
            $problems.Add("$service started again after the stop and stopped.")
        }
    }
    return [string[]]$problems.ToArray()
}

function ConvertFrom-GpuEventLog {
    <#
    Returns the orchestrator's lifecycle events found in Line, its log lines as "docker compose logs" gives them, in
    order: every line that ends with the JSON of one event (an object that starts with "seq", as LifecycleEvent.to_dict
    writes it) becomes that object. Other lines, and JSON that does not parse, are left out.
    #>
    [CmdletBinding()]
    [OutputType([object[]])]
    param([AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Line = @())

    $events = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($Line)) {
        if (([string]$entry) -notmatch '(\{"seq":.*\})\s*$') { continue }
        try { $events.Add(($Matches[1] | ConvertFrom-Json)) }
        catch { continue }
    }
    return [object[]]$events.ToArray()
}

function Get-GpuDrainEvent {
    <#
    Finds the GPU drain of stop.cmd in Transition, the orchestrator's events in any order: the last event with reason
    "drain" that leaves LLM_READY or IMAGE_READY for its unloading state, and the first event after it that leaves that
    unloading state for IDLE with reason "release verified". Returns @{ Unloading; Released }, either $null when it is
    missing.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([AllowEmptyCollection()] [object[]]$Transition = @())

    $ordered = @(@($Transition) | Sort-Object { [long](Get-ResilienceField $_ 'seq') })
    $unloading = @($ordered | Where-Object {
            (Get-ResilienceField $_ 'reason') -eq 'drain' -and (Get-ResilienceField $_ 'to') -like 'UNLOADING_*' -and
            (Get-ResilienceField $_ 'from') -in @('LLM_READY', 'IMAGE_READY')
        } | Select-Object -Last 1)
    $result = @{ Unloading = $null; Released = $null }
    if ($unloading.Length -eq 0) { return $result }
    $result.Unloading = $unloading[0]
    $seq = [long](Get-ResilienceField $unloading[0] 'seq')
    $state = [string](Get-ResilienceField $unloading[0] 'to')
    $released = @($ordered | Where-Object {
            [long](Get-ResilienceField $_ 'seq') -gt $seq -and (Get-ResilienceField $_ 'from') -eq $state -and
            (Get-ResilienceField $_ 'to') -eq 'IDLE' -and (Get-ResilienceField $_ 'reason') -eq 'release verified'
        } | Select-Object -First 1)
    if ($released.Length -eq 1) { $result.Released = $released[0] }
    return $result
}

function Get-GpuCudaRequest {
    <#
    Reads the log lines of a KoboldCpp load and returns @{ FreeMiB; Buffer; RequestedMiB }: FreeMiB is the free memory
    CUDA reported for CUDA0 ("using device CUDA0 ... - <n> MiB free"), Buffer the size of each CUDA0 buffer named in
    the log (model, KV and compute, the last value of each), and RequestedMiB their sum. FreeMiB is $null when the line
    is missing, and Buffer is empty when no buffer line is found.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([AllowEmptyCollection()] [AllowEmptyString()] [string[]]$Line = @())

    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $free = $null
    $buffer = [ordered]@{}
    foreach ($entry in @($Line)) {
        $text = [string]$entry
        if ($text -match 'using device CUDA0 .* - (\d+) MiB free') { $free = [int]$Matches[1] }
        if ($text -match '\bCUDA0 (model|KV|compute) buffer size\s*=\s*([0-9]+(?:\.[0-9]+)?) MiB') {
            $buffer[$Matches[1]] = [double]::Parse($Matches[2], $culture)
        }
    }
    $requested = 0.0
    foreach ($value in $buffer.Values) { $requested += $value }
    return @{ FreeMiB = $free; Buffer = $buffer; RequestedMiB = [math]::Round($requested, 1) }
}

function Get-GpuOomLaunchLine {
    <#
    Returns Line, the lines of a level-A launch file, with every layer forced on the GPU (GPU_LAYERS=99) and with
    CONTEXT_SIZE and BATCH_SIZE replaced by ContextSize and BatchSize, the other lines unchanged and in order. It is
    the load T-GPU-10 makes too large for the memory left free, so that the load fails for allocation. Throws when Line
    lacks one of the three keys.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string[]]$Line,
        [Parameter(Mandatory = $true)] [ValidateRange(512, 262144)] [int]$ContextSize,
        [Parameter(Mandatory = $true)] [ValidateRange(32, 8192)] [int]$BatchSize
    )

    $values = @{ GPU_LAYERS = '99'; CONTEXT_SIZE = "$ContextSize"; BATCH_SIZE = "$BatchSize" }
    $seen = @{}
    $result = foreach ($entry in $Line) {
        $name = ($entry -split '=', 2)[0]
        if ($values.ContainsKey($name)) {
            $seen[$name] = $true
            "$name=$($values[$name])"
        }
        else { $entry }
    }
    foreach ($name in $values.Keys) { if (-not $seen.ContainsKey($name)) { throw "The launch lines have no $name." } }
    return [string[]]@($result)
}

Export-ModuleMember -Function Get-GpuRuntimePattern, ConvertFrom-DockerTopText, ConvertFrom-GpuHostProcessText, Test-GpuDefaultRoute,
    ConvertFrom-GpuRouteProbe, Get-GpuOfflineProblem, Get-GpuOrphanProblem, Get-GpuStoppedProblem, ConvertFrom-GpuEventLog,
    Get-GpuDrainEvent, Get-GpuCudaRequest, Get-GpuOomLaunchLine
