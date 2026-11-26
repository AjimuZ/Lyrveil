# Tests of scripts/lib/GpuSession.psm1: the plan, schedule, driver and verdict of T-GPU-13, run in its shortened mode on a virtual clock with stand-in actions, so no GPU or Docker is needed.

BeforeAll {
    Set-StrictMode -Version Latest
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\GpuSession.psm1') -Force

    # The virtual clock: an action advances it by the time it takes, and a wait by the time it sleeps.
    $script:Now = 0.0
    $script:Clock = { $script:Now }
    $script:Wait = { param($Second) $script:Now += $Second }
    # The driver passes the conversation and the number of the event positionally; an action that needs neither omits them.
    $script:GoodText = { $script:Now += 4; return @{ Ok = $true; Detail = "text $($args[1])" } }
    $script:GoodImage = { $script:Now += 20; return @{ Ok = $true } }
    $script:GoodSample = { return @{ VramMiB = 7000; HostRamFreeMiB = 8000; ContainerMiB = @{ 'lyrveil-llm' = 5000; 'lyrveil-backend' = 300 }; State = 'LLM_READY' } }

    function Invoke-FakeSession {
        # Runs the shortened plan on the virtual clock with the given actions, the good ones by default.
        param([scriptblock]$Text = $script:GoodText, [scriptblock]$Image = $script:GoodImage, [scriptblock]$Sample = $script:GoodSample, [hashtable]$Plan = (Get-GpuSessionPlan -Mode Short))
        $script:Now = 0.0
        return (Invoke-GpuSession -Plan $Plan -Sample $Sample -Text $Text -Image $Image -Clock $script:Clock -Wait $script:Wait)
    }
}

Describe 'The plan and the schedule' {
    It 'plans two hours with 20 images and 3 conversations, and a shortened mode with the same conversations' {
        $full = Get-GpuSessionPlan
        $full.DurationSecond | Should -Be 7200
        $full.Images | Should -Be 20
        $full.Conversations | Should -Be 3
        $short = Get-GpuSessionPlan -Mode Short
        $short.Mode | Should -Be 'short'
        $short.DurationSecond | Should -BeLessThan 600
        $short.Images | Should -BeLessThan $full.Images
        $short.Conversations | Should -Be 3
    }

    It 'spreads the 20 images over the two hours and takes the conversations in turn' {
        $schedule = @(Get-GpuSessionSchedule -Plan (Get-GpuSessionPlan))
        $images = @($schedule | Where-Object { $_.Kind -eq 'image' })
        $images.Length | Should -Be 20
        $images[0].Due | Should -Be 180
        $images[19].Due | Should -Be 7020
        @($images | ForEach-Object { $_.Conversation } | Select-Object -Unique | Sort-Object) | Should -Be @(0, 1, 2)
        @($schedule | Where-Object { $_.Kind -eq 'text' }).Length | Should -Be 80
        @($schedule | ForEach-Object { $_.Seq }) | Should -Be (1..100)
        for ($i = 1; $i -lt $schedule.Length; $i++) { $schedule[$i].Due | Should -BeGreaterOrEqual $schedule[$i - 1].Due }
        # Every event falls due inside the session, so the last one has time to finish.
        $schedule[-1].Due | Should -BeLessThan 7200
    }

    It 'puts an image before a text that falls due at the same second' {
        $plan = @{ DurationSecond = 100; Images = 1; Conversations = 3; TextEverySecond = 50 }
        $schedule = @(Get-GpuSessionSchedule -Plan $plan)
        @($schedule | ForEach-Object { $_.Kind }) | Should -Be @('text', 'image', 'text')
        $schedule[1].Due | Should -Be 50
        $schedule[2].Due | Should -Be 50
        $plan.Images = 2
        $plan.TextEverySecond = 25
        @(Get-GpuSessionSchedule -Plan $plan | Where-Object { $_.Due -eq 25 } | ForEach-Object { $_.Kind }) | Should -Be @('image', 'text')
    }
}

Describe 'The shortened session' {
    It 'runs every event in order, never two at once, and passes with a healthy stack' {
        $record = Invoke-FakeSession
        $record.Aborted | Should -BeFalse
        $record.ElapsedSecond | Should -BeGreaterOrEqual 150
        @($record.Events).Length | Should -Be 7
        @($record.Events | Where-Object { $_.Kind -eq 'image' }).Length | Should -Be 2
        @($record.Events | ForEach-Object { $_.Seq }) | Should -Be (1..7)
        $events = @($record.Events)
        for ($i = 0; $i -lt $events.Length; $i++) {
            $events[$i].StartSecond | Should -BeGreaterOrEqual $events[$i].Due
            if ($i -gt 0) { $events[$i].StartSecond | Should -BeGreaterOrEqual ($events[$i - 1].StartSecond + $events[$i - 1].Seconds) }
        }
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Problems | Should -BeNullOrEmpty
        $verdict.Passed | Should -BeTrue
    }

    It 'records samples with their phases, and the latency, VRAM and RAM numbers' {
        $record = Invoke-FakeSession
        $phases = @($record.Samples | ForEach-Object { $_.Phase } | Select-Object -Unique | Sort-Object)
        $phases | Should -Be @('end', 'event', 'idle')
        $record.Samples[0].Second | Should -Be 0
        $record.Samples[-1].Phase | Should -Be 'end'
        $record.Samples[0].State | Should -Be 'LLM_READY'
        $summary = (Get-GpuSessionVerdict -Record $record).Summary
        $summary.Text.Count | Should -Be 5
        $summary.Text.P50 | Should -Be 4
        $summary.Image.Max | Should -Be 20
        $summary.VramPeakMiB | Should -Be 7000
        $summary.HostRamFreeMinMiB | Should -Be 8000
        $summary.ContainerPeakMiB['lyrveil-llm'] | Should -Be 5000
        $summary.Errors | Should -Be 0
    }

    It 'runs a late event as soon as the one before it ends' {
        $record = Invoke-FakeSession -Image { $script:Now += 100; return @{ Ok = $true } }
        $events = @($record.Events)
        # The first image falls due at 37.5 s and ends at 137.5 s; the texts due at 60 and 90 s follow at once.
        $events[2].Kind | Should -Be 'image'
        $events[3].StartSecond | Should -Be ($events[2].StartSecond + 100)
        $events[3].Due | Should -BeLessThan $events[3].StartSecond
        @($record.Events).Length | Should -Be 7
    }

    It 'records a failed event, keeps going and fails the verdict naming it' {
        $record = Invoke-FakeSession -Image { $script:Now += 2; if ($args[1] -eq 1) { throw 'the render broke' }; return @{ Ok = $true } }
        $record.Aborted | Should -BeFalse
        @($record.Events).Length | Should -Be 7
        $failed = @($record.Events | Where-Object { -not $_.Ok })
        $failed.Length | Should -Be 1
        $failed[0].Kind | Should -Be 'image'
        $failed[0].Error | Should -Be 'the render broke'
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match 'image \d+ of conversation 2 failed: the render broke'
        ($verdict.Problems -join "`n") | Should -Match '1 images succeeded, not the planned 2'
        $verdict.Summary.Errors | Should -Be 1
    }

    It 'stops early after five failed events in a row and fails the verdict' {
        $record = Invoke-FakeSession -Text { $script:Now += 1; return @{ Ok = $false; Error = 'no reply' } } -Image { $script:Now += 1; return @{ Ok = $false; Error = 'no image' } }
        $record.Aborted | Should -BeTrue
        @($record.Events).Length | Should -Be 5
        $record.AbortReason | Should -Match '5 events failed in a row'
        $record.ElapsedSecond | Should -BeLessThan 150
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match 'stopped early'
        ($verdict.Problems -join "`n") | Should -Match 'Conversation 1 had no successful text'
    }

    It 'counts an action that returns nothing as a failed event' {
        $record = Invoke-FakeSession -Text { $script:Now += 1 }
        @($record.Events | Where-Object { $_.Kind -eq 'text' -and $_.Ok }).Length | Should -Be 0
    }

    It 'keeps a sample that throws as unreadable and fails the verdict' {
        $record = Invoke-FakeSession -Sample { throw 'nvidia-smi is gone' }
        $record.Samples[0].VramMiB | Should -Be -1
        $record.Samples[0].Error | Should -Be 'nvidia-smi is gone'
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match "memory could not be read in \d+ samples"
        ($verdict.Problems -join "`n") | Should -Match 'host RAM was never read'
    }
}

Describe 'The verdict on VRAM and RAM' {
    It 'fails when the lowest VRAM keeps rising, as a model or cache that is never released would make it' {
        $record = Invoke-FakeSession -Sample { return @{ VramMiB = [int](500 + 20 * $script:Now); HostRamFreeMiB = 8000 } }
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match 'lowest VRAM rose from'
    }

    It 'accepts VRAM that swings between models but keeps its floor' {
        # Alternating readings: every window of the session holds the low one.
        $script:Tick = 0
        $record = Invoke-FakeSession -Sample { $script:Tick++; return @{ VramMiB = $(if ($script:Tick % 2 -eq 0) { 7000 } else { 9500 }); HostRamFreeMiB = 8000 } }
        (Get-GpuSessionVerdict -Record $record).Problems | Should -BeNullOrEmpty
    }

    It 'fails when the host runs low on RAM' {
        $record = Invoke-FakeSession -Sample { return @{ VramMiB = 7000; HostRamFreeMiB = 512 } }
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match '512 MiB of RAM free at its lowest'
    }

    It 'fails a session that ended before its planned duration' {
        $record = Invoke-FakeSession
        $record.ElapsedSecond = 100
        $verdict = Get-GpuSessionVerdict -Record $record
        $verdict.Passed | Should -BeFalse
        ($verdict.Problems -join "`n") | Should -Match 'lasted 100 s, not the planned 150 s'
    }
}
