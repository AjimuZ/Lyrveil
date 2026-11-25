# Tests of scripts/lib/GpuSwitches.psm1: the model phases read from NVML samples, the check of T-GPU-07 that no sample shows both models, and the switch times read from the orchestrator's events.

BeforeAll {
    Set-StrictMode -Version Latest
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\GpuSwitches.psm1') -Force

    function Get-Reading {
        # Returns NVML readings @{ At; UsedMiB } one second apart, from second Start, for the amounts of UsedMiB.
        param([int[]]$UsedMiB, [int]$Start = 0)
        $at = $Start
        foreach ($used in $UsedMiB) {
            [pscustomobject]@{ At = [double]($at * 1000); UsedMiB = $used }
            $at++
        }
    }

    function Get-ContainerReading {
        # Returns container readings @{ At; Running } one second apart, from second Start: 'l' for the text runtime
        # running, 'i' for the image runtime, 'b' for both and '-' for neither, one letter per reading.
        param([string]$State, [int]$Start = 0)
        $at = $Start
        foreach ($letter in $State.ToCharArray()) {
            $running = switch ($letter) { 'l' { @($true, $false) } 'i' { @($false, $true) } 'b' { @($true, $true) } default { @($false, $false) } }
            [pscustomobject]@{ At = [double]($at * 1000); Running = [bool[]]$running }
            $at++
        }
    }

    function Get-Transition {
        # Returns one orchestrator event as GET /v1/events gives it, at Second seconds after 22:00:00 UTC.
        param([int]$Seq, [double]$Second, [string]$From, [string]$To, [string]$JobId, [hashtable]$Details = @{})
        $at = ([DateTimeOffset]::new(2026, 11, 25, 22, 0, 0, [TimeSpan]::Zero)).AddSeconds($Second).ToString('yyyy-MM-ddTHH:mm:ss.fffzzz', [System.Globalization.CultureInfo]::InvariantCulture)
        return [pscustomobject]@{ seq = $Seq; at = $at; from = $From; to = $To; job_id = $JobId; details = $Details }
    }

    # A clean run of one round: the text model resident, released, the image runtime starting (CUDA context only), its
    # model resident, released, and the text model resident again. NVML and Docker agree on every phase.
    $script:CleanMemory = @(Get-Reading -UsedMiB @(9800, 9800, 900, 900, 1300, 8800, 8800, 900, 900, 9700, 9700))
    $script:CleanContainers = @(Get-ContainerReading -State 'll--iii--ll')
}

Describe 'Get-GpuMemoryPhase' {
    It 'starts a model phase at the resident threshold and ends it only at the released threshold' {
        $phases = @(Get-GpuMemoryPhase -Sample @(Get-Reading -UsedMiB @(900, 4000, 9000, 9500, 3000, 1000, 8000)) -ResidentMiB 4900 -ReleasedMiB 1900)
        @($phases | ForEach-Object { $_.Kind }) | Should -Be @('released', 'resident', 'released', 'resident')
        $phases[1].PeakMiB | Should -Be 9500
        $phases[1].LowMiB | Should -Be 3000
        $phases[1].StartAt | Should -Be 2000
        $phases[1].EndAt | Should -Be 4000
        $phases[0].Samples | Should -Be 2
    }

    It 'gives no phase for no reading and refuses thresholds in the wrong order' {
        @(Get-GpuMemoryPhase -Sample @() -ResidentMiB 4900 -ReleasedMiB 1900).Length | Should -Be 0
        { Get-GpuMemoryPhase -Sample @() -ResidentMiB 1000 -ReleasedMiB 1000 } | Should -Throw
    }
}

Describe 'Test-GpuExclusiveTimeline' {
    It 'passes a round in which each model phase belongs to one runtime and ends in a release' {
        $check = Test-GpuExclusiveTimeline -Memory $script:CleanMemory -Container $script:CleanContainers -Expected @('llm', 'image', 'llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $check.Problems | Should -BeNullOrEmpty
        @($check.Phases | Where-Object { $_.Kind -eq 'resident' } | ForEach-Object { $_.Runtime }) | Should -Be @('llm', 'image', 'llm')
    }

    It 'fails a container sample with both runtimes running' {
        $check = Test-GpuExclusiveTimeline -Memory $script:CleanMemory -Container @(Get-ContainerReading -State 'll-biii--ll') -Expected @('llm', 'image', 'llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $check.Problems | Should -Contain '1 container samples show both runtimes running, the first at 3000.'
    }

    It 'fails when two models are resident with no release between them' {
        $memory = @(Get-Reading -UsedMiB @(9800, 9800, 11900, 11900, 8800, 8800, 900, 900, 9700, 9700))
        $check = Test-GpuExclusiveTimeline -Memory $memory -Container @(Get-ContainerReading -State 'lliiii--ll') -Expected @('llm', 'image', 'llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $check.Problems | Should -Contain 'NVML shows 2 model phases separated by a release, not 3.'
        $check.Problems | Should -Contain 'Model phase 1 shows both runtimes running.'
    }

    It 'fails a model phase that no runtime explains, or the wrong runtime' {
        $check = Test-GpuExclusiveTimeline -Memory $script:CleanMemory -Container @(Get-ContainerReading -State 'll-------ll') -Expected @('llm', 'image', 'llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $check.Problems | Should -Contain 'Model phase 2 shows no runtime running.'
        $wrong = Test-GpuExclusiveTimeline -Memory $script:CleanMemory -Container $script:CleanContainers -Expected @('llm', 'llm', 'llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $wrong.Problems | Should -Contain 'Model phase 2 is the image runtime, not llm.'
    }

    It 'fails without samples' {
        $check = Test-GpuExclusiveTimeline -Memory @() -Container @() -Expected @('llm') -ResidentMiB 4900 -ReleasedMiB 1900
        $check.Problems | Should -Contain 'No NVML sample was read.'
        $check.Problems | Should -Contain 'No container sample was read.'
    }
}

Describe 'Get-GpuSwitchTime' {
    BeforeAll {
        $script:Transitions = @(
            (Get-Transition -Seq 5 -Second 0 -From 'LLM_READY' -To 'UNLOADING_LLM' -JobId 'round-1-image'),
            (Get-Transition -Seq 6 -Second 2.5 -From 'UNLOADING_LLM' -To 'IDLE' -JobId 'round-1-image' -Details @{ drop_mib = 8900 }),
            (Get-Transition -Seq 7 -Second 2.5 -From 'IDLE' -To 'LOADING_IMAGE' -JobId 'round-1-image'),
            (Get-Transition -Seq 8 -Second 9.75 -From 'LOADING_IMAGE' -To 'IMAGE_READY' -JobId 'round-1-image' -Details @{ footprint_mib = 400 }),
            (Get-Transition -Seq 9 -Second 10 -From 'IMAGE_READY' -To 'GENERATING_IMAGE' -JobId 'render-1'),
            (Get-Transition -Seq 10 -Second 30 -From 'GENERATING_IMAGE' -To 'IMAGE_READY' -JobId 'render-1'),
            (Get-Transition -Seq 11 -Second 31 -From 'IMAGE_READY' -To 'UNLOADING_IMAGE' -JobId 'round-1-llm'),
            (Get-Transition -Seq 12 -Second 34 -From 'UNLOADING_IMAGE' -To 'IDLE' -JobId 'round-1-llm'),
            (Get-Transition -Seq 13 -Second 34 -From 'IDLE' -To 'LOADING_LLM' -JobId 'round-1-llm'),
            (Get-Transition -Seq 14 -Second 50 -From 'LOADING_LLM' -To 'LLM_READY' -JobId 'round-1-llm')
        )
    }

    It 'times the stop with its verified release and the load of the new runtime' {
        $toImage = Get-GpuSwitchTime -Transition $script:Transitions -JobId 'round-1-image' -Runtime image
        $toImage.Complete | Should -BeTrue
        $toImage.UnloadSeconds | Should -Be 2.5
        $toImage.LoadSeconds | Should -Be 7.25
        $toImage.Seconds | Should -Be 9.75
        $toImage.Release.drop_mib | Should -Be 8900
        $toImage.Load.footprint_mib | Should -Be 400
        (Get-GpuSwitchTime -Transition $script:Transitions -JobId 'round-1-llm' -Runtime llm).Seconds | Should -Be 19
    }

    It 'is incomplete when an event of the switch is missing or belongs to another job' {
        $partial = @($script:Transitions | Where-Object { $_.seq -ne 14 })
        (Get-GpuSwitchTime -Transition $partial -JobId 'round-1-llm' -Runtime llm).Complete | Should -BeFalse
        (Get-GpuSwitchTime -Transition $script:Transitions -JobId 'round-1-image' -Runtime llm).Complete | Should -BeFalse
        (Get-GpuSwitchTime -Transition @() -JobId 'round-1-image' -Runtime image).Seconds | Should -BeNullOrEmpty
    }
}
