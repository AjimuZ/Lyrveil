# Plans and judges the quality level without Docker or a GPU: the arguments of "test quality", the scripted conversations and the scores of the consistency protocol T-QLT-05, and the SmartCache and SWA padding rules of ADR 0031.

Set-StrictMode -Version Latest

# docs/consistency-engine.md (Consistency protocol): 120 messages per conversation with four-question identity probes
# ending at messages 10, 60 and 120, and the profile change of C6 after message 60. The short mode is a smoke run of the
# same driver: the first probe block alone, with the change after its second question, so every step and every score
# runs within minutes; its scores are recorded but are no evidence for the catalog. SmartCachePairs is the number of
# measured chat replies after a structured task in each SmartCache trial.
$script:Modes = @{
    Full  = @{ Messages = 120; ProbeEnds = @(10, 60, 120); ChangeAt = 60; SmartCachePairs = 10 }
    Short = @{ Messages = 4; ProbeEnds = @(4); ChangeAt = 2; SmartCachePairs = 1 }
}
$script:ProbeSize = 4
$script:ProbesPerCharacter = 12

# docs/prompting.md (Length bands): the target median of each band, in tokens of the loaded model.
$script:BandTarget = @{ brevissimi = 30; brevi = 70; medi = 160; lunghi = 300 }

# docs/consistency-engine.md (Consistency protocol): the limits of each check.
$script:Limit = @{
    ProbeRate   = 0.95   # C1: share of correct identity probes
    V11Per200   = 1.0    # C2: replies with a delivered V11 hit per 200 replies
    V13Per100   = 2.0    # C3: replies with a V13 hit per 100 replies
    CutRate     = 0.10   # C4: share of replies cut by N4
}

# ADR 0031 and docs/gpu-orchestration.md (Measuring and tuning): SmartCache 2 needs the median last_process of the
# first chat reply after a structured task to fall by at least half, with the runtime's peak RAM at least 4 GiB below
# the memory Docker sees. SWA padding keeps the smallest value within 10 % of the best median, while the margin rule
# (512 MiB) and the speed rule hold.
$script:SmartCacheMinReduction = 0.5
$script:SmartCacheHeadroomMiB = 4096
$script:SwaPaddingValues = @(0, 1024, 2048)
$script:SwaTolerance = 0.10
$script:MarginMinMiB = 512

function ConvertTo-QualityFoldedText {
    <#
    Returns Text lowercase, without accents, with every typographic apostrophe as "'" and every run of whitespace as one
    space: the form the probe fragments of tests/quality/consistency/script.json are written in. $null gives ''.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowNull()] [AllowEmptyString()] [string]$Text)

    if (-not $Text) { return '' }
    $decomposed = $Text.ToLowerInvariant().Normalize([System.Text.NormalizationForm]::FormD)
    $builder = New-Object System.Text.StringBuilder
    foreach ($char in $decomposed.ToCharArray()) {
        if ([System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($char) -ne [System.Globalization.UnicodeCategory]::NonSpacingMark) {
            $null = $builder.Append($char)
        }
    }
    $plain = $builder.ToString().Replace([string][char]0x2019, "'").Replace('`', "'")
    return (($plain -split '\s+') | Where-Object { $_ }) -join ' '
}

function Get-QualityTextProfileId {
    <#
    Returns the ids of the text profiles of the catalog document Catalog (config/model-catalog.json, parsed), in
    catalog order. Returns none for a catalog without text profiles.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)] $Catalog)

    $text = $Catalog.PSObject.Properties['text']
    if ($null -eq $text) { return [string[]]@() }
    return [string[]]@($text.Value | ForEach-Object { [string]$_.id } | Where-Object { $_ })
}

function Resolve-QualityArgument {
    <#
    Sorts the arguments of "test quality" and returns @{ Files; Items; Problems }. An argument that starts with a capital
    letter names a test file, and must be one of TestFile (repository-relative tests/quality/<Name>.Tests.ps1); Files
    lists them once each in argument order, and is empty when none is named, meaning every file. "short" and the ids of
    TextProfileId are items, listed once each in argument order, which every file that runs receives as Select. Every
    other argument, and a file name that names no file, gives one message in Problems, so the level refuses the command
    before any GPU or Docker call.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [string[]]$Argument = @(),
        [string[]]$TestFile = @(),
        [string[]]$TextProfileId = @()
    )

    $files = New-Object System.Collections.Generic.List[string]
    $items = New-Object System.Collections.Generic.List[string]
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($argument in @($Argument | Where-Object { $_ })) {
        if ($argument -cmatch '^[A-Z][A-Za-z0-9-]*$') {
            $path = "tests/quality/$argument.Tests.ps1"
            if (@($TestFile) -contains $path) {
                if (-not $files.Contains($path)) { $files.Add($path) }
            }
            else { $problems.Add("'$argument' names no test file of tests/quality.") }
            continue
        }
        if ($argument -ceq 'short' -or @($TextProfileId) -ccontains $argument) {
            if (-not $items.Contains($argument)) { $items.Add($argument) }
            continue
        }
        $problems.Add("'$argument' is neither a test file of tests/quality, 'short', nor a text profile of the catalog.")
    }
    return @{ Files = [string[]]$files.ToArray(); Items = [string[]]$items.ToArray(); Problems = [string[]]$problems.ToArray() }
}

function Get-QualitySelection {
    <#
    Returns @{ Mode; Profiles; Unknown } for the Select items a quality test file receives: Mode is Short when "short" is
    among them and Full otherwise; Profiles are the named catalog text profiles in the order named, or every one of
    TextProfileId in catalog order when none is named; Unknown lists the items that are neither.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [string[]]$Select = @(),
        [string[]]$TextProfileId = @()
    )

    $given = [string[]]@($Select | Where-Object { $_ })
    $named = [string[]]@($given | Where-Object { @($TextProfileId) -ccontains $_ } | Select-Object -Unique)
    $unknown = [string[]]@($given | Where-Object { $_ -cne 'short' -and @($TextProfileId) -cnotcontains $_ })
    $mode = if ($given -ccontains 'short') { 'Short' } else { 'Full' }
    $profiles = if ($named.Length -gt 0) { $named } else { [string[]]@($TextProfileId) }
    return @{ Mode = $mode; Profiles = [string[]]$profiles; Unknown = $unknown }
}

function Get-QualityChangeProfile {
    <#
    Returns the profile that C6 starts a conversation on before changing to ProfileId: the catalog text profile after
    ProfileId in TextProfileId, wrapping to the first, or '' when the catalog has no other text profile.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)] [string]$ProfileId,
        [string[]]$TextProfileId = @()
    )

    $ids = [string[]]@($TextProfileId)
    $index = [array]::IndexOf($ids, $ProfileId)
    if ($index -lt 0 -or $ids.Length -lt 2) { return '' }
    return $ids[($index + 1) % $ids.Length]
}

function Get-QualityConsistencyPlan {
    <#
    Returns the plan of T-QLT-05 in Mode (Full or Short) from Document, tests/quality/consistency/script.json parsed:
    Mode, Messages, ProbeEnds, ChangeAt, SmartCachePairs, ChangeCharacter, and Characters, each with FirstName, Band,
    Narration, QuestionFrequency (the card's, "a_volte" when the script gives none) and Steps. Steps holds one entry per user message, numbered from 1: the four probes of a block occupy the
    numbers ending at each probe end, in the script's order, and the shared messages fill the other numbers in order.
    Each step is @{ Number; Kind; Text; Topic; Expect; Reject }, Kind being "probe" or the shared message's kind.
    Throws when a character has not exactly 12 probes, a probe has no expected fragment, a band or a question frequency is unknown, the change
    character is not a character of the script, or the shared messages are too few for the mode.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] $Document,
        [ValidateSet('Full', 'Short')] [string]$Mode = 'Full'
    )

    $shape = $script:Modes[$Mode]
    $probeNumbers = @{}
    $block = 0
    foreach ($end in $shape.ProbeEnds) {
        for ($offset = $script:ProbeSize - 1; $offset -ge 0; $offset--) {
            $probeNumbers[$end - $offset] = ($block * $script:ProbeSize) + ($script:ProbeSize - 1 - $offset)
        }
        $block++
    }
    $shared = @($Document.messages)
    $needed = $shape.Messages - $probeNumbers.Count
    if ($shared.Length -lt $needed) { throw "The script holds $($shared.Length) shared messages; the $Mode mode needs $needed." }

    $characters = New-Object System.Collections.Generic.List[object]
    foreach ($character in @($Document.characters)) {
        $name = [string]$character.first_name
        $probes = @($character.probes)
        if ($probes.Length -ne $script:ProbesPerCharacter) { throw "$name has $($probes.Length) probes; the protocol asks $script:ProbesPerCharacter." }
        if (-not $script:BandTarget.ContainsKey([string]$character.band)) { throw "$name has the unknown band '$($character.band)'." }
        # Optional in a script; the card's own default is "a_volte".
        $frequency = 'a_volte'
        if ($character.PSObject.Properties['question_frequency']) { $frequency = [string]$character.question_frequency }
        if (@('raramente', 'a_volte', 'spesso') -cnotcontains $frequency) { throw "$name has the unknown question frequency '$frequency'." }
        $steps = New-Object System.Collections.Generic.List[object]
        $next = 0
        for ($number = 1; $number -le $shape.Messages; $number++) {
            if ($probeNumbers.ContainsKey($number)) {
                $probe = $probes[$probeNumbers[$number]]
                $expect = [string[]]@($probe.expect | ForEach-Object { ConvertTo-QualityFoldedText -Text ([string]$_) } | Where-Object { $_ })
                if ($expect.Length -eq 0) { throw "A probe of $name has no expected fragment." }
                $reject = @()
                if ($probe.PSObject.Properties['reject']) { $reject = @($probe.reject | ForEach-Object { ConvertTo-QualityFoldedText -Text ([string]$_) } | Where-Object { $_ }) }
                $steps.Add(@{ Number = $number; Kind = 'probe'; Text = [string]$probe.question; Topic = [string]$probe.topic; Expect = $expect; Reject = [string[]]$reject })
            }
            else {
                $message = $shared[$next]
                $next++
                $steps.Add(@{ Number = $number; Kind = [string]$message.kind; Text = [string]$message.text; Topic = ''; Expect = [string[]]@(); Reject = [string[]]@() })
            }
        }
        $characters.Add(@{ FirstName = $name; Band = [string]$character.band; Narration = [string]$character.narration; QuestionFrequency = $frequency; Steps = $steps.ToArray() })
    }
    $change = [string]$Document.change_character
    if (@($characters | Where-Object { $_.FirstName -ceq $change }).Length -ne 1) { throw "The change character '$change' is not a character of the script." }
    return @{
        Mode = $Mode; Messages = $shape.Messages; ProbeEnds = [int[]]$shape.ProbeEnds; ChangeAt = $shape.ChangeAt
        SmartCachePairs = $shape.SmartCachePairs; ChangeCharacter = $change; Characters = $characters.ToArray()
    }
}

function Test-QualityProbeAnswer {
    <#
    Returns $true when the folded Text holds one of the Expect fragments and none of the Reject fragments of a probe
    step of Get-QualityConsistencyPlan, and $false otherwise, also for an empty text.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)] $Step,
        [AllowNull()] [AllowEmptyString()] [string]$Text
    )

    $folded = ConvertTo-QualityFoldedText -Text $Text
    if (-not $folded) { return $false }
    $hit = @($Step.Expect | Where-Object { $folded.Contains([string]$_) }).Length -gt 0
    $wrong = @($Step.Reject | Where-Object { $folded.Contains([string]$_) }).Length -gt 0
    return ($hit -and -not $wrong)
}

function Measure-QualityNarrationSpan {
    <#
    Returns the number of narration spans in Text: runs of text between two asterisks on one line, the form N3 removes
    for a "nessuna" character (docs/consistency-engine.md, Reply normalization). 0 for an empty text.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([AllowNull()] [AllowEmptyString()] [string]$Text)

    if (-not $Text) { return 0 }
    return ([regex]::Matches($Text, '\*[^*\r\n]+\*')).Count
}

function Get-QualityMedian {
    <#
    Returns the median of Value (the mean of the two middle values for an even count), or -1 when there is none.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param([double[]]$Value = @())

    $sorted = @($Value | Sort-Object)
    if ($sorted.Length -eq 0) { return -1.0 }
    $middle = [math]::Floor($sorted.Length / 2)
    if ($sorted.Length % 2 -eq 1) { return [double]$sorted[$middle] }
    return ([double]$sorted[$middle - 1] + [double]$sorted[$middle]) / 2
}

function Get-QualityCount {
    # Returns the count Name of Table (a hashtable or a parsed JSON object of check counts), 0 when absent. Not exported.
    param($Table, [string]$Name)

    if ($null -eq $Table) { return 0 }
    if ($Table -is [System.Collections.IDictionary]) {
        if ($Table.Contains($Name)) { return [int]$Table[$Name] }
        return 0
    }
    $property = $Table.PSObject.Properties[$Name]
    if ($null -eq $property) { return 0 }
    return [int]$property.Value
}

function Get-QualityProbeScore {
    # Returns C1 for the probe replies Probe and the replies Reply they belong to: probes, correct, rate, the replies
    # whose delivered text carries a V6 or V10 hit (a wrong name or age), and passed. Not exported.
    param([object[]]$Probe, [object[]]$Reply)

    $probes = @($Probe)
    $correct = @($probes | Where-Object { $_.Correct -eq $true }).Length
    $wrong = @(@($Reply) | Where-Object { $_.Status -eq 'complete' -and ((Get-QualityCount $_.Checks 'V6') -gt 0 -or (Get-QualityCount $_.Checks 'V10') -gt 0) }).Length
    $rate = if ($probes.Length -gt 0) { $correct / $probes.Length } else { 0.0 }
    return [ordered]@{
        probes = $probes.Length; correct = $correct; rate = [math]::Round($rate, 4); wrong_name_or_age = $wrong
        target = 'at least 95 % correct, and no wrong name or age delivered'
        passed = ($probes.Length -gt 0 -and $rate -ge $script:Limit.ProbeRate -and $wrong -eq 0)
    }
}

function Get-QualityLengthScore {
    # Returns C4 for the complete replies Reply: per band, the replies, the median length in tokens against the band's
    # target, the replies N4 cut and their share, and passed for every band. A band without measured lengths fails.
    # Not exported.
    param([object[]]$Reply)

    $bands = [ordered]@{}
    $passed = $true
    foreach ($band in @(@($Reply) | ForEach-Object { [string]$_.Band } | Select-Object -Unique)) {
        $inBand = @(@($Reply) | Where-Object { $_.Band -eq $band })
        $lengths = [double[]]@($inBand | Where-Object { [int]$_.Tokens -ge 0 } | ForEach-Object { [double]$_.Tokens })
        $median = Get-QualityMedian -Value $lengths
        $cut = @($inBand | Where-Object { (Get-QualityCount $_.Normalizer 'N4') -gt 0 }).Length
        $cutRate = if ($inBand.Length -gt 0) { $cut / $inBand.Length } else { 0.0 }
        $target = $script:BandTarget[$band]
        $ok = ($lengths.Length -gt 0 -and $median -le $target -and $cutRate -le $script:Limit.CutRate)
        $bands[$band] = [ordered]@{
            replies = $inBand.Length; measured = $lengths.Length; median_tokens = $median; target_median = $target
            cut = $cut; cut_rate = [math]::Round($cutRate, 4); passed = $ok
        }
        if (-not $ok) { $passed = $false }
    }
    if ($bands.Count -eq 0) { $passed = $false }
    return [ordered]@{ target = 'median within the band target, at most 10 % cut by N4'; bands = $bands; passed = $passed }
}

function Get-QualityConsistencyScore {
    <#
    Returns the scores of T-QLT-05 for ProfileId from Reply, the records of every planned reply of its run. A record is
    @{ Conversation ('main' or 'change'); Character; Number; Kind; Correct (probes); Status; Tokens (-1 unknown); Band;
    Narration; Checks; Normalizer; Spans (delivered narration spans); AfterChange }, where Checks and Normalizer are the
    two count tables of the reply's checks_json.

    The main conversations, all on ProfileId, give C1 (identity probes, with the V6 and V10 hits of every delivered
    reply), C2 (replies with a delivered V11 hit per 200 replies), C3 (V13 per 100), C4 (per band) and C5 (delivered
    narration spans of "nessuna" characters, with N3's removed spans as the model's raw tendency). C6 takes the change
    conversation's replies after the change, which ran on ProfileId: its probes must meet C1 and its replies C4; it is
    not applicable when the run has no change conversation. C7, the owner's blind test, is manual and always
    not_run. `replies` fails when a planned reply did not complete. HardPassed holds only when replies, C1, C2, C4 and
    C6 (when applicable) pass; C3, C5 and C7 are recorded and never decide it.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory = $true)] [string]$ProfileId,
        [object[]]$Reply = @(),
        [string]$ChangeFrom = ''
    )

    $all = @($Reply | Where-Object { $null -ne $_ })
    $main = @($all | Where-Object { $_.Conversation -eq 'main' })
    $complete = @($main | Where-Object { $_.Status -eq 'complete' })
    $notComplete = @($all | Where-Object { $_.Status -ne 'complete' }).Length

    $c1 = Get-QualityProbeScore -Probe @($main | Where-Object { $_.Kind -eq 'probe' }) -Reply $main
    $c1.hard = $true

    $v11 = @($complete | Where-Object { (Get-QualityCount $_.Checks 'V11') -gt 0 }).Length
    $v11Rate = if ($complete.Length -gt 0) { $v11 * 200.0 / $complete.Length } else { 0.0 }
    $c2 = [ordered]@{
        hard = $true; target = 'at most 1 reply with a delivered V11 hit per 200 replies'; replies = $complete.Length; hits = $v11
        per_200 = [math]::Round($v11Rate, 3); passed = ($complete.Length -gt 0 -and $v11Rate -le $script:Limit.V11Per200)
    }
    $v13 = @($complete | Where-Object { (Get-QualityCount $_.Checks 'V13') -gt 0 }).Length
    $v13Rate = if ($complete.Length -gt 0) { $v13 * 100.0 / $complete.Length } else { 0.0 }
    $c3 = [ordered]@{
        hard = $false; target = 'at most 2 replies with a V13 hit per 100 replies'; replies = $complete.Length; hits = $v13
        per_100 = [math]::Round($v13Rate, 3); passed = ($v13Rate -le $script:Limit.V13Per100)
    }
    $c4 = Get-QualityLengthScore -Reply $complete
    $c4.hard = $true

    $silent = @($complete | Where-Object { $_.Narration -eq 'nessuna' })
    $delivered = 0
    $raw = 0
    foreach ($item in $silent) {
        $delivered += [int]$item.Spans
        $raw += Get-QualityCount $item.Normalizer 'N3'
    }
    $c5 = [ordered]@{
        hard = $false; target = 'no narration span delivered for nessuna'; replies = $silent.Length
        delivered_spans = $delivered; removed_spans_n3 = $raw; passed = ($delivered -eq 0)
    }

    $after = @($all | Where-Object { $_.Conversation -eq 'change' -and $_.AfterChange -eq $true })
    if (@($all | Where-Object { $_.Conversation -eq 'change' }).Length -eq 0) {
        $c6 = [ordered]@{ hard = $true; applicable = $false; reason = 'no change conversation ran: the catalog has no other text profile'; passed = $true }
    }
    else {
        $probeAfter = Get-QualityProbeScore -Probe @($after | Where-Object { $_.Kind -eq 'probe' }) -Reply $after
        $lengthAfter = Get-QualityLengthScore -Reply @($after | Where-Object { $_.Status -eq 'complete' })
        $c6 = [ordered]@{
            hard = $true; applicable = $true; change_from = $ChangeFrom; change_to = $ProfileId
            target = 'C1 and C4 still met after the change'; c1 = $probeAfter; c4 = $lengthAfter
            passed = ($probeAfter.passed -and $lengthAfter.passed)
        }
    }
    $c7 = [ordered]@{ hard = $false; status = 'not_run'; reason = "the owner's blind test of 20 replies is manual" }

    $repliesOk = ($all.Length -gt 0 -and $notComplete -eq 0)
    return [ordered]@{
        profile = $ProfileId; replies = $all.Length; not_complete = $notComplete; all_complete = $repliesOk
        C1 = $c1; C2 = $c2; C3 = $c3; C4 = $c4; C5 = $c5; C6 = $c6; C7 = $c7
        HardPassed = ($repliesOk -and $c1.passed -and $c2.passed -and $c4.passed -and $c6.passed)
    }
}

function Get-QualityTrialLaunchLine {
    <#
    Returns Line, the launch lines of a profile's level A (Get-GpuLaunchLine), with each key of Setting set to its value
    and LEVEL=CUSTOM, as a trial of one changed lever: an existing key keeps its place and a new one goes before LEVEL.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string[]]$Line,
        [Parameter(Mandatory = $true)] [hashtable]$Setting
    )

    $values = @{} + $Setting
    $values['LEVEL'] = 'CUSTOM'
    $lines = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($entry in $Line) {
        $key = ($entry -split '=', 2)[0]
        if ($values.ContainsKey($key)) {
            $lines.Add("$key=$($values[$key])")
            $seen[$key] = $true
        }
        else { $lines.Add($entry) }
    }
    $missing = @($values.Keys | Where-Object { -not $seen.ContainsKey($_) -and $_ -ne 'LEVEL' } | Sort-Object)
    $levelAt = -1
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith('LEVEL=')) { $levelAt = $index }
    }
    foreach ($key in $missing) {
        if ($levelAt -lt 0) { $lines.Add("$key=$($values[$key])") }
        else {
            $lines.Insert($levelAt, "$key=$($values[$key])")
            $levelAt++
        }
    }
    if (-not $seen.ContainsKey('LEVEL')) { $lines.Add('LEVEL=CUSTOM') }
    return [string[]]$lines.ToArray()
}

function Get-QualitySmartCacheDecision {
    <#
    Applies the SmartCache rule of ADR 0031 to Trial, the records of the trials SMART_CACHE 0 and 2, each
    @{ Value; ProcessValue (the last_process of /api/extra/perf for every measured chat reply after a structured task,
    in KoboldCpp's own unit: it behaves as milliseconds per prompt token, like last_eval in T-GPU-04, which keeps the
    ratio of the processing times because both trials replay the same prompts); PeakRamMiB (the text
    runtime's highest docker stats amount, -1 unknown); DockerMemoryMiB (docker info, -1 unknown); Problems }.

    Returns Decision 2 when the median last_process falls by at least 50 % from 0 to 2 and the peak RAM of trial 2
    stays at least 4096 MiB below the memory Docker sees, 0 when either fails, and 'undecided' when a trial is missing,
    measured nothing, reported a problem or has an unknown amount, so a decision is never guessed. The record also
    holds both medians, the reduction, the headroom and the reasons. Whether the catalog may carry 2 is the caller's
    question: the validator allows it only for a target-tier profile.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param([object[]]$Trial = @())

    $off = @(@($Trial) | Where-Object { [int]$_.Value -eq 0 }) | Select-Object -First 1
    $on = @(@($Trial) | Where-Object { [int]$_.Value -eq 2 }) | Select-Object -First 1
    $record = [ordered]@{
        rule = 'SMART_CACHE 2 when the median last_process of the first chat reply after a structured task falls by at least 50 % and the peak RAM stays at least 4096 MiB below the memory Docker sees'
        decision = 'undecided'; median_off = -1.0; median_on = -1.0; reduction = $null; peak_ram_mib = -1.0
        docker_memory_mib = -1.0; headroom_mib = $null; reasons = @()
    }
    $reasons = New-Object System.Collections.Generic.List[string]
    # A key named Item would be shadowed by the hashtable's indexer, hence Trial.
    foreach ($pair in @(@{ Name = '0'; Trial = $off }, @{ Name = '2'; Trial = $on })) {
        if ($null -eq $pair.Trial) { $reasons.Add("The trial SMART_CACHE=$($pair.Name) is missing.") }
        elseif (@($pair.Trial.ProcessValue).Length -eq 0) { $reasons.Add("The trial SMART_CACHE=$($pair.Name) measured no reply.") }
        elseif (@($pair.Trial.Problems | Where-Object { $_ }).Length -gt 0) { $reasons.Add("The trial SMART_CACHE=$($pair.Name) reported problems.") }
    }
    if ($reasons.Count -gt 0) {
        $record.reasons = $reasons.ToArray()
        return $record
    }
    $record.median_off = Get-QualityMedian -Value ([double[]]@($off.ProcessValue))
    $record.median_on = Get-QualityMedian -Value ([double[]]@($on.ProcessValue))
    $record.peak_ram_mib = [double]$on.PeakRamMiB
    $record.docker_memory_mib = [double]$on.DockerMemoryMiB
    if ($record.peak_ram_mib -lt 0 -or $record.docker_memory_mib -lt 0) {
        $record.reasons = @('The peak RAM or the memory Docker sees is unknown.')
        return $record
    }
    if ($record.median_off -le 0) {
        $record.reasons = @('The median last_process without SmartCache is not above 0, so no reduction can be computed.')
        return $record
    }
    $reduction = 1.0 - ($record.median_on / $record.median_off)
    $record.reduction = [math]::Round($reduction, 4)
    $record.headroom_mib = [math]::Round($record.docker_memory_mib - $record.peak_ram_mib, 1)
    if ($reduction -lt $script:SmartCacheMinReduction) { $reasons.Add("last_process fell by $([math]::Round($reduction * 100, 1)) %, less than 50 %.") }
    if ($record.headroom_mib -lt $script:SmartCacheHeadroomMiB) { $reasons.Add("The peak RAM is $($record.headroom_mib) MiB below the memory Docker sees, less than 4096 MiB.") }
    $record.decision = if ($reasons.Count -eq 0) { 2 } else { 0 }
    $record.reasons = $reasons.ToArray()
    return $record
}

function Get-QualitySwaPaddingPlan {
    <#
    Returns @{ Applicable; Values; Reason } for TextProfile, a catalog text profile: the SWA padding values 0, 1024 and
    2048 for a "gemma4" profile, the only family with sliding-window attention, and none, with the reason, for any
    other (ADR 0031, ADR 0045: no catalog profile since then).
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)] $TextProfile)

    if ([string]$TextProfile.template -eq 'gemma4') {
        return @{ Applicable = $true; Values = [int[]]$script:SwaPaddingValues; Reason = '' }
    }
    return @{
        Applicable = $false; Values = [int[]]@()
        Reason = "The template '$($TextProfile.template)' has no sliding-window attention; only a gemma4 profile does (ADR 0031, ADR 0045), so SWA_PADDING stays 0."
    }
}

function Get-QualitySwaPaddingDecision {
    <#
    Applies the SWA padding rule of ADR 0031 to Trial, one record per value of Get-QualitySwaPaddingPlan, each
    @{ Value; ProcessValue; MarginMiB; SpeedOk }: among the trials whose median last_process is within 10 % of the best
    median and that keep a margin of at least 512 MiB and the speed rule, it keeps the smallest value. Returns
    @{ decision; best_median; trials; reasons }, decision being 'undecided' when a planned value is missing or
    measured nothing, or when no value qualifies.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param([object[]]$Trial = @())

    $record = [ordered]@{
        rule = 'the smallest SWA_PADDING whose median last_process is within 10 % of the best, with a margin of at least 512 MiB and the speed rule'
        decision = 'undecided'; best_median = -1.0; trials = @(); reasons = @()
    }
    $reasons = New-Object System.Collections.Generic.List[string]
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($value in $script:SwaPaddingValues) {
        $item = @(@($Trial) | Where-Object { [int]$_.Value -eq $value }) | Select-Object -First 1
        if ($null -eq $item -or @($item.ProcessValue).Length -eq 0) {
            $reasons.Add("The trial SWA_PADDING=$value is missing or measured nothing.")
            continue
        }
        $rows.Add([ordered]@{
                value = $value; median = (Get-QualityMedian -Value ([double[]]@($item.ProcessValue)))
                margin_mib = [double]$item.MarginMiB; speed_ok = [bool]$item.SpeedOk
            })
    }
    $record.trials = $rows.ToArray()
    if ($reasons.Count -gt 0) {
        $record.reasons = $reasons.ToArray()
        return $record
    }
    $best = [double](@($rows | ForEach-Object { $_.median } | Measure-Object -Minimum).Minimum)
    $record.best_median = $best
    $qualified = @($rows | Where-Object {
            $_.median -le $best * (1 + $script:SwaTolerance) -and $_.margin_mib -ge $script:MarginMinMiB -and $_.speed_ok
        } | Sort-Object { $_.value })
    if ($qualified.Length -eq 0) {
        $record.reasons = @('No value is within 10 % of the best median with the margin and speed rules met.')
        return $record
    }
    $record.decision = [int]$qualified[0].value
    return $record
}

Export-ModuleMember -Function ConvertTo-QualityFoldedText, Get-QualityTextProfileId, Resolve-QualityArgument, Get-QualitySelection,
    Get-QualityChangeProfile, Get-QualityConsistencyPlan, Test-QualityProbeAnswer, Measure-QualityNarrationSpan, Get-QualityMedian,
    Get-QualityConsistencyScore, Get-QualityTrialLaunchLine, Get-QualitySmartCacheDecision, Get-QualitySwaPaddingPlan,
    Get-QualitySwaPaddingDecision
