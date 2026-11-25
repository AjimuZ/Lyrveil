# T-GPU-07 to T-GPU-09: the real orchestrator moves the GPU between the real text and image runtimes of the project lyrveil-gpu-test, holds a load back while another program holds the VRAM, and the real backend answers a text sent during a real render once the text model is back.
#
# Run only by "scripts\lyrveil.cmd test gpu" (alone with "scripts\lyrveil.cmd test gpu ModeSwitch"). It builds the
# test-only orchestrator (stage gpu-test-runtime of orchestrator/Dockerfile: the real control API, lifecycle manager,
# supervisor and NVML probe, with a container gate that acts on this project's two runtimes only) and the backend's
# runtime image, both tagged ":gpu-test", writes the level-A launch file of the default text profile, creates the two
# runtime containers without starting them and lets the orchestrator start and stop them. The tests reach the control
# API and the backend from inside their containers (Invoke-GpuServiceApi). Every step runs in BeforeAll, so the numbers
# are recorded even when a check fails; test-results/gpu/mode-switches-<time>.json, ignored by Git, holds them.
#
# The items after the file name select parts: t-gpu-07, t-gpu-08 and t-gpu-09 run only those tests, in file order, and
# rounds-<n> (1 to 10) gives T-GPU-07 n round trips instead of ten, such as
# "scripts\lyrveil.cmd test gpu ModeSwitch t-gpu-07 rounds-5". A part not selected is reported as skipped. T-GPU-07
# asks for ten round trips; a run with fewer is a partial run, and its results file says so.

# Pester passes Select, the items after the file name in "test gpu", to BeforeDiscovery and BeforeAll, which the
# analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeDiscovery and BeforeAll.')]
param([string[]]$Select = @())

BeforeDiscovery {
    $parts = [string[]]@($Select | Where-Object { $_ -match '^t-gpu-0[789]$' })
    $script:RunsPart = @{}
    foreach ($part in @('t-gpu-07', 't-gpu-08', 't-gpu-09')) { $script:RunsPart[$part] = ($parts.Length -eq 0 -or $parts -contains $part) }
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuSwitches.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\mode-switches-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))

    $script:Selection = [string[]]@($Select | Where-Object { $_ })
    $script:Unknown = [string[]]@($script:Selection | Where-Object { $_ -notmatch '^t-gpu-0[789]$' -and $_ -notmatch '^rounds-([1-9]|10)$' })
    $script:Parts = [string[]]@($script:Selection | Where-Object { $_ -match '^t-gpu-0[789]$' })
    if ($script:Parts.Length -eq 0) { $script:Parts = [string[]]@('t-gpu-07', 't-gpu-08', 't-gpu-09') }
    $rounds = 10
    foreach ($item in $script:Selection) { if ($item -match '^rounds-([1-9]|10)$') { $rounds = [int]$Matches[1] } }

    # The defaults of the target tier, the reference machine's since ADR 0027: the profiles a full switch is planned
    # with (docs/gpu-orchestration.md, A-06 of docs/risks.md).
    $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:TextProfile = Get-GpuTestTextProfile -Root $script:Root -Id ([string]@($catalog.text | Where-Object { @($_.default_tiers) -contains 'target' })[0].id)
    $imageProfile = @($catalog.image | Where-Object { @($_.default_tiers) -contains 'target' })[0]
    # The resident and released limits split the NVML samples into model phases: a loaded model of either runtime
    # holds several GiB above the baseline, a released card at most the desktop's own use plus a CUDA context.
    $script:Workload = [ordered]@{
        parts = $script:Parts; rounds = $rounds; rounds_asked_by_t_gpu_07 = 10
        text_profile = $script:TextProfile.id; image_profile = [string]$imageProfile.id; image_checkpoint = [string]$imageProfile.file
        image_graph = 'Get-GpuImageWorkflow: 768 x 768, 8 steps'; text_max_length = 16
        vram_load_free_mib = 3072; resident_mib = $script:BaselineMiB + 4096; released_mib = $script:BaselineMiB + 1024
        switch_timeout_s = 300; render_timeout_s = 240; reply_timeout_s = 420; hold_observed_s = 12; steady_s = 4
        planning_target_s = 150
    }
    $script:Record = [ordered]@{ selection = $script:Selection; workload = $script:Workload; preparation = [ordered]@{} }
    $script:VramLoadId = ''

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

    function Request-GpuLease {
        # Asks for the lease of Mode for JobId, as the backend does before it uses a runtime, retrying a refusal for up
        # to 30 s. Returns the lease document, or $null when none was granted.
        param([string]$Mode, [string]$JobId)
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            $answer = Invoke-Control -Method 'POST' -Path '/v1/leases' -Body @{ mode = $Mode; job_id = $JobId; ttl_s = 120 }
            if ($answer.Http -eq 201 -and $null -ne $answer.Body) { return $answer.Body }
            Start-Sleep -Seconds 1
        }
        return $null
    }

    function Complete-GpuLease {
        # Releases Lease with the outcome ok and returns $true when the orchestrator accepted the release.
        param($Lease)
        return ((Invoke-Control -Method 'DELETE' -Path "/v1/leases/$(Get-Field $Lease 'id')" -Body @{ outcome = 'ok' }).Http -eq 200)
    }

    function Get-ContainerState {
        # Returns the State document Docker reports for the container of Service, or $null when there is none.
        param([string]$Service)
        return (Get-GpuTestContainerFact -Root $script:Root -Service $Service -Template '{{json .State}}')
    }

    function Test-NeverStarted {
        # Returns $true when the container of Service exists, is not running and was never started since it was created.
        param([string]$Service)
        $state = Get-ContainerState -Service $Service
        return ($null -ne $state -and (Get-Field $state 'Running') -eq $false -and ([string](Get-Field $state 'StartedAt')).StartsWith('0001-01-01'))
    }

    function Confirm-TextReady {
        <#
        Brings the stack to the state the later tests start from, the text runtime ready and wanted: removes the VRAM
        load of T-GPU-09 if it still runs, starts the orchestrator when it is not running, asks for mode llm and waits
        up to TimeoutSecond for LLM_READY. Returns what Wait-GpuState returns.
        #>
        param([int]$TimeoutSecond = 300)
        if ($script:VramLoadId -and (Stop-GpuVramLoad -Id $script:VramLoadId) -eq 0) { $script:VramLoadId = '' }
        if ((Get-Field (Get-ContainerState -Service 'lyrveil-orchestrator') 'Running') -ne $true) {
            $null = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
        }
        $null = Request-GpuMode -Mode 'llm' -JobId 'gpu-test-text'
        return (Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'LLM_READY' } -TimeoutSecond $TimeoutSecond)
    }

    function Find-Transition {
        # Returns the first event of Transition after sequence number After that goes From To ('*' for any), for JobId
        # when one is given, or $null.
        param([object[]]$Transition = @(), [string]$From = '*', [string]$To = '*', [string]$JobId = '', [long]$After = 0)
        foreach ($item in @($Transition | Sort-Object { [long](Get-Field $_ 'seq') })) {
            if ([long](Get-Field $item 'seq') -le $After) { continue }
            if ($From -ne '*' -and (Get-Field $item 'from') -ne $From) { continue }
            if ($To -ne '*' -and (Get-Field $item 'to') -ne $To) { continue }
            if ($JobId -and [string](Get-Field $item 'job_id') -ne $JobId) { continue }
            return $item
        }
        return $null
    }

    function Get-LifecycleEvent {
        # Returns every event the orchestrator still buffers, oldest first, or none when it did not answer.
        $answer = Invoke-Control -Path '/v1/events'
        return @(Get-Field $answer.Body 'events')
    }

    function Invoke-SwitchRound {
        <#
        Runs round Index of T-GPU-07 and returns its record: asks for mode image on behalf of t-gpu-07-<Index>-image
        and waits for IMAGE_READY, renders Workflow under an image lease (a fresh ComfyUI loads the checkpoint for it),
        asks for mode llm on behalf of t-gpu-07-<Index>-llm and waits for LLM_READY, then generates a short reply under
        a text lease. Every number is stored as soon as it is read; problems names every failure, and the round stops
        at the first state that is not reached.
        #>
        param([int]$Index, [string]$Workflow)
        $problems = New-Object System.Collections.Generic.List[string]
        $record = [ordered]@{ round = $Index; image_job = "t-gpu-07-$Index-image"; llm_job = "t-gpu-07-$Index-llm"; problems = @() }
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $record.image_requested = Request-GpuMode -Mode 'image' -JobId $record.image_job
        $toImage = Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'IMAGE_READY' } -TimeoutSecond $script:Workload.switch_timeout_s
        $record.to_image_client_s = $toImage.Seconds
        if (-not $toImage.Reached) {
            $problems.Add("IMAGE_READY was not reached; the last state read is $(Get-Field $toImage.State 'state').")
            $record.image_log = Get-GpuTestServiceLog -Root $script:Root -Service 'lyrveil-image'
            $record.problems = $problems.ToArray()
            return $record
        }
        $lease = Request-GpuLease -Mode 'image' -JobId "t-gpu-07-$Index-render"
        $record.render_status = ''
        if ($null -eq $lease) { $problems.Add('No image lease was granted.') }
        else {
            try {
                $render = Invoke-GpuImageGraph -Root $script:Root -WorkflowJson $Workflow -TimeoutSecond $script:Workload.render_timeout_s
                $record.render_status = [string](Get-Field $render.Result 'status')
                $record.render_seconds = [double](Get-Field $render.Result 'seconds')
                $record.render_images = [int](Get-Field $render.Result 'images')
                $record.vram_after_render_mib = (Get-GpuSample).UsedMiB
            }
            finally { $record.render_lease_released = Complete-GpuLease -Lease $lease }
            if ($record.render_status -ne 'success' -or $record['render_images'] -ne 1) {
                $problems.Add("The render ended with status '$($record.render_status)' and $($record['render_images']) images.")
            }
            if (-not $record.render_lease_released) { $problems.Add('The image lease was not released.') }
        }

        $back = [System.Diagnostics.Stopwatch]::StartNew()
        $record.llm_requested = Request-GpuMode -Mode 'llm' -JobId $record.llm_job
        $toText = Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'LLM_READY' } -TimeoutSecond $script:Workload.switch_timeout_s
        $record.to_llm_client_s = [math]::Round($back.Elapsed.TotalSeconds, 2)
        if (-not $toText.Reached) {
            $problems.Add("LLM_READY was not reached; the last state read is $(Get-Field $toText.State 'state').")
            $record.llm_log = Get-GpuTestServiceLog -Root $script:Root -Service 'lyrveil-llm'
            $record.problems = $problems.ToArray()
            return $record
        }
        $lease = Request-GpuLease -Mode 'llm' -JobId "t-gpu-07-$Index-reply"
        $record.reply_text = ''
        if ($null -eq $lease) { $problems.Add('No text lease was granted.') }
        else {
            try {
                $reply = Invoke-GpuTextPost -Root $script:Root -Path '/api/v1/generate' -TimeoutSecond 60 -Body @{
                    prompt = '[INST] Scrivi una frase breve sul porto di sera. [/INST]'; max_context_length = 4096
                    max_length = $script:Workload.text_max_length; genkey = "t-gpu-07-$Index"; temperature = 0.7
                }
                $record.reply_status = $reply.Status
                $record.reply_text = [string](Get-Field (@(Get-Field $reply.Body 'results') | Select-Object -First 1) 'text')
            }
            finally { $record.reply_lease_released = Complete-GpuLease -Lease $lease }
            if ($record['reply_status'] -ne 0 -or -not $record.reply_text.Trim()) { $problems.Add('The text runtime gave no reply.') }
            if (-not $record.reply_lease_released) { $problems.Add('The text lease was not released.') }
        }
        $record.round_trip_client_s = [math]::Round($clock.Elapsed.TotalSeconds, 2)
        $record.problems = $problems.ToArray()
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
        tests        = 'T-GPU-07, T-GPU-08, T-GPU-09'
        file         = 'tests/gpu/ModeSwitch.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 12), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'Mode switches: the selection and the test stack' {
    It 'selects only parts and round counts it knows' {
        $script:Unknown | Should -BeNullOrEmpty
    }

    It 'builds the test orchestrator and the backend, writes the launch file and creates the runtime containers' {
        $script:Record.preparation.stack_built | Should -BeTrue
        $script:Record.preparation.launch_written | Should -BeTrue
        $script:Record.preparation.containers_created | Should -BeTrue
    }
}

Describe 'T-GPU-09: another program holds the VRAM' -Skip:(-not $script:RunsPart['t-gpu-09']) {
    BeforeAll {
        $script:Hold = [ordered]@{ problems = @() }
        $script:Record.t_gpu_09 = $script:Hold
        $problems = New-Object System.Collections.Generic.List[string]
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                # The other program starts first, as one already open when Lyrveil starts would be.
                $load = Start-GpuVramLoad -Root $script:Root -FreeMiB $script:Workload.vram_load_free_mib
                $script:VramLoadId = $load.Id
                $script:Hold.load_status = $load.Status
                $held = $null
                if ($load.Id) {
                    $deadline = (Get-Date).AddSeconds(120)
                    do {
                        Start-Sleep -Seconds 2
                        $held = Get-GpuVramLoad -Id $load.Id
                    } while ($held.Running -and $held.HeldMiB -eq 0 -and (Get-Date) -lt $deadline)
                }
                $script:Hold.load_before = $held
                $script:Hold.nvml_with_load = Get-GpuSample
                if ($null -eq $held -or -not $held.Running -or $held.HeldMiB -eq 0) { $problems.Add('The VRAM load did not start or hold its memory.') }
                else {
                    $script:Hold.orchestrator_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-orchestrator' -TimeoutSecond 120
                    $wait = Wait-GpuState -Condition { param($s) $null -ne (Get-Field $s 'blocked_reason') } -TimeoutSecond 90
                    $script:Hold.blocked_llm = $wait.State
                    $script:Hold.nvml_at_llm_hold = Get-GpuSample
                    $script:Hold.image_requested = Request-GpuMode -Mode 'image' -JobId 't-gpu-09-image'
                    $wait = Wait-GpuState -Condition { param($s) (Get-Field (Get-Field $s 'blocked_vram') 'mode') -eq 'image' } -TimeoutSecond 60
                    $script:Hold.blocked_image = $wait.State
                    # Long enough for at least two of the orchestrator's checks, one every 5 s.
                    Start-Sleep -Seconds $script:Workload.hold_observed_s
                    $script:Hold.still_blocked = Get-GpuState
                    $script:Hold.llm_never_started = Test-NeverStarted -Service 'lyrveil-llm'
                    $script:Hold.image_never_started = Test-NeverStarted -Service 'lyrveil-image'
                    $script:Hold.llm_requested = Request-GpuMode -Mode 'llm' -JobId 't-gpu-09-llm'
                    $wait = Wait-GpuState -Condition { param($s) (Get-Field (Get-Field $s 'blocked_vram') 'mode') -eq 'llm' } -TimeoutSecond 60
                    $script:Hold.blocked_llm_again = $wait.State
                    $script:Hold.load_during = Get-GpuVramLoad -Id $load.Id
                    # The test removes only the container it started, by the id that start returned.
                    $script:Hold.load_removed = ((Stop-GpuVramLoad -Id $load.Id) -eq 0)
                    if ($script:Hold.load_removed) { $script:VramLoadId = '' }
                    $wait = Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'LLM_READY' } -TimeoutSecond 300
                    $script:Hold.ready_after_release = $wait.Reached
                    $script:Hold.ready_seconds = $wait.Seconds
                    $script:Hold.after_release = $wait.State
                    $script:Hold.events = @(Get-LifecycleEvent)
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }
        $script:Hold.problems = $problems.ToArray()
        $free = Get-Field (Get-Field $script:Hold['blocked_llm'] 'blocked_vram') 'free_mib'
        Write-Host ("    VRAM load held {0} MiB; text load held with {1} MiB free; LLM_READY {2} s after the load ended" -f (Get-Field $script:Hold['load_before'] 'HeldMiB'), $free, $script:Hold['ready_seconds'])
    }

    It 'starts the other program and the orchestrator' {
        $script:Hold.problems | Should -BeNullOrEmpty
        $script:Hold['orchestrator_up'] | Should -Be 0
    }

    It 'holds the text runtime back in IDLE with GPU_INSUFFICIENT_VRAM and the numbers of the card' {
        $state = $script:Hold['blocked_llm']
        Get-Field $state 'state' | Should -Be 'IDLE'
        Get-Field $state 'blocked_reason' | Should -Be 'GPU_INSUFFICIENT_VRAM'
        $vram = Get-Field $state 'blocked_vram'
        Get-Field $vram 'mode' | Should -Be 'llm'
        Get-Field $vram 'required_mib' | Should -Be 6000
        Get-Field $vram 'free_mib' | Should -BeLessThan 6000
        $nvmlFree = $script:Hold.nvml_at_llm_hold.TotalMiB - $script:Hold.nvml_at_llm_hold.UsedMiB
        [math]::Abs([int](Get-Field $vram 'free_mib') - $nvmlFree) | Should -BeLessOrEqual 512
    }

    It 'holds the image runtime back the same way' {
        $state = $script:Hold['blocked_image']
        Get-Field $state 'state' | Should -Be 'IDLE'
        Get-Field $state 'blocked_reason' | Should -Be 'GPU_INSUFFICIENT_VRAM'
        Get-Field (Get-Field $state 'blocked_vram') 'required_mib' | Should -Be 6500
        Get-Field (Get-Field $state 'blocked_vram') 'free_mib' | Should -BeLessThan 6500
        Get-Field $script:Hold['still_blocked'] 'blocked_reason' | Should -Be 'GPU_INSUFFICIENT_VRAM'
        Get-Field $script:Hold['blocked_llm_again'] 'blocked_reason' | Should -Be 'GPU_INSUFFICIENT_VRAM'
    }

    It 'starts no runtime and leaves the other program running, untouched, until the test ends it' {
        $script:Hold['llm_never_started'] | Should -BeTrue
        $script:Hold['image_never_started'] | Should -BeTrue
        $before = $script:Hold['load_before']
        $during = $script:Hold['load_during']
        $during.Running | Should -BeTrue
        $during.Pid | Should -Be $before.Pid
        $during.StartedAt | Should -Be $before.StartedAt
        $during.RestartCount | Should -Be 0
        $script:Hold['load_removed'] | Should -BeTrue
    }

    It 'loads the text runtime once the memory is free, after the recorded hold' {
        $script:Hold['ready_after_release'] | Should -BeTrue
        Get-Field $script:Hold['after_release'] 'blocked_reason' | Should -BeNullOrEmpty
        $events = @($script:Hold['events'])
        $hold = Find-Transition -Transition $events -From 'IDLE' -To 'IDLE'
        $hold | Should -Not -BeNullOrEmpty
        Get-Field $hold 'code' | Should -Be 'GPU_INSUFFICIENT_VRAM'
        Find-Transition -Transition $events -From 'LOADING_LLM' -To 'LLM_READY' -After ([long](Get-Field $hold 'seq')) | Should -Not -BeNullOrEmpty
    }
}

Describe 'T-GPU-07: full switches between the text and the image runtime' -Skip:(-not $script:RunsPart['t-gpu-07']) {
    BeforeAll {
        $script:Switches = [ordered]@{ rounds_run = 0; problems = @(); rounds = @() }
        $script:Record.t_gpu_07 = $script:Switches
        $problems = New-Object System.Collections.Generic.List[string]
        $rounds = New-Object System.Collections.Generic.List[object]
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                $start = Confirm-TextReady
                $script:Switches.ready_at_start = $start.Reached
                if (-not $start.Reached) { $problems.Add('The text runtime was not ready before the first switch.') }
                else {
                    $workflow = ConvertTo-GpuRequestJson -Body (Get-GpuImageWorkflow -Checkpoint $script:Workload.image_checkpoint)
                    $script:Switches.ids_before = @('lyrveil-llm', 'lyrveil-image' | ForEach-Object { [string](Get-GpuTestContainerFact -Root $script:Root -Service $_ -Template '{{json .Id}}') })
                    $sampler = Start-GpuTimelineSampler -ContainerId $script:Switches.ids_before
                    try {
                        # Both series first see the text model at rest, so its phase holds container samples too.
                        Start-Sleep -Seconds $script:Workload.steady_s
                        for ($index = 1; $index -le $script:Workload.rounds; $index++) {
                            $round = Invoke-SwitchRound -Index $index -Workflow $workflow
                            $rounds.Add($round)
                            Write-Host ("      round {0}: image ready in {1} s, render {2} in {3} s, text ready in {4} s, round trip {5} s" -f $index, $round['to_image_client_s'], $round['render_status'], $round['render_seconds'], $round['to_llm_client_s'], $round['round_trip_client_s'])
                            if (@($round.problems).Length -gt 0) { break }
                        }
                        Start-Sleep -Seconds $script:Workload.steady_s
                    }
                    finally { $timeline = Stop-GpuTimelineSampler -Sampler $sampler }
                    $script:Switches.ids_after = @('lyrveil-llm', 'lyrveil-image' | ForEach-Object { [string](Get-GpuTestContainerFact -Root $script:Root -Service $_ -Template '{{json .Id}}') })
                    $events = @(Get-LifecycleEvent)
                    $script:Switches.events = $events

                    # The orchestrator's own times of each switch, from its events: the stop with the verified release,
                    # then the load of the other runtime.
                    foreach ($round in $rounds) {
                        $toImage = Get-GpuSwitchTime -Transition $events -JobId $round.image_job -Runtime image
                        $toText = Get-GpuSwitchTime -Transition $events -JobId $round.llm_job -Runtime llm
                        $round.to_image = $toImage
                        $round.to_llm = $toText
                        $round.full_switch_s = $null
                        if ($toImage.Complete -and $toText.Complete) { $round.full_switch_s = [math]::Round($toImage.Seconds + $toText.Seconds, 2) }
                    }

                    # Every model phase NVML shows must belong to one runtime and end in a release: the text model
                    # before the first switch, then the image model and the text model of each complete round.
                    $complete = @($rounds | Where-Object { @($_.problems).Length -eq 0 })
                    $expected = [string[]](@('llm') + @($complete | ForEach-Object { 'image', 'llm' }))
                    $check = Test-GpuExclusiveTimeline -Memory $timeline.Memory -Container $timeline.Containers -Expected $expected -ResidentMiB $script:Workload.resident_mib -ReleasedMiB $script:Workload.released_mib
                    $script:Switches.timeline_problems = $check.Problems
                    $script:Switches.phases = $check.Phases
                    $script:Switches.memory_samples = @($timeline.Memory).Length
                    $script:Switches.container_samples = @($timeline.Containers).Length
                    $script:Switches.both_running_samples = @(@($timeline.Containers) | Where-Object { @($_.Running | Where-Object { $_ }).Length -ge 2 }).Length
                    $script:Switches.timeline = [ordered]@{ memory = $timeline.Memory; containers = $timeline.Containers }
                    $script:Switches.peak_mib = [int](@($timeline.Memory | ForEach-Object { $_.UsedMiB }) | Measure-Object -Maximum).Maximum

                    $full = [double[]]@($rounds | Where-Object { $null -ne $_['full_switch_s'] } | ForEach-Object { [double]$_.full_switch_s })
                    if ($full.Length -gt 0) {
                        $script:Switches.full_switch_max_s = ($full | Measure-Object -Maximum).Maximum
                        $script:Switches.full_switch_mean_s = [math]::Round(($full | Measure-Object -Average).Average, 2)
                        $script:Switches.under_planning_target = ($script:Switches.full_switch_max_s -lt $script:Workload.planning_target_s)
                    }
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }
        $script:Switches.rounds = $rounds.ToArray()
        $script:Switches.rounds_run = $rounds.Count
        $script:Switches.partial = ($script:Workload.rounds -lt $script:Workload.rounds_asked_by_t_gpu_07)
        $script:Switches.problems = $problems.ToArray()
        foreach ($round in $rounds) {
            Write-Host ("    round {0}: to image {1} s (stop and release {2} s, load {3} s), to text {4} s (stop and release {5} s, load {6} s), full switch {7} s" -f $round.round, (Get-Field $round['to_image'] 'Seconds'), (Get-Field $round['to_image'] 'UnloadSeconds'), (Get-Field $round['to_image'] 'LoadSeconds'), (Get-Field $round['to_llm'] 'Seconds'), (Get-Field $round['to_llm'] 'UnloadSeconds'), (Get-Field $round['to_llm'] 'LoadSeconds'), $round['full_switch_s'])
        }
        foreach ($problem in @($script:Switches['timeline_problems'])) { if ($problem) { Write-Host "    timeline: $problem" } }
    }

    It 'runs every round trip: image mode, a render under the image lease, text mode and a reply under the text lease' {
        $script:Switches.problems | Should -BeNullOrEmpty
        $script:Switches.rounds_run | Should -Be $script:Workload.rounds
        foreach ($round in $script:Switches.rounds) { $round.problems | Should -BeNullOrEmpty }
    }

    It 'never shows both runtimes running in the container samples' {
        $script:Switches['container_samples'] | Should -BeGreaterThan 0
        $script:Switches['both_running_samples'] | Should -Be 0
    }

    It 'never shows both models in the NVML samples: each model phase is one runtime and ends in a release' {
        $script:Switches['memory_samples'] | Should -BeGreaterThan 0
        $script:Switches['timeline_problems'] | Should -BeNullOrEmpty
    }

    It 'only stops and starts the same two containers, never recreating them' {
        @($script:Switches['ids_before'] | Where-Object { $_ }).Length | Should -Be 2
        $script:Switches['ids_after'] | Should -Be $script:Switches['ids_before']
    }

    It 'times every switch from the orchestrator events' {
        foreach ($round in $script:Switches.rounds) {
            (Get-Field $round['to_image'] 'Complete') | Should -BeTrue
            (Get-Field $round['to_llm'] 'Complete') | Should -BeTrue
        }
    }
}

Describe 'T-GPU-08: a text sent during a real render is answered after the switch back' -Skip:(-not $script:RunsPart['t-gpu-08']) {
    BeforeAll {
        $script:Queued = [ordered]@{ problems = @() }
        $script:Record.t_gpu_08 = $script:Queued
        $problems = New-Object System.Collections.Generic.List[string]
        if (-not $script:Ready) { $problems.Add('The test stack was not prepared.') }
        else {
            try {
                $start = Confirm-TextReady
                $script:Queued.ready_at_start = $start.Reached
                $script:Queued.owner_set = Set-GpuTestDataOwner -Root $script:Root
                $script:Queued.backend_up = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-backend' -TimeoutSecond 180
                if (-not $start.Reached -or $script:Queued.backend_up -ne 0) { $problems.Add('The text runtime or the backend was not ready.') }
                else {
                    $demo = Invoke-Backend -Method 'POST' -Path '/api/v1/characters/demo'
                    $characterId = [string](Get-Field (@(Get-Field $demo.Body 'created') | Select-Object -First 1) 'id')
                    $conversation = Invoke-Backend -Method 'POST' -Path '/api/v1/conversations' -Body @{ character_id = $characterId }
                    $conversationId = [string](Get-Field $conversation.Body 'id')
                    $script:Queued.conversation_status = $conversation.Http
                    $seqBefore = [long](Get-Field (Get-GpuState) 'seq')
                    $script:Queued.seq_before = $seqBefore

                    $picture = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$conversationId/images" -Body @{
                        request = 'Lei al porto, al tramonto, mentre studia una mappa.'; aspect = 'square'; quality = 'rapida'
                    }
                    $script:Queued.image_status = $picture.Http
                    $imageJob = [string](Get-Field (Get-Field $picture.Body 'job') 'id')
                    $imageMessage = [string](Get-Field (Get-Field $picture.Body 'reply') 'id')
                    $script:Queued.image_job = $imageJob
                    if (-not $imageJob) { $problems.Add("The image request was not queued (HTTP $($picture.Http)).") }
                    else {
                        # The text goes in while the image job holds the GPU for its render.
                        $render = Wait-GpuState -Condition { param($s) (Get-Field $s 'state') -eq 'GENERATING_IMAGE' -and (Get-Field (Get-Field $s 'lease') 'job_id') -eq $imageJob } -TimeoutSecond 300
                        $script:Queued.state_before_send = $render.State
                        $sentAt = Get-Date
                        $send = Invoke-Backend -Method 'POST' -Path "/api/v1/conversations/$conversationId/messages" -Body @{
                            client_message_id = [guid]::NewGuid().ToString(); content = 'Intanto che disegni: come va la giornata al porto?'; intent = 'text'
                        }
                        $script:Queued.send_status = $send.Http
                        $script:Queued.state_after_send = Get-GpuState
                        $replyId = [string](Get-Field (Get-Field $send.Body 'reply') 'id')
                        $replyJob = [string](Get-Field (Get-Field $send.Body 'job') 'id')
                        $script:Queued.reply_job = $replyJob
                        $script:Queued.reply_status_at_send = [string](Get-Field (Get-Field $send.Body 'reply') 'status')
                        $script:Queued.reply_created_at = [string](Get-Field (Get-Field $send.Body 'reply') 'created_at')
                        if (-not $render.Reached) { $problems.Add('The image job never held the GPU for its render.') }

                        $deadline = (Get-Date).AddSeconds($script:Workload.reply_timeout_s)
                        $final = @('complete', 'failed', 'interrupted')
                        $reply = $null
                        $image = $null
                        while ((Get-Date) -lt $deadline) {
                            $items = @(Get-Field (Invoke-Backend -Path "/api/v1/conversations/$conversationId/messages?limit=20").Body 'items')
                            $reply = @($items | Where-Object { (Get-Field $_ 'id') -eq $replyId }) | Select-Object -First 1
                            $image = @($items | Where-Object { (Get-Field $_ 'id') -eq $imageMessage }) | Select-Object -First 1
                            if ($final -contains (Get-Field $reply 'status') -and $final -contains (Get-Field $image 'status')) { break }
                            Start-Sleep -Seconds 2
                        }
                        $script:Queued.answered_after_send_s = [math]::Round(((Get-Date) - $sentAt).TotalSeconds, 1)
                        $script:Queued.reply = $reply
                        $script:Queued.image = $image
                        $script:Queued.image_job_detail = (Invoke-Backend -Path "/api/v1/jobs/$imageJob").Body
                        if ($replyJob) { $script:Queued.reply_job_detail = (Invoke-Backend -Path "/api/v1/jobs/$replyJob").Body }
                        $script:Queued.events = @(Get-LifecycleEvent | Where-Object { [long](Get-Field $_ 'seq') -gt $seqBefore })
                    }
                }
            }
            catch { $problems.Add("The test stopped: $($_.Exception.Message)") }
        }

        # The order the orchestrator recorded: the image lease taken and given back, the switch to text, then the
        # reply's text lease.
        $events = @($script:Queued['events'])
        $order = [ordered]@{}
        $order.image_lease = Find-Transition -Transition $events -To 'GENERATING_IMAGE' -JobId $script:Queued['image_job']
        $order.image_released = Find-Transition -Transition $events -From 'GENERATING_IMAGE' -To 'IMAGE_READY' -JobId $script:Queued['image_job'] -After ([long](Get-Field $order.image_lease 'seq'))
        $order.image_unloading = Find-Transition -Transition $events -From 'IMAGE_READY' -To 'UNLOADING_IMAGE' -After ([long](Get-Field $order.image_released 'seq'))
        $order.text_ready = Find-Transition -Transition $events -From 'LOADING_LLM' -To 'LLM_READY' -After ([long](Get-Field $order.image_unloading 'seq'))
        $order.reply_lease = Find-Transition -Transition $events -From 'LLM_READY' -To 'GENERATING_TEXT' -JobId $script:Queued['reply_job'] -After ([long](Get-Field $order.text_ready 'seq'))
        $order.reply_released = Find-Transition -Transition $events -From 'GENERATING_TEXT' -To 'LLM_READY' -JobId $script:Queued['reply_job'] -After ([long](Get-Field $order.reply_lease 'seq'))
        $order.first_reply_lease = Find-Transition -Transition $events -To 'GENERATING_TEXT' -JobId $script:Queued['reply_job']
        $script:Queued.order = $order
        $script:Queued.problems = $problems.ToArray()
        if ($null -ne $order.image_released -and $null -ne $order.reply_released) {
            $culture = [System.Globalization.CultureInfo]::InvariantCulture
            $script:Queued.release_to_reply_s = [math]::Round(([DateTimeOffset]::Parse([string]$order.reply_released.at, $culture) - [DateTimeOffset]::Parse([string]$order.image_released.at, $culture)).TotalSeconds, 2)
        }
        Write-Host ("    reply {0} {1} s after the send; {2} s from the end of the render to the end of the reply" -f (Get-Field $script:Queued['reply'] 'status'), $script:Queued['answered_after_send_s'], $script:Queued['release_to_reply_s'])
    }

    It 'starts the backend on the test data volume beside the test orchestrator' {
        $script:Queued.problems | Should -BeNullOrEmpty
        $script:Queued['backend_up'] | Should -Be 0
    }

    It 'saves and queues the text while the image job holds the GPU for its render' {
        $script:Queued['send_status'] | Should -Be 202
        $script:Queued['reply_status_at_send'] | Should -Be 'queued'
        Get-Field $script:Queued['state_before_send'] 'state' | Should -Be 'GENERATING_IMAGE'
        # Both containers read the clock of the same Docker VM, so the reply's creation and the orchestrator's events
        # can be compared.
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        $created = [DateTimeOffset]::Parse($script:Queued['reply_created_at'], $culture)
        $created | Should -BeGreaterThan ([DateTimeOffset]::Parse([string]$script:Queued.order.image_lease.at, $culture))
        $created | Should -BeLessThan ([DateTimeOffset]::Parse([string]$script:Queued.order.image_released.at, $culture))
    }

    It 'finishes the image' {
        Get-Field $script:Queued['image'] 'status' | Should -Be 'complete'
        Get-Field $script:Queued['image'] 'image_id' | Should -Not -BeNullOrEmpty
        Get-Field $script:Queued['image_job_detail'] 'status' | Should -Be 'completed'
    }

    It 'answers the text once the GPU is back in text mode' {
        Get-Field $script:Queued['reply'] 'status' | Should -Be 'complete'
        ([string](Get-Field $script:Queued['reply'] 'content')).Trim() | Should -Not -BeNullOrEmpty
        Get-Field $script:Queued['reply_job_detail'] 'status' | Should -Be 'completed'
        foreach ($step in @('image_lease', 'image_released', 'image_unloading', 'text_ready', 'reply_lease', 'reply_released')) {
            $script:Queued.order[$step] | Should -Not -BeNullOrEmpty -Because "the event $step must follow the one before it"
        }
        Get-Field $script:Queued.order.first_reply_lease 'seq' | Should -Be (Get-Field $script:Queued.order.reply_lease 'seq')
    }
}
