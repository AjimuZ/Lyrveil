# T-GPU-13: a two-hour session of 3 conversations and 20 images on the real stack of the project lyrveil-gpu-test, driven automatically, with VRAM, RAM, errors and latency recorded and judged.
#
# Opt-in: a plain "scripts\lyrveil.cmd test gpu" leaves this file out (TestCommand.psm1). Run it with
# "scripts\lyrveil.cmd test gpu Session" for the two hours, or with "scripts\lyrveil.cmd test gpu Session short" for the
# same driver over 150 s with 2 images. It prepares the stack like Resilience.Tests.ps1: the test-only orchestrator, the
# backend, the level-A launch file of the default text profile and both runtime containers, which the orchestrator
# starts and stops. The plan, the driver and the verdict are in scripts/lib/GpuSession.psm1, which the unit level covers.
# test-results/gpu/session-<mode>-<time>.json, ignored by Git, holds every sample, every event and the verdict.

# Pester passes Select, the items after the file name in "test gpu", to BeforeAll, which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeAll.')]
param([string[]]$Select = @())

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuSession.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date

    $script:Selection = [string[]]@($Select | Where-Object { $_ })
    $script:Unknown = [string[]]@($script:Selection | Where-Object { $_ -cne 'short' })
    $script:Mode = if ($script:Selection -contains 'short') { 'Short' } else { 'Full' }
    $script:Plan = Get-GpuSessionPlan -Mode $script:Mode
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\session-{0}-{1}.json' -f $script:Plan.Mode, $script:StartedAt.ToString('yyyyMMdd-HHmmss'))

    $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:TextProfile = Get-GpuTestTextProfile -Root $script:Root -Id ([string]@($catalog.text | Where-Object { @($_.default_tiers) -contains 'target' })[0].id)
    $script:Workload = [ordered]@{
        plan = $script:Plan; text_profile = $script:TextProfile.id; ready_timeout_s = 420; reply_timeout_s = 300; image_timeout_s = 420
        image_request = 'quality rapida, aspect square'
    }
    $script:TextPrompts = @(
        'Come e andata la giornata?', 'Raccontami che cosa hai fatto stamattina.', 'Che cosa ti piace del porto di sera?',
        'Hai mai pensato di partire?', 'Cosa porteresti con te in un lungo viaggio?', 'Dimmi qualcosa che ti ha fatto sorridere oggi.',
        'Che musica ascolti quando sei sola?', 'Come immagini la casa dei tuoi sogni?', 'Qual e il tuo ricordo piu caro?')
    $script:ImageRequests = @(
        'Lei al porto, al tramonto, mentre studia una mappa.', 'Lei in una libreria antica, con una tazza di te in mano.',
        'Lei su una terrazza sul mare, con il vento tra i capelli.', 'Lei in un mercato al mattino, tra le bancarelle di frutta.',
        'Lei davanti a una finestra, mentre piove sulla citta.')
    $script:Record = [ordered]@{ selection = $script:Selection; workload = $script:Workload; preparation = [ordered]@{} }
    $script:Conversations = @{}
    $script:Characters = @()
    $script:OwnerSet = $false
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

    function Get-OrchestratorState {
        # Returns the orchestrator's state document, or $null when it did not answer.
        $answer = Invoke-Control -Path '/v1/state'
        if ($answer.Http -ne 200) { return $null }
        return $answer.Body
    }

    function Wait-TextReady {
        # Asks the orchestrator for mode llm and long-polls its state until LLM_READY, up to TimeoutSecond. Returns
        # $true when it was reached.
        param([int]$TimeoutSecond)
        $null = Invoke-Control -Method 'PUT' -Path '/v1/desired-mode' -Body @{ mode = 'llm'; job_id = 'gpu-test-session' }
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

    function Get-SessionConversation {
        # Returns the id of the conversation with number Index (zero-based), opening it on the first call with the
        # character of that number, so the conversations differ; '' when the backend refused. A refusal is retried at
        # the next call.
        param([int]$Index)
        if ($script:Conversations.ContainsKey($Index)) { return $script:Conversations[$Index] }
        if (@($script:Characters).Length -eq 0) {
            $null = Invoke-Backend -Method 'POST' -Path '/api/v1/characters/demo'
            $script:Characters = @(@(Get-Field (Invoke-Backend -Path '/api/v1/characters').Body 'items') | Where-Object { $null -ne $_ } | ForEach-Object { [string](Get-Field $_ 'id') })
        }
        if (@($script:Characters).Length -eq 0) { return '' }
        $characterId = $script:Characters[$Index % @($script:Characters).Length]
        $conversation = Invoke-Backend -Method 'POST' -Path '/api/v1/conversations' -Body @{ character_id = $characterId }
        $id = [string](Get-Field $conversation.Body 'id')
        if ($id) { $script:Conversations[$Index] = $id }
        return $id
    }

    function Wait-Message {
        # Reads the messages of ConversationId every 2 s until MessageId is complete, failed or interrupted, or
        # TimeoutSecond passes; returns the last version of it read, or $null.
        param([string]$ConversationId, [string]$MessageId, [int]$TimeoutSecond)
        $deadline = (Get-Date).AddSeconds($TimeoutSecond)
        $final = @('complete', 'failed', 'interrupted')
        $message = $null
        while ((Get-Date) -lt $deadline) {
            $items = @(Get-Field (Invoke-Backend -Path "/api/v1/conversations/$ConversationId/messages?limit=20").Body 'items')
            $message = @($items | Where-Object { (Get-Field $_ 'id') -eq $MessageId }) | Select-Object -First 1
            if ($final -contains (Get-Field $message 'status')) { break }
            Start-Sleep -Seconds 2
        }
        return $message
    }

    function Test-SessionMessage {
        # Returns '' when the send was accepted, the message completed and its job completed (with Content when Text),
        # and otherwise the first thing that was not as expected, for the record of the failed event.
        param([int]$SendStatus, $Message, $Job, [switch]$Text)
        if ($SendStatus -ne 202) { return "the send answered HTTP $SendStatus" }
        $status = [string](Get-Field $Message 'status')
        if ($status -ne 'complete') { return "the message ended as '$status'" }
        if ($Text -and -not ([string](Get-Field $Message 'content')).Trim()) { return 'the reply has no text' }
        if (-not $Text -and -not (Get-Field $Message 'image_id')) { return 'the message has no image' }
        $jobStatus = [string](Get-Field $Job 'status')
        if ($jobStatus -ne 'completed') { return "the job ended as '$jobStatus'" }
        return ''
    }

    # The actions of the driver. Each returns @{ Ok; Detail; Error } and is timed by the driver.
    $script:TextAction = {
        param($Conversation, $Number)
        $id = Get-SessionConversation -Index $Conversation
        if (-not $id) { return @{ Ok = $false; Error = 'The conversation could not be opened.' } }
        $prompt = $script:TextPrompts[$Number % $script:TextPrompts.Length]
        $send = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$id/messages" -Body @{
            client_message_id = [guid]::NewGuid().ToString(); content = $prompt; intent = 'text'
        }
        $replyId = [string](Get-Field (Get-Field $send.Body 'reply') 'id')
        $jobId = [string](Get-Field (Get-Field $send.Body 'job') 'id')
        $reply = $null
        if ($replyId) { $reply = Wait-Message -ConversationId $id -MessageId $replyId -TimeoutSecond $script:Workload.reply_timeout_s }
        $job = $null
        if ($jobId) { $job = (Invoke-Backend -Path "/api/v1/jobs/$jobId").Body }
        $problem = Test-SessionMessage -SendStatus $send.Http -Message $reply -Job $job -Text
        $length = ([string](Get-Field $reply 'content')).Length
        Write-Host ("    text {0}, conversation {1}: {2}" -f ($Number + 1), ($Conversation + 1), $(if ($problem) { "FAILED, $problem" } else { "ok, $length characters" }))
        return @{ Ok = ($problem -eq ''); Error = $problem; Detail = @{ reply_id = $replyId; job_id = $jobId; characters = $length } }
    }
    $script:ImageAction = {
        param($Conversation, $Number)
        $id = Get-SessionConversation -Index $Conversation
        if (-not $id) { return @{ Ok = $false; Error = 'The conversation could not be opened.' } }
        $request = $script:ImageRequests[$Number % $script:ImageRequests.Length]
        $picture = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$id/images" -Body @{ request = $request; aspect = 'square'; quality = 'rapida' }
        $messageId = [string](Get-Field (Get-Field $picture.Body 'reply') 'id')
        $jobId = [string](Get-Field (Get-Field $picture.Body 'job') 'id')
        $message = $null
        if ($messageId) { $message = Wait-Message -ConversationId $id -MessageId $messageId -TimeoutSecond $script:Workload.image_timeout_s }
        $job = $null
        if ($jobId) { $job = (Invoke-Backend -Path "/api/v1/jobs/$jobId").Body }
        $problem = Test-SessionMessage -SendStatus $picture.Http -Message $message -Job $job
        Write-Host ("    image {0}, conversation {1}: {2}" -f ($Number + 1), ($Conversation + 1), $(if ($problem) { "FAILED, $problem" } else { 'ok' }))
        return @{ Ok = ($problem -eq ''); Error = $problem; Detail = @{ message_id = $messageId; job_id = $jobId } }
    }
    $script:SampleAction = {
        $card = Get-GpuSample
        $free = -1
        try { $free = [math]::Round([double](Get-CimInstance -ClassName Win32_OperatingSystem).FreePhysicalMemory / 1024, 0) }
        catch { Write-Verbose 'The host RAM could not be read.' }
        return @{
            VramMiB = $(if ($card.Available) { $card.UsedMiB } else { -1 }); HostRamFreeMiB = $free
            ContainerMiB = (Get-GpuTestMemory); State = [string](Get-Field (Get-OrchestratorState) 'state')
        }
    }

    $prep = $script:Record.preparation
    Open-GpuCommandLog
    $sampler = $null
    try {
        $prep.stack_built = Initialize-GpuStackImage -Root $script:Root
        $prep.launch_written = $prep.stack_built -and (Set-GpuTestTextLaunch -Root $script:Root -TextProfile $script:TextProfile -Level A)
        $prep.containers_created = $prep.launch_written -and ((New-GpuTestRuntimeContainer -Root $script:Root) -eq 0)
        if ($prep.containers_created) {
            $prep.orchestrator_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
            $script:OwnerSet = Set-GpuTestDataOwner -Root $script:Root
            $prep.backend_up = if ($script:OwnerSet) { Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-backend' -TimeoutSecond 180 } else { 1 }
            $prep.text_ready = ($prep.orchestrator_up -eq 0 -and $prep.backend_up -eq 0) -and (Wait-TextReady -TimeoutSecond $script:Workload.ready_timeout_s)
        }
        if ($prep.text_ready) {
            Write-Host ("    T-GPU-13 {0}: {1} s, {2} images, {3} conversations" -f $script:Plan.Mode, $script:Plan.DurationSecond, $script:Plan.Images, $script:Plan.Conversations)
            $sampler = Start-GpuMemorySampler
            $script:Record.session = Invoke-GpuSession -Plan $script:Plan -Sample $script:SampleAction -Text $script:TextAction -Image $script:ImageAction
        }
        else { $script:Problems.Add('The test stack was not prepared or the text runtime did not load.') }
    }
    catch { $script:Problems.Add("The session stopped: $($_.Exception.Message)") }
    finally {
        if ($null -ne $sampler) { $script:Record.nvml_fast = Stop-GpuMemorySampler -Job $sampler }
        $prep.commands = [string[]](Close-GpuCommandLog)
    }
    if ($script:Record.Contains('session')) {
        $script:Record.verdict = Get-GpuSessionVerdict -Record $script:Record.session
        foreach ($problem in $script:Record.verdict.Problems) { Write-Host "    $problem" }
        Write-Host ("    verdict: {0}; {1} events, {2} errors" -f $(if ($script:Record.verdict.Passed) { 'passed' } else { 'FAILED' }), $script:Record.verdict.Summary.Events, $script:Record.verdict.Summary.Errors)
    }
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
        test         = 'T-GPU-13'
        file         = 'tests/gpu/Session.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 12), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-GPU-13: the session' {
    It 'selects only the item short' {
        $script:Unknown | Should -BeNullOrEmpty -Because 'the only item Session takes is short'
    }

    It 'prepares the stack and loads the text runtime' {
        $script:Problems | Should -BeNullOrEmpty
        $script:Record.preparation.stack_built | Should -BeTrue
        $script:Record.preparation.text_ready | Should -BeTrue
    }

    It 'runs its whole plan without stopping early' {
        $session = Get-Field $script:Record 'session'
        $session | Should -Not -BeNullOrEmpty -Because 'the session must have run'
        Get-Field $session 'Aborted' | Should -BeFalse -Because ([string](Get-Field $session 'AbortReason'))
        Get-Field $session 'ElapsedSecond' | Should -BeGreaterOrEqual $script:Plan.DurationSecond
    }

    It 'passes the verdict: no error, every image and conversation, VRAM and RAM within limits' {
        $verdict = Get-Field $script:Record 'verdict'
        $verdict | Should -Not -BeNullOrEmpty -Because 'the session must have been judged'
        Get-Field $verdict 'Problems' | Should -BeNullOrEmpty
        Get-Field $verdict 'Passed' | Should -BeTrue
    }
}
