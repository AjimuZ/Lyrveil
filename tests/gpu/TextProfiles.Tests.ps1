# T-GPU-02 to T-GPU-04: the catalog text profiles load at launch level A on the real GPU, tokenize, stream and abort, in the project lyrveil-gpu-test.
#
# Run only by "scripts\lyrveil.cmd test gpu", after Passthrough.Tests.ps1. For each profile of $script:ProfileRuns, in
# order, it writes the level-A launch file of the catalog into lyrveil-gpu-test-data, starts the real lyrveil-llm, and:
#   T-GPU-02 (both catalog text profiles, docs/gpu-orchestration.md, Measuring and tuning): reads NVML before and
#     after the load, the container's RAM from "docker stats", and the VRAM peak over a few generations, and applies the
#     margin rule. rocinante-12b-v1.1 takes the launch values GPU.3 decided for rocinante-x-12b, the same architecture,
#     so this is where its margin at those values is checked.
#   T-GPU-03: tokenizes the family's chat prompt of backend/tests/fixtures/tokenization/prompts.json and compares the
#     identifiers with <family>.tokens.json, which it records when the file is missing (the first run, or a family whose
#     fixture was deleted on purpose). backend/tests/unit/test_tokenization_fixtures.py ties both files to the formatter.
#     No catalog profile is of the gemma4 family since ADR 0045, so gemma4.tokens.json, recorded with gemma-4-12b by
#     GPU.2, is no longer compared here.
#   T-GPU-04: streams a chat reply and checks the event stream and the token counts, then aborts a long generation.
# Every request and measurement runs in the profile's BeforeAll, so the numbers are recorded even when a check fails.
# test-results/gpu/text-profiles-<time>.json, ignored by Git, holds them all. Only one text runtime runs at a time, and
# each profile starts only after the previous one is removed and its VRAM released.

BeforeDiscovery {
    $script:ProfileRuns = @(
        @{ Id = 'rocinante-x-12b'; Family = 'mistral_v3_tekken'; Measure = $true; Label = 'T-GPU-02' }
        @{ Id = 'rocinante-12b-v1.1'; Family = 'chatml'; Measure = $true; Label = 'T-GPU-02' }
    )
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:FixtureFolder = Join-Path $script:Root 'backend\tests\fixtures\tokenization'
    $script:Prompts = [System.IO.File]::ReadAllText((Join-Path $script:FixtureFolder 'prompts.json'), $script:Utf8) | ConvertFrom-Json
    $script:Results = New-Object System.Collections.Generic.List[object]
    $script:StartedAt = Get-Date
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\text-profiles-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))

    # The size of the run. docs/gpu-orchestration.md asks for 20 generations for the peak; a few keep the level inside
    # its time budget, and the results file says how many ran.
    $script:GenerationCount = 3
    $script:GenerationLength = 128
    $script:ReplyLength = 160
    $script:AbortLength = 600
    $script:AbortAfter = 6
    $script:LoadTimeoutSecond = 240

    # The identifiers that the control texts of each family must become (docs/runtime-contracts.md, Text model facts),
    # and the chatml markers, which are ordinary text in that tokenizer (ADR 0014).
    $script:ControlTokens = @{
        mistral_v3_tekken = @{ '[INST]' = 3; '[/INST]' = 4; '</s>' = 2 }
        chatml            = @{}
    }
    $script:PlainMarkers = @{ mistral_v3_tekken = @(); chatml = @('<|im_start|>', '<|im_end|>') }

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
        param($Value, [string]$Name)
        if ($null -eq $Value) { return $null }
        $property = $Value.PSObject.Properties[$Name]
        if ($null -eq $property) { return $null }
        return $property.Value
    }

    function Get-Sha256Hex {
        # Returns the lowercase SHA-256 of the UTF-8 bytes of Text, as Python's hashlib gives it.
        param([string]$Text)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { return (($sha.ComputeHash($script:Utf8.GetBytes($Text)) | ForEach-Object { $_.ToString('x2') }) -join '') }
        finally { $sha.Dispose() }
    }

    function Get-Occurrence {
        # Returns how often Needle appears in Text.
        param([string]$Text, [string]$Needle)
        return [regex]::Matches($Text, [regex]::Escape($Needle)).Count
    }

    function Get-TokenId {
        # Returns the identifiers of POST /api/extra/tokencount for Text, with what "special" adds when Special, or
        # $null when the request failed.
        param([string]$Text, [bool]$Special)
        $answer = Invoke-GpuTextPost -Root $script:Root -Path '/api/extra/tokencount' -Body @{ prompt = $Text; special = $Special } -TimeoutSecond 30
        $ids = Get-Field $answer.Body 'ids'
        if ($answer.Status -ne 0 -or $null -eq $ids) { return $null }
        return , ([long[]]@($ids))
    }

    function Get-Perf {
        # Returns the parsed answer of GET /api/extra/perf, or $null.
        return (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/perf').Body
    }

    function Get-EvalSpeed {
        # Returns the generation speed of the last request in tokens per second, from last_eval of Perf read as
        # milliseconds per generated token, or 0 without a value. The first run showed that unit: 66 tokens streamed
        # within about 1.1 s while last_eval was 21.3, which is not a time in seconds.
        param($Perf)
        $msPerToken = [double](Get-Field $Perf 'last_eval')
        if ($msPerToken -le 0) { return 0 }
        return [math]::Round(1000 / $msPerToken, 1)
    }

    function Get-GenerationBody {
        # Returns a GenerationInput for Prompt at the profile's context with a fresh genkey. With NoEnd it has no stop
        # sequence and bans the end token, so only max_length or an abort can end it.
        param([string]$Prompt, [string[]]$Stop = @(), [int]$Length, [int]$Seed, [switch]$NoEnd)
        $body = @{
            prompt = $Prompt; max_context_length = [int]$script:TextProfile.context_size; max_length = $Length
            temperature = 0.8; top_p = 0.95; top_k = 64; rep_pen = 1.05; sampler_seed = $Seed; trim_stop = $true
            genkey = 'gputest' + [guid]::NewGuid().ToString('N').Substring(0, 12)
        }
        if ($NoEnd) { $body.use_default_badwordsids = $true }
        else { $body.stop_sequence = [string[]]@($Stop) }
        return $body
    }

    function Get-TuningOutcome {
        # Returns the outcome of steps 4 and 5 of the margin rule (docs/gpu-orchestration.md, Measuring and tuning).
        param([int]$MarginMiB)
        if ($MarginMiB -lt 512) { return 'level B becomes the default: the margin is under 512 MiB' }
        if ($MarginMiB -ge 1024) {
            return 'level A stays; the context may grow one step only if a load at the larger context keeps 1024 MiB, which this run did not measure'
        }
        return 'level A stays: the margin is between 512 and 1023 MiB'
    }

    function Save-TokenFixture {
        # Writes the recorded tokenization of the family's chat prompt to Path, UTF-8 without BOM.
        param([string]$Path, [string]$Family, [string]$Sha, [long[]]$Ids, [long[]]$BosIds, [string]$Version)
        $lines = @(
            '{'
            ('  "family": "{0}",' -f $Family)
            ('  "profile": "{0}",' -f $script:TextProfile.id)
            ('  "model_file": "{0}",' -f $script:TextProfile.file)
            ('  "runtime": "koboldcpp {0}",' -f $Version)
            ('  "recorded": "{0}",' -f (Get-Date -Format 'yyyy-MM-dd'))
            ('  "prompt_sha256": "{0}",' -f $Sha)
            '  "special": false,'
            ('  "count": {0},' -f $Ids.Length)
            ('  "bos_ids": [{0}],' -f ($BosIds -join ', '))
            ('  "ids": [{0}]' -f ($Ids -join ', '))
            '}'
        )
        [System.IO.File]::WriteAllText($Path, (($lines -join "`n") + "`n"), $script:Utf8)
    }

    function Measure-Tokenization {
        # T-GPU-03: tokenizes the family's chat prompt and returns what was observed and every problem found.
        param([string]$Family, [string]$Version)
        $prompt = [string]$script:Fixture.prompt
        $sha = Get-Sha256Hex $prompt
        $record = [ordered]@{ prompt_sha256 = $sha; count = 0; bos_ids = @(); fixture = ''; problems = @() }
        $problems = New-Object System.Collections.Generic.List[string]
        $ids = Get-TokenId -Text $prompt -Special $false
        $withSpecial = Get-TokenId -Text $prompt -Special $true
        if ($null -eq $ids -or $ids.Length -eq 0) {
            $problems.Add('POST /api/extra/tokencount gave no identifiers for the prompt.')
            $record.problems = $problems.ToArray()
            return $record
        }
        $record.count = $ids.Length
        foreach ($entry in $script:ControlTokens[$Family].GetEnumerator()) {
            $expected = Get-Occurrence $prompt $entry.Key
            $found = @($ids | Where-Object { $_ -eq $entry.Value }).Length
            $record["id_$($entry.Value)"] = $found
            if ($found -ne $expected) { $problems.Add("$($entry.Key) appears $expected times but id $($entry.Value) $found times.") }
        }
        foreach ($marker in $script:PlainMarkers[$Family]) {
            $alone = Get-TokenId -Text $marker -Special $false
            $length = if ($null -eq $alone) { 0 } else { $alone.Length }
            $record["tokens_of_$marker"] = $length
            if ($length -lt 2) { $problems.Add("$marker is $length token(s), not ordinary text.") }
        }
        if ($null -eq $withSpecial -or $withSpecial.Length -lt $ids.Length) {
            $problems.Add('The tokenization with "special" is missing or shorter than without it.')
        }
        else {
            $extra = $withSpecial.Length - $ids.Length
            $record.bos_ids = [long[]]@($withSpecial | Select-Object -First $extra)
            $tail = @($withSpecial | Select-Object -Skip $extra)
            if (($tail -join ',') -ne ($ids -join ',')) { $problems.Add('With "special" the identifiers differ beyond a leading prefix.') }
        }
        $path = Join-Path $script:FixtureFolder "$Family.tokens.json"
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $saved = [System.IO.File]::ReadAllText($path, $script:Utf8) | ConvertFrom-Json
            $record.fixture = "compared with $Family.tokens.json, recorded with $($saved.profile)"
            if ($saved.prompt_sha256 -ne $sha) { $problems.Add("$Family.tokens.json was recorded for another prompt.") }
            elseif ((@($saved.ids) -join ',') -ne ($ids -join ',')) {
                $problems.Add("The identifiers differ from $Family.tokens.json ($($saved.count) recorded, $($ids.Length) now).")
            }
        }
        elseif ($problems.Count -eq 0) {
            Save-TokenFixture -Path $path -Family $Family -Sha $sha -Ids $ids -BosIds ([long[]]$record.bos_ids) -Version $Version
            $record.fixture = "recorded into $Family.tokens.json"
        }
        else { $record.fixture = 'not recorded, because of the problems' }
        $record.problems = $problems.ToArray()
        return $record
    }

    function Measure-Stream {
        # T-GPU-04: streams a chat reply and returns what was observed and every problem found.
        $body = Get-GenerationBody -Prompt ([string]$script:Fixture.prompt) -Stop ([string[]]@($script:Fixture.stop_sequences)) -Length $script:ReplyLength -Seed 7
        $stream = Invoke-GpuTextStream -Root $script:Root -Body $body -TimeoutSecond 120
        $perf = Get-Perf
        $tokens = @($stream.Events | Where-Object { $null -eq $_.FinishReason })
        $finals = @($stream.Events | Where-Object { $null -ne $_.FinishReason })
        $text = ($tokens | ForEach-Object { $_.Token }) -join ''
        $counted = Get-TokenId -Text $text -Special $false
        $generated = [int](Get-Field $perf 'last_token_count')
        $record = [ordered]@{
            curl_status        = $stream.Status
            events             = $stream.Events.Length
            token_events       = $tokens.Length
            finish_reason      = if ($finals.Length -gt 0) { [string]$finals[-1].FinishReason } else { '' }
            first_token_second = if ($tokens.Length -gt 0) { [math]::Round($tokens[0].Second, 2) } else { 0 }
            token_span_second  = if ($tokens.Length -gt 1) { [math]::Round($tokens[-1].Second - $tokens[0].Second, 2) } else { 0 }
            distinct_arrivals  = @($tokens | ForEach-Object { [math]::Round($_.Second, 2) } | Sort-Object -Unique).Length
            last_token_count   = $generated
            retokenized_count  = if ($null -eq $counted) { -1 } else { $counted.Length }
            perf_last_process  = Get-Field $perf 'last_process'
            perf_last_eval     = Get-Field $perf 'last_eval'
            tokens_per_second  = Get-EvalSpeed $perf
            perf_stop_reason   = Get-Field $perf 'stop_reason'
            control_text_found = @($script:Fixture.control_texts | Where-Object { $text.Contains([string]$_) })
            problems           = @()
        }
        $problems = New-Object System.Collections.Generic.List[string]
        if ($stream.Status -ne 0) { $problems.Add("curl exited with status $($stream.Status).") }
        if ($finals.Length -ne 1 -or $stream.Events.Length -eq 0 -or $null -eq $stream.Events[-1].FinishReason) {
            $problems.Add("The stream has $($finals.Length) closing events, not exactly one at the end.")
        }
        elseif ($finals[0].Token -ne '') { $problems.Add('The closing event carries a token.') }
        if (@('stop', 'length') -notcontains $record.finish_reason) { $problems.Add("The finish reason is '$($record.finish_reason)'.") }
        if ($tokens.Length -lt 2 -or $record.distinct_arrivals -lt 2 -or $record.token_span_second -le 0.1) {
            $problems.Add("The tokens did not arrive over time: $($tokens.Length) events over $($record.token_span_second) s.")
        }
        if ($generated -lt 1 -or $generated -gt $script:ReplyLength) { $problems.Add("last_token_count is $generated.") }
        if ($record.finish_reason -eq 'length' -and $generated -ne $script:ReplyLength) {
            $problems.Add("The reply ended by length after $generated tokens, not $($script:ReplyLength).")
        }
        if ($tokens.Length -gt $generated + 1) { $problems.Add("$($tokens.Length) token events for $generated generated tokens.") }
        $tolerance = 3 + [math]::Ceiling($generated * 0.1)
        if ($record.retokenized_count -lt 0 -or [math]::Abs($record.retokenized_count - $generated) -gt $tolerance) {
            $problems.Add("The streamed text is $($record.retokenized_count) tokens, against $generated generated (tolerance $tolerance).")
        }
        if ($record.control_text_found.Length -gt 0) { $problems.Add("The reply holds control text: $($record.control_text_found -join ' ').") }
        $record.problems = $problems.ToArray()
        return $record
    }

    function Measure-Abort {
        # T-GPU-04: starts a long generation that only max_length can end, aborts it after a few tokens and returns
        # what was observed and every problem found.
        $body = Get-GenerationBody -Prompt ([string]$script:Fixture.structured_prompt) -Length $script:AbortLength -Seed 11 -NoEnd
        $stream = Invoke-GpuTextStream -Root $script:Root -Body $body -AbortAfter $script:AbortAfter -TimeoutSecond 120
        $perf = Get-Perf
        $tokens = @($stream.Events | Where-Object { $null -eq $_.FinishReason })
        $finals = @($stream.Events | Where-Object { $null -ne $_.FinishReason })
        $endSecond = if ($stream.Events.Length -gt 0) { $stream.Events[-1].Second } else { 0 }
        # Abort is the hashtable of Invoke-GpuTextPost, whose keys Get-Field cannot see.
        $abortBody = if ($null -ne $stream.Abort) { $stream.Abort.Body } else { $null }
        $record = [ordered]@{
            curl_status        = $stream.Status
            abort_status       = if ($null -ne $stream.Abort) { $stream.Abort.Status } else { -1 }
            abort_success      = [string](Get-Field $abortBody 'success')
            abort_done         = [string](Get-Field $abortBody 'done')
            abort_sent_second  = [math]::Round($stream.AbortSecond, 2)
            end_after_abort_s  = if ($null -ne $stream.Abort) { [math]::Round($endSecond - $stream.AbortSecond, 2) } else { -1 }
            token_events       = $tokens.Length
            finish_reason      = if ($finals.Length -gt 0) { [string]$finals[-1].FinishReason } else { '' }
            last_token_count   = [int](Get-Field $perf 'last_token_count')
            perf_stop_reason   = Get-Field $perf 'stop_reason'
            max_length         = $script:AbortLength
            problems           = @()
        }
        $problems = New-Object System.Collections.Generic.List[string]
        if ($null -eq $stream.Abort) { $problems.Add("No abort was sent: only $($tokens.Length) token events arrived.") }
        elseif ($record.abort_status -ne 0 -or $record.abort_success -ne 'true') {
            $problems.Add("The abort answered status $($record.abort_status) with success '$($record.abort_success)'.")
        }
        if ($stream.Status -ne 0) { $problems.Add("curl exited with status $($stream.Status).") }
        if ($finals.Length -ne 1) { $problems.Add("The aborted stream has $($finals.Length) closing events.") }
        if (@('stop', 'length') -notcontains $record.finish_reason) { $problems.Add("The finish reason is '$($record.finish_reason)'.") }
        if ($record.end_after_abort_s -lt 0 -or $record.end_after_abort_s -gt 10) {
            $problems.Add("The stream ended $($record.end_after_abort_s) s after the abort.")
        }
        if ($record.last_token_count -ge $script:AbortLength -or $tokens.Length -ge $script:AbortLength) {
            $problems.Add("The generation ran to $($record.last_token_count) of $($script:AbortLength) tokens.")
        }
        $record.problems = $problems.ToArray()
        return $record
    }
}

AfterAll {
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        tests            = 'T-GPU-02 to T-GPU-04'
        file             = 'tests/gpu/TextProfiles.Tests.ps1'
        started          = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu              = $script:Card.Name
        total_mib        = $script:Card.TotalMiB
        baseline_mib     = $script:BaselineMiB
        generation_count = $script:GenerationCount
        generation_length = $script:GenerationLength
        profiles         = $script:Results.ToArray()
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 8), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe '<Id>, <Family>, at launch level A' -ForEach $script:ProfileRuns {
    BeforeAll {
        # Set first, so a failure below never leaves the record of the previous profile in its place.
        $script:Record = [ordered]@{ profile = $Id; measured = $Measure }
        $script:TextProfile = Get-GpuTestTextProfile -Root $script:Root -Id $Id
        $script:Fixture = $script:Prompts.families.$Family
        $script:Record.family = [string]$script:TextProfile.template
        $script:Record.file = $script:TextProfile.file
        $script:Record.launch = [string[]](Get-GpuLaunchLine -TextProfile $script:TextProfile -Level A)
        $script:Released = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
        $script:Written = Set-GpuTestTextLaunch -Root $script:Root -TextProfile $script:TextProfile -Level A
        $script:Before = Get-GpuSample
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $script:Up = if ($script:Written) { Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond $script:LoadTimeoutSecond } else { -1 }
        $script:Record.up_status = $script:Up
        $script:Record.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        $script:Loaded = Get-GpuSample
        $script:Record.vram_before_mib = $script:Before.UsedMiB
        $script:Record.vram_loaded_mib = $script:Loaded.UsedMiB
        $script:Record.total_mib = $script:Loaded.TotalMiB
        if ($script:Up -eq 0) {
            $script:Record.model = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model').Body 'result')
            $script:Record.true_context = [int](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value')
            $script:Record.version = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/version').Body 'version')
            $script:Record.ram_loaded_mib = Get-GpuTestContainerMemory -Root $script:Root -Service 'lyrveil-llm'
            if ($Measure) {
                $generated = 0
                $speeds = @()
                $sampler = Start-GpuMemorySampler
                try {
                    for ($index = 1; $index -le $script:GenerationCount; $index++) {
                        $body = Get-GenerationBody -Prompt ([string]$script:Fixture.prompt) -Stop ([string[]]@($script:Fixture.stop_sequences)) -Length $script:GenerationLength -Seed (100 + $index)
                        $answer = Invoke-GpuTextPost -Root $script:Root -Path '/api/v1/generate' -Body $body -TimeoutSecond 120
                        if ($answer.Status -eq 0 -and $null -ne (Get-Field $answer.Body 'results')) { $generated++ }
                        $speeds += Get-EvalSpeed (Get-Perf)
                    }
                }
                finally { $peak = Stop-GpuMemorySampler -Job $sampler }
                $script:Record.generations = $generated
                $script:Record.generation_tokens_per_second = [double[]]$speeds
                $script:Record.vram_samples = $peak.Samples
                $script:Record.vram_peak_mib = [math]::Max($peak.PeakMiB, $script:Loaded.UsedMiB)
                $script:Record.margin_mib = $script:Loaded.TotalMiB - $script:Record.vram_peak_mib
                $script:Record.tuning = Get-TuningOutcome -MarginMiB $script:Record.margin_mib
                $script:Record.ram_after_mib = Get-GpuTestContainerMemory -Root $script:Root -Service 'lyrveil-llm'
            }
            $script:Record.tokenization = Measure-Tokenization -Family $Family -Version $script:Record.version
            $script:Record.stream = Measure-Stream
            $script:Record.abort = Measure-Abort
        }
        Write-Host ("    {0}: ready {1} (exit {2}) in {3} s; NVML {4} MiB before, {5} MiB loaded of {6} MiB" -f $Id, ($script:Up -eq 0), $script:Up, $script:Record.ready_seconds, $script:Before.UsedMiB, $script:Loaded.UsedMiB, $script:Loaded.TotalMiB)
        if ($script:Record.Contains('vram_peak_mib')) {
            Write-Host ("    {0}: peak {1} MiB over {2} generations, margin {3} MiB, RAM {4} MiB loaded and {5} MiB after; {6}" -f $Id, $script:Record.vram_peak_mib, $script:Record.generations, $script:Record.margin_mib, $script:Record.ram_loaded_mib, $script:Record.ram_after_mib, $script:Record.tuning)
        }
    }

    AfterAll {
        $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
        $script:Results.Add($script:Record)
    }

    It '<Label>: becomes healthy at level A and serves its model at its catalog context' {
        $script:Released | Should -BeTrue -Because 'the previous runtime must release its VRAM first'
        $script:Written | Should -BeTrue
        $script:Up | Should -Be 0
        $script:Record.family | Should -Be $Family
        $script:Record.model | Should -Be ('koboldcpp/' + [System.IO.Path]::GetFileNameWithoutExtension($script:TextProfile.file))
        $script:Record.true_context | Should -Be ([int]$script:TextProfile.context_size)
        ($script:Loaded.UsedMiB - $script:Before.UsedMiB) | Should -BeGreaterThan 1024
    }

    if ($Measure) {
        It 'T-GPU-02: has its VRAM peak and its RAM measured over the generations' {
            $script:Record.generations | Should -Be $script:GenerationCount
            $script:Record.vram_samples | Should -BeGreaterThan 0
            $script:Record.ram_loaded_mib | Should -BeGreaterThan 0
            $script:Record.ram_after_mib | Should -BeGreaterThan 0
        }

        It 'T-GPU-02: keeps at least 512 MiB of VRAM free at its peak (A-01)' {
            $script:Record.margin_mib | Should -BeGreaterOrEqual 512
        }
    }

    It 'T-GPU-03: tokenizes the <Family> fixture prompt as recorded, with its control tokens' {
        $script:Record.Contains('tokenization') | Should -BeTrue
        $script:Record.tokenization.problems | Should -BeNullOrEmpty
        $script:Record.tokenization.count | Should -BeGreaterThan 0
    }

    It 'T-GPU-04: streams a reply token by token, with token counts that agree' {
        $script:Record.Contains('stream') | Should -BeTrue
        $script:Record.stream.problems | Should -BeNullOrEmpty
    }

    It 'T-GPU-04: ends a running generation soon after its abort' {
        $script:Record.Contains('abort') | Should -BeTrue
        $script:Record.abort.problems | Should -BeNullOrEmpty
    }
}
