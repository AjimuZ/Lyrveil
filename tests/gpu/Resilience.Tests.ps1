# T-GPU-10 to T-GPU-12: the stack of the project lyrveil-gpu-test works with no way out of its internal networks, and the stop of stop.cmd leaves no process of it running and gives all the GPU memory back; a real out-of-memory at load (T-GPU-10) is written but pending GPU.7b.
#
# Run only by "scripts\lyrveil.cmd test gpu" (alone with "scripts\lyrveil.cmd test gpu Resilience"). Like
# ModeSwitch.Tests.ps1 it builds the test-only orchestrator and the backend's runtime image as ":gpu-test", writes the
# level-A launch file of the default text profile and creates the two runtime containers without starting them, so
# the real orchestrator starts and stops them. Every step runs in BeforeAll, so the numbers are recorded even when a
# check fails; test-results/gpu/resilience-<time>.json, ignored by Git, holds them.
#
# The items after the file name select parts: t-gpu-12 and t-gpu-11 run only those, in file order (T-GPU-11 stops the
# stack, so it runs last). Without items both run. T-GPU-10 runs only alone, with
# "scripts\lyrveil.cmd test gpu Resilience t-gpu-10", because its forced launch file and the VRAM load it starts would
# change what the other parts measure. In every other run its tests are reported as skipped, pending GPU.7b, with the
# measured reason: they cannot pass on this machine yet and are never counted as passed.

# Pester passes Select, the items after the file name in "test gpu", to BeforeDiscovery and BeforeAll, which the
# analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeDiscovery and BeforeAll.')]
param([string[]]$Select = @())

BeforeDiscovery {
    $parts = [string[]]@($Select | Where-Object { $_ -match '^t-gpu-1[012]$' })
    $script:RunsPart = @{}
    foreach ($part in @('t-gpu-11', 't-gpu-12')) {
        $script:RunsPart[$part] = ($parts -notcontains 't-gpu-10') -and ($parts.Length -eq 0 -or $parts -contains $part)
    }
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuResilience.psm1') -Force
    # The stop of stop.cmd itself, Invoke-LyrveilStop, which T-GPU-11 runs on the test project.
    Import-Module (Join-Path $script:Root 'scripts\lib\Lifecycle.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\resilience-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    $script:Settings = Get-GpuTestSetting

    $script:Selection = [string[]]@($Select | Where-Object { $_ })
    $script:Unknown = [string[]]@($script:Selection | Where-Object { $_ -notmatch '^t-gpu-1[012]$' })
    $named = [string[]]@($script:Selection | Where-Object { $_ -match '^t-gpu-1[012]$' })
    $script:RunsOom = ($named -contains 't-gpu-10')
    $script:Combined = ($script:RunsOom -and $named.Length -gt 1)
    $script:Parts = [string[]]@('t-gpu-12', 't-gpu-11')
    if ($script:RunsOom) { $script:Parts = [string[]]@('t-gpu-10') }
    elseif ($named.Length -gt 0) { $script:Parts = [string[]]@($script:Parts | Where-Object { $named -contains $_ }) }

    # The default text profile of the target tier, the reference machine's since ADR 0027, as in ModeSwitch.Tests.ps1.
    $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:TextProfile = Get-GpuTestTextProfile -Root $script:Root -Id ([string]@($catalog.text | Where-Object { @($_.default_tiers) -contains 'target' })[0].id)
    $script:Workload = [ordered]@{
        parts = $script:Parts; text_profile = $script:TextProfile.id
        oom_launch = 'level A of the profile with GPU_LAYERS=99, CONTEXT_SIZE=oom_context_size and BATCH_SIZE=oom_batch_size'
        oom_context_size = 24576; oom_batch_size = 2048; oom_free_mib = 6912; llm_required_mib = 6000; lowered_level = 'B'
        ready_timeout_s = 420; reply_timeout_s = 300; image_timeout_s = 420
        stop_tolerance_mib = 128; release_wait_s = 60; restart_watch_s = 10
        offline_services = @('lyrveil-backend', 'lyrveil-orchestrator', 'lyrveil-llm', 'lyrveil-image')
    }
    # Why T-GPU-10 cannot pass yet, measured on the reference machine (docs/verification/2026-11-26-gpu-resilience.md).
    $script:Pending = 'pending GPU.7b, which needs an owner decision: on Windows 11 with WSL2 the NVIDIA driver falls back to shared ' +
        'system memory instead of failing the allocation. Measured twice on 2026-11-26: with the test VRAM load holding 4352 MiB, CUDA ' +
        'still reported 11090 MiB free to the text runtime, and the forced level-A load put 11924 MiB of buffers on CUDA0 with no ' +
        'allocation failure while NVML saw it take about 6724 MiB. Run it alone: scripts\lyrveil.cmd test gpu Resilience t-gpu-10'
    $script:Record = [ordered]@{ selection = $script:Selection; workload = $script:Workload; preparation = [ordered]@{} }
    $script:VramLoadId = ''
    $script:OwnerSet = $false

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

    function Get-GpuState {
        # Returns the orchestrator's state document, or $null when it did not answer.
        $answer = Invoke-Control -Path '/v1/state'
        if ($answer.Http -ne 200) { return $null }
        return $answer.Body
    }

    function Wait-GpuState {
        <#
        Long-polls the orchestrator's state until Condition, a script block given the state document, returns $true,
        or TimeoutSecond passes. Returns @{ Reached; State; Seconds }: State is the last document read, $null when none
        was, and Seconds the time waited as this process saw it.
        #>
        param([scriptblock]$Condition, [int]$TimeoutSecond = 300)
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $state = $null
        $path = '/v1/state'
        while ($clock.Elapsed.TotalSeconds -lt $TimeoutSecond) {
            $answer = Invoke-Control -Path $path -TimeoutSecond 40
            if ($answer.Http -eq 200 -and $null -ne $answer.Body) {
                $state = $answer.Body
                if (& $Condition $state) { return @{ Reached = $true; State = $state; Seconds = [math]::Round($clock.Elapsed.TotalSeconds, 2) } }
                $path = "/v1/state?since=$([long](Get-Field $state 'seq'))&wait=20"
            }
            else {
                $path = '/v1/state'
                Start-Sleep -Seconds 1
            }
        }
        return @{ Reached = $false; State = $state; Seconds = [math]::Round($clock.Elapsed.TotalSeconds, 2) }
    }

    function Request-GpuMode {
        # Asks the orchestrator for Mode on behalf of JobId, as the backend's scheduler does; returns $true for 202.
        param([string]$Mode, [string]$JobId)
        return ((Invoke-Control -Method 'PUT' -Path '/v1/desired-mode' -Body @{ mode = $Mode; job_id = $JobId }).Http -eq 202)
    }

    function Get-ContainerState {
        # Returns the State document Docker reports for the container of Service, or $null when there is none.
        param([string]$Service)
        return (Get-GpuTestContainerFact -Root $script:Root -Service $Service -Template '{{json .State}}')
    }

    function Get-ServiceState {
        # Returns the state of the container of Service as Get-GpuStoppedProblem reads it; Known is $false without one.
        param([string]$Service)
        $state = Get-ContainerState -Service $Service
        if ($null -eq $state) { return [ordered]@{ Service = $Service; Known = $false } }
        return [ordered]@{
            Service = $Service; Known = $true; Ran = -not ([string](Get-Field $state 'StartedAt')).StartsWith('0001-01-01')
            Running = ((Get-Field $state 'Running') -eq $true); Restarting = ((Get-Field $state 'Restarting') -eq $true)
            Status = [string](Get-Field $state 'Status'); ExitCode = Get-Field $state 'ExitCode'
            OOMKilled = ((Get-Field $state 'OOMKilled') -eq $true); StartedAt = [string](Get-Field $state 'StartedAt')
            FinishedAt = [string](Get-Field $state 'FinishedAt')
        }
    }

    function Confirm-TextReady {
        # Starts the orchestrator when it is not running, asks for mode llm and waits up to TimeoutSecond for
        # LLM_READY. Returns what Wait-GpuState returns.
        param([int]$TimeoutSecond = 300)
        if ((Get-Field (Get-ContainerState -Service 'lyrveil-orchestrator') 'Running') -ne $true) {
            $null = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
        }
        $null = Request-GpuMode -Mode 'llm' -JobId 'gpu-test-text'
        return (Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'LLM_READY' } -TimeoutSecond $TimeoutSecond)
    }

    function Confirm-BackendReady {
        # Starts the test backend when it is not running, on the test data volume owned by 10001 as setup.cmd leaves a
        # volume, and returns 0 when it runs healthy.
        if ((Get-Field (Get-ContainerState -Service 'lyrveil-backend') 'Running') -eq $true) { return 0 }
        if (-not $script:OwnerSet) { $script:OwnerSet = Set-GpuTestDataOwner -Root $script:Root }
        return (Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-backend' -TimeoutSecond 180)
    }

    function Get-LifecycleEvent {
        # Returns every event the orchestrator still buffers after sequence number After, oldest first.
        param([long]$After = 0)
        return @(@(Get-Field (Invoke-Control -Path '/v1/events').Body 'events') | Where-Object { $null -ne $_ -and [long](Get-Field $_ 'seq') -gt $After })
    }

    function Open-TestConversation {
        # Loads the demo characters, or takes the first character when they are loaded already, opens a conversation
        # with it and returns its id, or '' when the backend refused.
        $demo = Invoke-Backend -Method 'POST' -Path '/api/v1/characters/demo'
        $characterId = [string](Get-Field (@(Get-Field $demo.Body 'created') | Select-Object -First 1) 'id')
        if (-not $characterId) { $characterId = [string](Get-Field (@(Get-Field (Invoke-Backend -Path '/api/v1/characters').Body 'items') | Select-Object -First 1) 'id') }
        if (-not $characterId) { return '' }
        $conversation = Invoke-Backend -Method 'POST' -Path '/api/v1/conversations' -Body @{ character_id = $characterId }
        return [string](Get-Field $conversation.Body 'id')
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

    function Invoke-Reply {
        # Sends Content as a text message to ConversationId and waits for the character's reply. Returns its record:
        # the send's HTTP status, the reply and its job as the backend reports them at the end, and the seconds from
        # the send to the end as this process saw them.
        param([string]$ConversationId, [string]$Content)
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $send = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$ConversationId/messages" -Body @{
            client_message_id = [guid]::NewGuid().ToString(); content = $Content; intent = 'text'
        }
        $record = [ordered]@{
            send_status = $send.Http; reply_id = [string](Get-Field (Get-Field $send.Body 'reply') 'id')
            job_id = [string](Get-Field (Get-Field $send.Body 'job') 'id'); status_at_send = [string](Get-Field (Get-Field $send.Body 'reply') 'status')
            reply = $null; job = $null; seconds = $null
        }
        if ($record.reply_id) {
            $record.reply = Wait-Message -ConversationId $ConversationId -MessageId $record.reply_id -TimeoutSecond $script:Workload.reply_timeout_s
        }
        if ($record.job_id) { $record.job = (Invoke-Backend -Path "/api/v1/jobs/$($record.job_id)").Body }
        $record.seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        return $record
    }

    function Invoke-Picture {
        # Asks ConversationId for a Rapida square image and waits for it. When DuringRender is given, it runs once the
        # orchestrator reports the image runtime ready or rendering, and its result is stored as during_render.
        # Returns the record: the request's HTTP status, the message and its job at the end, and the seconds taken.
        param([string]$ConversationId, [scriptblock]$DuringRender = $null)
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $picture = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$ConversationId/images" -Body @{
            request = 'Lei al porto, al tramonto, mentre studia una mappa.'; aspect = 'square'; quality = 'rapida'
        }
        $record = [ordered]@{
            status = $picture.Http; job_id = [string](Get-Field (Get-Field $picture.Body 'job') 'id')
            message_id = [string](Get-Field (Get-Field $picture.Body 'reply') 'id'); message = $null; job = $null
            image_runtime_seen = $false; during_render = $null; seconds = $null
        }
        if ($record.job_id -and $null -ne $DuringRender) {
            $render = Wait-GpuState -Condition { param($s) @('IMAGE_READY', 'GENERATING_IMAGE') -contains (Get-Field $s 'state') } -TimeoutSecond $script:Workload.image_timeout_s
            $record.image_runtime_seen = $render.Reached
            if ($render.Reached) { $record.during_render = & $DuringRender }
        }
        if ($record.message_id) {
            $record.message = Wait-Message -ConversationId $ConversationId -MessageId $record.message_id -TimeoutSecond $script:Workload.image_timeout_s
        }
        if ($record.job_id) { $record.job = (Invoke-Backend -Path "/api/v1/jobs/$($record.job_id)").Body }
        $record.seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        return $record
    }

    # The preparation every part needs. Each step is stored as it ends, and a failed one stops the rest.
    $prep = $script:Record.preparation
    Open-GpuCommandLog
    try {
        $prep.stack_built = Initialize-GpuStackImage -Root $script:Root
        $prep.launch_written = $prep.stack_built -and (Set-GpuTestTextLaunch -Root $script:Root -TextProfile $script:TextProfile -Level A)
        $prep.containers_created = $prep.launch_written -and ((New-GpuTestRuntimeContainer -Root $script:Root) -eq 0)
    }
    finally { $prep.commands = [string[]](Close-GpuCommandLog) }
    $script:Ready = [bool]$prep.containers_created
}

AfterAll {
    # The VRAM load is the test's own container, named by the id its start returned. The orchestrator goes before the
    # runtimes, so it cannot start one of them again while they are removed.
    if ($script:VramLoadId) { $null = Stop-GpuVramLoad -Id $script:VramLoadId }
    foreach ($service in @('lyrveil-backend', 'lyrveil-orchestrator', 'lyrveil-image', 'lyrveil-llm')) {
        $null = Remove-GpuTestRuntime -Root $script:Root -Service $service
    }
    $script:Record.released_after = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + 1024) -TimeoutSecond 90
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        tests        = 'T-GPU-10, T-GPU-11, T-GPU-12'
        file         = 'tests/gpu/Resilience.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 12), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'Resilience: the selection and the test stack' {
    It 'selects only parts it knows, and t-gpu-10 alone' {
        $script:Unknown | Should -BeNullOrEmpty
        $script:Combined | Should -BeFalse -Because 't-gpu-10 changes the launch file and holds VRAM, so it runs alone'
    }

    It 'builds the test orchestrator and the backend, writes the launch file and creates the runtime containers' {
        $script:Record.preparation.stack_built | Should -BeTrue
        $script:Record.preparation.launch_written | Should -BeTrue
        $script:Record.preparation.containers_created | Should -BeTrue
    }
}

Describe 'T-GPU-10: a real CUDA out-of-memory at load is handled' {
    BeforeAll {
        $script:Oom = [ordered]@{ selected = $script:RunsOom; problems = @() }
        $script:Record.t_gpu_10 = $script:Oom
        if (-not $script:RunsOom) {
            $script:Oom.pending = $script:Pending
            return
        }
        $problems = New-Object System.Collections.Generic.List[string]
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                # Level A with every layer forced on the GPU and a context and batch too large for the memory that the
                # VRAM load leaves, above the 6000 MiB the orchestrator requires, so the load itself must fail.
                $lines = Get-GpuOomLaunchLine -Line (Get-GpuLaunchLine -TextProfile $script:TextProfile -Level A) -ContextSize $script:Workload.oom_context_size -BatchSize $script:Workload.oom_batch_size
                $script:Oom.launch_before = $lines
                $script:Oom.orchestrator_ran_before = ((Get-Field (Get-ContainerState -Service 'lyrveil-orchestrator') 'Running') -eq $true)
                $script:Oom.launch_written = Set-GpuTestLaunchLine -Root $script:Root -Line $lines
                # The backend lowers the level by replacing the launch file, so it must own runtime/ as it would own
                # the folder it wrote itself.
                $script:Oom.owner_set = Set-GpuTestDataOwner -Root $script:Root -WithRuntime
                $script:OwnerSet = $script:Oom.owner_set
                $script:Oom.backend_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-backend' -TimeoutSecond 180
                if (-not $script:Oom.launch_written -or -not $script:Oom.owner_set -or $script:Oom.backend_up -ne 0) {
                    $problems.Add('The forced launch file or the backend was not ready.')
                }
                else {
                    $load = Start-GpuVramLoad -Root $script:Root -FreeMiB $script:Workload.oom_free_mib
                    $script:VramLoadId = $load.Id
                    $script:Oom.load_status = $load.Status
                    $held = $null
                    if ($load.Id) {
                        $deadline = (Get-Date).AddSeconds(120)
                        do {
                            Start-Sleep -Seconds 2
                            $held = Get-GpuVramLoad -Id $load.Id
                        } while ($held.Running -and $held.HeldMiB -eq 0 -and (Get-Date) -lt $deadline)
                    }
                    $script:Oom.load_before = $held
                    $script:Oom.nvml_with_load = Get-GpuSample
                    if ($null -eq $held -or -not $held.Running -or $held.HeldMiB -eq 0) { $problems.Add('The VRAM load did not start or hold its memory.') }
                    else {
                        $script:Oom.orchestrator_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
                        $null = Request-GpuMode -Mode 'llm' -JobId 't-gpu-10-llm'
                        $wait = Wait-GpuState -TimeoutSecond $script:Workload.ready_timeout_s -Condition {
                            param($s)
                            (Get-Field (Get-Field $s 'load_failure') 'mode') -eq 'llm' -or @('LLM_READY', 'ERROR') -contains (Get-Field $s 'state')
                        }
                        $script:Oom.after_failure = $wait.State
                        $script:Oom.failure_seconds = $wait.Seconds
                        $log = @(Get-GpuTestServiceLog -Root $script:Root -Service 'lyrveil-llm' -Tail 300)
                        $script:Oom.load_log_lines = $log.Length
                        $script:Oom.cuda_request = Get-GpuCudaRequest -Line $log
                        $script:Oom.allocation_failure_lines = @($log | Where-Object { $_ -match 'out of memory|cudaMalloc failed|failed to allocate|unable to allocate' }).Length
                        $final = Wait-GpuState -Condition { param($s) @('LLM_READY', 'ERROR') -contains (Get-Field $s 'state') } -TimeoutSecond $script:Workload.ready_timeout_s
                        $script:Oom.final = $final.State
                        $script:Oom.ready = ((Get-Field $final.State 'state') -eq 'LLM_READY')
                        $script:Oom.ready_seconds = $final.Seconds
                        $script:Oom.nvml_at_ready = Get-GpuSample
                        $script:Oom.events = @(Get-LifecycleEvent)
                        $launch = Read-GpuTestLaunchFile -Root $script:Root
                        $script:Oom.launch_read = $launch.Status
                        $script:Oom.launch_after = $launch.Line
                        if ($script:Oom.ready) {
                            $script:Oom.true_max_context = Get-Field (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/extra/true_max_context_length').Body 'value'
                            $conversation = Open-TestConversation
                            $script:Oom.conversation_opened = [bool]$conversation
                            if ($conversation) { $script:Oom.reply = Invoke-Reply -ConversationId $conversation -Content 'Come va il lavoro oggi?' }
                        }
                        $script:Oom.load_during = Get-GpuVramLoad -Id $load.Id
                        $script:Oom.load_removed = ((Stop-GpuVramLoad -Id $load.Id) -eq 0)
                        if ($script:Oom.load_removed) { $script:VramLoadId = '' }
                        if ($null -eq (Get-Field $script:Oom.after_failure 'load_failure')) {
                            $script:Oom.no_failure_reason = 'CUDA reported {0} MiB free to the text runtime and its first load put {1} MiB of buffers on CUDA0 without an allocation failure' -f $script:Oom.cuda_request.FreeMiB, $script:Oom.cuda_request.RequestedMiB
                        }
                    }
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }
        $script:Oom.problems = $problems.ToArray()
        Write-Host ("    load failure: {0}; final state {1}; launch level {2}; reply {3} s" -f (Get-Field (Get-Field $script:Oom['after_failure'] 'load_failure') 'code'), (Get-Field $script:Oom['final'] 'state'), (@($script:Oom['launch_after']) | Where-Object { $_ -like 'LEVEL=*' }), (Get-Field $script:Oom['reply'] 'seconds'))
    }

    It 'holds its VRAM load and lets the orchestrator start the forced level-A load' {
        if (-not $script:RunsOom) { Set-ItResult -Skipped -Because $script:Pending; return }
        $script:Oom.problems | Should -BeNullOrEmpty
        $script:Oom['orchestrator_ran_before'] | Should -BeFalse
        $script:Oom['orchestrator_up'] | Should -Be 0
    }

    It 'ends that load with LLM_LOAD_FAILED and an allocation failure' {
        if (-not $script:RunsOom) { Set-ItResult -Skipped -Because $script:Pending; return }
        $failure = Get-Field $script:Oom['after_failure'] 'load_failure'
        $failure | Should -Not -BeNullOrEmpty -Because ([string]$script:Oom['no_failure_reason'])
        Get-Field $failure 'code' | Should -Be 'LLM_LOAD_FAILED'
        Get-Field $failure 'oom' | Should -BeTrue
    }

    It 'lowers the launch file to level B' {
        if (-not $script:RunsOom) { Set-ItResult -Skipped -Because $script:Pending; return }
        $script:Oom['launch_read'] | Should -Be 0
        @($script:Oom['launch_after']) | Should -Contain "LEVEL=$($script:Workload.lowered_level)"
    }

    It 'loads the text runtime at level B and answers' {
        if (-not $script:RunsOom) { Set-ItResult -Skipped -Because $script:Pending; return }
        Get-Field (Get-Field $script:Oom['after_failure'] 'load_failure') 'oom' | Should -BeTrue -Because 'the ready load must follow the failed one'
        @($script:Oom['launch_after']) | Should -Contain "LEVEL=$($script:Workload.lowered_level)"
        $script:Oom['ready'] | Should -BeTrue
        Get-Field (Get-Field $script:Oom['reply'] 'reply') 'status' | Should -Be 'complete'
    }

    It 'leaves its VRAM load running and untouched until the test removes it' {
        if (-not $script:RunsOom) { Set-ItResult -Skipped -Because $script:Pending; return }
        $before = $script:Oom['load_before']
        $during = $script:Oom['load_during']
        Get-Field $during 'Running' | Should -BeTrue
        Get-Field $during 'Pid' | Should -Be (Get-Field $before 'Pid')
        Get-Field $during 'StartedAt' | Should -Be (Get-Field $before 'StartedAt')
        Get-Field $during 'RestartCount' | Should -Be 0
        $script:Oom['load_removed'] | Should -BeTrue
    }
}

Describe 'T-GPU-12: everything works with no way out of the internal networks' -Skip:(-not $script:RunsPart['t-gpu-12']) {
    BeforeAll {
        $script:Offline = [ordered]@{ problems = @() }
        $script:Record.t_gpu_12 = $script:Offline
        $problems = New-Object System.Collections.Generic.List[string]
        $probes = @{}
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                $start = Confirm-TextReady -TimeoutSecond $script:Workload.ready_timeout_s
                $script:Offline.ready_at_start = $start.Reached
                $script:Offline.backend_up = Confirm-BackendReady
                if (-not $start.Reached -or $script:Offline.backend_up -ne 0) { $problems.Add('The text runtime or the backend was not ready.') }
                else {
                    # The control: a container on a network with a route out has a default route, so the probe can
                    # tell the two apart. It opens no connection.
                    $control = Invoke-GpuRouteControl -Root $script:Root
                    $script:Offline.control = [ordered]@{ Status = $control.Status; DefaultRoute = (ConvertFrom-GpuRouteProbe -Line $control.Line).DefaultRoute }
                    foreach ($service in @('lyrveil-backend', 'lyrveil-orchestrator', 'lyrveil-llm')) {
                        $probes[$service] = Invoke-GpuRouteProbe -Root $script:Root -Service $service
                    }
                    $seqBefore = [long](Get-Field (Get-GpuState) 'seq')
                    $script:Offline.seq_before = $seqBefore
                    $conversation = Open-TestConversation
                    $script:Offline.conversation_opened = [bool]$conversation
                    if (-not $conversation) { $problems.Add('No conversation could be opened.') }
                    else {
                        $script:Offline.first_reply = Invoke-Reply -ConversationId $conversation -Content 'Come passi le serate al porto?'
                        # The image runtime is probed while it runs: the orchestrator stops it once the render ends.
                        $script:Offline.picture = Invoke-Picture -ConversationId $conversation -DuringRender { Invoke-GpuRouteProbe -Root $script:Root -Service 'lyrveil-image' }
                        if ($null -ne $script:Offline.picture.during_render) { $probes['lyrveil-image'] = $script:Offline.picture.during_render }
                        $script:Offline.second_reply = Invoke-Reply -ConversationId $conversation -Content 'Bella foto. E adesso dove vai?'
                    }
                    $script:Offline.events = @(Get-LifecycleEvent -After $seqBefore)
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }

        # Every service of the stack, whether it runs now or not: its networks and ports, and the probe it ran.
        $network = New-Object System.Collections.Generic.List[object]
        foreach ($service in $script:Workload.offline_services) {
            $fact = Get-GpuTestNetworkFact -Root $script:Root -Service $service
            $fact.ProbeStatus = $null
            $fact.ProbeLine = @()
            $fact.Probe = $null
            if ($probes.ContainsKey($service)) {
                $fact.ProbeStatus = $probes[$service].Status
                $fact.ProbeLine = @($probes[$service].Line | Where-Object { $_ -notlike 'route*' })
                $fact.Probe = ConvertFrom-GpuRouteProbe -Line $probes[$service].Line
            }
            $network.Add($fact)
        }
        $script:Offline.network = $network.ToArray()
        $script:Offline.network_problems = Get-GpuOfflineProblem -Network $network.ToArray()
        $script:Offline.problems = $problems.ToArray()
        foreach ($problem in @($script:Offline.network_problems)) { if ($problem) { Write-Host "    network: $problem" } }
        Write-Host ("    replies {0} and {1}, image {2}; {3} s, {4} s and {5} s" -f (Get-Field (Get-Field $script:Offline['first_reply'] 'reply') 'status'), (Get-Field (Get-Field $script:Offline['second_reply'] 'reply') 'status'), (Get-Field (Get-Field $script:Offline['picture'] 'message') 'status'), (Get-Field $script:Offline['first_reply'] 'seconds'), (Get-Field $script:Offline['second_reply'] 'seconds'), (Get-Field $script:Offline['picture'] 'seconds'))
    }

    It 'starts the text runtime and the backend' {
        $script:Offline.problems | Should -BeNullOrEmpty
        $script:Offline['backend_up'] | Should -Be 0
    }

    It 'probes the routes of all four services, the image runtime while it renders' {
        @($script:Offline.network).Length | Should -Be @($script:Workload.offline_services).Length
        foreach ($record in $script:Offline.network) {
            Get-Field $record 'ProbeStatus' | Should -Be 0 -Because "the route probe of $(Get-Field $record 'Service') must run"
            Get-Field (Get-Field $record 'Probe') 'Complete' | Should -BeTrue -Because "the route probe of $(Get-Field $record 'Service') must report its routes, its connection and its lookup"
        }
    }

    It 'keeps every service on internal networks, with no published port, no default route, no connection out and no public name' {
        $script:Offline.network_problems | Should -BeNullOrEmpty
    }

    It 'gives a container on a network with a route out a default route, so the route check can fail' {
        Get-Field $script:Offline['control'] 'Status' | Should -Be 0
        Get-Field $script:Offline['control'] 'DefaultRoute' | Should -BeTrue
    }

    It 'answers a text with the real model' {
        $reply = $script:Offline['first_reply']
        Get-Field $reply 'send_status' | Should -Be 202
        Get-Field (Get-Field $reply 'reply') 'status' | Should -Be 'complete'
        ([string](Get-Field (Get-Field $reply 'reply') 'content')).Trim() | Should -Not -BeNullOrEmpty
        Get-Field (Get-Field $reply 'job') 'status' | Should -Be 'completed'
    }

    It 'renders an image with the real image runtime' {
        $picture = $script:Offline['picture']
        Get-Field $picture 'status' | Should -Be 202
        Get-Field $picture 'image_runtime_seen' | Should -BeTrue
        Get-Field (Get-Field $picture 'message') 'status' | Should -Be 'complete'
        Get-Field (Get-Field $picture 'message') 'image_id' | Should -Not -BeNullOrEmpty
        Get-Field (Get-Field $picture 'job') 'status' | Should -Be 'completed'
    }

    It 'answers again after the switch back to the text runtime' {
        $reply = $script:Offline['second_reply']
        Get-Field (Get-Field $reply 'reply') 'status' | Should -Be 'complete'
        Get-Field (Get-Field $reply 'job') 'status' | Should -Be 'completed'
    }
}

Describe 'T-GPU-11: stop.cmd leaves no orphan process and gives all the GPU memory back' -Skip:(-not $script:RunsPart['t-gpu-11']) {
    BeforeAll {
        $script:Stop = [ordered]@{ problems = @() }
        $script:Record.t_gpu_11 = $script:Stop
        $problems = New-Object System.Collections.Generic.List[string]
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                $start = Confirm-TextReady -TimeoutSecond $script:Workload.ready_timeout_s
                $script:Stop.ready_at_start = $start.Reached
                $script:Stop.backend_up = Confirm-BackendReady
                if (-not $start.Reached -or $script:Stop.backend_up -ne 0) { $problems.Add('The text runtime or the backend was not ready.') }
                else {
                    $script:Stop.nvml_before = Get-GpuSample
                    # The processes of every running container, then the same PIDs seen from the Docker VM itself, as
                    # the control that the probe sees them while they run.
                    $listed = New-Object System.Collections.Generic.List[object]
                    foreach ($service in $script:Workload.offline_services) {
                        if ((Get-Field (Get-ContainerState -Service $service) 'Running') -ne $true) { continue }
                        foreach ($process in @(ConvertFrom-DockerTopText -Line (Get-GpuTestServiceTop -Root $script:Root -Service $service).Line)) {
                            $listed.Add([pscustomobject]@{ Service = $service; Pid = $process.Pid; Command = $process.Command })
                        }
                    }
                    $script:Stop.processes = $listed.ToArray()
                    $ids = [int[]]@($listed | ForEach-Object { $_.Pid })
                    $pattern = Get-GpuRuntimePattern
                    $before = Invoke-GpuHostProcessProbe -Root $script:Root -ProcessId $ids -Pattern $pattern
                    $script:Stop.probe_before_status = $before.Status
                    $script:Stop.seen_before = @(ConvertFrom-GpuHostProcessText -Line $before.Line)
                    $script:Stop.runtime_seen_before = @($script:Stop.seen_before | Where-Object { $command = $_.Command; @($pattern | Where-Object { $command.Contains($_) }).Length -gt 0 }).Length

                    # The stop of stop.cmd, on this project only: its env file does not exist, so Compose reads
                    # .env.example and never the installation's .env, and its backup folder is in Pester's test drive.
                    $backups = Join-Path $TestDrive 'backups'
                    $clock = [System.Diagnostics.Stopwatch]::StartNew()
                    $output = @(Invoke-LyrveilStop -Root $script:Root -ProjectName $script:Settings.Project -ComposeFile @('compose.yaml', 'tests/gpu/compose.gpu-test.yaml') -EnvPath (Join-Path $TestDrive 'no-installation.env') -BackupFolder $backups 6>&1)
                    $script:Stop.stop_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
                    $status = @($output | Where-Object { $_ -is [int] })
                    $script:Stop.stop_status = if ($status.Length -gt 0) { $status[-1] } else { $null }
                    $script:Stop.stop_output = [string[]]@($output | Where-Object { $_ -isnot [int] } | ForEach-Object { [string]$(if ($_ -is [System.Management.Automation.InformationRecord]) { $_.MessageData } else { $_ }) })
                    foreach ($line in $script:Stop.stop_output) { Write-Host "      $line" }
                    $script:Stop.warnings = [string[]]@($script:Stop.stop_output | Where-Object { $_ -match '^WARNING\b' })

                    $after = Invoke-GpuHostProcessProbe -Root $script:Root -ProcessId $ids -Pattern $pattern
                    $script:Stop.probe_after_status = $after.Status
                    $script:Stop.seen_after = @(ConvertFrom-GpuHostProcessText -Line $after.Line)
                    $script:Stop.stopped = @($script:Workload.offline_services | ForEach-Object { Get-ServiceState -Service $_ })
                    $script:Stop.released = Wait-GpuMemoryBelow -LimitMiB ($script:BaselineMiB + $script:Workload.stop_tolerance_mib) -TimeoutSecond $script:Workload.release_wait_s
                    $script:Stop.nvml_after = Get-GpuSample
                    Start-Sleep -Seconds $script:Workload.restart_watch_s
                    $script:Stop.later = @($script:Workload.offline_services | ForEach-Object { Get-ServiceState -Service $_ })
                    $script:Stop.backup_folder_created = Test-Path -LiteralPath $backups
                    $script:Stop.orchestrator_events = @(ConvertFrom-GpuEventLog -Line (Get-GpuTestServiceLog -Root $script:Root -Service 'lyrveil-orchestrator' -Tail 500))
                    $script:Stop.drain = Get-GpuDrainEvent -Transition $script:Stop.orchestrator_events
                    $script:Stop.process_problems = Get-GpuOrphanProblem -Listed $script:Stop.processes -SeenBefore $script:Stop.seen_before -SeenAfter $script:Stop.seen_after
                    $script:Stop.container_problems = Get-GpuStoppedProblem -Stopped $script:Stop.stopped -Later $script:Stop.later
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }
        $script:Stop.problems = $problems.ToArray()
        foreach ($problem in @($script:Stop['process_problems']) + @($script:Stop['container_problems'])) { if ($problem) { Write-Host "    $problem" } }
        Write-Host ("    VRAM {0} MiB before the stop, {1} MiB after it (baseline {2} MiB); stop took {3} s" -f (Get-Field $script:Stop['nvml_before'] 'UsedMiB'), (Get-Field $script:Stop['nvml_after'] 'UsedMiB'), $script:BaselineMiB, $script:Stop['stop_seconds'])
    }

    It 'runs the stop of stop.cmd on the test project, which succeeds with only the warning about backups' {
        $script:Stop.problems | Should -BeNullOrEmpty
        $script:Stop['stop_status'] | Should -Be 0
        # The test data volume has no installation label, so the copy is refused and no backup folder is written.
        foreach ($warning in @($script:Stop['warnings'])) { $warning | Should -Match '^WARNING\s+Backups: not copied' }
        $script:Stop['backup_folder_created'] | Should -BeFalse
    }

    It 'drains the GPU first: the orchestrator stops the text runtime and verifies its release' {
        Get-Field $script:Stop['drain'] 'Unloading' | Should -Not -BeNullOrEmpty
        Get-Field $script:Stop['drain'] 'Released' | Should -Not -BeNullOrEmpty
    }

    It 'leaves every container stopped with an exit code, and none starts again' {
        @($script:Stop['stopped']).Length | Should -Be 4
        $script:Stop['container_problems'] | Should -BeNullOrEmpty
    }

    It 'leaves no process of the stack and no runtime process in the Docker VM' {
        $script:Stop['probe_before_status'] | Should -Be 0
        $script:Stop['probe_after_status'] | Should -Be 0
        $script:Stop['runtime_seen_before'] | Should -BeGreaterThan 0 -Because 'the probe must see the text runtime while it runs'
        $script:Stop['process_problems'] | Should -BeNullOrEmpty
    }

    It 'gives the GPU memory back to the baseline of the run' {
        $script:Stop['released'] | Should -BeTrue
        ((Get-Field $script:Stop['nvml_after'] 'UsedMiB') - $script:BaselineMiB) | Should -BeLessThan $script:Workload.stop_tolerance_mib
    }
}
