# T-GPU-14 and the measuring and tuning procedure (docs/gpu-orchestration.md, steps 1 to 9) for the three P02 text profiles on the real GPU, in the project lyrveil-gpu-test.
#
# Run only by "scripts\lyrveil.cmd test gpu", after TextProfiles.Tests.ps1, or alone, for some trials, with selectors:
#   scripts\lyrveil.cmd test gpu gemma-4-12b/A gemma-4-12b/batch-*
# scripts/lib/GpuTuning.psm1 plans the trials of gemma-4-12b, gemma-4-26b-a4b and rocinante-x-12b (each one launch
# file, the catalog's level A with one lever changed) and computes every decision from their records; this file only
# measures. For each trial it waits for the VRAM of the previous runtime to fall back, writes the launch file, starts
# the real lyrveil-llm, and records NVML before and after the load, the ready time, the container's RAM ("docker
# stats", after the load and after the workload) and the VRAM peak sampled every 250 ms during the workload:
#   - replies: the family's structured prompt with the end token banned and no stop sequence, 200 tokens each; the
#     speed of a reply is its generated tokens over curl's time inside the container;
#   - long prompts: a new prompt of at least 6000 tokens ("Sessione <nonce>." before its system text, numbered filler
#     sentences before its request), answered with one token; curl's time is the time to the first token, and the
#     tokens over it the prompt speed;
#   - the mmap trials first load the same launch values, then run an image job on lyrveil-image (the default image
#     profile's checkpoint, 768x768, 8 steps), then measure the reload that follows.
# The decisions run last, for every profile. With selectors, a trial not selected is taken from the newest earlier
# results file of the same procedure version and GPU, and the results file names its source; a decision that still
# lacks a trial is skipped then, and fails in a full run. A full run takes about an hour.
# test-results/gpu/tuning-<time>.json, ignored by Git, holds every trial, its commands, the reused trials and the
# decisions. Only one runtime runs at a time.

# Pester passes Trial, the selectors of "test gpu", to BeforeDiscovery and BeforeAll, which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Trial', Justification = 'Read in BeforeDiscovery and BeforeAll.')]
param([string[]]$Trial = @())

BeforeDiscovery {
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $root 'scripts\lib\GpuTuning.psm1') -Force
    $catalog = [System.IO.File]::ReadAllText((Join-Path $root 'config\model-catalog.json'), (New-Object System.Text.UTF8Encoding $false)) | ConvertFrom-Json
    $plan = @(Get-GpuTuningPlan -Catalog $catalog)
    $script:TrialRuns = @(Select-GpuTuningTrial -Plan $plan -Pattern $Trial | ForEach-Object {
            @{ ProfileId = $_.Profile; TrialName = $_.Name; Step = $_.Step; Definition = $_ }
        })
    $default = @($catalog.text | Where-Object { @($_.default_tiers) -contains 'target' } | Select-Object -First 1)
    $script:DecisionRuns = @(Get-GpuTuningProfileId | ForEach-Object {
            @{ ProfileId = $_; IsDefault = ($default.Length -eq 1 -and $default[0].id -eq $_) }
        })
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuTuning.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Selection = [string[]]@($Trial | Where-Object { $_ })
    $script:StartedAt = Get-Date

    # Step 1: the baseline is the lowest of three NVML samples two seconds apart, so the release of a runtime that the
    # previous test file removed a moment ago is not counted.
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    for ($index = 0; $index -lt 2; $index++) {
        Start-Sleep -Seconds 2
        $sample = Get-GpuSample
        if ($sample.Available -and $sample.UsedMiB -lt $script:BaselineMiB) { $script:BaselineMiB = $sample.UsedMiB }
    }

    $script:Catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:Plan = @(Get-GpuTuningPlan -Catalog $script:Catalog)
    $script:Prompts = [System.IO.File]::ReadAllText((Join-Path $script:Root 'backend\tests\fixtures\tokenization\prompts.json'), $script:Utf8) | ConvertFrom-Json
    $script:Rule = Get-GpuTuningRule
    $script:Records = New-Object System.Collections.Generic.List[object]
    $script:Reused = New-Object System.Collections.Generic.List[object]
    $script:Decisions = [ordered]@{}
    $script:ResultFolder = Join-Path $script:Root 'test-results\gpu'
    $script:ResultFile = Join-Path $script:ResultFolder ('tuning-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    $script:Earlier = @{}
    if ($script:Selection.Length -gt 0 -and $script:Card.Available) {
        $script:Earlier = Get-GpuEarlierTrial -Folder $script:ResultFolder -GpuName $script:Card.Name
    }

    $script:ReplyTokens = [int]$script:Rule.SpeedReplyTokens
    $script:PromptTokens = [int]$script:Rule.FirstTokenPromptTokens
    $script:LoadTimeoutSecond = 240
    $script:ReleaseTimeoutSecond = 120
    $script:SentenceCount = @{}
    $image = @($script:Catalog.image | Where-Object { @($_.default_tiers) -contains 'target' } | Select-Object -First 1)
    $script:ImageJob = [ordered]@{ checkpoint = ''; width = 768; height = 768; steps = 8 }
    if ($image.Length -eq 1) { $script:ImageJob.checkpoint = [string]$image[0].file }
    $fallback = @($script:Catalog.text | Where-Object { @($_.default_tiers) -contains 'reduced' } | Select-Object -First 1)
    $script:FallbackProfile = 'gemma-4-12b'
    if ($fallback.Length -eq 1) { $script:FallbackProfile = [string]$fallback[0].id }

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
        param($Value, [string]$Name)
        if ($null -eq $Value) { return $null }
        $property = $Value.PSObject.Properties[$Name]
        if ($null -eq $property) { return $null }
        return $property.Value
    }

    function Get-Perf {
        # Returns the parsed answer of GET /api/extra/perf, or $null.
        return (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/perf').Body
    }

    function Get-RequestBody {
        # Returns a GenerationInput for Prompt at the trial's context, Length tokens long, with the end token banned and
        # no stop sequence, so only Length ends it.
        param([string]$Prompt, [int]$Length, [int]$Seed)
        return @{
            prompt = $Prompt; max_context_length = $script:ContextSize; max_length = $Length
            temperature = 0.8; top_p = 0.95; top_k = 64; rep_pen = 1.05; sampler_seed = $Seed; trim_stop = $true
            use_default_badwordsids = $true; genkey = 'gputune' + [guid]::NewGuid().ToString('N').Substring(0, 12)
        }
    }

    function Get-PromptTokenCount {
        # Returns how many tokens the runtime processes for Text, BOS included, or -1 when the count failed.
        param([string]$Text)
        $answer = Invoke-GpuTextPost -Root $script:Root -Path '/api/extra/tokencount' -Body @{ prompt = $Text; special = $true } -TimeoutSecond 60
        if ($answer.Status -ne 0) { return -1 }
        $value = Get-Field $answer.Body 'value'
        if ($null -ne $value) { return [int]$value }
        $ids = Get-Field $answer.Body 'ids'
        if ($null -eq $ids) { return -1 }
        return @($ids).Length
    }

    function Get-LongPromptText {
        # Returns the family's structured prompt with "Sessione <Nonce>." before its system text and Sentences numbered
        # filler sentences before its request, so no two prompts share more than a few tokens and each is processed
        # whole. Throws when the fixture prompt does not hold the texts of prompts.json.
        param([int]$Sentences, [string]$Nonce)
        $builder = New-Object System.Text.StringBuilder
        for ($index = 1; $index -le $Sentences; $index++) {
            $sentence = 'Nota {0}: la libreria numero {1} di via Po apre alle {2} e chiude alle {3}; oggi il libraio ha registrato {4} titoli nuovi e {5} clienti. ' -f $index, (($index * 7) % 97), (7 + $index % 4), (18 + $index % 5), (($index * 13) % 41), (($index * 29) % 83)
            $null = $builder.Append($sentence)
            if ($index % 10 -eq 0) { $null = $builder.Append("`n") }
        }
        $system = [string]$script:Prompts.structured.system_text
        $user = [string]$script:Prompts.structured.user_text
        $prompt = [string]$script:Fixture.structured_prompt
        if (-not $prompt.Contains($system) -or -not $prompt.Contains($user)) { throw 'The structured prompt does not hold the texts of prompts.json.' }
        return $prompt.Replace($system, "Sessione $Nonce. $system").Replace($user, $builder.ToString() + "`n`n" + $user)
    }

    function Find-LongPromptSentence {
        # Returns how many filler sentences make the long prompt at least PromptTokens tokens for the loaded model, with
        # a reserve of 40 tokens so another nonce never takes it below, or -1 when tokencount fails.
        $target = $script:PromptTokens + 40
        $empty = Get-PromptTokenCount -Text (Get-LongPromptText -Sentences 0 -Nonce 'calibration')
        $probe = 100
        $count = Get-PromptTokenCount -Text (Get-LongPromptText -Sentences $probe -Nonce 'calibration')
        if ($empty -le 0 -or $count -le $empty) { return -1 }
        $perSentence = ($count - $empty) / $probe
        $sentences = [int][math]::Ceiling(($target - $empty) / $perSentence)
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            $count = Get-PromptTokenCount -Text (Get-LongPromptText -Sentences $sentences -Nonce 'calibration')
            if ($count -le 0) { return -1 }
            if ($count -ge $target) { return $sentences }
            $sentences += [int][math]::Ceiling(($target - $count) / $perSentence) + 1
        }
        return $sentences
    }

    function Measure-Reply {
        # Generates one reply of ReplyTokens tokens to the family's structured prompt and returns its seed, status (0
        # when it answered), curl's seconds, the generated tokens of /api/extra/perf (0 when it failed), their speed
        # and last_eval.
        param([int]$Seed)
        $body = Get-RequestBody -Prompt ([string]$script:Fixture.structured_prompt) -Length $script:ReplyTokens -Seed $Seed
        $answer = Invoke-GpuTextTimed -Root $script:Root -Path '/api/v1/generate' -Body $body -TimeoutSecond 180
        $perf = Get-Perf
        $answered = ($answer.Status -eq 0 -and $null -ne (Get-Field $answer.Body 'results'))
        $tokens = 0
        if ($answered) { $tokens = [int](Get-Field $perf 'last_token_count') }
        return [ordered]@{
            seed              = $Seed
            status            = if ($answered) { 0 } else { [math]::Max(1, $answer.Status) }
            seconds           = [math]::Round($answer.Seconds, 3)
            tokens            = $tokens
            tokens_per_second = if ($answer.Seconds -gt 0) { [math]::Round($tokens / $answer.Seconds, 2) } else { 0 }
            perf_last_eval    = Get-Field $perf 'last_eval'
        }
    }

    function Measure-LongPrompt {
        # Sends one new long prompt answered with a single token and returns its nonce, sentences, token count, status
        # (0 when it answered), curl's seconds (the time to the first token, inside the container), the prompt tokens
        # per second and last_process.
        param([int]$Index, [int]$Sentences)
        $nonce = '{0}x{1}' -f $Index, [guid]::NewGuid().ToString('N').Substring(0, 10)
        $text = Get-LongPromptText -Sentences $Sentences -Nonce $nonce
        $tokens = Get-PromptTokenCount -Text $text
        $answer = Invoke-GpuTextTimed -Root $script:Root -Path '/api/v1/generate' -Body (Get-RequestBody -Prompt $text -Length 1 -Seed (500 + $Index)) -TimeoutSecond 180
        $answered = ($answer.Status -eq 0 -and $null -ne (Get-Field $answer.Body 'results'))
        return [ordered]@{
            nonce             = $nonce
            sentences         = $Sentences
            tokens            = $tokens
            status            = if ($answered) { 0 } else { [math]::Max(1, $answer.Status) }
            seconds           = [math]::Round($answer.Seconds, 3)
            tokens_per_second = if ($answered -and $answer.Seconds -gt 0 -and $tokens -gt 0) { [math]::Round($tokens / $answer.Seconds, 1) } else { 0 }
            perf_last_process = Get-Field (Get-Perf) 'last_process'
        }
    }

    function Invoke-ImageJobStep {
        # Starts lyrveil-image, runs one image job with the default image profile's checkpoint, removes the runtime and
        # waits for its VRAM, and returns up_status, ready_seconds, client_status, status (ComfyUI's), seconds and
        # released.
        $record = [ordered]@{ up_status = -1; ready_seconds = 0; client_status = -1; status = ''; seconds = 0; released = $false }
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $record.up_status = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image' -TimeoutSecond 180
        $record.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        if ($record.up_status -eq 0) {
            $job = Invoke-GpuImageJob -Root $script:Root -Checkpoint $script:ImageJob.checkpoint -Width $script:ImageJob.width `
                -Height $script:ImageJob.height -Steps $script:ImageJob.steps -TimeoutSecond 240
            $record.client_status = $job.Status
            $record.status = [string](Get-Field $job.Result 'status')
            $record.seconds = [double](Get-Field $job.Result 'seconds')
        }
        $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
        $record.released = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond $script:ReleaseTimeoutSecond
        return $record
    }

    function Measure-TuningTrial {
        <#
        Runs the trial Definition of the plan and returns its record. Every number is stored as soon as it is read, so
        a record that ends early keeps what was measured. Its status is "measured" (it loaded and ran its workload),
        "not_loaded" (the runtime did not become healthy, a result for the decisions) or "invalid" (a precondition
        failed, or the entrypoint refused the launch file); problems names every failure, also of single requests.
        #>
        param($Definition)
        $record = [ordered]@{
            profile = $Definition.Profile; trial = $Definition.Name; step = $Definition.Step
            procedure_version = $script:Rule.ProcedureVersion; measured_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
            settings = $Definition.Setting; status = 'invalid'; problems = @()
        }
        $problems = New-Object System.Collections.Generic.List[string]
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        Open-GpuCommandLog
        try {
            $textProfile = Get-GpuTestTextProfile -Root $script:Root -Id $Definition.Profile
            $script:Fixture = $script:Prompts.families.([string]$textProfile.template)
            $launch = Get-GpuTrialLaunchLine -BaseLine (Get-GpuLaunchLine -TextProfile $textProfile -Level A) -Setting $Definition.Setting
            $record.launch = $launch
            $script:ContextSize = [int](@($launch | Where-Object { $_ -like 'CONTEXT_SIZE=*' })[0] -replace '^CONTEXT_SIZE=', '')
            $record.context_size = $script:ContextSize
            if (-not (Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond $script:ReleaseTimeoutSecond)) {
                $problems.Add('The VRAM of the previous runtime was not released.')
                return $record
            }
            if (-not (Set-GpuTestLaunchLine -Root $script:Root -Line $launch)) {
                $problems.Add('The launch file could not be written.')
                return $record
            }
            if ($Definition.ImageJobFirst) {
                $warmClock = [System.Diagnostics.Stopwatch]::StartNew()
                $warm = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond $script:LoadTimeoutSecond
                $record.first_load_status = $warm
                $record.first_load_seconds = [math]::Round($warmClock.Elapsed.TotalSeconds, 1)
                $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
                if ($warm -ne 0) {
                    $problems.Add("The load before the image job failed (exit $warm).")
                    return $record
                }
                if (-not (Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond $script:ReleaseTimeoutSecond)) {
                    $problems.Add('The VRAM of the load before the image job was not released.')
                    return $record
                }
                $record.image_job = Invoke-ImageJobStep
                if ($record.image_job.status -ne 'success' -or -not $record.image_job.released) {
                    $problems.Add("The image job did not succeed and release its VRAM (status '$($record.image_job.status)').")
                    return $record
                }
            }

            $before = Get-GpuSample
            $loadClock = [System.Diagnostics.Stopwatch]::StartNew()
            $up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond $script:LoadTimeoutSecond
            $record.up_status = $up
            $record.ready_seconds = [math]::Round($loadClock.Elapsed.TotalSeconds, 1)
            $loaded = Get-GpuSample
            $record.vram_before_mib = $before.UsedMiB
            $record.vram_loaded_mib = $loaded.UsedMiB
            $record.total_mib = $loaded.TotalMiB
            if ($up -ne 0) {
                $state = Get-GpuTestContainerFact -Root $script:Root -Service 'lyrveil-llm' -Template '{{json .State}}'
                $record.exit_code = Get-Field $state 'ExitCode'
                $record.running = Get-Field $state 'Running'
                $record.oom_killed = Get-Field $state 'OOMKilled'
                if ($record.exit_code -eq 78) {
                    $problems.Add('The entrypoint refused the launch file (exit 78).')
                    return $record
                }
                $record.status = 'not_loaded'
                return $record
            }

            $record.model = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model').Body 'result')
            $record.version = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/version').Body 'version')
            $record.true_context = [int](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value')
            $record.ram_loaded_mib = Get-GpuTestContainerMemory -Root $script:Root -Service 'lyrveil-llm'
            $expected = 'koboldcpp/' + [System.IO.Path]::GetFileNameWithoutExtension([string]$textProfile.file)
            if ($record.model -ne $expected -or $record.true_context -ne $script:ContextSize) {
                $problems.Add("The runtime serves '$($record.model)' at $($record.true_context) tokens, not '$expected' at $($script:ContextSize).")
                return $record
            }

            $sentences = 0
            if ($Definition.Prompts -gt 0) {
                if (-not $script:SentenceCount.ContainsKey($Definition.Profile)) { $script:SentenceCount[$Definition.Profile] = Find-LongPromptSentence }
                $sentences = [int]$script:SentenceCount[$Definition.Profile]
                $record.long_prompt_sentences = $sentences
                if ($sentences -le 0) {
                    $problems.Add('The long prompt could not be sized: tokencount failed.')
                    return $record
                }
            }
            $replies = New-Object System.Collections.Generic.List[object]
            $prompts = New-Object System.Collections.Generic.List[object]
            $stopped = ''
            $sampler = Start-GpuMemorySampler
            try {
                for ($index = 1; $index -le $Definition.Replies; $index++) {
                    $replies.Add((Measure-Reply -Seed (100 + $index)))
                    if ($Definition.StopWhenRuleFails) {
                        $stopped = Get-GpuSpeedRuleStop -Reply $replies.ToArray() -Planned $Definition.Replies
                        if ($stopped) { break }
                    }
                }
                for ($index = 1; $index -le $Definition.Prompts -and -not $stopped; $index++) {
                    $prompt = Measure-LongPrompt -Index $index -Sentences $sentences
                    $prompts.Add($prompt)
                    if ($Definition.StopWhenRuleFails -and ($prompt.status -ne 0 -or $prompt.seconds -gt $script:Rule.FirstTokenSeconds)) {
                        $stopped = "long prompt $index took $($prompt.seconds) s or failed, so the first token misses $($script:Rule.FirstTokenSeconds) s"
                    }
                }
            }
            finally { $peak = Stop-GpuMemorySampler -Job $sampler }
            $record.replies = $replies.ToArray()
            $record.prompts = $prompts.ToArray()
            # Why a trial that serves only the speed rule stopped before its planned workload; empty when it did not.
            $record.workload_stopped = $stopped
            $record.vram_samples = $peak.Samples
            $record.vram_peak_mib = [math]::Max($peak.PeakMiB, $loaded.UsedMiB)
            $record.margin_mib = $loaded.TotalMiB - $record.vram_peak_mib
            $record.ram_after_mib = Get-GpuTestContainerMemory -Root $script:Root -Service 'lyrveil-llm'
            $failedReplies = @($record.replies | Where-Object { $_.status -ne 0 -or $_.tokens -lt $script:ReplyTokens }).Length
            $failedPrompts = @($record.prompts | Where-Object { $_.status -ne 0 -or $_.tokens -lt $script:PromptTokens }).Length
            if ($failedReplies -gt 0) { $problems.Add("$failedReplies of $($replies.Count) replies failed or had fewer than $($script:ReplyTokens) tokens.") }
            if ($failedPrompts -gt 0) { $problems.Add("$failedPrompts of $($prompts.Count) long prompts failed or had fewer than $($script:PromptTokens) tokens.") }
            if ($peak.Samples -eq 0) { $problems.Add('The VRAM sampler read no sample.') }
            $record.status = 'measured'
        }
        catch { $problems.Add("The trial stopped: $($_.Exception.Message)") }
        finally {
            $record.problems = $problems.ToArray()
            $record.seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
            $record.commands = Close-GpuCommandLog
        }
        return $record
    }

    function Format-TrialLine {
        # Returns one console line with the numbers of Record.
        param($Record)
        $summary = Get-GpuTrialSummary -Trial $Record
        $line = '    {0}/{1}: {2} in {3} s' -f $Record.profile, $Record.trial, $Record.status, $Record.seconds
        if ($Record.Contains('ready_seconds')) { $line += ('; ready in {0} s' -f $Record.ready_seconds) }
        if ($Record.Contains('exit_code')) { $line += ('; container exit code {0}, running {1}, OOM killed {2}' -f $Record.exit_code, $Record.running, $Record.oom_killed) }
        if ($summary.Loaded) {
            $line += ('; VRAM {0} -> {1} MiB, peak {2} MiB, margin {3} MiB; RAM {4} MiB' -f $Record.vram_before_mib, $Record.vram_loaded_mib, $summary.PeakMiB, $summary.MarginMiB, $summary.RamMiB)
            if ($summary.LongReplies -gt 0) { $line += ('; median {0:0.0} T/s over {1} replies' -f $summary.MedianSpeed, $summary.LongReplies) }
            if ($summary.Prompts -gt 0) { $line += ('; first token {0:0.00} s for {1} tokens ({2:0} T/s)' -f $summary.FirstTokenSeconds, $summary.PromptTokens, $summary.PromptSpeed) }
            if ($Record.Contains('workload_stopped') -and $Record.workload_stopped) { $line += "; stopped early: $($Record.workload_stopped)" }
        }
        foreach ($problem in @($Record.problems)) { if ($problem) { $line += "`n      problem: $problem" } }
        return $line
    }

    function Assert-Decision {
        # Passes when Decision was made. Otherwise the test is skipped in a run with selectors, where the trials it needs
        # may run in another part, and fails in a full run.
        param($Decision)
        if ($Decision.Decided) { return }
        if ($script:Selection.Length -gt 0) { Set-ItResult -Skipped -Because "a partial run: $($Decision.Reason)" }
        $Decision.Decided | Should -BeTrue -Because $Decision.Reason
    }
}

AfterAll {
    if (-not (Test-Path -LiteralPath $script:ResultFolder)) { $null = New-Item -ItemType Directory -Path $script:ResultFolder }
    $document = [ordered]@{
        tests             = 'T-GPU-14 and the measuring and tuning procedure, steps 1 to 9'
        file              = 'tests/gpu/Tuning.Tests.ps1'
        procedure_version = $script:Rule.ProcedureVersion
        started           = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        finished          = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu               = $script:Card.Name
        total_mib         = $script:Card.TotalMiB
        baseline_mib      = $script:BaselineMiB
        selection         = $script:Selection
        rules             = $script:Rule
        workload          = [ordered]@{
            reply_tokens = $script:ReplyTokens
            reply_prompt = 'the structured_prompt of the family in backend/tests/fixtures/tokenization/prompts.json, end token banned, no stop sequence'
            long_prompt  = 'the same prompt with "Sessione <nonce>. " before its system text and <sentences> filler sentences before its request (Get-LongPromptText), answered with one token'
            prompt_tokens = $script:PromptTokens
            image_job    = $script:ImageJob
        }
        trials            = $script:Records.ToArray()
        reused            = $script:Reused.ToArray()
        decisions         = $script:Decisions
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 12), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe '<ProfileId>/<TrialName>, step <Step>' -ForEach $script:TrialRuns {
    BeforeAll {
        $script:Record = [ordered]@{ profile = $ProfileId; trial = $TrialName; status = 'invalid'; problems = @('The trial did not run.'); seconds = 0 }
        $script:Record = Measure-TuningTrial -Definition $Definition
        Write-Host (Format-TrialLine -Record $script:Record)
    }

    AfterAll {
        $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
        $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
        $script:Records.Add($script:Record)
    }

    It 'gives a usable measurement' {
        $script:Record.problems | Should -BeNullOrEmpty
        @('measured', 'not_loaded') | Should -Contain $script:Record.status
    }

    if ($Definition.MustLoad) {
        It 'loads with the catalog''s own launch values' {
            $script:Record.status | Should -Be 'measured'
        }
    }
}

Describe 'Decisions of the procedure for <ProfileId>' -ForEach $script:DecisionRuns {
    BeforeAll {
        $textProfile = Get-GpuTestTextProfile -Root $script:Root -Id $ProfileId
        $trials = @{}
        $sources = [ordered]@{}
        foreach ($record in $script:Records) {
            if ($record.profile -eq $ProfileId) { $trials[[string]$record.trial] = $record; $sources[[string]$record.trial] = 'this run' }
        }
        foreach ($definition in @($script:Plan | Where-Object { $_.Profile -eq $ProfileId })) {
            $key = "$ProfileId/$($definition.Name)"
            if ($trials.ContainsKey($definition.Name) -or -not $script:Earlier.ContainsKey($key)) { continue }
            $earlier = $script:Earlier[$key]
            $trials[$definition.Name] = $earlier
            $sources[$definition.Name] = [string]$earlier.source_file
            $script:Reused.Add([ordered]@{ profile = $ProfileId; trial = $definition.Name; source_file = [string]$earlier.source_file; measured_at = [string]$earlier.measured_at })
        }
        $script:Decision = Get-GpuTuningDecision -TextProfile $textProfile -Trial $trials -IsDefault:$IsDefault -FallbackProfile $script:FallbackProfile
        $script:Decisions[$ProfileId] = [ordered]@{ sources = $sources; decision = $script:Decision }
        foreach ($name in @('Level', 'Context', 'Batch', 'Moe', 'Mmap', 'Kv', 'SpeedRule')) {
            $item = $script:Decision.$name
            if ($null -eq $item) { continue }
            $value = if ($item.Decided) { "$($item.Value)" } else { 'undecided' }
            Write-Host ('    {0} {1}: {2}. {3}' -f $ProfileId, $name, $value, $item.Reason)
        }
        if ($null -ne $script:Decision.SpeedRule -and $null -ne $script:Decision.SpeedRule.PSObject.Properties['Settings']) {
            foreach ($row in @($script:Decision.SpeedRule.Settings)) {
                Write-Host ('      {0}: loaded {1}, margin {2} MiB, median {3:0.0} T/s over {4} replies, first token {5:0.00} s for {6} tokens; {7}' -f $row.Label, $row.Loaded, $row.MarginMiB, $row.MedianTokensPerSecond, $row.LongReplies, $row.FirstTokenSeconds, $row.PromptTokens, $row.SpeedReason)
            }
        }
    }

    It 'step 4 decides the default launch level' { Assert-Decision $script:Decision.Level }

    It 'step 5 decides the context' { Assert-Decision $script:Decision.Context }

    It 'step 6 decides the batch size' { Assert-Decision $script:Decision.Batch }

    It 'step 7 decides MOE_CPU_LAYERS' { Assert-Decision $script:Decision.Moe }

    It 'step 8 decides USE_MMAP' { Assert-Decision $script:Decision.Mmap }

    It 'step 9 decides KV_QUANT' { Assert-Decision $script:Decision.Kv }

    if ($IsDefault) {
        It 'T-GPU-14: the default profile meets the speed rule with a setting that keeps the margin rule' {
            Assert-Decision $script:Decision.SpeedRule
            $script:Decision.SpeedRule.Met | Should -BeTrue -Because $script:Decision.SpeedRule.Reason
        }
    }
}
