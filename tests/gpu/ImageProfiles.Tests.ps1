# T-GPU-06 and the real-GPU part of T-IMG-14: ComfyUI starts and renders with each catalog image profile, with the checkpoint load, the render time and the peak VRAM of each quality preset measured, in the project lyrveil-gpu-test.
#
# Run only by "scripts\lyrveil.cmd test gpu" (alone with "scripts\lyrveil.cmd test gpu ImageProfiles", or for some
# profiles with "scripts\lyrveil.cmd test gpu ImageProfiles noobai-xl-1.1"). It builds the backend's test stage
# (Initialize-GpuBackendProbe), where backend/tests/gpu_probe/image_presets.py gives the graphs the render stage builds
# for a profile and a preset, and computes the preset rule of docs/image-generation.md from the measured times, so this
# file copies no workflow, sampler value or rule. For each profile it starts lyrveil-image alone, renders Rapida
# $script:Workload.rapida_seeds (the first render also loads the checkpoint), then Alta $script:Workload.alta_seeds,
# while a background job samples NVML every 250 ms, and removes the runtime. Every render runs in BeforeAll, so the
# numbers are recorded even when a check fails; test-results/gpu/image-profiles-<time>.json, ignored by Git, holds
# them. The images stay in the runtime's tmpfs and are removed with it.

# Pester passes Select, the profile identifiers after the file name in "test gpu", to BeforeDiscovery and BeforeAll,
# which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeDiscovery and BeforeAll.')]
param([string[]]$Select = @())

BeforeDiscovery {
    $root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $catalog = [System.IO.File]::ReadAllText((Join-Path $root 'config\model-catalog.json'), (New-Object System.Text.UTF8Encoding $false)) | ConvertFrom-Json
    $wanted = [string[]]@($Select | Where-Object { $_ })
    $script:ImageProfiles = @($catalog.image | Where-Object { $wanted.Length -eq 0 -or $wanted -contains $_.id } | ForEach-Object { @{ Id = [string]$_.id } })
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date
    $script:ResultFile = Join-Path $script:Root ('test-results\gpu\image-profiles-{0}.json' -f $script:StartedAt.ToString('yyyyMMdd-HHmmss'))
    # The reference machine is of the target tier since ADR 0027; the rule's default is that tier's default profile.
    $script:Tier = 'target'
    # Square is the largest final size of both presets (1536 x 1536 for Alta), so its times and peaks bound the others.
    # The first Rapida render after the start also loads the checkpoint; the rule leaves it out.
    $script:Workload = [ordered]@{
        aspect = 'square'; rapida_seeds = @(101, 102, 103); alta_seeds = @(201, 202, 203)
        start_timeout_s = 180; render_timeout_s = 240; release_limit_mib = $script:BaselineMiB + 1024
    }

    $catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:CatalogIds = [string[]]@($catalog.image | ForEach-Object { [string]$_.id })
    $script:Selection = [string[]]@($Select | Where-Object { $_ })
    $script:Unknown = [string[]]@($script:Selection | Where-Object { $script:CatalogIds -notcontains $_ })

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
        param($Value, [string]$Name)
        if ($null -eq $Value) { return $null }
        $property = $Value.PSObject.Properties[$Name]
        if ($null -eq $property) { return $null }
        return $property.Value
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

    function Invoke-PresetRender {
        # Renders the graph Graph of Document (a workflow document of the probe) on the running lyrveil-image and
        # returns its record, adding to Problems every way it failed: a client that did not finish, a ComfyUI status
        # other than success, not exactly one image, or an image of another size than the preset's final size.
        param($Document, [int]$Index, [System.Collections.Generic.List[string]]$Problems)
        $quality = [string]$Document.quality
        $seed = @($script:Workload["$($quality)_seeds"])[$Index]
        $job = Invoke-GpuImageGraph -Root $script:Root -WorkflowJson ([string]@($Document.workflows)[$Index]) -TimeoutSecond $script:Workload.render_timeout_s
        $render = [ordered]@{
            quality           = $quality
            seed              = $seed
            cold              = ($quality -eq 'rapida' -and $Index -eq 0)
            client_status     = $job.Status
            status            = [string](Get-Field $job.Result 'status')
            seconds           = [double](Get-Field $job.Result 'seconds')
            execution_seconds = Get-Field $job.Result 'execution_seconds'
            images            = [int](Get-Field $job.Result 'images')
            width             = [int](Get-Field $job.Result 'width')
            height            = [int](Get-Field $job.Result 'height')
        }
        $name = "$quality seed $seed"
        if ($render.client_status -ne 0) { $Problems.Add("${name}: the client exited with status $($render.client_status).") }
        if ($render.status -ne 'success') { $Problems.Add("${name}: ComfyUI status '$($render.status)', not success.") }
        if ($render.images -ne 1) { $Problems.Add("${name}: $($render.images) images saved, not 1.") }
        if ($render.width -ne [int]$Document.width -or $render.height -ne [int]$Document.height) {
            $Problems.Add("${name}: $($render.width) x $($render.height), not $($Document.width) x $($Document.height).")
        }
        Write-Host ("      {0}: {1} in {2} s (ComfyUI {3} s), {4} x {5}" -f $name, $render.status, $render.seconds, $render.execution_seconds, $render.width, $render.height)
        return $render
    }

    function Measure-PresetGraph {
        # Renders every graph of Document while NVML is sampled, and returns @{ Renders; PeakMiB; Samples }. The
        # sampler stops also when a render throws.
        param($Document, [System.Collections.Generic.List[string]]$Problems)
        $renders = New-Object System.Collections.Generic.List[object]
        $sampler = Start-GpuMemorySampler
        try {
            for ($index = 0; $index -lt @($Document.workflows).Length; $index++) {
                $renders.Add((Invoke-PresetRender -Document $Document -Index $index -Problems $Problems))
            }
        }
        finally { $peak = Stop-GpuMemorySampler -Job $sampler }
        return @{ Renders = $renders.ToArray(); PeakMiB = $peak.PeakMiB; Samples = $peak.Samples }
    }

    function Measure-ImageProfile {
        <#
        Starts lyrveil-image for the catalog image profile Id, renders its Rapida then its Alta graphs, removes the
        runtime and waits for its VRAM. Returns the profile's record; every number is stored as soon as it is read, and
        problems names every failure.
        #>
        param([string]$Id)
        $problems = New-Object System.Collections.Generic.List[string]
        $record = [ordered]@{ profile = $Id; problems = @(); renders = @() }
        $documents = [ordered]@{}
        foreach ($quality in @('rapida', 'alta')) {
            $answer = Invoke-GpuBackendProbe -Root $script:Root -Probe 'image_presets' -Action 'workflow' -Body @{
                profile = $Id; quality = $quality; aspect = $script:Workload.aspect; seeds = @($script:Workload["$($quality)_seeds"])
            }
            if ($answer.Status -ne 0 -or $null -eq $answer.Body) { $problems.Add("The probe gave no $quality graph (exit $($answer.Status)).") }
            else { $documents[$quality] = $answer.Body }
        }
        if ($documents.Count -ne 2) {
            $record.problems = $problems.ToArray()
            return $record
        }
        foreach ($name in @('checkpoint', 'prompt_preset', 'steps', 'sampler')) { $record[$name] = Get-Field $documents.rapida $name }
        $record.refine_steps = Get-Field $documents.alta 'refine_steps'
        $record.templates = [ordered]@{ rapida = $documents.rapida.template; alta = $documents.alta.template }
        $record.final_sizes = [ordered]@{ rapida = "$($documents.rapida.width) x $($documents.rapida.height)"; alta = "$($documents.alta.width) x $($documents.alta.height)" }

        Open-GpuCommandLog
        try {
            $record.released_before = Wait-GpuMemoryBelow -LimitMiB $script:Workload.release_limit_mib -TimeoutSecond 90
            if (-not $record.released_before) { $problems.Add('The VRAM of the previous runtime was not released.') }
            $record.vram_before_mib = (Get-GpuSample).UsedMiB
            $clock = [System.Diagnostics.Stopwatch]::StartNew()
            $record.up_status = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image' -TimeoutSecond $script:Workload.start_timeout_s
            $record.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
            $record.vram_started_mib = (Get-GpuSample).UsedMiB
            if ($record.up_status -ne 0) { $problems.Add("lyrveil-image did not become healthy (exit $($record.up_status)).") }
            else {
                $stats = (Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-image' -Path '/system_stats').Body
                $device = @(Get-Field $stats 'devices') | Select-Object -First 1
                $record.comfyui_version = [string](Get-Field (Get-Field $stats 'system') 'comfyui_version')
                $record.device_type = [string](Get-Field $device 'type')
                $record.device_name = [string](Get-Field $device 'name')

                $rapida = Measure-PresetGraph -Document $documents.rapida -Problems $problems
                $record.rapida_peak_mib = $rapida.PeakMiB
                $record.rapida_samples = $rapida.Samples
                $alta = Measure-PresetGraph -Document $documents.alta -Problems $problems
                $record.alta_peak_mib = $alta.PeakMiB
                $record.alta_samples = $alta.Samples
                $record.renders = @($rapida.Renders) + @($alta.Renders)

                # The checkpoint load: the first render after the start, which loads it, less the median of the later
                # renders of the same graph with the checkpoint loaded.
                $cold = @($rapida.Renders | Where-Object { $_.cold -and $_.status -eq 'success' })
                $warm = @($rapida.Renders | Where-Object { -not $_.cold -and $_.status -eq 'success' })
                if ($cold.Length -eq 1 -and $warm.Length -gt 0) {
                    $record.checkpoint_load_seconds = [math]::Round($cold[0].seconds - (Get-Median -Value ([double[]]@($warm | ForEach-Object { $_.seconds }))), 2)
                    $coldExecution = $cold[0].execution_seconds
                    $warmExecution = [double[]]@($warm | Where-Object { $null -ne $_.execution_seconds } | ForEach-Object { [double]$_.execution_seconds })
                    if ($null -ne $coldExecution -and $warmExecution.Length -gt 0) {
                        $record.checkpoint_load_execution_seconds = [math]::Round([double]$coldExecution - (Get-Median -Value $warmExecution), 2)
                    }
                }
                else { $problems.Add('The checkpoint load could not be measured: a Rapida render failed.') }
                $record.rapida_warm_seconds = [double[]]@($warm | ForEach-Object { $_.seconds })
                $record.alta_seconds = [double[]]@($alta.Renders | Where-Object { $_.status -eq 'success' } | ForEach-Object { $_.seconds })
                foreach ($quality in @('rapida', 'alta')) {
                    $peak = [int]$record["$($quality)_peak_mib"]
                    $record["$($quality)_peak_over_start_mib"] = $peak - [int]$record.vram_before_mib
                    $record["$($quality)_margin_mib"] = $script:Card.TotalMiB - $peak
                }
            }
        }
        finally {
            $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
            $record.released_after = Wait-GpuMemoryBelow -LimitMiB $script:Workload.release_limit_mib -TimeoutSecond 90
            $record.commands = [string[]](Close-GpuCommandLog)
        }
        if (-not $record.released_after) { $problems.Add('The VRAM was not released after the runtime was removed.') }
        $record.problems = $problems.ToArray()
        return $record
    }

    $script:Record = [ordered]@{ tier = $script:Tier; selection = $script:Selection; workload = $script:Workload; profiles = [ordered]@{} }
    $script:Records = $script:Record.profiles
    $script:Built = Initialize-GpuBackendProbe -Root $script:Root
}

AfterAll {
    $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        tests        = 'T-GPU-06, T-IMG-14 (real GPU)'
        file         = 'tests/gpu/ImageProfiles.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 10), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-GPU-06: the backend probe and the profiles' {
    It 'builds the backend test stage that gives the graphs and the rule' {
        $script:Built | Should -BeTrue
    }

    It 'selects only image profiles of the catalog' {
        $script:Unknown | Should -BeNullOrEmpty
        $script:CatalogIds.Length | Should -BeGreaterThan 0
    }
}

Describe 'T-GPU-06: ComfyUI with <Id>' -ForEach $script:ImageProfiles {
    BeforeAll {
        # Set first, so a profile that cannot run still leaves its own record with its reason.
        $script:Records[$Id] = [ordered]@{ profile = $Id; problems = @('The backend test stage did not build.'); renders = @() }
        if ($script:Built) { $script:Records[$Id] = Measure-ImageProfile -Id $Id }
        $profileRecord = $script:Records[$Id]
        Write-Host ("    {0}: ready in {1} s, checkpoint load {2} s, peak {3} MiB Rapida / {4} MiB Alta, {5} problems" -f $Id, $profileRecord['ready_seconds'], $profileRecord['checkpoint_load_seconds'], $profileRecord['rapida_peak_mib'], $profileRecord['alta_peak_mib'], @($profileRecord.problems).Length)
    }

    It 'starts ComfyUI on the GPU' {
        $script:Records[$Id]['up_status'] | Should -Be 0
        $script:Records[$Id]['device_type'] | Should -Be 'cuda'
    }

    It 'renders every Rapida and Alta graph at its final size' {
        @($script:Records[$Id].renders).Length | Should -Be (@($script:Workload.rapida_seeds).Length + @($script:Workload.alta_seeds).Length)
        $script:Records[$Id].problems | Should -BeNullOrEmpty
    }

    It 'measures the checkpoint load' {
        $script:Records[$Id]['checkpoint_load_seconds'] | Should -BeGreaterThan 0
    }

    It 'measures the peak VRAM of each preset' {
        $script:Records[$Id]['rapida_samples'] | Should -BeGreaterThan 0
        $script:Records[$Id]['alta_samples'] | Should -BeGreaterThan 0
        $script:Records[$Id]['alta_peak_mib'] | Should -BeGreaterThan $script:Records[$Id]['vram_before_mib']
    }
}

Describe 'T-IMG-14: the preset rule from the measured render times' {
    BeforeAll {
        $script:Rule = $null
        # A profile whose runtime never rendered has no times and gets no verdict; its own checks already fail.
        $measured = @($script:Records.Values | Where-Object { $_.Contains('alta_seconds') })
        if ($script:Built -and $measured.Length -gt 0) {
            $profiles = @($measured | ForEach-Object {
                    @{
                        profile        = $_.profile
                        alta_seconds   = @($_['alta_seconds'] | Where-Object { $null -ne $_ })
                        rapida_seconds = @($_['rapida_warm_seconds'] | Where-Object { $null -ne $_ })
                    }
                })
            $answer = Invoke-GpuBackendProbe -Root $script:Root -Probe 'image_presets' -Action 'rule' -Body @{ tier = $script:Tier; profiles = $profiles }
            $script:Rule = $answer.Body
            $script:Record.preset_rule = $script:Rule
        }
        if ($null -ne $script:Rule) {
            foreach ($verdict in @($script:Rule.profiles)) {
                Write-Host ("    {0}: Alta p95 {1} s over {2}, Rapida p95 {3} s over {4}: {5}" -f $verdict.profile, $verdict.alta_p95_s, $verdict.alta_samples, $verdict.rapida_p95_s, $verdict.rapida_samples, $verdict.preset)
            }
            Write-Host "    default quality preset for $($script:Rule.default_profile): $($script:Rule.default_preset)"
        }
    }

    It 'gives a verdict for every measured profile' {
        $script:Rule | Should -Not -BeNullOrEmpty
        foreach ($verdict in @($script:Rule.profiles)) { $verdict.preset | Should -BeIn @('alta', 'rapida') }
    }

    It 'decides the default preset when the default image profile was measured' {
        $expected = if ($script:Records.Contains([string]$script:Rule.default_profile)) { @('alta', 'rapida') } else { @('undecided') }
        $script:Rule.default_preset | Should -BeIn $expected
    }
}
