# T-GPU-05: the character draft's structured output under its GBNF grammar on the real default text model, in the project lyrveil-gpu-test.
#
# Run only by "scripts\lyrveil.cmd test gpu" (alone with "scripts\lyrveil.cmd test gpu StructuredOutput"). It builds the
# backend's test stage (Initialize-GpuBackendProbe), where backend/tests/gpu_probe/structured_task.py gives the prompt,
# the grammar and the request values of the character_draft job and judges every output with the job's validation
# (lyrveil.services.character_creator.check_draft), so this file never copies the backend's prompt, grammar or rules.
# It loads the catalog's default text profile of the reference machine's tier at launch level A, then for each
# description of $script:Descriptions generates through POST /api/v1/generate with the grammar, as the job does, and
# repairs a refused output once with the job's repair prompt (ADR 0013). Then it generates the first description once
# without the grammar, for the grammar's speed cost. Every request runs in BeforeAll, so the numbers are recorded even
# when a check fails; test-results/gpu/structured-output-<time>.json, ignored by Git, holds them and the outputs.

BeforeDiscovery {
    # Original adult characters, in Italian like the user's descriptions; each asks for something different of the
    # schema (a place, a writing style, emoji). ASCII only, because Windows PowerShell 5.1 reads a file without a BOM
    # in the ANSI code page.
    $script:Descriptions = @(
        @{ Id = 'libraia'; Text = 'Una libraia di 41 anni, ironica e riservata, che vive sopra il suo negozio in un paese di mare.' }
        @{ Id = 'meccanico'; Text = 'Un ex pilota di 29 anni, ora meccanico, testardo e generoso, che scrive messaggi lunghi e fa molte domande.' }
        @{ Id = 'astronoma'; Text = "Una ricercatrice di 35 anni appassionata di astronomia, curiosa e un po' distratta, che usa spesso le emoji." }
    )
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\structured-output-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    $script:LoadTimeoutSecond = 240
    # The reference machine is of the target tier since ADR 0027; its default text profile is the one T-GPU-05 uses.
    $script:Tier = 'target'

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
        param($Value, [string]$Name)
        if ($null -eq $Value) { return $null }
        $property = $Value.PSObject.Properties[$Name]
        if ($null -eq $property) { return $null }
        return $property.Value
    }

    function Get-DefaultTextProfileId {
        # Returns the identifier of the one catalog text profile that is the default of $script:Tier, or throws.
        $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
        $defaults = @($catalog.text | Where-Object { @($_.default_tiers) -contains $script:Tier })
        if ($defaults.Length -ne 1) { throw "config/model-catalog.json has $($defaults.Length) default text profiles for the $($script:Tier) tier, not one." }
        return [string]$defaults[0].id
    }

    function Get-Median {
        # Returns the median of Value, or 0 for none.
        param([double[]]$Value)
        $sorted = @($Value | Sort-Object)
        if ($sorted.Length -eq 0) { return 0 }
        $middle = [math]::Floor($sorted.Length / 2)
        if ($sorted.Length % 2 -eq 1) { return [double]$sorted[$middle] }
        return ([double]$sorted[$middle - 1] + [double]$sorted[$middle]) / 2
    }

    function Invoke-DraftGeneration {
        # Generates Document's prompt as the character_draft job does, with its grammar unless NoGrammar, and returns
        # the output and the timing of the request. A request that fails or runs past the job's total timeout is
        # aborted by its genkey, as the backend's adapter does, so it never holds the runtime for the next one.
        param($Document, [switch]$NoGrammar)
        $genkey = 'gputest' + [guid]::NewGuid().ToString('N').Substring(0, 12)
        $body = @{
            prompt = [string]$Document.prompt; max_context_length = $script:Record.true_context
            max_length = [int]$Document.max_length; genkey = $genkey; temperature = [double]$Document.temperature
        }
        if (-not $NoGrammar) { $body.grammar = [string]$Document.grammar }
        $timeout = [int][math]::Ceiling([double]$Document.total_timeout_s)
        $answer = Invoke-GpuTextTimed -Root $script:Root -Path '/api/v1/generate' -Body $body -TimeoutSecond $timeout
        if ($answer.Status -ne 0) {
            $null = Invoke-GpuTextPost -Root $script:Root -Path '/api/extra/abort' -Body @{ genkey = $genkey } -TimeoutSecond 30
        }
        $perf = (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/perf').Body
        $results = @(Get-Field $answer.Body 'results')
        $text = if ($results.Length -gt 0) { [string](Get-Field $results[0] 'text') } else { '' }
        $tokens = [int](Get-Field $perf 'last_token_count')
        # last_eval is milliseconds per generated token (docs/runtime-contracts.md, observed in T-GPU-04).
        $msPerToken = [double](Get-Field $perf 'last_eval')
        return [ordered]@{
            grammar                 = -not $NoGrammar
            curl_status             = $answer.Status
            seconds                 = [math]::Round($answer.Seconds, 2)
            generated_tokens        = $tokens
            tokens_per_second       = if ($answer.Seconds -gt 0) { [math]::Round($tokens / $answer.Seconds, 1) } else { 0 }
            eval_tokens_per_second  = if ($msPerToken -gt 0) { [math]::Round(1000 / $msPerToken, 1) } else { 0 }
            perf_last_process       = Get-Field $perf 'last_process'
            perf_stop_reason        = Get-Field $perf 'stop_reason'
            output_characters       = $text.Length
            output                  = $text
        }
    }

    function Measure-Draft {
        # Runs one draft as the job does: a generation under the grammar, judged by the probe, and one repair of a
        # refused output. Returns every attempt with its verdict, and whether and when the draft was accepted.
        param([string]$Description)
        $record = [ordered]@{ attempts = @(); accepted = $false; accepted_attempt = 0; problems = @() }
        $problems = New-Object System.Collections.Generic.List[string]
        $attempts = New-Object System.Collections.Generic.List[object]
        $repair = $null
        for ($number = 1; $number -le [int]$script:Probe.max_attempts; $number++) {
            $prompt = Invoke-GpuBackendProbe -Root $script:Root -Action 'prompt' -Body @{ family = $script:Family; description = $Description; repair = $repair }
            if ($prompt.Status -ne 0 -or $null -eq $prompt.Body) {
                $problems.Add("The probe gave no prompt for attempt $number (exit $($prompt.Status)).")
                break
            }
            $attempt = Invoke-DraftGeneration -Document $prompt.Body
            $attempt.number = $number
            $verdict = Invoke-GpuBackendProbe -Root $script:Root -Action 'check' -Body @{ output = [string]$attempt.output }
            $attempt.check_status = $verdict.Status
            foreach ($name in @('json', 'keys', 'accepted', 'blocked', 'detail', 'age', 'proposed_fields')) {
                $attempt[$name] = Get-Field $verdict.Body $name
            }
            $attempts.Add($attempt)
            if ($attempt.curl_status -ne 0) { $problems.Add("Attempt $number`: curl exited with status $($attempt.curl_status).") }
            if ($verdict.Status -ne 0 -or $null -eq $verdict.Body) { $problems.Add("Attempt $number`: the probe gave no verdict (exit $($verdict.Status)).") }
            if ($attempt.json -ne $true) { $problems.Add("Attempt $number`: the output under the grammar is not a JSON object.") }
            elseif ((@($attempt.keys) -join ',') -ne (@($script:Probe.draft_keys) -join ',')) {
                $problems.Add("Attempt $number`: the top-level keys are '$(@($attempt.keys) -join ',')', not the schema's.")
            }
            if ($attempt.seconds -ge [double]$script:Probe.total_timeout_s) {
                $problems.Add("Attempt $number`: $($attempt.seconds) s, past the job's $($script:Probe.total_timeout_s) s.")
            }
            if ($attempt.accepted -eq $true) {
                $record.accepted = $true
                $record.accepted_attempt = $number
                break
            }
            $repair = @{ previous_output = [string]$attempt.output; error = [string]$attempt.detail }
        }
        if (-not $record.accepted) { $problems.Add("No attempt of $($script:Probe.max_attempts) was accepted.") }
        $record.attempts = $attempts.ToArray()
        $record.problems = $problems.ToArray()
        return $record
    }

    $script:Record = [ordered]@{ tier = $script:Tier; drafts = [ordered]@{} }
    $script:Records = $script:Record.drafts
    $script:ProfileId = Get-DefaultTextProfileId
    $script:TextProfile = Get-GpuTestTextProfile -Root $script:Root -Id $script:ProfileId
    $script:Family = [string]$script:TextProfile.template
    $script:Record.profile = $script:ProfileId
    $script:Record.family = $script:Family
    $script:Record.file = $script:TextProfile.file
    $script:Record.launch = [string[]](Get-GpuLaunchLine -TextProfile $script:TextProfile -Level A)

    $script:Built = Initialize-GpuBackendProbe -Root $script:Root
    $script:Probe = $null
    if ($script:Built) {
        $script:Probe = (Invoke-GpuBackendProbe -Root $script:Root -Action 'prompt' -Body @{ family = $script:Family; description = 'x'; repair = $null }).Body
    }
    if ($null -ne $script:Probe) {
        foreach ($name in @('task', 'template_version', 'grammar_sha256', 'grammar_rules', 'draft_keys', 'max_length', 'temperature', 'max_attempts', 'total_timeout_s')) {
            $script:Record[$name] = Get-Field $script:Probe $name
        }
    }

    $script:Released = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
    $script:Written = ($null -ne $script:Probe) -and (Set-GpuTestTextLaunch -Root $script:Root -TextProfile $script:TextProfile -Level A)
    $script:Before = Get-GpuSample
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $script:Up = if ($script:Written) { Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond $script:LoadTimeoutSecond } else { -1 }
    $script:Record.up_status = $script:Up
    $script:Record.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
    $script:Record.vram_before_mib = $script:Before.UsedMiB
    $script:Record.vram_loaded_mib = (Get-GpuSample).UsedMiB
    if ($script:Up -eq 0) {
        $script:Record.model = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model').Body 'result')
        $script:Record.true_context = [int](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value')
        $script:Record.version = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/version').Body 'version')
    }
    Write-Host ("    {0}: ready {1} (exit {2}) in {3} s; NVML {4} MiB before, {5} MiB loaded" -f $script:ProfileId, ($script:Up -eq 0), $script:Up, $script:Record.ready_seconds, $script:Before.UsedMiB, $script:Record.vram_loaded_mib)
}

AfterAll {
    $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        tests        = 'T-GPU-05'
        file         = 'tests/gpu/StructuredOutput.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 10), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-GPU-05: structured output with grammar on the default text model' {
    It 'takes the character_draft prompt, grammar and validation from the backend' {
        $script:Built | Should -BeTrue -Because 'the backend test stage must build'
        $script:Probe | Should -Not -BeNullOrEmpty
        [string]$script:Probe.grammar | Should -Match '^root ::= '
        [int]$script:Probe.max_attempts | Should -BeGreaterOrEqual 1
        @($script:Probe.draft_keys).Length | Should -BeGreaterThan 0
    }

    It 'loads the default text profile at level A' {
        $script:Released | Should -BeTrue -Because 'the previous runtime must release its VRAM first'
        $script:Written | Should -BeTrue
        $script:Up | Should -Be 0
        $script:Record.model | Should -Be ('koboldcpp/' + [System.IO.Path]::GetFileNameWithoutExtension($script:TextProfile.file))
        $script:Record.true_context | Should -Be ([int]$script:TextProfile.context_size)
    }

}

Describe 'T-GPU-05: the draft of <Id>' -ForEach $script:Descriptions {
    BeforeAll {
        # Set first, so a draft that cannot run still leaves its own record with its reason.
        $script:Records[$Id] = [ordered]@{ description = $Text; attempts = @(); accepted = $false; accepted_attempt = 0; problems = @('The model did not load.') }
        if ($script:Up -eq 0) {
            $script:Records[$Id] = Measure-Draft -Description $Text
            $script:Records[$Id].description = $Text
        }
        $draft = $script:Records[$Id]
        $seconds = @($draft.attempts | ForEach-Object { "$($_.seconds) s, $($_.generated_tokens) tokens" }) -join '; '
        Write-Host ("    {0}: accepted {1} at attempt {2}; {3}" -f $Id, $draft.accepted, $draft.accepted_attempt, $seconds)
    }

    It 'writes a JSON object with the schema keys on every attempt, within the job timeout' {
        @($script:Records[$Id].attempts).Length | Should -BeGreaterThan 0
        $script:Records[$Id].problems | Should -BeNullOrEmpty
    }

    It 'is accepted as a card within the job attempts' {
        $script:Records[$Id].accepted | Should -BeTrue
    }
}

Describe 'T-GPU-05: the cost of the grammar' {
    BeforeAll {
        $script:Free = $null
        $drafts = @($script:Records.Values)
        if ($script:Up -eq 0 -and $drafts.Length -gt 0) {
            # The first description's prompt, generated once without the grammar, as the speed reference.
            $first = Invoke-GpuBackendProbe -Root $script:Root -Action 'prompt' -Body @{ family = $script:Family; description = [string]$drafts[0].description; repair = $null }
            if ($first.Status -eq 0 -and $null -ne $first.Body) {
                $script:Free = Invoke-DraftGeneration -Document $first.Body -NoGrammar
                $verdict = Invoke-GpuBackendProbe -Root $script:Root -Action 'check' -Body @{ output = [string]$script:Free.output }
                foreach ($name in @('json', 'accepted', 'detail')) { $script:Free[$name] = Get-Field $verdict.Body $name }
                $script:Record.without_grammar = $script:Free
            }
        }
        # The first attempt of each draft, the one every job makes.
        $firstAttempts = @($drafts | ForEach-Object { @($_.attempts) | Select-Object -First 1 } | Where-Object { $null -ne $_ })
        $script:Record.grammar_first_attempts = $firstAttempts.Length
        $script:Record.grammar_median_tokens_per_second = Get-Median -Value ([double[]]@($firstAttempts | ForEach-Object { $_.tokens_per_second }))
        $script:Record.grammar_median_eval_tokens_per_second = Get-Median -Value ([double[]]@($firstAttempts | ForEach-Object { $_.eval_tokens_per_second }))
        $allSeconds = @($drafts | ForEach-Object { @($_.attempts) } | ForEach-Object { [double]$_.seconds })
        $script:Record.grammar_max_seconds = if ($allSeconds.Length -gt 0) { ($allSeconds | Measure-Object -Maximum).Maximum } else { 0 }
        $freeSpeed = if ($null -ne $script:Free) { [double]$script:Free.eval_tokens_per_second } else { 0 }
        if ($freeSpeed -gt 0) {
            $script:Record.grammar_eval_cost_percent = [math]::Round(100 * (1 - $script:Record.grammar_median_eval_tokens_per_second / $freeSpeed), 1)
        }
        $cost = if ($script:Record.Contains('grammar_eval_cost_percent')) { "$($script:Record.grammar_eval_cost_percent) %" } else { 'not measured' }
        Write-Host ("    grammar: median {0} T/s generated ({1} T/s over the whole request), slowest request {2} s; without it {3} T/s; cost {4}" -f $script:Record.grammar_median_eval_tokens_per_second, $script:Record.grammar_median_tokens_per_second, $script:Record.grammar_max_seconds, $freeSpeed, $cost)
    }

    It 'measures the generation speed with and without the grammar' {
        $script:Free | Should -Not -BeNullOrEmpty
        $script:Free.curl_status | Should -Be 0
        $script:Free.eval_tokens_per_second | Should -BeGreaterThan 0
        $script:Record.grammar_median_eval_tokens_per_second | Should -BeGreaterThan 0
    }
}
