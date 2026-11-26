# T-QLT-05: the consistency protocol of docs/consistency-engine.md on the real stack of the project lyrveil-gpu-test, with the SmartCache and SWA padding measurements of ADR 0031, on one, several or every catalog text profile.
#
# "scripts\lyrveil.cmd test quality" runs it on every catalog text profile, "... test quality Consistency
# rocinante-x-12b" on that one, and "short" adds the smoke mode of QualityLevel.psm1. It prepares the stack like
# tests/gpu/Session.Tests.ps1 (the test-only orchestrator, the backend, both runtime containers), turns diagnostic mode
# on so every reply keeps its prompt, and loads the demo characters. For each profile it makes that profile active
# through the backend (PATCH /settings/models and Applica ora), drives one scripted conversation per demo character
# (tests/quality/consistency/script.json), then the C6 conversation: the first messages on the next catalog profile,
# the rest after changing back. Each reply's checks_json is read from the test database, read-only, and its length is
# counted by the loaded model's tokenizer. Then it removes the stack and replays the last chat prompts of the first
# conversation directly on lyrveil-llm, each after the extraction prompt of a T-QLT-06 window, with SMART_CACHE 0 and 2.
# QualityLevel.psm1, which the unit level covers, computes every score and decision.
# test-results/quality/consistency-<mode>-<time>.json, ignored by Git, holds every reply, score, trial and decision.
#
# The same replies are the generation of T-QLT-01 (the Italian quality protocol of docs/prompting.md): its scripts are
# these three conversations. ItalianQuality.psm1, which the unit level covers, computes M1 to M4 for each profile and
# builds one blind sheet that mixes the sampled replies of every profile; test-results/quality/italian-<mode>-<time>/
# holds sheet.md (for the owner), key.json (which profile wrote each id), metrics.json and, once the owner's ratings
# are imported with "scripts\lyrveil.cmd test quality-rating <sheet.md>", verdict.json.

# Pester passes Select, the items after the level in "test quality", to BeforeAll, which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeAll.')]
param([string[]]$Select = @())

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\ItalianQuality.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\QualityLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date

    $script:Catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:ProfileIds = [string[]]@(Get-QualityTextProfileId -Catalog $script:Catalog)
    # Image profile ids are accepted and ignored, so "test quality <image profile>" reaches ImageGrid.Tests.ps1 too.
    $script:Selection = Get-QualitySelection -Select $Select -TextProfileId $script:ProfileIds -ImageProfileId @(Get-QualityImageProfileId -Catalog $script:Catalog)
    $scriptText = [System.IO.File]::ReadAllText((Join-Path $script:Root 'tests\quality\consistency\script.json'), $script:Utf8)
    $script:Plan = Get-QualityConsistencyPlan -Document ($scriptText | ConvertFrom-Json) -Mode $script:Selection.Mode
    $script:ResultFile = Join-Path $script:Root ('test-results\quality\consistency-{0}-{1}.json' -f $script:Plan.Mode.ToLowerInvariant(), $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    $script:ItalianFolder = Join-Path $script:Root ('test-results\quality\italian-{0}-{1}' -f $script:Plan.Mode.ToLowerInvariant(), $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    # What the blind sheet's second part tells the owner about each demo character (docs/character-system.md, Demo characters).
    $script:CharacterNote = @{
        Nerea  = 'cartographer in a port city; informal, dry irony'
        Ilario = 'night-train conductor and amateur astronomer; polite, measured, long sentences'
        Tessa  = 'apprentice alchemist and part-time barista; very informal, lively, short bursts'
    }
    $script:Timeout = @{ Ready = 420; Reply = 300; Job = 600 }
    $script:Record = [ordered]@{
        selection = [string[]]@($Select); mode = $script:Plan.Mode; profiles_selected = $script:Selection.Profiles
        plan = [ordered]@{
            messages = $script:Plan.Messages; probe_ends = $script:Plan.ProbeEnds; change_at = $script:Plan.ChangeAt
            change_character = $script:Plan.ChangeCharacter; smart_cache_pairs = $script:Plan.SmartCachePairs
        }
        timeouts = $script:Timeout; preparation = [ordered]@{}; profiles = [ordered]@{}; smart_cache = [ordered]@{}; swa_padding = [ordered]@{}
        italian = [ordered]@{ test = 'T-QLT-01'; folder = $script:ItalianFolder; metrics = [ordered]@{}; written = $false }
    }
    $script:Problems = New-Object System.Collections.Generic.List[string]
    $script:Characters = @{}
    $script:Material = @{}

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

    function Invoke-Control {
        # Sends Method Path with Body to the test orchestrator's control API; returns what Invoke-GpuServiceApi returns.
        param([string]$Method = 'GET', [string]$Path, [hashtable]$Body = $null, [int]$TimeoutSecond = 60)
        return (Invoke-GpuServiceApi -Root $script:Root -Service 'lyrveil-orchestrator' -Method $Method -Path $Path -Body $Body -TimeoutSecond $TimeoutSecond)
    }

    function Invoke-Backend {
        # Sends Method Path with Body to the test backend's REST API; returns what Invoke-GpuServiceApi returns.
        param([string]$Method = 'GET', [string]$Path, [hashtable]$Body = $null)
        return (Invoke-GpuServiceApi -Root $script:Root -Service 'lyrveil-backend' -Method $Method -Path $Path -Body $Body -TimeoutSecond 60)
    }

    function Wait-TextReady {
        # Asks the orchestrator for mode llm and long-polls its state until LLM_READY, up to TimeoutSecond. Returns
        # $true when it was reached.
        param([int]$TimeoutSecond)
        $null = Invoke-Control -Method 'PUT' -Path '/v1/desired-mode' -Body @{ mode = 'llm'; job_id = 'gpu-test-quality' }
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $path = '/v1/state'
        while ($clock.Elapsed.TotalSeconds -lt $TimeoutSecond) {
            $answer = Invoke-Control -Path $path -TimeoutSecond 40
            if ($answer.Http -eq 200 -and $null -ne $answer.Body) {
                if ((Get-Field $answer.Body 'state') -eq 'LLM_READY') { return $true }
                $path = "/v1/state?since=$([long](Get-Field $answer.Body 'seq'))&wait=20"
            }
            else {
                $path = '/v1/state'
                Start-Sleep -Seconds 1
            }
        }
        return $false
    }

    function Wait-BackendJob {
        # Reads the backend job JobId every 2 s until it is completed, failed or cancelled, or TimeoutSecond passes, and
        # returns its last status, '' when it could not be read.
        param([string]$JobId, [int]$TimeoutSecond)
        $deadline = (Get-Date).AddSeconds($TimeoutSecond)
        $status = ''
        while ((Get-Date) -lt $deadline) {
            $status = [string](Get-Field (Invoke-Backend -Path "/api/v1/jobs/$JobId").Body 'status')
            if (@('completed', 'failed', 'cancelled') -contains $status) { break }
            Start-Sleep -Seconds 2
        }
        return $status
    }

    function Invoke-TextProfileSwitch {
        # Makes ProfileId the backend's active text profile and loads it as Applica ora does: PATCH /settings/models,
        # POST /settings/models/apply, the reload job, then LLM_READY and /api/v1/model naming the profile's file.
        # Returns '' when the profile is loaded and otherwise what went wrong.
        param([string]$ProfileId)
        $textProfile = Get-GpuTestTextProfile -Root $script:Root -Id $ProfileId
        $patch = Invoke-Backend -Method 'PATCH' -Path '/api/v1/settings/models' -Body @{ llm_profile_id = $ProfileId }
        if ($patch.Http -ne 200) { return "PATCH /settings/models for $ProfileId answered HTTP $($patch.Http)" }
        $apply = Invoke-Backend -Method 'POST' -Path '/api/v1/settings/models/apply'
        $jobId = [string](Get-Field $apply.Body 'id')
        if ($apply.Http -ne 202 -or -not $jobId) { return "POST /settings/models/apply answered HTTP $($apply.Http)" }
        $status = Wait-BackendJob -JobId $jobId -TimeoutSecond $script:Timeout.Job
        if ($status -ne 'completed') { return "the reload job for $ProfileId ended as '$status'" }
        if (-not (Wait-TextReady -TimeoutSecond $script:Timeout.Ready)) { return "the text runtime did not reach LLM_READY with $ProfileId" }
        $model = [string](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model').Body 'result')
        $stem = [System.IO.Path]::GetFileNameWithoutExtension([string]$textProfile.file)
        if (-not $model.Contains($stem)) { return "the text runtime reports the model '$model', not $($textProfile.file)" }
        Write-Host "    active text profile: $ProfileId"
        return ''
    }

    function Wait-Message {
        # Reads the newest messages of ConversationId every second until MessageId is complete, failed or interrupted,
        # or TimeoutSecond passes; returns the last version of it read, or $null.
        param([string]$ConversationId, [string]$MessageId, [int]$TimeoutSecond)
        $deadline = (Get-Date).AddSeconds($TimeoutSecond)
        $final = @('complete', 'failed', 'interrupted')
        $message = $null
        while ((Get-Date) -lt $deadline) {
            $items = @(Get-Field (Invoke-Backend -Path "/api/v1/conversations/$ConversationId/messages?limit=4").Body 'items')
            $message = @($items | Where-Object { (Get-Field $_ 'id') -eq $MessageId }) | Select-Object -First 1
            if ($final -contains (Get-Field $message 'status')) { break }
            Start-Sleep -Seconds 1
        }
        return $message
    }

    function Get-TokenCount {
        # Returns the number of tokens of Text for the loaded model (/api/extra/tokencount), or -1 when it failed.
        param([string]$Text)
        if (-not $Text) { return 0 }
        $answer = Invoke-GpuTextPost -Root $script:Root -Path '/api/extra/tokencount' -Body @{ prompt = $Text } -TimeoutSecond 60
        if ($answer.Status -ne 0 -or $null -eq (Get-Field $answer.Body 'value')) { return -1 }
        return [int](Get-Field $answer.Body 'value')
    }

    # Read by the backend's own Python from its standard input: the status and checks_json of the active variant of
    # every character reply of one conversation, from the test database opened read-only. __CONVERSATION__ is
    # replaced by an id that Read-ConversationCheck has checked.
    $script:CheckReader = @'
import json, sqlite3
db = sqlite3.connect('file:/data/db/lyrveil.sqlite3?mode=ro', uri=True)
rows = db.execute(
    "SELECT m.id, m.seq, m.status, v.checks_json FROM messages AS m LEFT JOIN message_variants AS v ON v.id = m.active_variant_id "
    "WHERE m.conversation_id = ? AND m.author = 'character' ORDER BY m.seq", ('__CONVERSATION__',)).fetchall()
print(json.dumps({row[0]: {'seq': row[1], 'status': row[2], 'checks': json.loads(row[3]) if row[3] else None} for row in rows}, ensure_ascii=True))
'@

    function Read-ConversationCheck {
        # Returns a hashtable from reply id to its parsed checks_json, read from the test database, empty when the read
        # failed (recorded as a problem). The id must be letters, digits and hyphens, so it cannot leave the SQL string.
        param([string]$ConversationId)
        $checks = @{}
        if ($ConversationId -notmatch '^[A-Za-z0-9-]+$') { return $checks }
        $run = Invoke-GpuCompose -Root $script:Root -Capture -InputText $script:CheckReader.Replace('__CONVERSATION__', $ConversationId) -Argument @('exec', '-T', 'lyrveil-backend', 'python', '-')
        $last = @($run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1)
        if ($run.Status -ne 0 -or $last.Length -ne 1) {
            $script:Problems.Add("The checks of conversation $ConversationId could not be read (exit $($run.Status)).")
            return $checks
        }
        $parsed = $last[0] | ConvertFrom-Json
        foreach ($property in $parsed.PSObject.Properties) { $checks[$property.Name] = Get-Field $property.Value 'checks' }
        return $checks
    }

    function Open-Conversation {
        # Opens a new conversation with the demo character FirstName and returns its id, '' when the backend refused.
        param([string]$FirstName)
        $answer = Invoke-Backend -Method 'POST' -Path '/api/v1/conversations' -Body @{ character_id = $script:Characters[$FirstName] }
        return [string](Get-Field $answer.Body 'id')
    }

    function Invoke-ConversationStep {
        # Sends Step's text to ConversationId, waits for the reply and returns the record Get-QualityConsistencyScore
        # reads, without the checks, which Read-ConversationCheck adds later. A send that failed is a record with the
        # status 'not_sent'.
        param([string]$ConversationId, $Step, $Character, [string]$Conversation, [string]$ProfileId, [bool]$AfterChange)
        $send = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$ConversationId/messages" -Body @{
            client_message_id = [guid]::NewGuid().ToString(); content = $Step.Text; intent = 'text'
        }
        $replyId = [string](Get-Field (Get-Field $send.Body 'reply') 'id')
        $reply = $null
        if ($send.Http -eq 202 -and $replyId) { $reply = Wait-Message -ConversationId $ConversationId -MessageId $replyId -TimeoutSecond $script:Timeout.Reply }
        $status = if ($null -eq $reply) { if ($replyId) { 'timeout' } else { 'not_sent' } } else { [string](Get-Field $reply 'status') }
        $text = [string](Get-Field $reply 'content')
        $tokens = if ($status -eq 'complete') { Get-TokenCount -Text $text } else { -1 }
        $correct = $null
        if ($Step.Kind -eq 'probe') { $correct = Test-QualityProbeAnswer -Step $Step -Text $text }
        $mark = if ($Step.Kind -eq 'probe') { if ($correct) { ' probe ok' } else { ' probe WRONG' } } else { '' }
        Write-Host ("    {0} {1} #{2} on {3}: {4}, {5} tokens{6}" -f $Character.FirstName, $Conversation, $Step.Number, $ProfileId, $status, $tokens, $mark)
        return @{
            Conversation = $Conversation; ConversationId = $ConversationId; Character = $Character.FirstName; Number = $Step.Number
            Kind = $Step.Kind; Topic = $Step.Topic; Question = $Step.Text; Correct = $correct; Status = $status; ReplyId = $replyId
            Text = $text; Tokens = $tokens; Band = $Character.Band; Narration = $Character.Narration; Profile = $ProfileId
            AfterChange = $AfterChange; Spans = (Measure-QualityNarrationSpan -Text $text); Checks = $null; Normalizer = $null
        }
    }

    function Add-ConversationCheck {
        # Sets Checks and Normalizer of each record of Reply from the checks_json of its conversation.
        param([object[]]$Reply)
        foreach ($conversationId in @($Reply | ForEach-Object { $_.ConversationId } | Where-Object { $_ } | Select-Object -Unique)) {
            $read = Read-ConversationCheck -ConversationId $conversationId
            foreach ($item in @($Reply | Where-Object { $_.ConversationId -eq $conversationId -and $_.ReplyId })) {
                $stored = $read[$item.ReplyId]
                $item.Checks = Get-Field $stored 'checks'
                $item.Normalizer = Get-Field $stored 'normalizer'
            }
        }
    }

    function Invoke-ProfileRun {
        # Runs the protocol for ProfileId: the main conversations, then C6 from the next catalog profile, and returns
        # the records of every planned reply. Stops a conversation at the first reply that did not complete, since the
        # rest of its script would no longer be the protocol; the missing replies count as not complete.
        param([string]$ProfileId)
        $records = New-Object System.Collections.Generic.List[object]
        $problem = Invoke-TextProfileSwitch -ProfileId $ProfileId
        if ($problem) {
            $script:Problems.Add($problem)
            return @()
        }
        foreach ($character in $script:Plan.Characters) {
            $id = Open-Conversation -FirstName $character.FirstName
            foreach ($step in $character.Steps) {
                $item = if ($id) { Invoke-ConversationStep -ConversationId $id -Step $step -Character $character -Conversation 'main' -ProfileId $ProfileId -AfterChange $false } else { $null }
                if ($null -eq $item) { $item = @{ Conversation = 'main'; ConversationId = ''; Character = $character.FirstName; Number = $step.Number; Kind = $step.Kind; Status = 'not_sent'; ReplyId = ''; Correct = $false; Tokens = -1; Band = $character.Band; Narration = $character.Narration; Spans = 0; Checks = $null; Normalizer = $null; AfterChange = $false } }
                $records.Add($item)
                if ($item.Status -ne 'complete') { $id = '' }
            }
        }
        $changeFrom = Get-QualityChangeProfile -ProfileId $ProfileId -TextProfileId $script:ProfileIds
        if ($changeFrom) {
            $character = @($script:Plan.Characters | Where-Object { $_.FirstName -ceq $script:Plan.ChangeCharacter })[0]
            $problem = Invoke-TextProfileSwitch -ProfileId $changeFrom
            $active = $changeFrom
            $id = if ($problem) { $script:Problems.Add($problem); '' } else { Open-Conversation -FirstName $character.FirstName }
            foreach ($step in $character.Steps) {
                $after = $step.Number -gt $script:Plan.ChangeAt
                if ($after -and $id -and $active -ne $ProfileId) {
                    $problem = Invoke-TextProfileSwitch -ProfileId $ProfileId
                    $active = $ProfileId
                    if ($problem) {
                        $script:Problems.Add($problem)
                        $id = ''
                    }
                }
                $generator = if ($after) { $ProfileId } else { $changeFrom }
                $item = if ($id) { Invoke-ConversationStep -ConversationId $id -Step $step -Character $character -Conversation 'change' -ProfileId $generator -AfterChange $after } else { $null }
                if ($null -eq $item) { $item = @{ Conversation = 'change'; ConversationId = ''; Character = $character.FirstName; Number = $step.Number; Kind = $step.Kind; Status = 'not_sent'; ReplyId = ''; Correct = $false; Tokens = -1; Band = $character.Band; Narration = $character.Narration; Spans = 0; Checks = $null; Normalizer = $null; AfterChange = $after } }
                $records.Add($item)
                if ($item.Status -ne 'complete') { $id = '' }
            }
            # The profile must be the measured one again even when the change conversation stopped early.
            if ($active -ne $ProfileId) { $null = Invoke-TextProfileSwitch -ProfileId $ProfileId }
        }
        $all = $records.ToArray()
        Add-ConversationCheck -Reply $all
        $script:Record.profiles[$ProfileId] = [ordered]@{ change_from = $changeFrom }
        return $all
    }

    function Save-SmartCacheMaterial {
        # Keeps, for ProfileId, the stored prompts of the last 2 x SmartCachePairs replies of its first main
        # conversation, in order, from the diagnostics of each reply. Returns the number of prompts kept.
        param([string]$ProfileId, [object[]]$Reply)
        $first = $script:Plan.Characters[0].FirstName
        $replies = @($Reply | Where-Object { $_.Conversation -eq 'main' -and $_.Character -eq $first -and $_.Status -eq 'complete' } | Sort-Object { $_.Number })
        $wanted = 2 * $script:Plan.SmartCachePairs
        $chosen = @($replies | Select-Object -Last $wanted)
        $prompts = New-Object System.Collections.Generic.List[string]
        foreach ($item in $chosen) {
            $text = [string](Get-Field (Invoke-Backend -Path "/api/v1/messages/$($item.ReplyId)/diagnostics").Body 'prompt_text')
            if ($text) { $prompts.Add($text) }
        }
        if ($prompts.Count -ne $wanted) { $script:Problems.Add("$ProfileId kept $($prompts.Count) chat prompts for SmartCache, not $wanted.") }
        $script:Material[$ProfileId] = $prompts.ToArray()
        return $prompts.Count
    }

    function Invoke-SmartCacheTrial {
        # Starts lyrveil-llm alone with the level A of TextProfile and SMART_CACHE Value, and replays each chat prompt
        # pair: the first prompt, the extraction prompt of a T-QLT-06 window under its grammar (the structured task),
        # then the second prompt, whose last_process (/api/extra/perf) is the measurement. docker stats of the runtime
        # is read after every request for the peak RAM. Returns the trial record of Get-QualitySmartCacheDecision.
        param($TextProfile, [int]$Value, [string[]]$Prompt, $Structured)
        $trial = @{ Value = $Value; ProcessValue = [double[]]@(); PeakRamMiB = -1.0; DockerMemoryMiB = (Get-GpuDockerMemoryTotal); Problems = @(); Launch = @() }
        $problems = New-Object System.Collections.Generic.List[string]
        $lines = Get-QualityTrialLaunchLine -Line (Get-GpuLaunchLine -TextProfile $TextProfile -Level A) -Setting @{ SMART_CACHE = "$Value" }
        $trial.Launch = $lines
        $null = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
        if (-not (Set-GpuTestLaunchLine -Root $script:Root -Line $lines -AsBackendUser)) { $problems.Add('The launch file could not be written.') }
        elseif ((Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm' -TimeoutSecond 240) -ne 0) { $problems.Add('lyrveil-llm did not become healthy.') }
        else {
            $context = [int](Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value')
            $seconds = New-Object System.Collections.Generic.List[double]
            $peak = -1.0
            $ids = @($Structured.ids)
            for ($pair = 0; $pair -lt [math]::Floor($Prompt.Length / 2); $pair++) {
                $structuredPrompt = [string](Get-Field $Structured.prompts ([string]$ids[$pair % $ids.Length]))
                $requests = @(
                    @{ prompt = $Prompt[2 * $pair]; max_length = 8 },
                    @{ prompt = $structuredPrompt; max_length = 64; grammar = [string]$Structured.grammar; temperature = [double]$Structured.temperature },
                    @{ prompt = $Prompt[2 * $pair + 1]; max_length = 8 })
                foreach ($request in $requests) {
                    $request.max_context_length = $context
                    $request.genkey = 'gputest' + [guid]::NewGuid().ToString('N').Substring(0, 12)
                    $answer = Invoke-GpuTextTimed -Root $script:Root -Path '/api/v1/generate' -Body $request -TimeoutSecond 180
                    if ($answer.Status -ne 0) { $problems.Add("A generation of pair $($pair + 1) failed (curl $($answer.Status)).") }
                    $memory = Get-GpuTestContainerMemory -Root $script:Root -Service 'lyrveil-llm'
                    if ($memory -gt $peak) { $peak = $memory }
                }
                $process = Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/perf').Body 'last_process'
                if ($null -eq $process) { $problems.Add("Pair $($pair + 1) reported no last_process.") }
                else { $seconds.Add([double]$process) }
            }
            $trial.ProcessValue = [double[]]$seconds.ToArray()
            $trial.PeakRamMiB = $peak
        }
        $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
        $trial.Problems = $problems.ToArray()
        Write-Host ("    SmartCache {0} on {1}: last_process {2}, peak RAM {3} MiB of {4} MiB" -f $Value, $TextProfile.id, (@($trial.ProcessValue) -join ', '), $trial.PeakRamMiB, $trial.DockerMemoryMiB)
        return $trial
    }

    $prep = $script:Record.preparation
    Open-GpuCommandLog
    $replies = @{}
    try {
        if ($script:Selection.Unknown.Length -gt 0) { throw "Unknown items: $($script:Selection.Unknown -join ', ')." }
        $first = Get-GpuTestTextProfile -Root $script:Root -Id $script:Selection.Profiles[0]
        $prep.stack_built = Initialize-GpuStackImage -Root $script:Root
        $prep.probe_built = $prep.stack_built -and (Initialize-GpuBackendProbe -Root $script:Root)
        # The backend writes the launch file when it changes the profile, so it owns runtime/ as an installation's does,
        # and this file writes as that owner too; giving it again is harmless when Extraction.Tests.ps1 ran first.
        $prep.owner_set = $prep.probe_built -and (Set-GpuTestDataOwner -Root $script:Root -WithRuntime)
        $prep.launch_written = $prep.owner_set -and (Set-GpuTestLaunchLine -Root $script:Root -Line (Get-GpuLaunchLine -TextProfile $first -Level A) -AsBackendUser)
        $prep.containers_created = $prep.launch_written -and ((New-GpuTestRuntimeContainer -Root $script:Root) -eq 0)
        if ($prep.containers_created) {
            $prep.orchestrator_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
            $prep.backend_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-backend' -TimeoutSecond 180
        }
        if ($prep.Contains('backend_up') -and $prep.orchestrator_up -eq 0 -and $prep.backend_up -eq 0) {
            $prep.diagnostic_mode = (Invoke-Backend -Method 'PATCH' -Path '/api/v1/settings' -Body @{ 'app.diagnostic_mode' = $true }).Http
            $null = Invoke-Backend -Method 'POST' -Path '/api/v1/characters/demo'
            foreach ($item in @(Get-Field (Invoke-Backend -Path '/api/v1/characters').Body 'items')) {
                if ($null -ne $item) { $script:Characters[[string](Get-Field $item 'first_name')] = [string](Get-Field $item 'id') }
            }
            $cards = [ordered]@{}
            foreach ($character in $script:Plan.Characters) {
                $id = $script:Characters[$character.FirstName]
                if (-not $id) { $script:Problems.Add("The demo character $($character.FirstName) was not loaded."); continue }
                $communication = Get-Field (Get-Field (Invoke-Backend -Path "/api/v1/characters/$id").Body 'card') 'communication'
                $cards[$character.FirstName] = [ordered]@{ band = Get-Field $communication 'message_length'; narration = Get-Field $communication 'narration'; question_frequency = Get-Field $communication 'question_frequency' }
                $card = $cards[$character.FirstName]
                if ($card.band -ne $character.Band -or $card.narration -ne $character.Narration -or $card.question_frequency -ne $character.QuestionFrequency) {
                    $script:Problems.Add("The card of $($character.FirstName) no longer matches the script's band, narration and question frequency.")
                }
            }
            $prep.cards = $cards
        }
        else { $script:Problems.Add('The test stack was not prepared.') }

        if ($script:Problems.Count -eq 0) {
            $structuredProbe = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'prompt' -Body @{ family = [string]$first.template; ids = $null }
            foreach ($profileId in $script:Selection.Profiles) {
                Write-Host "    T-QLT-05 $($script:Plan.Mode) on ${profileId}: $($script:Plan.Characters.Length) conversations of $($script:Plan.Messages) messages, then C6"
                $replies[$profileId] = Invoke-ProfileRun -ProfileId $profileId
                $null = Save-SmartCacheMaterial -ProfileId $profileId -Reply $replies[$profileId]
            }
            # The trials start lyrveil-llm by themselves, so nothing else may start or use a runtime meanwhile.
            foreach ($service in @('lyrveil-backend', 'lyrveil-orchestrator', 'lyrveil-image', 'lyrveil-llm')) {
                $null = Remove-GpuTestRuntime -Root $script:Root -Service $service
            }
            foreach ($profileId in $script:Selection.Profiles) {
                $textProfile = Get-GpuTestTextProfile -Root $script:Root -Id $profileId
                $family = Invoke-GpuBackendProbe -Root $script:Root -Probe 'extraction_eval' -Action 'prompt' -Body @{ family = [string]$textProfile.template; ids = $null }
                if ($family.Status -ne 0 -or $null -eq $family.Body) { $family = $structuredProbe }
                $trials = @()
                if (@($script:Material[$profileId]).Length -ge 2 -and $null -ne $family.Body) {
                    foreach ($value in @(0, 2)) { $trials += Invoke-SmartCacheTrial -TextProfile $textProfile -Value $value -Prompt $script:Material[$profileId] -Structured $family.Body }
                }
                $decision = Get-QualitySmartCacheDecision -Trial $trials
                $decision.catalog_allows_2 = ([string]$textProfile.min_tier -eq 'target')
                $decision.catalog_value = [int]$textProfile.smart_cache
                $script:Record.smart_cache[$profileId] = [ordered]@{ trials = $trials; decision = $decision }
                $swa = Get-QualitySwaPaddingPlan -TextProfile $textProfile
                $script:Record.swa_padding[$profileId] = if ($swa.Applicable) {
                    [ordered]@{ applicable = $true; decision = 'undecided'; reason = 'The SWA padding trials run only for a gemma4 profile, and the catalog has none since ADR 0045; this file has no runner for them.' }
                }
                else { [ordered]@{ applicable = $false; decision = 'not_applicable'; reason = $swa.Reason } }
            }
        }
    }
    catch { $script:Problems.Add("The protocol stopped: $($_.Exception.Message)") }
    finally { $prep.commands = [string[]](Close-GpuCommandLog) }

    foreach ($profileId in @($replies.Keys)) {
        $entry = $script:Record.profiles[$profileId]
        if ($null -eq $entry) { $entry = [ordered]@{ change_from = '' } }
        $entry.score = Get-QualityConsistencyScore -ProfileId $profileId -Reply $replies[$profileId] -ChangeFrom $entry.change_from
        $entry.replies = @($replies[$profileId] | Where-Object { $null -ne $_ } | ForEach-Object {
                [ordered]@{
                    conversation = $_.Conversation; character = $_.Character; number = $_.Number; kind = $_.Kind; topic = (Get-Field $_ 'Topic')
                    question = (Get-Field $_ 'Question'); profile = (Get-Field $_ 'Profile'); after_change = $_.AfterChange; status = $_.Status
                    correct = $_.Correct; tokens = $_.Tokens; spans = $_.Spans; checks = $_.Checks; normalizer = $_.Normalizer; text = (Get-Field $_ 'Text')
                }
            })
        $script:Record.profiles[$profileId] = $entry
        $score = $entry.score
        Write-Host ("    {0}: hard targets {1}; C1 {2}/{3}, C2 {4} per 200, C4 {5}, C6 {6}" -f $profileId, $(if ($score.HardPassed) { 'met' } else { 'MISSED' }), $score.C1.correct, $score.C1.probes, $score.C2.per_200, $score.C4.passed, $score.C6.passed)
    }

    # T-QLT-01 reads the replies of these same conversations (the protocol's three demo characters, time jumps,
    # corrections and a long topic change): M1 to M4 are computed for each profile, and one blind sheet mixes the
    # sampled replies of every profile selected, so the owner rates M5 and M6 without knowing the profile.
    try {
        $frequency = @{}
        foreach ($character in $script:Plan.Characters) { $frequency[$character.FirstName] = $character.QuestionFrequency }
        $metrics = @{}
        foreach ($profileId in @($replies.Keys)) {
            $metrics[$profileId] = Get-ItalianMetricScore -ProfileId $profileId -Reply $replies[$profileId] -QuestionFrequency $frequency
            $script:Record.italian.metrics[$profileId] = $metrics[$profileId]
            $m = $metrics[$profileId]
            Write-Host ("    {0}: T-QLT-01 automatic metrics {1}; M1 {2}, M2 {3} per 100, M3 {4}, M4 {5}" -f $profileId, $(if ($m.automatic_passed) { 'met' } else { 'MISSED' }), $m.M1.italian_rate, $m.M2.per_100, $m.M3.rate, $m.M4.passed)
        }
        if ($metrics.Count -gt 0) {
            $names = [string[]]@($script:Plan.Characters | ForEach-Object { $_.FirstName })
            $blind = Get-ItalianBlindSheet -ReplyByProfile $replies -CharacterName $names -CharacterNote $script:CharacterNote -Seed (Get-Random -Maximum 1000000) -Mode $script:Plan.Mode
            $paths = Write-ItalianArtifact -Folder $script:ItalianFolder -Blind $blind -Metric $metrics
            $script:Record.italian.written = $true
            $script:Record.italian.sheet = $paths.Sheet
            $script:Record.italian.evidence = $blind.Key.evidence
            $script:Record.italian.items = $blind.Key.items.Count
            Write-Host "    T-QLT-01 blind sheet: $($paths.Sheet)"
        }
    }
    catch { $script:Problems.Add("The T-QLT-01 artifacts could not be written: $($_.Exception.Message)") }
}

AfterAll {
    # The orchestrator goes before the runtimes, so it cannot start one of them again while they are removed.
    foreach ($service in @('lyrveil-backend', 'lyrveil-orchestrator', 'lyrveil-image', 'lyrveil-llm')) {
        $null = Remove-GpuTestRuntime -Root $script:Root -Service $service
    }
    $script:Record.released_after = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
    $script:Record.problems = $script:Problems.ToArray()
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        test         = 'T-QLT-05'
        file         = 'tests/quality/Consistency.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        evidence     = $(if ($script:Plan.Mode -eq 'Full') { 'protocol' } else { 'smoke run only: the short mode is no evidence for the catalog' })
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 14), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-QLT-05: the consistency protocol' {
    It 'takes only short and catalog text profiles' {
        $script:Selection.Unknown | Should -BeNullOrEmpty -Because 'Consistency takes short and catalog text profile ids'
    }

    It 'prepares the stack and loads demo characters that match the script' {
        $script:Problems | Should -BeNullOrEmpty
        $script:Record.preparation.stack_built | Should -BeTrue
        $script:Record.preparation.diagnostic_mode | Should -Be 200
    }

    It 'completes every planned reply on every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $entry = $script:Record.profiles[$profileId]
            $entry | Should -Not -BeNullOrEmpty -Because "$profileId must have run"
            $entry.score.not_complete | Should -Be 0 -Because "every planned reply on $profileId must complete"
            $entry.score.all_complete | Should -BeTrue
        }
    }

    It 'meets the hard targets C1, C2, C4 and C6 on every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $score = (Get-Field $script:Record.profiles[$profileId] 'score')
            $score | Should -Not -BeNullOrEmpty -Because "$profileId must have been scored"
            $missed = @(@('C1', 'C2', 'C4', 'C6') | Where-Object { -not $score[$_].passed })
            $missed | Should -BeNullOrEmpty -Because "$profileId must meet every hard target"
            $score.HardPassed | Should -BeTrue
        }
    }

    It 'writes the T-QLT-01 metrics and a blind sheet that names no profile' {
        $script:Record.italian.written | Should -BeTrue -Because 'the sheet, key and metrics of T-QLT-01 must be written'
        $sheet = [System.IO.File]::ReadAllText((Join-Path $script:ItalianFolder 'sheet.md'), $script:Utf8)
        foreach ($profileId in $script:Selection.Profiles) {
            $script:Record.italian.metrics[$profileId] | Should -Not -BeNullOrEmpty -Because "$profileId must have T-QLT-01 metrics"
            $sheet | Should -Not -Match ([regex]::Escape($profileId)) -Because 'the sheet is blind'
        }
    }

    It 'meets the automatic targets M1 to M4 of T-QLT-01 on every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $metrics = $script:Record.italian.metrics[$profileId]
            $metrics | Should -Not -BeNullOrEmpty -Because "$profileId must have T-QLT-01 metrics"
            $missed = @(@('M1', 'M2', 'M3', 'M4') | Where-Object { -not $metrics[$_].passed })
            $missed | Should -BeNullOrEmpty -Because "$profileId must meet M1 to M4; the owner's M5 and M6 come from the blind sheet"
        }
    }

    It 'decides SmartCache from both trials for every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $entry = $script:Record.smart_cache[$profileId]
            $entry | Should -Not -BeNullOrEmpty -Because "$profileId must have SmartCache trials"
            $entry.decision.decision | Should -Not -Be 'undecided' -Because (@($entry.decision.reasons) -join ' ')
        }
    }

    It 'records the SWA padding rule for every selected profile' {
        foreach ($profileId in $script:Selection.Profiles) {
            $entry = $script:Record.swa_padding[$profileId]
            $entry | Should -Not -BeNullOrEmpty
            $entry.decision | Should -Not -Be 'undecided' -Because ([string]$entry.reason)
        }
    }
}
