# Plans, drives and judges T-GPU-13, the long session of conversations and images, with injected actions and clock so the same driver runs two hours on the GPU or a few seconds in a unit test.

Set-StrictMode -Version Latest

# What the verdict tolerates. The VRAM floor of the last quarter of the session may exceed that of the second quarter by
# this much (the same 1 GiB hysteresis that GpuSwitches.psm1 uses for model phases); the host must keep this much RAM
# free at every sample; a session ends early after this many failed events in a row, so a broken stack does not burn
# two hours.
$script:VramGrowthMiB = 1024
$script:MinFreeRamMiB = 1024
$script:MaxConsecutiveError = 5

function Get-SessionField {
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

function Get-GpuSessionPlan {
    <#
    Returns the plan of T-GPU-13 as @{ Mode; DurationSecond; Images; Conversations; TextEverySecond; SampleEverySecond;
    VramGrowthMiB; MinFreeRamMiB; MaxConsecutiveError }. Mode Full is the two-hour session of the roadmap with 20
    images and 3 conversations. Mode Short is the same driver in about two and a half minutes, with 2 images and the same
    3 conversations, to prove the driver, the records and the verdict on the real stack before the long run.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([ValidateSet('Full', 'Short')] [string]$Mode = 'Full')

    $plan = @{
        Mode = $Mode.ToLowerInvariant(); DurationSecond = 7200; Images = 20; Conversations = 3
        TextEverySecond = 90; SampleEverySecond = 30
        VramGrowthMiB = $script:VramGrowthMiB; MinFreeRamMiB = $script:MinFreeRamMiB; MaxConsecutiveError = $script:MaxConsecutiveError
    }
    if ($Mode -eq 'Short') {
        $plan.DurationSecond = 150
        $plan.Images = 2
        $plan.TextEverySecond = 30
        $plan.SampleEverySecond = 15
    }
    return $plan
}

function Get-GpuSessionSchedule {
    <#
    Returns the events of Plan in the order they fall due, as @{ Seq; Kind; Conversation; Due }. Kind is "text" or
    "image", Conversation the zero-based conversation it belongs to, and Due the second after the start. Texts fall due
    at 0, TextEverySecond, 2 x TextEverySecond and so on while below DurationSecond, taking the conversations in turn, so
    each conversation gets its first text within its first turns. The images are spread evenly: image i falls due at
    (i + 0.5) x DurationSecond / Images, taking the conversations in turn. At the same second an image goes first.
    #>
    [CmdletBinding()]
    [OutputType([hashtable[]])]
    param([Parameter(Mandatory = $true)] [hashtable]$Plan)

    $items = New-Object System.Collections.Generic.List[hashtable]
    $turn = 0
    for ($due = 0.0; $due -lt $Plan.DurationSecond; $due += $Plan.TextEverySecond) {
        $items.Add(@{ Kind = 'text'; Conversation = ($turn % $Plan.Conversations); Due = [math]::Round($due, 2); Order = 1 })
        $turn++
    }
    for ($image = 0; $image -lt $Plan.Images; $image++) {
        $due = ($image + 0.5) * $Plan.DurationSecond / $Plan.Images
        $items.Add(@{ Kind = 'image'; Conversation = ($image % $Plan.Conversations); Due = [math]::Round($due, 2); Order = 0 })
    }
    $sorted = @($items.ToArray() | Sort-Object -Property @{ Expression = { $_.Due } }, @{ Expression = { $_.Order } })
    $seq = 0
    $result = foreach ($item in $sorted) {
        $seq++
        @{ Seq = $seq; Kind = $item.Kind; Conversation = $item.Conversation; Due = $item.Due }
    }
    return [hashtable[]]@($result)
}

function Invoke-GpuSession {
    <#
    Drives the session of Plan and returns its record @{ Plan; ElapsedSecond; Aborted; AbortReason; Samples; Events }.
    Clock returns the seconds since the start and Wait sleeps the seconds it is given; both default to a stopwatch and
    Start-Sleep, and a unit test passes a virtual pair. The driver runs the events of Get-GpuSessionSchedule one after
    the other, each as soon as it is due, or at once when an earlier one overran, and never two at the same time. Text
    and Image are called with the zero-based conversation and the zero-based number of the event of that kind, and
    return @{ Ok; Detail; Error }; an exception counts as a failed event with its message. Sample, called every
    SampleEverySecond while idle, after every event and at the end, returns a hashtable of readings (VramMiB,
    HostRamFreeMiB, ContainerMiB, State), kept with its Second and Phase ("idle", "event" or "end"); a sample that
    throws is kept with VramMiB -1 and its error. Each event is kept with its Seq, Kind, Conversation, Due, StartSecond,
    Seconds (its latency as the driver measured it), Ok, Detail and Error. After MaxConsecutiveError failed events in a
    row the session stops early and Aborted is $true with the reason; otherwise it ends once every event ran and
    DurationSecond has passed.
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Sample', Justification = 'Called inside the take script block.')]
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Plan,
        [Parameter(Mandatory = $true)] [scriptblock]$Sample,
        [Parameter(Mandatory = $true)] [scriptblock]$Text,
        [Parameter(Mandatory = $true)] [scriptblock]$Image,
        [scriptblock]$Clock = $null,
        [scriptblock]$Wait = $null
    )

    if ($null -eq $Clock) {
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $Clock = { $watch.Elapsed.TotalSeconds }.GetNewClosure()
    }
    if ($null -eq $Wait) { $Wait = { param($Second) Start-Sleep -Milliseconds ([int]([math]::Ceiling($Second * 1000))) } }

    $schedule = @(Get-GpuSessionSchedule -Plan $Plan)
    $samples = New-Object System.Collections.Generic.List[object]
    $events = New-Object System.Collections.Generic.List[object]
    $counts = @{ text = 0; image = 0 }
    $nextSample = 0.0
    $index = 0
    $consecutive = 0
    $aborted = $false
    $reason = ''

    $take = {
        param([string]$Phase)
        $second = [math]::Round([double](& $Clock), 1)
        $entry = [ordered]@{ Second = $second; Phase = $Phase; VramMiB = -1 }
        try {
            $reading = & $Sample
            if ($reading -is [System.Collections.IDictionary]) { foreach ($key in $reading.Keys) { $entry[[string]$key] = $reading[$key] } }
            else { $entry.Error = 'The sample returned no readings.' }
        }
        catch { $entry.Error = $_.Exception.Message }
        $samples.Add([pscustomobject]$entry)
    }

    while ($true) {
        $now = [double](& $Clock)
        if ($now -ge $nextSample) {
            & $take 'idle'
            $nextSample = $now + $Plan.SampleEverySecond
        }
        if ($index -lt $schedule.Length) {
            $planned = $schedule[$index]
            if ($now -ge $planned.Due) {
                $number = $counts[$planned.Kind]
                $counts[$planned.Kind] = $number + 1
                $action = if ($planned.Kind -eq 'image') { $Image } else { $Text }
                $record = [ordered]@{
                    Seq = $planned.Seq; Kind = $planned.Kind; Conversation = $planned.Conversation; Due = $planned.Due
                    StartSecond = [math]::Round($now, 2); Seconds = 0; Ok = $false; Detail = $null; Error = ''
                }
                try {
                    $answer = & $action $planned.Conversation $number
                    $record.Ok = ((Get-SessionField $answer 'Ok') -eq $true)
                    $record.Detail = Get-SessionField $answer 'Detail'
                    $record.Error = [string](Get-SessionField $answer 'Error')
                }
                catch { $record.Error = $_.Exception.Message }
                $finished = [double](& $Clock)
                $record.Seconds = [math]::Round($finished - $now, 2)
                $events.Add([pscustomobject]$record)
                & $take 'event'
                $nextSample = $finished + $Plan.SampleEverySecond
                if ($record.Ok) { $consecutive = 0 } else { $consecutive++ }
                $index++
                if ($consecutive -ge $Plan.MaxConsecutiveError) {
                    $aborted = $true
                    $reason = "$consecutive events failed in a row; the last one was $($planned.Kind) $($planned.Seq): $($record.Error)"
                    break
                }
                continue
            }
        }
        elseif ($now -ge $Plan.DurationSecond) { break }

        $target = $nextSample
        $limit = if ($index -lt $schedule.Length) { $schedule[$index].Due } else { $Plan.DurationSecond }
        if ($limit -lt $target) { $target = $limit }
        & $Wait ([math]::Max(0.05, $target - $now))
    }
    & $take 'end'
    return @{
        Plan = $Plan; ElapsedSecond = [math]::Round([double](& $Clock), 1); Aborted = $aborted; AbortReason = $reason
        Samples = $samples.ToArray(); Events = $events.ToArray()
    }
}

function Get-GpuSessionPercentile {
    # Returns the Percent-th percentile of Value by the nearest-rank rule, or $null for no value. Not exported.
    param([double[]]$Value = @(), [double]$Percent)

    if ($Value.Length -eq 0) { return $null }
    $sorted = @($Value | Sort-Object)
    $rank = [int][math]::Ceiling($Percent / 100.0 * $sorted.Length)
    return [double]$sorted[[math]::Min([math]::Max($rank, 1), $sorted.Length) - 1]
}

function Get-GpuSessionLatency {
    # Returns @{ Count; Ok; P50; P95; Max } of the seconds of the Entry records of Kind, the percentiles over the ones
    # that succeeded (a failed event's time says nothing about a reply), $null where there are none. Not exported.
    param($Entry, [string]$Kind)

    $own = @($Entry | Where-Object { $_.Kind -eq $Kind })
    $ok = @($own | Where-Object { $_.Ok })
    $seconds = [double[]]@($ok | ForEach-Object { [double]$_.Seconds })
    return @{
        Count = $own.Length; Ok = $ok.Length
        P50 = (Get-GpuSessionPercentile -Value $seconds -Percent 50); P95 = (Get-GpuSessionPercentile -Value $seconds -Percent 95)
        Max = (Get-GpuSessionPercentile -Value $seconds -Percent 100)
    }
}

function Get-GpuSessionFloor {
    # Returns the lowest readable VRAM (not negative) among the samples whose Second lies in [From, To], or $null when
    # there is none. Not exported.
    param($Sample, [double]$From, [double]$To)

    $values = @($Sample | Where-Object { $_.Second -ge $From -and $_.Second -le $To -and [double]$_.VramMiB -ge 0 } | ForEach-Object { [double]$_.VramMiB })
    if ($values.Length -eq 0) { return $null }
    return [double](($values | Measure-Object -Minimum).Minimum)
}

function Get-GpuSessionSummary {
    <#
    Returns the numbers of a session Record: @{ Events; Errors; Text; Image; Samples; VramPeakMiB; VramFloor2MiB;
    VramFloor4MiB; HostRamFreeMinMiB; ContainerPeakMiB }. Text and Image hold the latency of Get-GpuSessionLatency; the
    two floors are the lowest VRAM in the second and in the last quarter of the planned duration; ContainerPeakMiB maps
    each container to its highest memory reading. Readings that are missing or unreadable (negative) are left out, and
    a number with no reading is $null.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] $Record)

    $duration = [double]$Record.Plan.DurationSecond
    $events = @($Record.Events)
    $samples = @($Record.Samples)
    $vram = @($samples | Where-Object { [double]$_.VramMiB -ge 0 } | ForEach-Object { [double]$_.VramMiB })
    $ram = @($samples | Where-Object { $null -ne (Get-SessionField $_ 'HostRamFreeMiB') -and [double]$_.HostRamFreeMiB -ge 0 } | ForEach-Object { [double]$_.HostRamFreeMiB })
    $peak = @{}
    foreach ($sample in $samples) {
        $containers = Get-SessionField $sample 'ContainerMiB'
        if ($null -eq $containers) { continue }
        $names = if ($containers -is [System.Collections.IDictionary]) { @($containers.Keys) } else { @($containers.PSObject.Properties | ForEach-Object { $_.Name }) }
        foreach ($name in $names) {
            $value = [double](Get-SessionField $containers $name)
            if ($value -lt 0) { continue }
            if (-not $peak.ContainsKey($name) -or $value -gt $peak[$name]) { $peak[$name] = $value }
        }
    }
    return @{
        Events = $events.Length; Errors = @($events | Where-Object { -not $_.Ok }).Length
        Text = (Get-GpuSessionLatency -Entry $events -Kind 'text'); Image = (Get-GpuSessionLatency -Entry $events -Kind 'image')
        Samples = $samples.Length
        VramPeakMiB = $(if ($vram.Length -gt 0) { ($vram | Measure-Object -Maximum).Maximum } else { $null })
        VramFloor2MiB = (Get-GpuSessionFloor -Sample $samples -From ($duration / 4) -To ($duration / 2))
        VramFloor4MiB = (Get-GpuSessionFloor -Sample $samples -From ($duration * 3 / 4) -To ([math]::Max($duration, [double]$Record.ElapsedSecond)))
        HostRamFreeMinMiB = $(if ($ram.Length -gt 0) { ($ram | Measure-Object -Minimum).Minimum } else { $null })
        ContainerPeakMiB = $peak
    }
}

function Get-GpuSessionVerdict {
    <#
    Judges a session Record and returns @{ Passed; Problems; Summary }, Problems being one message for every rule it
    breaks and Summary what Get-GpuSessionSummary returns. The session passes only when: it was not stopped early; it
    lasted DurationSecond; every scheduled event ran and succeeded; the images that succeeded number Plan.Images and each
    conversation had at least one successful text; at least half of the expected samples were taken and the card's
    memory could be read in each of them; the host RAM never fell below MinFreeRamMiB free; and the lowest VRAM of the
    last quarter of the planned duration exceeds that of the second quarter by at most VramGrowthMiB, so a model or a
    cache that is never released shows. A rule that cannot be judged for lack of readings counts as broken.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] $Record)

    $plan = $Record.Plan
    $problems = New-Object System.Collections.Generic.List[string]
    $summary = Get-GpuSessionSummary -Record $Record
    $events = @($Record.Events)
    $schedule = @(Get-GpuSessionSchedule -Plan $plan)

    if ($Record.Aborted) { $problems.Add("The session stopped early: $($Record.AbortReason)") }
    if ([double]$Record.ElapsedSecond -lt [double]$plan.DurationSecond) {
        $problems.Add("The session lasted $($Record.ElapsedSecond) s, not the planned $($plan.DurationSecond) s.")
    }
    if ($events.Length -ne $schedule.Length) { $problems.Add("$($events.Length) of $($schedule.Length) planned events ran.") }
    foreach ($failed in @($events | Where-Object { -not $_.Ok })) {
        $problems.Add("The $($failed.Kind) $($failed.Seq) of conversation $($failed.Conversation + 1) failed: $($failed.Error)")
    }
    $images = @($events | Where-Object { $_.Kind -eq 'image' -and $_.Ok }).Length
    if ($images -ne $plan.Images) { $problems.Add("$images images succeeded, not the planned $($plan.Images).") }
    for ($conversation = 0; $conversation -lt $plan.Conversations; $conversation++) {
        $replies = @($events | Where-Object { $_.Kind -eq 'text' -and $_.Ok -and $_.Conversation -eq $conversation }).Length
        if ($replies -eq 0) { $problems.Add("Conversation $($conversation + 1) had no successful text.") }
    }

    $expected = [math]::Floor($plan.DurationSecond / $plan.SampleEverySecond)
    if ($summary.Samples -lt $expected / 2) { $problems.Add("$($summary.Samples) samples were taken, fewer than half of the expected $expected.") }
    $unreadable = @($Record.Samples | Where-Object { [double]$_.VramMiB -lt 0 }).Length
    if ($unreadable -gt 0) { $problems.Add("The card's memory could not be read in $unreadable samples.") }
    if ($null -eq $summary.HostRamFreeMinMiB) { $problems.Add('The host RAM was never read.') }
    elseif ($summary.HostRamFreeMinMiB -lt $plan.MinFreeRamMiB) {
        $problems.Add("The host had only $($summary.HostRamFreeMinMiB) MiB of RAM free at its lowest, under $($plan.MinFreeRamMiB) MiB.")
    }
    if ($null -eq $summary.VramFloor2MiB -or $null -eq $summary.VramFloor4MiB) { $problems.Add('The VRAM trend could not be judged: no readable sample in the second or the last quarter.') }
    elseif ($summary.VramFloor4MiB - $summary.VramFloor2MiB -gt $plan.VramGrowthMiB) {
        $problems.Add("The lowest VRAM rose from $($summary.VramFloor2MiB) MiB in the second quarter to $($summary.VramFloor4MiB) MiB in the last, over $($plan.VramGrowthMiB) MiB.")
    }
    return @{ Passed = ($problems.Count -eq 0); Problems = [string[]]$problems.ToArray(); Summary = $summary }
}

Export-ModuleMember -Function Get-GpuSessionPlan, Get-GpuSessionSchedule, Invoke-GpuSession, Get-GpuSessionSummary, Get-GpuSessionVerdict
