# Plans the trials of the measuring and tuning procedure (docs/gpu-orchestration.md) and computes its decisions from the measured trials.
#
# Pure code with no Docker, GPU or Pester call, so every rule is unit-tested (tests/scripts/GpuTuning.Tests.ps1).
# tests/gpu/Tuning.Tests.ps1 runs the trials on the real GPU and passes its records here. A record is an ordered
# dictionary while the run holds it and a parsed JSON object once a results file stores it; every function reads both.
# Dictionaries keyed by a batch size or a MOE_CPU_LAYERS value use string keys: an ordered dictionary indexed with an
# integer reads and writes by position, not by key.

Set-StrictMode -Version Latest

# The version of the trial plan, the workloads and the record fields. A results file of another version is never
# reused, so a change that alters what an existing record means must raise it. Version 2 (ADR 0045): the catalog's
# level A of rocinante-x-12b moved from context 8192 to 12288, so its version-1 records measured other launch values.
$script:ProcedureVersion = 2

# The profiles that P02 measures. The other catalog profiles reuse the result of the profile with the same architecture:
# rocinante-12b-v1.1 takes the values of rocinante-x-12b. The Gemma 4 profiles left the catalog with ADR 0045.
$script:MeasuredProfiles = @('rocinante-x-12b')
# The profile that becomes the default on every tier when a mixture-of-experts default fails the speed rule (step 7).
# No catalog profile is a mixture of experts since ADR 0045, so step 7 has nothing to compare; the rule stays for one.
$script:DefaultFallbackProfile = 'rocinante-x-12b'

# The CONTEXT_SIZE values that docker/llm/entrypoint.sh accepts, in order. A context step is the next value.
$script:ContextSizes = @(4096, 6144, 8192, 12288, 16384, 24576)

# Step 6 compares these batch sizes with 512; step 7 compares AutoFit with these MOE_CPU_LAYERS values.
$script:BatchSizes = @(256, 512, 1024, 2048)
$script:BaselineBatch = 512
$script:MoeCpuLayers = @(8, 12, 16)

# The thresholds of docs/gpu-orchestration.md (Measuring and tuning) and ADR 0030.
$script:Rule = [ordered]@{
    LevelBMarginMiB        = 512
    ContextMarginMiB       = 1024
    BatchGain              = 0.20
    SpeedTokensPerSecond   = 15
    SpeedReplies           = 20
    SpeedReplyTokens       = 200
    FirstTokenSeconds      = 20
    FirstTokenPromptTokens = 6000
    MmapRamSavingMiB       = 2048
    MmapReloadSaving       = 0.20
    MaxSpeedCost           = 0.05
    KvSavingMiB            = 256
}

function Get-TuningValue {
    # Returns the key or property Name of Value, or $null when Value is $null or has neither, for the dictionaries of
    # a run and the parsed JSON of a results file alike, under strict mode. Not exported.
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

function Format-TuningText {
    # Returns Format filled with Argument in the invariant culture, so the reasons in a results file read "4.5", not
    # "4,5", whatever the console's culture is. Not exported.
    param([string]$Format, [object[]]$Argument)

    return [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, $Format, $Argument)
}

function Get-TuningDecision {
    # Returns a decision object: Decided, Value and Reason, then the members of Extra in their order. Not exported.
    param([bool]$Decided, $Value = $null, [string]$Reason, [System.Collections.IDictionary]$Extra = @{})

    $decision = [ordered]@{ Decided = $Decided; Value = $Value; Reason = $Reason }
    foreach ($key in $Extra.Keys) { $decision[[string]$key] = $Extra[$key] }
    return [pscustomobject]$decision
}

function Get-GpuTuningRule {
    <#
    Returns the thresholds of the procedure as an ordered dictionary, with ProcedureVersion first, so a results file
    records the rules that decided it.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param()

    $copy = [ordered]@{ ProcedureVersion = $script:ProcedureVersion }
    foreach ($key in $script:Rule.Keys) { $copy[$key] = $script:Rule[$key] }
    return $copy
}

function Get-GpuTuningProfileId {
    # Returns the identifiers of the profiles that P02 measures, in the order their trials run.
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    return [string[]]$script:MeasuredProfiles
}

function Get-GpuNextContextSize {
    <#
    Returns the allowed CONTEXT_SIZE right above ContextSize: 16384 for the Mistral Nemo profiles at 12288, and 24576
    for a profile at 16384. Throws when ContextSize is not an allowed value or is already the largest.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)] [int]$ContextSize)

    $index = [array]::IndexOf([int[]]$script:ContextSizes, $ContextSize)
    if ($index -lt 0) { throw "The context size $ContextSize is not an allowed CONTEXT_SIZE." }
    if ($index -ge $script:ContextSizes.Length - 1) { throw "The context size $ContextSize has no larger step." }
    return [int]$script:ContextSizes[$index + 1]
}

function Get-TuningTrial {
    # Returns one trial of the plan. Not exported.
    param(
        [string]$ProfileId, [string]$Name, [int]$Step, [System.Collections.Specialized.OrderedDictionary]$Setting,
        [int]$Replies, [int]$Prompts, [switch]$ImageJobFirst, [switch]$MustLoad, [switch]$StopWhenRuleFails
    )

    return [pscustomobject]@{
        Profile           = $ProfileId
        Name              = $Name
        Step              = $Step
        Setting           = $Setting
        Replies           = $Replies
        Prompts           = $Prompts
        ImageJobFirst     = [bool]$ImageJobFirst
        MustLoad          = [bool]$MustLoad
        StopWhenRuleFails = [bool]$StopWhenRuleFails
    }
}

function Get-GpuTuningPlan {
    <#
    Returns the trials of the procedure for each profile of ProfileId (by default the measured P02 profiles) of Catalog,
    the parsed config/model-catalog.json, in the order they run. Each trial has:
      Profile and Name ("A", "context-24576", "batch-1024", "moe-12", "mmap-1", "kv-q8_0", ...);
      Step, the step of the procedure it serves;
      Setting, the launch keys it changes from the catalog's level A, as an ordered dictionary (empty for "A");
      Replies, how many replies of SpeedReplyTokens tokens it generates, and Prompts, how many new long prompts of at
        least FirstTokenPromptTokens tokens it sends;
      ImageJobFirst: it loads its launch values once, runs an image job, then measures the reload that follows;
      MustLoad: its launch values are the catalog's own, so a failed load is a failure, not a result;
      StopWhenRuleFails: its workload serves only the speed rule, so it stops once Get-GpuSpeedRuleStop or a slow
        first token shows the rule cannot hold (the MOE_CPU_LAYERS trials, which can be much slower than AutoFit).
    No trial depends on what another one measured, so a partial run can measure any of them. Only a
    mixture-of-experts profile ("moe": true) gets the MOE_CPU_LAYERS trials. Throws when a profile is missing from the
    catalog or its context has no larger step.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter(Mandatory = $true)] $Catalog,
        [string[]]$ProfileId = $script:MeasuredProfiles
    )

    $plan = New-Object System.Collections.Generic.List[object]
    foreach ($id in $ProfileId) {
        $found = @(@(Get-TuningValue $Catalog 'text') | Where-Object { $null -ne $_ -and (Get-TuningValue $_ 'id') -eq $id })
        if ($found.Length -ne 1) { throw "config/model-catalog.json has no text profile '$id'." }
        $entry = $found[0]
        $next = Get-GpuNextContextSize -ContextSize ([int](Get-TuningValue $entry 'context_size'))
        $batch = [int](Get-TuningValue $entry 'batch_size')
        $replies = [int]$script:Rule.SpeedReplies

        $plan.Add((Get-TuningTrial -ProfileId $id -Name 'A' -Step 2 -Setting ([ordered]@{}) -Replies $replies -Prompts 2 -MustLoad))
        $plan.Add((Get-TuningTrial -ProfileId $id -Name "context-$next" -Step 5 -Setting ([ordered]@{ CONTEXT_SIZE = $next }) -Replies 3 -Prompts 1))
        foreach ($size in $script:BatchSizes) {
            if ($size -eq $batch) { continue }
            $plan.Add((Get-TuningTrial -ProfileId $id -Name "batch-$size" -Step 6 -Setting ([ordered]@{ BATCH_SIZE = $size }) -Replies 0 -Prompts 2))
        }
        if ((Get-TuningValue $entry 'moe') -eq $true) {
            foreach ($layers in $script:MoeCpuLayers) {
                $plan.Add((Get-TuningTrial -ProfileId $id -Name "moe-$layers" -Step 7 -Setting ([ordered]@{ MOE_CPU_LAYERS = $layers }) -Replies $replies -Prompts 2 -StopWhenRuleFails))
            }
        }
        $plan.Add((Get-TuningTrial -ProfileId $id -Name 'mmap-0' -Step 8 -Setting ([ordered]@{ USE_MMAP = 0 }) -Replies 5 -Prompts 0 -ImageJobFirst -MustLoad))
        $plan.Add((Get-TuningTrial -ProfileId $id -Name 'mmap-1' -Step 8 -Setting ([ordered]@{ USE_MMAP = 1 }) -Replies 5 -Prompts 0 -ImageJobFirst))
        $plan.Add((Get-TuningTrial -ProfileId $id -Name 'kv-q8_0' -Step 9 -Setting ([ordered]@{ KV_QUANT = 'q8_0' }) -Replies 5 -Prompts 1))
        $plan.Add((Get-TuningTrial -ProfileId $id -Name "kv-q8_0-context-$next" -Step 9 -Setting ([ordered]@{ KV_QUANT = 'q8_0'; CONTEXT_SIZE = $next }) -Replies 3 -Prompts 1))
    }
    return $plan.ToArray()
}

function Select-GpuTuningTrial {
    <#
    Returns the trials of Plan whose "<profile>/<name>" matches one of Pattern, with the wildcards of -like, in plan
    order, or every trial when Pattern is empty.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject[]])]
    param(
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [object[]]$Plan,
        [string[]]$Pattern = @()
    )

    $patterns = @($Pattern | Where-Object { $_ })
    if ($patterns.Length -eq 0) { return $Plan }
    return @($Plan | Where-Object {
            $key = "$($_.Profile)/$($_.Name)"
            @($patterns | Where-Object { $key -like $_ }).Length -gt 0
        })
}

function Test-GpuTuningSelection {
    <#
    Returns one message for every Pattern that is not "<profile>/<trial>" made of letters, digits, ".", "_", "-" and
    the wildcard "*", and, when Plan is given, for every pattern that selects no trial of it. Returns none when every
    pattern selects a trial.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [AllowEmptyCollection()] [object[]]$Plan = $null,
        [Parameter(Mandatory = $true)] [string[]]$Pattern
    )

    $messages = New-Object System.Collections.Generic.List[string]
    foreach ($item in $Pattern) {
        if ($item -notmatch '^[A-Za-z0-9.*_-]+/[A-Za-z0-9.*_-]+$') {
            $messages.Add("'$item' is not a trial selector of the form <profile>/<trial>, such as rocinante-x-12b/batch-*.")
            continue
        }
        if ($null -ne $Plan -and @(Select-GpuTuningTrial -Plan $Plan -Pattern @($item)).Length -eq 0) {
            $messages.Add("'$item' selects no trial of the tuning plan.")
        }
    }
    return [string[]]$messages.ToArray()
}

function Get-GpuTrialLaunchLine {
    <#
    Returns BaseLine, the KEY=VALUE lines of a level-A launch file, with the value of each key of Setting replaced,
    and LEVEL=CUSTOM when Setting changes anything, because the file is then no longer the catalog's level A. Throws
    when Setting names a key that BaseLine lacks.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string[]]$BaseLine,
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [System.Collections.IDictionary]$Setting
    )

    $lines = [string[]]@($BaseLine)
    $changes = [ordered]@{}
    foreach ($key in $Setting.Keys) { $changes[[string]$key] = [string]$Setting[$key] }
    if ($changes.Count -gt 0 -and -not $changes.Contains('LEVEL')) { $changes['LEVEL'] = 'CUSTOM' }
    foreach ($key in @($changes.Keys)) {
        $index = -1
        for ($position = 0; $position -lt $lines.Length; $position++) {
            if ($lines[$position].StartsWith("$key=")) { $index = $position; break }
        }
        if ($index -lt 0) { throw "The launch lines have no key $key." }
        $lines[$index] = "$key=$($changes[$key])"
    }
    return $lines
}

function Get-GpuSpeedRuleStop {
    <#
    Returns why the speed rule can no longer hold after Reply, the reply records measured so far by a trial that plans
    Planned replies, or an empty string while it still can. It cannot hold once a reply failed or had fewer than
    SpeedReplyTokens tokens, because the trial can then never have Planned long replies, or once more than half of
    Planned replies are under SpeedTokensPerSecond, because the median of Planned values is then under it whatever the
    remaining replies measure. Stopping there never changes a decision of the speed rule.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [object[]]$Reply,
        [Parameter(Mandatory = $true)] [int]$Planned
    )

    $rule = $script:Rule
    $slow = 0
    $position = 0
    foreach ($item in $Reply) {
        $position++
        $tokens = [int](Get-TuningValue $item 'tokens')
        $seconds = [double](Get-TuningValue $item 'seconds')
        if ([int](Get-TuningValue $item 'status') -ne 0 -or $tokens -lt $rule.SpeedReplyTokens -or $seconds -le 0) {
            return "reply $position failed or had fewer than $($rule.SpeedReplyTokens) tokens, so $Planned long replies cannot be reached"
        }
        if ($tokens / $seconds -lt $rule.SpeedTokensPerSecond) { $slow++ }
    }
    if ($slow -gt [math]::Floor($Planned / 2)) {
        return "$slow of the $Planned planned replies are under $($rule.SpeedTokensPerSecond) tokens per second, so their median cannot reach it"
    }
    return ''
}

function Get-GpuMedian {
    # Returns the median of Value, the mean of the two middle values for an even count, or $null when Value is empty.
    [CmdletBinding()]
    [OutputType([double])]
    param([AllowEmptyCollection()] [double[]]$Value = @())

    if ($null -eq $Value -or $Value.Length -eq 0) { return $null }
    $sorted = [double[]]@($Value | Sort-Object)
    $middle = [int][math]::Floor($sorted.Length / 2)
    if ($sorted.Length % 2 -eq 1) { return $sorted[$middle] }
    return ($sorted[$middle - 1] + $sorted[$middle]) / 2
}

function Get-GpuTrialSummary {
    <#
    Returns the numbers that the decisions read from Trial, a record of tests/gpu/Tuning.Tests.ps1, or $null for
    $null:
      Status; Usable: the status is "measured" (it loaded and ran its workload) or "not_loaded" (the runtime did not
        become healthy, which is a result too); an "invalid" record, whose preconditions failed, is not;
      Loaded; PeakMiB; MarginMiB, total minus peak ($null unless loaded); ReadySeconds; RamMiB, the container's memory
        after the workload ($null when unknown);
      Speeds, the tokens per second of every reply that answered, generated tokens over curl's time; LongReplies, how
        many of them have at least SpeedReplyTokens tokens; SlowReplies, how many of those are under
        SpeedTokensPerSecond; MedianSpeed, the median over the long replies;
      Prompts, how many long prompts answered and PromptFailures how many did not; FirstTokenSeconds, the slowest
        answered long prompt, the first one included; PromptTokens, the smallest of them; PromptSpeed, the median prompt
        tokens per second of the answered prompts after the first (of the first alone when only one answered), because
        the first long prompt after a load is processed several times slower (measured on the reference machine:
        7.7 s against 2.1 s for the same 6119 tokens), a warm-up that would hide the difference between batch sizes;
        FirstPromptSpeed, that of the first.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param($Trial)

    if ($null -eq $Trial) { return $null }
    $status = [string](Get-TuningValue $Trial 'status')
    $loaded = ($status -eq 'measured')
    $total = Get-TuningValue $Trial 'total_mib'
    $peak = Get-TuningValue $Trial 'vram_peak_mib'
    $margin = $null
    if ($loaded -and $null -ne $total -and $null -ne $peak) { $margin = [int]$total - [int]$peak }
    $ram = Get-TuningValue $Trial 'ram_after_mib'
    if ($null -ne $ram -and [double]$ram -le 0) { $ram = $null }

    $speeds = New-Object System.Collections.Generic.List[double]
    $longSpeeds = New-Object System.Collections.Generic.List[double]
    foreach ($reply in @(Get-TuningValue $Trial 'replies')) {
        if ($null -eq $reply) { continue }
        $tokens = [int](Get-TuningValue $reply 'tokens')
        $seconds = [double](Get-TuningValue $reply 'seconds')
        if ([int](Get-TuningValue $reply 'status') -ne 0 -or $tokens -le 0 -or $seconds -le 0) { continue }
        $speed = $tokens / $seconds
        $speeds.Add($speed)
        if ($tokens -ge $script:Rule.SpeedReplyTokens) { $longSpeeds.Add($speed) }
    }
    $slowReplies = @($longSpeeds | Where-Object { $_ -lt $script:Rule.SpeedTokensPerSecond }).Length

    $answered = 0
    $failed = 0
    $slowest = $null
    $smallest = $null
    $promptSpeeds = New-Object System.Collections.Generic.List[double]
    foreach ($prompt in @(Get-TuningValue $Trial 'prompts')) {
        if ($null -eq $prompt) { continue }
        $tokens = [int](Get-TuningValue $prompt 'tokens')
        $seconds = [double](Get-TuningValue $prompt 'seconds')
        if ([int](Get-TuningValue $prompt 'status') -ne 0 -or $tokens -le 0 -or $seconds -le 0) { $failed++; continue }
        $answered++
        if ($null -eq $slowest -or $seconds -gt $slowest) { $slowest = $seconds }
        if ($null -eq $smallest -or $tokens -lt $smallest) { $smallest = $tokens }
        $promptSpeeds.Add($tokens / $seconds)
    }
    $firstPromptSpeed = $null
    $warmSpeeds = [double[]]$promptSpeeds.ToArray()
    if ($warmSpeeds.Length -gt 0) { $firstPromptSpeed = $warmSpeeds[0] }
    if ($warmSpeeds.Length -gt 1) { $warmSpeeds = [double[]]@($warmSpeeds | Select-Object -Skip 1) }

    $ready = Get-TuningValue $Trial 'ready_seconds'
    return [pscustomobject]@{
        Status            = $status
        Usable            = (@('measured', 'not_loaded') -contains $status)
        Loaded            = $loaded
        PeakMiB           = $peak
        MarginMiB         = $margin
        ReadySeconds      = if ($null -ne $ready) { [double]$ready } else { $null }
        RamMiB            = $ram
        Speeds            = [double[]]$speeds.ToArray()
        LongReplies       = $longSpeeds.Count
        SlowReplies       = $slowReplies
        MedianSpeed       = Get-GpuMedian -Value $longSpeeds.ToArray()
        Prompts           = $answered
        PromptFailures    = $failed
        FirstTokenSeconds = $slowest
        PromptTokens      = $smallest
        PromptSpeed       = Get-GpuMedian -Value $warmSpeeds
        FirstPromptSpeed  = $firstPromptSpeed
    }
}

function Test-TrialUsable {
    # Returns $true when Summary is a usable record, so a decision can read it. Not exported.
    param($Summary)
    return ($null -ne $Summary -and $Summary.Usable)
}

function Get-GpuLevelDecision {
    <#
    Step 4: returns the default launch level from Baseline, the summary of trial "A". Value is "B" when the margin is
    under LevelBMarginMiB or level A did not load, otherwise "A". Decided is $false, with the reason, when the trial
    is missing or unusable.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param($Baseline)

    if (-not (Test-TrialUsable $Baseline)) { return (Get-TuningDecision -Decided $false -Reason 'The trial A was not measured.') }
    $limit = $script:Rule.LevelBMarginMiB
    if (-not $Baseline.Loaded) {
        return (Get-TuningDecision -Decided $true -Value 'B' -Reason 'Level A did not load, so level B becomes the default; level B itself was not measured.')
    }
    $extra = @{ MarginMiB = $Baseline.MarginMiB }
    if ($Baseline.MarginMiB -lt $limit) {
        return (Get-TuningDecision -Decided $true -Value 'B' -Extra $extra -Reason "The margin at level A is $($Baseline.MarginMiB) MiB, under $limit MiB, so level B becomes the default.")
    }
    return (Get-TuningDecision -Decided $true -Value 'A' -Extra $extra -Reason "The margin at level A is $($Baseline.MarginMiB) MiB, at least $limit MiB, so level A stays.")
}

function Get-GpuContextDecision {
    <#
    Step 5: returns the profile's context. Value is NextContextSize only when the margin of Baseline (trial "A", at
    ContextSize) and the margin of Step (the trial at NextContextSize) are both at least ContextMarginMiB; otherwise
    it is ContextSize. Decided is $false when a trial the answer depends on is missing or unusable.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)] [int]$ContextSize,
        [Parameter(Mandatory = $true)] [int]$NextContextSize,
        $Baseline,
        $Step
    )

    $limit = $script:Rule.ContextMarginMiB
    if (-not (Test-TrialUsable $Baseline)) { return (Get-TuningDecision -Decided $false -Reason 'The trial A was not measured.') }
    if (-not $Baseline.Loaded) { return (Get-TuningDecision -Decided $true -Value $ContextSize -Reason 'Level A did not load, so the context does not grow.') }
    if ($Baseline.MarginMiB -lt $limit) {
        return (Get-TuningDecision -Decided $true -Value $ContextSize -Reason "The margin at $ContextSize is $($Baseline.MarginMiB) MiB, under $limit MiB, so the context does not grow.")
    }
    if (-not (Test-TrialUsable $Step)) {
        return (Get-TuningDecision -Decided $false -Reason "The margin at $ContextSize allows a step, but the trial context-$NextContextSize was not measured.")
    }
    if (-not $Step.Loaded) {
        return (Get-TuningDecision -Decided $true -Value $ContextSize -Reason "The runtime did not load at $NextContextSize, so the context does not grow.")
    }
    $extra = @{ StepMarginMiB = $Step.MarginMiB }
    if ($Step.MarginMiB -lt $limit) {
        return (Get-TuningDecision -Decided $true -Value $ContextSize -Extra $extra -Reason "The margin at $NextContextSize is $($Step.MarginMiB) MiB, under $limit MiB, so the context does not grow.")
    }
    return (Get-TuningDecision -Decided $true -Value $NextContextSize -Extra $extra -Reason "The margins are $($Baseline.MarginMiB) MiB at $ContextSize and $($Step.MarginMiB) MiB at $NextContextSize, both at least $limit MiB, so the context may grow to $NextContextSize.")
}

function Get-GpuBatchDecision {
    <#
    Step 6: returns the batch size from Candidate, an ordered dictionary of batch size (a string key) to trial
    summary that holds "512" and every other size compared. Value is the size with the largest prompt speed gain over
    512 among those that gain at least BatchGain and keep a margin of at least LevelBMarginMiB, or 512 when none does.
    Comparisons lists each size with its prompt speed, gain, margin and whether it qualified. Decided is $false when a
    size is missing or unusable, or 512 has no prompt speed.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory = $true)] [System.Collections.IDictionary]$Candidate)

    $baseSize = [string]$script:BaselineBatch
    $base = $null
    if ($Candidate.Contains($baseSize)) { $base = $Candidate[$baseSize] }
    if (-not (Test-TrialUsable $base) -or -not $base.Loaded -or $null -eq $base.PromptSpeed) {
        return (Get-TuningDecision -Decided $false -Reason "Batch $baseSize has no measured prompt speed.")
    }
    $comparisons = New-Object System.Collections.Generic.List[object]
    $missing = New-Object System.Collections.Generic.List[string]
    $best = $null
    foreach ($size in @($Candidate.Keys)) {
        if ([string]$size -eq $baseSize) { continue }
        $summary = $Candidate[$size]
        if (-not (Test-TrialUsable $summary)) { $missing.Add("batch-$size"); continue }
        $row = [ordered]@{ BatchSize = [int]$size; Loaded = $summary.Loaded; PromptSpeed = $summary.PromptSpeed; Gain = $null; MarginMiB = $summary.MarginMiB; Qualifies = $false }
        if ($summary.Loaded -and $null -ne $summary.PromptSpeed) {
            $row.Gain = [math]::Round($summary.PromptSpeed / $base.PromptSpeed - 1, 4)
            $row.Qualifies = ($row.Gain -ge $script:Rule.BatchGain -and $summary.MarginMiB -ge $script:Rule.LevelBMarginMiB)
        }
        $comparisons.Add([pscustomobject]$row)
        if ($row.Qualifies -and ($null -eq $best -or $row.Gain -gt $best.Gain)) { $best = $row }
    }
    $extra = [ordered]@{ BaselinePromptSpeed = $base.PromptSpeed; Comparisons = $comparisons.ToArray() }
    if ($missing.Count -gt 0) { return (Get-TuningDecision -Decided $false -Extra $extra -Reason "Not measured: $($missing -join ', ').") }
    if ($null -eq $best) {
        $percent = [int]($script:Rule.BatchGain * 100)
        return (Get-TuningDecision -Decided $true -Value ([int]$baseSize) -Extra $extra -Reason "No batch size gains at least $percent % of prompt speed over $baseSize with the margin rule, so $baseSize stays.")
    }
    $gain = [math]::Round($best.Gain * 100, 1)
    return (Get-TuningDecision -Decided $true -Value $best.BatchSize -Extra $extra -Reason "Batch $($best.BatchSize) gains $gain % of prompt speed over $baseSize and keeps a margin of $($best.MarginMiB) MiB.")
}

function Test-GpuSpeedRule {
    <#
    Step 7, the speed rule: returns Decided ($false when Summary is missing or unusable), Value and Met (the same
    boolean), Reason and the numbers. Met is $true only when the runtime loaded, at least SpeedReplies replies of at
    least SpeedReplyTokens tokens have a median of at least SpeedTokensPerSecond, every long prompt answered, and the
    slowest of them, of at least FirstTokenPromptTokens tokens, brought its first token within FirstTokenSeconds.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param($Summary)

    $rule = $script:Rule
    if (-not (Test-TrialUsable $Summary)) { return (Get-TuningDecision -Decided $false -Value $false -Reason 'The trial was not measured.' -Extra @{ Met = $false }) }
    $numbers = [ordered]@{
        Met = $false; MedianTokensPerSecond = $Summary.MedianSpeed; LongReplies = $Summary.LongReplies
        FirstTokenSeconds = $Summary.FirstTokenSeconds; PromptTokens = $Summary.PromptTokens; PromptFailures = $Summary.PromptFailures
    }
    $problems = New-Object System.Collections.Generic.List[string]
    if (-not $Summary.Loaded) { $problems.Add('the runtime did not load') }
    else {
        if ($Summary.LongReplies -lt $rule.SpeedReplies -and $Summary.SlowReplies -gt [math]::Floor($rule.SpeedReplies / 2)) {
            $problems.Add("$($Summary.SlowReplies) replies are under $($rule.SpeedTokensPerSecond) tokens per second, so the median of $($rule.SpeedReplies) cannot reach it (the trial stopped there)")
        }
        elseif ($Summary.LongReplies -lt $rule.SpeedReplies) {
            $problems.Add("only $($Summary.LongReplies) replies of at least $($rule.SpeedReplyTokens) tokens, not $($rule.SpeedReplies)")
        }
        elseif ($Summary.MedianSpeed -lt $rule.SpeedTokensPerSecond) {
            $problems.Add((Format-TuningText 'the median is {0:0.0} tokens per second, under {1}' @($Summary.MedianSpeed, $rule.SpeedTokensPerSecond)))
        }
        if ($Summary.PromptFailures -gt 0) { $problems.Add("$($Summary.PromptFailures) long prompt(s) did not answer") }
        if ($Summary.Prompts -eq 0) { $problems.Add('no long prompt answered') }
        elseif ($Summary.PromptTokens -lt $rule.FirstTokenPromptTokens) {
            $problems.Add("the long prompt has $($Summary.PromptTokens) tokens, under $($rule.FirstTokenPromptTokens)")
        }
        elseif ($Summary.FirstTokenSeconds -gt $rule.FirstTokenSeconds) {
            $problems.Add((Format-TuningText 'the first token took {0:0.00} s, over {1} s' @($Summary.FirstTokenSeconds, $rule.FirstTokenSeconds)))
        }
    }
    if ($problems.Count -gt 0) {
        return (Get-TuningDecision -Decided $true -Value $false -Extra $numbers -Reason "The speed rule fails: $($problems -join '; ').")
    }
    $numbers.Met = $true
    $reason = Format-TuningText 'The speed rule holds: median {0:0.0} tokens per second over {1} replies of at least {2} tokens, first token of a {3}-token prompt in {4:0.00} s.' @(
        $Summary.MedianSpeed, $Summary.LongReplies, $rule.SpeedReplyTokens, $Summary.PromptTokens, $Summary.FirstTokenSeconds)
    return (Get-TuningDecision -Decided $true -Value $true -Extra $numbers -Reason $reason)
}

function Get-GpuMoeDecision {
    <#
    Step 7: returns MOE_CPU_LAYERS for the profile. For a dense profile (Moe $false) Value is 0 and Applicable is
    $false. Otherwise Setting is an ordered dictionary of MOE_CPU_LAYERS value (a string key, "0" for AutoFit) to trial
    summary. A setting qualifies when it loaded with a margin of at least LevelBMarginMiB and meets the speed rule;
    Value is the qualifying setting with the highest median speed (the first in Setting's order on a tie), and
    SpeedRuleMet is $true. When none qualifies, SpeedRuleMet is $false, DefaultProfile is FallbackProfile (the profile
    that becomes the default on every tier), and Value is the fastest setting that keeps the margin rule, or 0.
    Settings lists every setting with its numbers. Decided is $false when a setting is missing or unusable.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)] [bool]$Moe,
        [System.Collections.IDictionary]$Setting = @{},
        [string]$FallbackProfile = $script:DefaultFallbackProfile
    )

    if (-not $Moe) {
        return (Get-TuningDecision -Decided $true -Value 0 -Extra ([ordered]@{ Applicable = $false; SpeedRuleMet = $null }) -Reason 'Not a mixture-of-experts profile: MOE_CPU_LAYERS stays 0.')
    }
    $rows = New-Object System.Collections.Generic.List[object]
    $missing = New-Object System.Collections.Generic.List[string]
    $kept = $null
    $marginOnly = $null
    foreach ($layers in @($Setting.Keys)) {
        $summary = $Setting[$layers]
        $label = if ([int]$layers -eq 0) { 'AutoFit' } else { "MOE_CPU_LAYERS $layers" }
        if (-not (Test-TrialUsable $summary)) { $missing.Add($label); continue }
        $speed = Test-GpuSpeedRule -Summary $summary
        $marginOk = ($summary.Loaded -and $summary.MarginMiB -ge $script:Rule.LevelBMarginMiB)
        $row = [pscustomobject][ordered]@{
            MoeCpuLayers = [int]$layers; Label = $label; Loaded = $summary.Loaded; MarginMiB = $summary.MarginMiB
            MarginRule = $marginOk; MedianTokensPerSecond = $summary.MedianSpeed; LongReplies = $summary.LongReplies
            FirstTokenSeconds = $summary.FirstTokenSeconds; PromptTokens = $summary.PromptTokens
            SpeedRule = [bool]$speed.Met; SpeedReason = $speed.Reason
        }
        $rows.Add($row)
        if ($marginOk -and ($null -eq $marginOnly -or $summary.MedianSpeed -gt $marginOnly.MedianTokensPerSecond)) { $marginOnly = $row }
        if ($marginOk -and $speed.Met -and ($null -eq $kept -or $summary.MedianSpeed -gt $kept.MedianTokensPerSecond)) { $kept = $row }
    }
    $extra = [ordered]@{ Applicable = $true; SpeedRuleMet = $false; DefaultProfile = $null; Settings = $rows.ToArray() }
    if ($missing.Count -gt 0) { return (Get-TuningDecision -Decided $false -Extra $extra -Reason "Not measured: $($missing -join ', ').") }
    if ($null -ne $kept) {
        $extra.SpeedRuleMet = $true
        $reason = Format-TuningText '{0} is the fastest setting that keeps the margin rule and meets the speed rule: median {1:0.0} tokens per second, first token in {2:0.00} s.' @(
            $kept.Label, $kept.MedianTokensPerSecond, $kept.FirstTokenSeconds)
        return (Get-TuningDecision -Decided $true -Value $kept.MoeCpuLayers -Extra $extra -Reason $reason)
    }
    $extra.DefaultProfile = $FallbackProfile
    $value = if ($null -ne $marginOnly) { $marginOnly.MoeCpuLayers } else { 0 }
    return (Get-TuningDecision -Decided $true -Value $value -Extra $extra -Reason "No setting meets both the margin rule and the speed rule, so $FallbackProfile becomes the default on every tier.")
}

function Get-GpuMmapDecision {
    <#
    Step 8: returns USE_MMAP from Off and On, the summaries of the reloads after an image job with USE_MMAP 0 and 1.
    Value is 1 when On loaded, saves at least MmapRamSavingMiB of RAM or MmapReloadSaving of the reload time, and
    costs at most MaxSpeedCost of the median speed; otherwise 0. Decided is $false when a trial is missing or
    unusable, Off did not load, or a number is missing.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param($Off, $On)

    $rule = $script:Rule
    if (-not (Test-TrialUsable $Off) -or -not (Test-TrialUsable $On)) {
        return (Get-TuningDecision -Decided $false -Reason 'The trials mmap-0 and mmap-1 were not both measured.')
    }
    if (-not $Off.Loaded) { return (Get-TuningDecision -Decided $false -Reason 'USE_MMAP=0, the catalog value, did not load.') }
    if (-not $On.Loaded) { return (Get-TuningDecision -Decided $true -Value 0 -Reason 'USE_MMAP=1 did not load, so 0 stays.') }
    if ($null -eq $Off.RamMiB -or $null -eq $On.RamMiB -or $null -eq $Off.MedianSpeed -or $null -eq $On.MedianSpeed -or
        -not $Off.ReadySeconds -or $null -eq $On.ReadySeconds) {
        return (Get-TuningDecision -Decided $false -Reason 'A RAM, reload time or speed figure is missing.')
    }
    $ramSaving = [math]::Round($Off.RamMiB - $On.RamMiB, 1)
    $reloadSaving = [math]::Round(($Off.ReadySeconds - $On.ReadySeconds) / $Off.ReadySeconds, 4)
    $speedCost = [math]::Round(1 - $On.MedianSpeed / $Off.MedianSpeed, 4)
    $extra = [ordered]@{ RamSavingMiB = $ramSaving; ReloadSaving = $reloadSaving; SpeedCost = $speedCost }
    $saves = ($ramSaving -ge $rule.MmapRamSavingMiB -or $reloadSaving -ge $rule.MmapReloadSaving)
    $text = Format-TuningText 'USE_MMAP=1 saves {0:0.0} MiB of RAM and {1:0.0} % of the reload time, at a speed cost of {2:0.0} %' @(
        $ramSaving, ($reloadSaving * 100), ($speedCost * 100))
    if ($saves -and $speedCost -le $rule.MaxSpeedCost) { return (Get-TuningDecision -Decided $true -Value 1 -Extra $extra -Reason "$text, so 1 is kept.") }
    return (Get-TuningDecision -Decided $true -Value 0 -Extra $extra -Reason "$text, so 0 stays.")
}

function Get-GpuKvDecision {
    <#
    Step 9: returns KV_QUANT from Baseline (trial "A", f16), Quantized (q8_0 at the same context) and QuantizedNext
    (q8_0 at the next context step). q8_0 qualifies on the measurements when it loaded, costs at most MaxSpeedCost of
    the median speed, and either lowers the peak by at least KvSavingMiB or allows the next context step: the context
    does not grow with f16 (ContextGrows $false), and the margins of Quantized and QuantizedNext are both at least
    ContextMarginMiB. Value is "q8_0" only when it qualifies and ProtocolScoredNoLower is $true, the P04 Italian
    protocol having scored it no lower than f16; until then "f16" stays (ADR 0030). QualifiesOnMeasurements gives the
    measured part. Decided is $false when a trial the answer depends on is missing or a median speed is missing.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        $Baseline,
        $Quantized,
        $QuantizedNext,
        [Parameter(Mandatory = $true)] [bool]$ContextGrows,
        $ProtocolScoredNoLower = $null
    )

    $rule = $script:Rule
    $none = @{ QualifiesOnMeasurements = $false }
    if (-not (Test-TrialUsable $Baseline) -or -not $Baseline.Loaded) {
        return (Get-TuningDecision -Decided $false -Extra $none -Reason 'The trial A was not measured or did not load.')
    }
    if (-not (Test-TrialUsable $Quantized)) { return (Get-TuningDecision -Decided $false -Extra $none -Reason 'The trial kv-q8_0 was not measured.') }
    if (-not $Quantized.Loaded) { return (Get-TuningDecision -Decided $true -Value 'f16' -Extra $none -Reason 'q8_0 did not load, so f16 stays.') }
    if ($null -eq $Baseline.MedianSpeed -or $null -eq $Quantized.MedianSpeed) {
        return (Get-TuningDecision -Decided $false -Extra $none -Reason 'A median speed is missing.')
    }

    $frees = [int]$Baseline.PeakMiB - [int]$Quantized.PeakMiB
    $speedCost = [math]::Round(1 - $Quantized.MedianSpeed / $Baseline.MedianSpeed, 4)
    $allowsNext = $false
    $nextNote = 'the context already grows with f16'
    if (-not $ContextGrows) {
        $nextNote = "the margin with q8_0 is $($Quantized.MarginMiB) MiB, under $($rule.ContextMarginMiB) MiB, so it allows no context step"
        if ($Quantized.MarginMiB -ge $rule.ContextMarginMiB) {
            if (-not (Test-TrialUsable $QuantizedNext)) {
                return (Get-TuningDecision -Decided $false -Extra $none -Reason 'The trial of q8_0 at the next context step was not measured.')
            }
            if (-not $QuantizedNext.Loaded) { $nextNote = 'q8_0 did not load at the next context step' }
            elseif ($QuantizedNext.MarginMiB -lt $rule.ContextMarginMiB) {
                $nextNote = "the margin with q8_0 at the next context step is $($QuantizedNext.MarginMiB) MiB, under $($rule.ContextMarginMiB) MiB"
            }
            else {
                $allowsNext = $true
                $nextNote = "q8_0 keeps $($QuantizedNext.MarginMiB) MiB at the next context step, which f16 does not allow"
            }
        }
    }
    $qualifies = (($frees -ge $rule.KvSavingMiB -or $allowsNext) -and $speedCost -le $rule.MaxSpeedCost)
    $extra = [ordered]@{ QualifiesOnMeasurements = $qualifies; FreesMiB = $frees; AllowsNextContext = $allowsNext; SpeedCost = $speedCost }
    $text = Format-TuningText 'q8_0 frees {0} MiB at its peak and costs {1:0.0} % of speed; {2}' @($frees, ($speedCost * 100), $nextNote)
    if (-not $qualifies) { return (Get-TuningDecision -Decided $true -Value 'f16' -Extra $extra -Reason "$text, so f16 stays.") }
    if ($ProtocolScoredNoLower -eq $true) {
        return (Get-TuningDecision -Decided $true -Value 'q8_0' -Extra $extra -Reason "$text, and the Italian protocol scored it no lower, so q8_0 is kept.")
    }
    return (Get-TuningDecision -Decided $true -Value 'f16' -Extra $extra -Reason "$text. It qualifies on the measurements, but the P04 Italian protocol has not scored q8_0, so f16 stays (ADR 0030).")
}

function Get-GpuTuningDecision {
    <#
    Returns every decision of the procedure for TextProfile, a catalog entry, from Trial, a dictionary of trial name
    to record: Level (step 4), Context (5), Batch (6), Moe (7), Mmap (8) and Kv (9), each with Decided, Value and
    Reason; SpeedRule (T-GPU-14) when IsDefault, else $null: for a mixture-of-experts profile the outcome of step 7
    (Met, the kept setting as Value, DefaultProfile and every setting), for a dense one Test-GpuSpeedRule on trial
    "A"; and Complete, $true when every decision was made. Missing trials leave the decisions that need them
    undecided, never guessed.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)] $TextProfile,
        [Parameter(Mandatory = $true)] [AllowEmptyCollection()] [System.Collections.IDictionary]$Trial,
        [switch]$IsDefault,
        [string]$FallbackProfile = $script:DefaultFallbackProfile,
        $ProtocolScoredNoLower = $null
    )

    $summaries = @{}
    foreach ($key in $Trial.Keys) { $summaries[[string]$key] = Get-GpuTrialSummary -Trial $Trial[$key] }
    $find = { param([string]$Name) if ($summaries.ContainsKey($Name)) { $summaries[$Name] } else { $null } }

    $context = [int](Get-TuningValue $TextProfile 'context_size')
    $next = Get-GpuNextContextSize -ContextSize $context
    $catalogBatch = [int](Get-TuningValue $TextProfile 'batch_size')
    $moe = ((Get-TuningValue $TextProfile 'moe') -eq $true)
    $baseline = & $find 'A'

    $level = Get-GpuLevelDecision -Baseline $baseline
    $contextDecision = Get-GpuContextDecision -ContextSize $context -NextContextSize $next -Baseline $baseline -Step (& $find "context-$next")

    $batches = [ordered]@{}
    foreach ($size in $script:BatchSizes) {
        if ($size -eq $catalogBatch) { $batches["$size"] = $baseline } else { $batches["$size"] = & $find "batch-$size" }
    }
    $batch = Get-GpuBatchDecision -Candidate $batches

    $settings = [ordered]@{ '0' = $baseline }
    if ($moe) { foreach ($layers in $script:MoeCpuLayers) { $settings["$layers"] = & $find "moe-$layers" } }
    $moeDecision = Get-GpuMoeDecision -Moe $moe -Setting $settings -FallbackProfile $FallbackProfile

    $speedRule = $null
    if ($IsDefault) {
        if ($moe) {
            $speedRule = [pscustomobject][ordered]@{
                Decided = $moeDecision.Decided; Value = $moeDecision.Value; Reason = $moeDecision.Reason
                Met = [bool]$moeDecision.SpeedRuleMet; DefaultProfile = $moeDecision.DefaultProfile; Settings = $moeDecision.Settings
            }
        }
        else { $speedRule = Test-GpuSpeedRule -Summary $baseline }
    }

    $mmap = Get-GpuMmapDecision -Off (& $find 'mmap-0') -On (& $find 'mmap-1')
    if ($contextDecision.Decided) {
        $kv = Get-GpuKvDecision -Baseline $baseline -Quantized (& $find 'kv-q8_0') -QuantizedNext (& $find "kv-q8_0-context-$next") `
            -ContextGrows ($contextDecision.Value -ne $context) -ProtocolScoredNoLower $ProtocolScoredNoLower
    }
    else {
        $kv = Get-TuningDecision -Decided $false -Extra @{ QualifiesOnMeasurements = $false } -Reason 'The context step is not decided, so whether q8_0 allows it is unknown.'
    }

    $all = @($level, $contextDecision, $batch, $moeDecision, $mmap, $kv)
    if ($null -ne $speedRule) { $all += $speedRule }
    return [pscustomobject][ordered]@{
        Profile   = [string](Get-TuningValue $TextProfile 'id')
        Complete  = (@($all | Where-Object { -not $_.Decided }).Length -eq 0)
        Level     = $level
        Context   = $contextDecision
        Batch     = $batch
        Moe       = $moeDecision
        SpeedRule = $speedRule
        Mmap      = $mmap
        Kv        = $kv
    }
}

function Get-GpuEarlierTrial {
    <#
    Returns the newest record of each "<profile>/<trial>" in the results files tuning-*.json of Folder, as a hashtable
    of that key to the record with "source_file" added, keeping only records whose status is "measured" or
    "not_loaded" and that list no problem (so their own test passed), from files of ProcedureVersion measured on the
    GPU named GpuName. Files are taken newest first by their name, which holds their start time. Returns an empty
    hashtable when Folder is missing; a file that is not JSON is skipped.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Folder,
        [Parameter(Mandatory = $true)] [string]$GpuName,
        [int]$ProcedureVersion = $script:ProcedureVersion
    )

    $found = @{}
    if (-not (Test-Path -LiteralPath $Folder -PathType Container)) { return $found }
    $files = @(Get-ChildItem -LiteralPath $Folder -Filter 'tuning-*.json' -File | Sort-Object -Property Name -Descending)
    foreach ($file in $files) {
        try { $document = [System.IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json }
        catch { Write-Verbose "Skipped $($file.Name): not JSON."; continue }
        if ([int](Get-TuningValue $document 'procedure_version') -ne $ProcedureVersion) { continue }
        if ([string](Get-TuningValue $document 'gpu') -ne $GpuName) { continue }
        foreach ($record in @(Get-TuningValue $document 'trials')) {
            if ($null -eq $record) { continue }
            if (@('measured', 'not_loaded') -notcontains [string](Get-TuningValue $record 'status')) { continue }
            if (@(@(Get-TuningValue $record 'problems') | Where-Object { $_ }).Length -gt 0) { continue }
            $key = '{0}/{1}' -f (Get-TuningValue $record 'profile'), (Get-TuningValue $record 'trial')
            if ($found.ContainsKey($key)) { continue }
            $record | Add-Member -NotePropertyName 'source_file' -NotePropertyValue $file.Name -Force
            $found[$key] = $record
        }
    }
    return $found
}

Export-ModuleMember -Function Get-GpuTuningRule, Get-GpuTuningProfileId, Get-GpuNextContextSize, Get-GpuTuningPlan,
    Select-GpuTuningTrial, Test-GpuTuningSelection, Get-GpuTrialLaunchLine, Get-GpuSpeedRuleStop, Get-GpuMedian, Get-GpuTrialSummary,
    Get-GpuLevelDecision, Get-GpuContextDecision, Get-GpuBatchDecision, Test-GpuSpeedRule, Get-GpuMoeDecision,
    Get-GpuMmapDecision, Get-GpuKvDecision, Get-GpuTuningDecision, Get-GpuEarlierTrial
