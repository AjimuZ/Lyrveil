# T-QLT-06: the extraction evaluation of docs/consistency-engine.md, the memory extraction job's prompt, grammar and validation on 40 synthetic windows, on one, several or every catalog text profile of the real text runtime.
#
# "scripts\lyrveil.cmd test quality" runs it on every catalog text profile, "... test quality Extraction
# rocinante-x-12b" on that one, and "short" on the first 5 windows only, a smoke run that is no evidence. It copies none
# of the backend's prompt, grammar or rules: backend/tests/gpu_probe/extraction_eval.py, in the backend's test stage
# with no network (Invoke-GpuBackendProbe), gives the job's prompt for each window of
# backend/tests/fixtures/extraction_eval/windows.json, judges each output as the job does, gives the repair prompt of a
# refused one, and scores the accepted results against the four targets. For each profile this file starts lyrveil-llm
# alone at the catalog's level A and generates as the job does: the prompt, the grammar, the job's length and
# temperature, and one repair of a refused output. The unit level covers the probe (backend/tests/unit/
# test_gpu_extraction_probe.py). test-results/quality/extraction-<mode>-<time>.json, ignored by Git, holds every
# output, verdict and score.

# Pester passes Select, the items after the level in "test quality", to BeforeAll, which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeAll.')]
param([string[]]$Select = @())

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\QualityLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date
    # The short mode's windows: enough to run every step, including a repair when the model needs one.
    $script:ShortWindows = 5

    $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:ProfileIds = [string[]]@(Get-QualityTextProfileId -Catalog $catalog)
    $script:Selection = Get-QualitySelection -Select $Select -TextProfileId $script:ProfileIds
    $script:ResultFile = Join-Path $script:Root ('test-results\quality\extraction-{0}-{1}.json' -f $script:Selection.Mode.ToLowerInvariant(), $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    $script:Record = [ordered]@{ selection = [string[]]@($Select); mode = $script:Selection.Mode; profiles_selected = $script:Selection.Profiles; preparation = [ordered]@{}; profiles = [ordered]@{} }
    $script:Problems = New-Object System.Collections.Generic.List[string]

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
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

    function Invoke-ExtractionGeneration {
        # Generates Prompt as the memory_extraction job does, with the grammar, length and temperature of Document,
        # and returns the output with curl's status and time. A request that fails or runs past the job's total timeout
        # is aborted by its genkey, as the backend's adapter does, so it never holds the runtime for the next one.
        param([string]$Prompt, $Document, [int]$Context)
        $genkey = 'gputest' + [guid]::NewGuid().ToString('N').Substring(0, 12)
        $body = @{
            prompt = $Prompt; max_context_length = $Context; max_length = [int]$Document.max_length; genkey = $genkey
            temperature = [double]$Document.temperature; grammar = [string]$Document.grammar
        }
        $timeout = [int][math]::Ceiling([double]$Document.total_timeout_s)
        $answer = Invoke-GpuTextTimed -Root $script:Root -Path '/api/v1/generate' -Body $body -TimeoutSecond $timeout
        if ($answer.Status -ne 0) {
            $null = Invoke-GpuTextPost -Root $script:Root -Path '/api/extra/abort' -Body @{ genkey = $genkey } -TimeoutSecond 30
        }
        $results = @(Get-Field $answer.Body 'results')
        $text = if ($results.Length -gt 0) { [string](Get-Field $results[0] 'text') } else { '' }
        return [ordered]@{ curl_status = $answer.Status; seconds = [math]::Round($answer.Seconds, 2); output = $text }
    }

    function Invoke-ProfileEvaluation {
        # Loads TextProfile alone at level A, generates every window's first output, judges them all, repairs each
        # refused one once, and returns the profile's record with the probe's score, or with its problems.
        param($TextProfile)
        $entry = [ordered]@{ launch = [string[]](Get-GpuLaunchLine -TextProfile $TextProfile -Level A); problems = @() }
        $problems = New-Object System.Collections.Generic.List[string]
        $null = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
        $written = Set-GpuTestLaunchLine -Root $script:Root -Line $entry.launch -AsBackendUser
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $up = if ($written) { Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond 240 } else { -1 }
        $entry.up_status = $up
        $entry.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        try {
            if ($up -ne 0) {
                $problems.Add("lyrveil-llm did not load $($TextProfile.id) (exit $up).")
                return $entry
            }
            $entry.model = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model').Body 'result')
            $context = [int](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value')
            $entry.true_context = $context
            $family = [string]$TextProfile.template
            $first = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'prompt' -Body @{ family = $family; ids = $null }
            if ($first.Status -ne 0 -or $null -eq $first.Body) {
                $problems.Add("The probe gave no prompts (exit $($first.Status)).")
                return $entry
            }
            $document = $first.Body
            $ids = [string[]]@($document.ids)
            if ($script:Selection.Mode -eq 'Short') { $ids = [string[]]@($ids | Select-Object -First $script:ShortWindows) }
            foreach ($name in @('prompt_version', 'grammar_sha256', 'max_length', 'temperature', 'max_attempts', 'total_timeout_s', 'window_count')) {
                $entry[$name] = Get-Field $document $name
            }
            $entry.windows = $ids
            $outputs = [ordered]@{}
            $attempts = [ordered]@{}
            foreach ($id in $ids) {
                $generation = Invoke-ExtractionGeneration -Prompt ([string](Get-Field $document.prompts $id)) -Document $document -Context $context
                $outputs[$id] = @([string]$generation.output)
                $attempts[$id] = @($generation)
                Write-Host ("    {0} {1}: attempt 1, curl {2}, {3} s" -f $TextProfile.id, $id, $generation.curl_status, $generation.seconds)
            }
            $firstOutputs = @{}
            foreach ($id in $ids) { $firstOutputs[$id] = [string]$outputs[$id][0] }
            $checked = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'check' -Body @{ outputs = $firstOutputs }
            if ($checked.Status -ne 0 -or $null -eq $checked.Body) { $problems.Add("The probe gave no verdicts (exit $($checked.Status)).") }
            else {
                $repairs = @{}
                foreach ($id in $ids) {
                    $verdict = Get-Field $checked.Body $id
                    if ((Get-Field $verdict 'accepted') -ne $true) { $repairs[$id] = @{ previous_output = [string]$outputs[$id][0]; error = [string](Get-Field $verdict 'detail') } }
                }
                if ($repairs.Count -gt 0 -and [int]$document.max_attempts -ge 2) {
                    $repaired = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'prompt' -Body @{ family = $family; ids = [string[]]@($repairs.Keys); repairs = $repairs }
                    if ($repaired.Status -ne 0 -or $null -eq $repaired.Body) { $problems.Add("The probe gave no repair prompts (exit $($repaired.Status)).") }
                    else {
                        foreach ($id in @($repairs.Keys)) {
                            $generation = Invoke-ExtractionGeneration -Prompt ([string](Get-Field $repaired.Body.prompts $id)) -Document $document -Context $context
                            $outputs[$id] = @($outputs[$id]) + @([string]$generation.output)
                            $attempts[$id] = @($attempts[$id]) + @($generation)
                            Write-Host ("    {0} {1}: repair, curl {2}, {3} s" -f $TextProfile.id, $id, $generation.curl_status, $generation.seconds)
                        }
                    }
                }
            }
            $entry.attempts = $attempts
            $scored = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'score' -Body @{ ids = $ids; outputs = $outputs }
            if ($scored.Status -ne 0 -or $null -eq $scored.Body) { $problems.Add("The probe gave no score (exit $($scored.Status)).") }
            else { $entry.score = $scored.Body }
            foreach ($id in $ids) {
                foreach ($attempt in @($attempts[$id])) {
                    if ($attempt.curl_status -ne 0) { $problems.Add("$id`: a generation failed (curl $($attempt.curl_status)).") }
                }
            }
        }
        finally {
            $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
            $entry.problems = $problems.ToArray()
        }
        return $entry
    }

    $prep = $script:Record.preparation
    Open-GpuCommandLog
    try {
        if ($script:Selection.Unknown.Length -gt 0) { throw "Unknown items: $($script:Selection.Unknown -join ', ')." }
        $prep.probe_built = Initialize-GpuBackendProbe -Root $script:Root
        # Consistency.Tests.ps1 hands runtime/ to the backend's user; this file does the same, so either may run first.
        $prep.owner_set = $prep.probe_built -and (Set-GpuTestDataOwner -Root $script:Root -WithRuntime)
        if ($prep.owner_set) {
            foreach ($profileId in $script:Selection.Profiles) {
                Write-Host "    T-QLT-06 $($script:Selection.Mode) on $profileId"
                $entry = Invoke-ProfileEvaluation -TextProfile (Get-GpuTestTextProfile -Root $script:Root -Id $profileId)
                $script:Record.profiles[$profileId] = $entry
                $score = Get-Field $entry 'score'
                if ($null -ne $score) {
                    $targets = $score.targets
                    Write-Host ("    {0}: {1}; found {2}, inferences stated {3}, canonical changes {4}, valid {5}" -f $profileId, $(if ($score.passed) { 'targets met' } else { 'targets MISSED' }), $targets.found_rate.value, $targets.inferences_stated_rate.value, $targets.canonical_changes.value, $targets.valid_rate.value)
                }
            }
        }
        else { $script:Problems.Add('The backend probe image or the test data volume was not prepared.') }
    }
    catch { $script:Problems.Add("The evaluation stopped: $($_.Exception.Message)") }
    finally { $prep.commands = [string[]](Close-GpuCommandLog) }
}

AfterAll {
    $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
    $script:Record.released_after = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
    $script:Record.problems = $script:Problems.ToArray()
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        test         = 'T-QLT-06'
        file         = 'tests/quality/Extraction.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        evidence     = $(if ($script:Selection.Mode -eq 'Full') { 'protocol' } else { 'smoke run only: the short mode is no evidence for the catalog' })
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 14), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-QLT-06: the extraction evaluation' {
    It 'takes only short and catalog text profiles' {
        $script:Selection.Unknown | Should -BeNullOrEmpty -Because 'Extraction takes short and catalog text profile ids'
    }

    It 'prepares the probe and evaluates every selected profile without a failed step' {
        $script:Problems | Should -BeNullOrEmpty
        foreach ($profileId in $script:Selection.Profiles) {
            $entry = $script:Record.profiles[$profileId]
            $entry | Should -Not -BeNullOrEmpty -Because "$profileId must have run"
            @($entry.problems) | Should -BeNullOrEmpty -Because "every step on $profileId must succeed"
            Get-Field $entry 'score' | Should -Not -BeNullOrEmpty -Because "$profileId must have been scored"
        }
    }

    It 'meets every target on every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $score = Get-Field $script:Record.profiles[$profileId] 'score'
            $score | Should -Not -BeNullOrEmpty -Because "$profileId must have been scored"
            $missed = @(@('found_rate', 'inferences_stated_rate', 'canonical_changes', 'valid_rate') | Where-Object { -not (Get-Field (Get-Field $score.targets $_) 'passed') })
            $missed | Should -BeNullOrEmpty -Because "$profileId must meet every target of T-QLT-06"
            $score.passed | Should -BeTrue
        }
    }
}
