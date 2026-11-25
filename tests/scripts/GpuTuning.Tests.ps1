# Tests of scripts/lib/GpuTuning.psm1: the trial plan, the trial selectors and launch lines, and every decision of the measuring and tuning procedure (docs/gpu-orchestration.md, steps 4 to 9 and the speed rule of T-GPU-14) at and around its thresholds, from records shaped as tests/gpu/Tuning.Tests.ps1 stores them.

BeforeAll {
    Set-StrictMode -Version Latest
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\GpuTuning.psm1') -Force

    function Get-TrialRecord {
        <#
        Returns a trial record as Measure-TuningTrial stores it: Status, the card's TotalMiB and the PeakMiB of the
        workload, RamMiB and ReadySecond, one answered reply of ReplyTokens tokens for each value of Speed (tokens per
        second) and one answered long prompt of PromptTokens tokens for each value of PromptSecond, in order.
        #>
        param(
            [string]$Status = 'measured', [int]$PeakMiB = 9000, [int]$TotalMiB = 12282,
            [double[]]$Speed = @(), [int]$ReplyTokens = 200,
            [double[]]$PromptSecond = @(), [int]$PromptTokens = 6120,
            [double]$RamMiB = 2000, [double]$ReadySecond = 40
        )
        $replies = @($Speed | ForEach-Object { [ordered]@{ status = 0; tokens = $ReplyTokens; seconds = $ReplyTokens / $_ } })
        $prompts = @($PromptSecond | ForEach-Object { [ordered]@{ status = 0; tokens = $PromptTokens; seconds = $_ } })
        return [ordered]@{
            status = $Status; problems = @(); total_mib = $TotalMiB; vram_peak_mib = $PeakMiB; ram_after_mib = $RamMiB
            ready_seconds = $ReadySecond; replies = $replies; prompts = $prompts
        }
    }

    function Get-Summary {
        # Returns Get-GpuTrialSummary of Get-TrialRecord with the same parameters.
        param([hashtable]$Parameter = @{})
        return (Get-GpuTrialSummary -Trial (Get-TrialRecord @Parameter))
    }

    function Get-Repeat {
        # Returns Count copies of Value.
        param([double]$Value, [int]$Count)
        return [double[]]@(1..$Count | ForEach-Object { $Value })
    }

    $script:MixtureProfile = [pscustomobject]@{ id = 'mixture'; context_size = 16384; batch_size = 512; moe = $true }
    $script:DenseProfile = [pscustomobject]@{ id = 'dense'; context_size = 8192; batch_size = 512; moe = $false }
}

Describe 'Get-GpuNextContextSize' {
    It 'gives the next allowed CONTEXT_SIZE' {
        Get-GpuNextContextSize -ContextSize 16384 | Should -Be 24576
        Get-GpuNextContextSize -ContextSize 8192 | Should -Be 12288
    }

    It 'throws for a value that is not allowed or has no larger step' {
        { Get-GpuNextContextSize -ContextSize 5000 } | Should -Throw '*not an allowed*'
        { Get-GpuNextContextSize -ContextSize 24576 } | Should -Throw '*no larger step*'
    }
}

Describe 'Get-GpuTuningPlan' {
    BeforeAll {
        $script:Catalog = '{"text": [{"id": "dense", "context_size": 8192, "batch_size": 512, "moe": false}, {"id": "mixture", "context_size": 16384, "batch_size": 512, "moe": true}]}' | ConvertFrom-Json
    }

    It 'plans every step for a dense profile, without the MOE_CPU_LAYERS trials and without the catalog batch' {
        $plan = @(Get-GpuTuningPlan -Catalog $script:Catalog -ProfileId @('dense'))
        @($plan | ForEach-Object { $_.Name }) | Should -Be @('A', 'context-12288', 'batch-256', 'batch-1024', 'batch-2048', 'mmap-0', 'mmap-1', 'kv-q8_0', 'kv-q8_0-context-12288')
        $plan[0].Replies | Should -Be 20
        $plan[0].MustLoad | Should -BeTrue
        $plan[0].Setting.Count | Should -Be 0
        @($plan | Where-Object { $_.Name -eq 'kv-q8_0-context-12288' })[0].Setting['CONTEXT_SIZE'] | Should -Be 12288
        @($plan | Where-Object { $_.ImageJobFirst } | ForEach-Object { $_.Name }) | Should -Be @('mmap-0', 'mmap-1')
    }

    It 'adds MOE_CPU_LAYERS 8, 12 and 16 for a mixture-of-experts profile, each stopping once the speed rule cannot hold' {
        $moe = @(Get-GpuTuningPlan -Catalog $script:Catalog -ProfileId @('mixture') | Where-Object { $_.Step -eq 7 })
        @($moe | ForEach-Object { $_.Name }) | Should -Be @('moe-8', 'moe-12', 'moe-16')
        @($moe | Where-Object { $_.StopWhenRuleFails -and $_.Replies -eq 20 }).Length | Should -Be 3
    }

    It 'throws for a profile the catalog lacks' {
        { Get-GpuTuningPlan -Catalog $script:Catalog -ProfileId @('missing') } | Should -Throw "*no text profile 'missing'*"
    }

    It 'plans the three P02 profiles of the real catalog, with MOE_CPU_LAYERS trials only for gemma-4-26b-a4b' {
        $path = Join-Path $PSScriptRoot '..\..\config\model-catalog.json'
        $catalog = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json
        $plan = @(Get-GpuTuningPlan -Catalog $catalog)
        @($plan | ForEach-Object { $_.Profile } | Select-Object -Unique) | Should -Be @(Get-GpuTuningProfileId)
        @($plan | Where-Object { $_.Step -eq 7 } | ForEach-Object { $_.Profile } | Select-Object -Unique) | Should -Be @('gemma-4-26b-a4b')
    }
}

Describe 'Trial selectors' {
    BeforeAll {
        $catalog = '{"text": [{"id": "dense", "context_size": 8192, "batch_size": 512, "moe": false}]}' | ConvertFrom-Json
        $script:Plan = @(Get-GpuTuningPlan -Catalog $catalog -ProfileId @('dense'))
    }

    It 'selects with wildcards in plan order, and everything without a pattern' {
        @(Select-GpuTuningTrial -Plan $script:Plan -Pattern @('dense/mmap-*', 'dense/A') | ForEach-Object { $_.Name }) | Should -Be @('A', 'mmap-0', 'mmap-1')
        @(Select-GpuTuningTrial -Plan $script:Plan).Length | Should -Be $script:Plan.Length
    }

    It 'reports a malformed selector and one that selects no trial' {
        @(Test-GpuTuningSelection -Pattern @('dense/batch-*')) | Should -BeNullOrEmpty
        @(Test-GpuTuningSelection -Pattern @('dense')) | Should -Match 'is not a trial selector'
        @(Test-GpuTuningSelection -Pattern @('dense/A;rm')) | Should -Match 'is not a trial selector'
        @(Test-GpuTuningSelection -Plan $script:Plan -Pattern @('other/A')) | Should -Match 'selects no trial'
    }
}

Describe 'Get-GpuTrialLaunchLine' {
    BeforeAll {
        $script:Base = [string[]]@('MODEL_PROFILE=m', 'CONTEXT_SIZE=16384', 'BATCH_SIZE=512', 'MOE_CPU_LAYERS=0', 'LEVEL=A')
    }

    It 'keeps level A unchanged when the trial changes nothing' {
        Get-GpuTrialLaunchLine -BaseLine $script:Base -Setting ([ordered]@{}) | Should -Be $script:Base
    }

    It 'replaces the changed keys in place and marks the file CUSTOM' {
        $lines = @(Get-GpuTrialLaunchLine -BaseLine $script:Base -Setting ([ordered]@{ MOE_CPU_LAYERS = 12; CONTEXT_SIZE = 24576 }))
        $lines | Should -Be @('MODEL_PROFILE=m', 'CONTEXT_SIZE=24576', 'BATCH_SIZE=512', 'MOE_CPU_LAYERS=12', 'LEVEL=CUSTOM')
    }

    It 'throws for a key the launch lines lack' {
        { Get-GpuTrialLaunchLine -BaseLine $script:Base -Setting ([ordered]@{ SWA_PADDING = 1024 }) } | Should -Throw '*no key SWA_PADDING*'
    }
}

Describe 'Get-GpuSpeedRuleStop and Get-GpuMedian' {
    It 'stops once more than half of the planned replies are slow, and not before' {
        $slow = @(1..10 | ForEach-Object { [ordered]@{ status = 0; tokens = 200; seconds = 20 } })
        Get-GpuSpeedRuleStop -Reply $slow -Planned 20 | Should -Be ''
        $slow += [ordered]@{ status = 0; tokens = 200; seconds = 20 }
        Get-GpuSpeedRuleStop -Reply $slow -Planned 20 | Should -Match '11 of the 20 planned replies'
    }

    It 'stops at a failed or short reply' {
        Get-GpuSpeedRuleStop -Reply @([ordered]@{ status = 0; tokens = 150; seconds = 3 }) -Planned 20 | Should -Match 'reply 1 failed or had fewer than 200 tokens'
        Get-GpuSpeedRuleStop -Reply @([ordered]@{ status = 28; tokens = 0; seconds = 180 }) -Planned 20 | Should -Match 'reply 1'
    }

    It 'gives the median of an odd and an even count, and null for none' {
        Get-GpuMedian -Value @(3, 1, 2) | Should -Be 2
        Get-GpuMedian -Value @(4, 1, 3, 2) | Should -Be 2.5
        Get-GpuMedian -Value @() | Should -BeNullOrEmpty
    }
}

Describe 'Get-GpuTrialSummary' {
    It 'gives the margin, the median of the long replies and the warm prompt speed' {
        $record = Get-TrialRecord -PeakMiB 11091 -Speed @(40, 50, 20) -PromptSecond @(8, 2, 3) -PromptTokens 6000
        $record.replies += [ordered]@{ status = 0; tokens = 120; seconds = 1 }
        $summary = Get-GpuTrialSummary -Trial $record
        $summary.MarginMiB | Should -Be 1191
        $summary.LongReplies | Should -Be 3
        $summary.SlowReplies | Should -Be 0
        $summary.MedianSpeed | Should -Be 40
        $summary.Speeds.Length | Should -Be 4
        $summary.FirstTokenSeconds | Should -Be 8
        $summary.PromptTokens | Should -Be 6000
        $summary.FirstPromptSpeed | Should -Be 750
        $summary.PromptSpeed | Should -Be 2500
    }

    It 'reads a record parsed from a results file the same way' {
        $record = Get-TrialRecord -Speed @(40, 50, 20) -PromptSecond @(8, 2)
        $parsed = ConvertTo-Json -InputObject $record -Depth 6 | ConvertFrom-Json
        $summary = Get-GpuTrialSummary -Trial $parsed
        $summary.MedianSpeed | Should -Be 40
        $summary.PromptSpeed | Should -Be 3060
        $summary.Usable | Should -BeTrue
    }

    It 'gives no margin for a runtime that did not load, and an invalid record is not usable' {
        $summary = Get-Summary @{ Status = 'not_loaded' }
        $summary.Loaded | Should -BeFalse
        $summary.MarginMiB | Should -BeNullOrEmpty
        $summary.Usable | Should -BeTrue
        (Get-Summary @{ Status = 'invalid' }).Usable | Should -BeFalse
    }
}

Describe 'Step 4: Get-GpuLevelDecision' {
    It 'makes level B the default under 512 MiB of margin and keeps A at 512 MiB' {
        $low = Get-GpuLevelDecision -Baseline (Get-Summary @{ PeakMiB = 11771 })
        $low.Value | Should -Be 'B'
        $low.MarginMiB | Should -Be 511
        (Get-GpuLevelDecision -Baseline (Get-Summary @{ PeakMiB = 11770 })).Value | Should -Be 'A'
    }

    It 'makes level B the default when level A did not load, and decides nothing without the trial' {
        (Get-GpuLevelDecision -Baseline (Get-Summary @{ Status = 'not_loaded' })).Value | Should -Be 'B'
        (Get-GpuLevelDecision -Baseline $null).Decided | Should -BeFalse
        (Get-GpuLevelDecision -Baseline (Get-Summary @{ Status = 'invalid' })).Decided | Should -BeFalse
    }
}

Describe 'Step 5: Get-GpuContextDecision' {
    It 'grows the context only when both margins are at least 1024 MiB' {
        $base = Get-Summary @{ PeakMiB = 11258 }
        (Get-GpuContextDecision -ContextSize 16384 -NextContextSize 24576 -Baseline $base -Step (Get-Summary @{ PeakMiB = 11258 })).Value | Should -Be 24576
        (Get-GpuContextDecision -ContextSize 16384 -NextContextSize 24576 -Baseline $base -Step (Get-Summary @{ PeakMiB = 11259 })).Value | Should -Be 16384
        (Get-GpuContextDecision -ContextSize 16384 -NextContextSize 24576 -Baseline (Get-Summary @{ PeakMiB = 11259 }) -Step $null).Value | Should -Be 16384
    }

    It 'keeps the context when the step did not load, and decides nothing when a needed trial is missing' {
        $base = Get-Summary @{ PeakMiB = 9000 }
        (Get-GpuContextDecision -ContextSize 8192 -NextContextSize 12288 -Baseline $base -Step (Get-Summary @{ Status = 'not_loaded' })).Value | Should -Be 8192
        (Get-GpuContextDecision -ContextSize 8192 -NextContextSize 12288 -Baseline $base -Step $null).Decided | Should -BeFalse
    }
}

Describe 'Step 6: Get-GpuBatchDecision' {
    It 'keeps the size with the largest gain of at least 20 % among those that keep a 512 MiB margin' {
        $candidates = [ordered]@{
            '256'  = Get-Summary @{ PromptSecond = @(9, 4); PromptTokens = 6000 }
            '512'  = Get-Summary @{ PromptSecond = @(9, 3); PromptTokens = 6000 }
            '1024' = Get-Summary @{ PromptSecond = @(9, 2.4); PromptTokens = 6000 }
            '2048' = Get-Summary @{ PromptSecond = @(9, 2); PromptTokens = 6000; PeakMiB = 11800 }
        }
        $decision = Get-GpuBatchDecision -Candidate $candidates
        $decision.Decided | Should -BeTrue
        $decision.Value | Should -Be 1024
        @($decision.Comparisons | Where-Object { $_.BatchSize -eq 2048 })[0].Qualifies | Should -BeFalse
        @($decision.Comparisons | Where-Object { $_.BatchSize -eq 1024 })[0].Gain | Should -Be 0.25
    }

    It 'keeps 512 when no size gains 20 %' {
        $candidates = [ordered]@{
            '512'  = Get-Summary @{ PromptSecond = @(9, 3); PromptTokens = 6000 }
            '1024' = Get-Summary @{ PromptSecond = @(9, 2.6); PromptTokens = 6000 }
        }
        (Get-GpuBatchDecision -Candidate $candidates).Value | Should -Be 512
    }

    It 'decides nothing while a size is not measured' {
        $candidates = [ordered]@{ '512' = Get-Summary @{ PromptSecond = @(9, 3) }; '1024' = $null }
        $decision = Get-GpuBatchDecision -Candidate $candidates
        $decision.Decided | Should -BeFalse
        $decision.Reason | Should -Match 'batch-1024'
    }
}

Describe 'Step 7: Test-GpuSpeedRule' {
    It 'holds at a median of 15 tokens per second and a first token in 20 s' {
        $rule = Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 15 20); ReplyTokens = 300; PromptSecond = @(20, 3) })
        $rule.Met | Should -BeTrue
        $rule.Value | Should -BeTrue
        $rule.MedianTokensPerSecond | Should -Be 15
    }

    It 'fails under the speed, over the time, with too few replies and with a prompt under 6000 tokens' {
        (Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 14.9 20); ReplyTokens = 300; PromptSecond = @(9) })).Met | Should -BeFalse
        $late = Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 40 20); PromptSecond = @(20.5) })
        $late.Met | Should -BeFalse
        $late.Reason | Should -Match 'first token took 20.50 s'
        (Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 40 19); PromptSecond = @(9) })).Reason | Should -Match 'only 19 replies'
        (Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 40 20); PromptSecond = @(9); PromptTokens = 5999 })).Reason | Should -Match '5999 tokens, under 6000'
    }

    It 'fails for a trial that stopped early because most replies were slow' {
        $rule = Test-GpuSpeedRule -Summary (Get-Summary @{ Speed = (Get-Repeat 10 11) })
        $rule.Met | Should -BeFalse
        $rule.Reason | Should -Match 'trial stopped there'
    }
}

Describe 'Step 7: Get-GpuMoeDecision' {
    It 'keeps the fastest setting that meets the margin rule and the speed rule' {
        $settings = [ordered]@{
            '0'  = Get-Summary @{ Speed = (Get-Repeat 40 20); PromptSecond = @(9); PeakMiB = 11900 }
            '8'  = Get-Summary @{ Speed = (Get-Repeat 30 20); PromptSecond = @(12); PeakMiB = 8000 }
            '12' = Get-Summary @{ Speed = (Get-Repeat 25 20); PromptSecond = @(13); PeakMiB = 7000 }
            '16' = Get-Summary @{ Speed = (Get-Repeat 50 20); PromptSecond = @(25); PeakMiB = 6000 }
        }
        $decision = Get-GpuMoeDecision -Moe $true -Setting $settings
        $decision.Value | Should -Be 8
        $decision.SpeedRuleMet | Should -BeTrue
        $decision.DefaultProfile | Should -BeNullOrEmpty
        @($decision.Settings | Where-Object { $_.MoeCpuLayers -eq 0 })[0].MarginRule | Should -BeFalse
    }

    It 'makes the fallback profile the default when no setting meets both rules' {
        $settings = [ordered]@{
            '0' = Get-Summary @{ Speed = (Get-Repeat 12 20); PromptSecond = @(9) }
            '8' = Get-Summary @{ Speed = (Get-Repeat 14 20); PromptSecond = @(9) }
        }
        $decision = Get-GpuMoeDecision -Moe $true -Setting $settings -FallbackProfile 'gemma-4-12b'
        $decision.SpeedRuleMet | Should -BeFalse
        $decision.DefaultProfile | Should -Be 'gemma-4-12b'
        $decision.Value | Should -Be 8
    }

    It 'keeps 0 for a dense profile and decides nothing while a setting is missing' {
        $dense = Get-GpuMoeDecision -Moe $false
        $dense.Value | Should -Be 0
        $dense.Applicable | Should -BeFalse
        (Get-GpuMoeDecision -Moe $true -Setting ([ordered]@{ '0' = (Get-Summary @{ Speed = (Get-Repeat 40 20) }); '8' = $null })).Decided | Should -BeFalse
    }
}

Describe 'Step 8: Get-GpuMmapDecision' {
    It 'keeps 1 when it saves 2 GiB of RAM at a speed cost of at most 5 %' {
        $off = Get-Summary @{ Speed = (Get-Repeat 40 5); RamMiB = 7000; ReadySecond = 60 }
        $on = Get-Summary @{ Speed = (Get-Repeat 39 5); RamMiB = 4952; ReadySecond = 59 }
        $decision = Get-GpuMmapDecision -Off $off -On $on
        $decision.Value | Should -Be 1
        $decision.RamSavingMiB | Should -Be 2048
    }

    It 'keeps 1 when it saves 20 % of the reload time' {
        $off = Get-Summary @{ Speed = (Get-Repeat 40 5); RamMiB = 7000; ReadySecond = 50 }
        $on = Get-Summary @{ Speed = (Get-Repeat 40 5); RamMiB = 6900; ReadySecond = 40 }
        (Get-GpuMmapDecision -Off $off -On $on).Value | Should -Be 1
    }

    It 'keeps 0 when the speed cost is over 5 % or nothing is saved' {
        $off = Get-Summary @{ Speed = (Get-Repeat 40 5); RamMiB = 7000; ReadySecond = 60 }
        (Get-GpuMmapDecision -Off $off -On (Get-Summary @{ Speed = (Get-Repeat 37.6 5); RamMiB = 2000; ReadySecond = 60 })).Value | Should -Be 0
        (Get-GpuMmapDecision -Off $off -On (Get-Summary @{ Speed = (Get-Repeat 40 5); RamMiB = 6000; ReadySecond = 55 })).Value | Should -Be 0
    }

    It 'decides nothing without both trials' {
        (Get-GpuMmapDecision -Off (Get-Summary @{ Speed = (Get-Repeat 40 5) }) -On $null).Decided | Should -BeFalse
    }
}

Describe 'Step 9: Get-GpuKvDecision' {
    BeforeAll {
        $script:F16 = Get-Summary @{ Speed = (Get-Repeat 40 5); PeakMiB = 9000 }
    }

    It 'keeps f16 for a q8_0 that qualifies on the measurements until the Italian protocol scores it' {
        $q8 = Get-Summary @{ Speed = (Get-Repeat 39 5); PeakMiB = 8744 }
        $decision = Get-GpuKvDecision -Baseline $script:F16 -Quantized $q8 -QuantizedNext $null -ContextGrows $true
        $decision.QualifiesOnMeasurements | Should -BeTrue
        $decision.FreesMiB | Should -Be 256
        $decision.Value | Should -Be 'f16'
        $decision.Reason | Should -Match 'ADR 0030'
        (Get-GpuKvDecision -Baseline $script:F16 -Quantized $q8 -QuantizedNext $null -ContextGrows $true -ProtocolScoredNoLower $true).Value | Should -Be 'q8_0'
    }

    It 'qualifies q8_0 when it allows the context step that f16 does not' {
        $q8 = Get-Summary @{ Speed = (Get-Repeat 40 5); PeakMiB = 8900 }
        $next = Get-Summary @{ Speed = (Get-Repeat 40 3); PeakMiB = 11258 }
        $decision = Get-GpuKvDecision -Baseline $script:F16 -Quantized $q8 -QuantizedNext $next -ContextGrows $false
        $decision.AllowsNextContext | Should -BeTrue
        $decision.QualifiesOnMeasurements | Should -BeTrue
    }

    It 'does not qualify q8_0 for too small a saving or too high a speed cost' {
        $small = Get-Summary @{ Speed = (Get-Repeat 40 5); PeakMiB = 8745 }
        (Get-GpuKvDecision -Baseline $script:F16 -Quantized $small -QuantizedNext $null -ContextGrows $true).QualifiesOnMeasurements | Should -BeFalse
        $slow = Get-Summary @{ Speed = (Get-Repeat 37.6 5); PeakMiB = 8000 }
        (Get-GpuKvDecision -Baseline $script:F16 -Quantized $slow -QuantizedNext $null -ContextGrows $true -ProtocolScoredNoLower $true).Value | Should -Be 'f16'
    }

    It 'decides nothing while the next-step trial it needs is missing' {
        $q8 = Get-Summary @{ Speed = (Get-Repeat 40 5); PeakMiB = 8900 }
        (Get-GpuKvDecision -Baseline $script:F16 -Quantized $q8 -QuantizedNext $null -ContextGrows $false).Decided | Should -BeFalse
    }
}

Describe 'Get-GpuTuningDecision' {
    BeforeAll {
        function Get-MixtureTrial {
            # Returns every trial of the mixture profile's plan, AutoFit at 42 and MOE_CPU_LAYERS at 21 tokens per second.
            $trials = @{
                'A'                     = Get-TrialRecord -Speed (Get-Repeat 42 20) -PromptSecond @(9, 3.3) -PeakMiB 11091 -RamMiB 7900 -ReadySecond 61
                'context-24576'         = Get-TrialRecord -Speed (Get-Repeat 40 3) -PromptSecond @(9) -PeakMiB 10851
                'batch-256'             = Get-TrialRecord -PromptSecond @(11, 5.3) -PeakMiB 11063
                'batch-1024'            = Get-TrialRecord -PromptSecond @(8, 2.4) -PeakMiB 10943
                'batch-2048'            = Get-TrialRecord -PromptSecond @(8, 2) -PeakMiB 11057
                'mmap-0'                = Get-TrialRecord -Speed (Get-Repeat 43.5 5) -PeakMiB 11087 -RamMiB 6604 -ReadySecond 55.6
                'mmap-1'                = Get-TrialRecord -Speed (Get-Repeat 42.8 5) -PeakMiB 11087 -RamMiB 1867 -ReadySecond 49.6
                'kv-q8_0'               = Get-TrialRecord -Speed (Get-Repeat 42.6 5) -PromptSecond @(9) -PeakMiB 11081
                'kv-q8_0-context-24576' = Get-TrialRecord -Speed (Get-Repeat 41 3) -PromptSecond @(9) -PeakMiB 10897
            }
            foreach ($layers in @(8, 12, 16)) { $trials["moe-$layers"] = Get-TrialRecord -Speed (Get-Repeat 21 20) -PromptSecond @(13, 7) -PeakMiB 3123 }
            return $trials
        }
    }

    It 'makes every decision of a complete mixture-of-experts profile and evaluates T-GPU-14 for the default' {
        $decision = Get-GpuTuningDecision -TextProfile $script:MixtureProfile -Trial (Get-MixtureTrial) -IsDefault
        $decision.Complete | Should -BeTrue
        $decision.Level.Value | Should -Be 'A'
        $decision.Context.Value | Should -Be 24576
        $decision.Batch.Value | Should -Be 2048
        $decision.Moe.Value | Should -Be 0
        $decision.SpeedRule.Met | Should -BeTrue
        $decision.SpeedRule.DefaultProfile | Should -BeNullOrEmpty
        @($decision.SpeedRule.Settings).Length | Should -Be 4
        $decision.Mmap.Value | Should -Be 1
        $decision.Kv.Value | Should -Be 'f16'
    }

    It 'gives the same decisions from records parsed out of a results file' {
        $trials = Get-MixtureTrial
        $parsed = @{}
        foreach ($key in $trials.Keys) { $parsed[$key] = ConvertTo-Json -InputObject $trials[$key] -Depth 6 | ConvertFrom-Json }
        $decision = Get-GpuTuningDecision -TextProfile $script:MixtureProfile -Trial $parsed -IsDefault
        $decision.Complete | Should -BeTrue
        $decision.Batch.Value | Should -Be 2048
        $decision.SpeedRule.Met | Should -BeTrue
    }

    It 'leaves the decisions that need a missing trial undecided, and gives no speed rule outside the default' {
        $trials = Get-MixtureTrial
        $trials.Remove('batch-1024')
        $trials.Remove('moe-12')
        $decision = Get-GpuTuningDecision -TextProfile $script:MixtureProfile -Trial $trials
        $decision.Complete | Should -BeFalse
        $decision.Batch.Decided | Should -BeFalse
        $decision.Moe.Decided | Should -BeFalse
        $decision.Level.Decided | Should -BeTrue
        $decision.SpeedRule | Should -BeNullOrEmpty
    }

    It 'applies the speed rule to trial A of a dense default profile' {
        $trials = @{ 'A' = Get-TrialRecord -Speed (Get-Repeat 30 20) -PromptSecond @(9, 3) }
        $decision = Get-GpuTuningDecision -TextProfile $script:DenseProfile -Trial $trials -IsDefault
        $decision.SpeedRule.Met | Should -BeTrue
        $decision.Moe.Applicable | Should -BeFalse
    }
}

Describe 'Get-GpuEarlierTrial' {
    BeforeAll {
        function Write-ResultFile {
            # Writes a results file Name into Folder with Trial records, for GPU Gpu and procedure version Version.
            param([string]$Folder, [string]$Name, [object[]]$Trial, [string]$Gpu = 'Card', [int]$Version = 1)
            $document = [ordered]@{ procedure_version = $Version; gpu = $Gpu; trials = $Trial }
            [System.IO.File]::WriteAllText((Join-Path $Folder $Name), (ConvertTo-Json -InputObject $document -Depth 8))
        }

        function Get-StoredTrial {
            # Returns a stored record of Profile/Name measured at Time, with Problem when given.
            param([string]$Name, [string]$Time, [string[]]$Problem = @(), [string]$Status = 'measured')
            return [ordered]@{ profile = 'm'; trial = $Name; status = $Status; problems = $Problem; measured_at = $Time }
        }
    }

    It 'returns the newest passing record of each trial from files of the same GPU and procedure version' {
        $folder = Join-Path $TestDrive 'results'
        $null = New-Item -ItemType Directory -Path $folder
        Write-ResultFile $folder 'tuning-20261125-100000.json' @((Get-StoredTrial 'A' 'old'), (Get-StoredTrial 'batch-256' 'old'), (Get-StoredTrial 'mmap-0' 'old'))
        Write-ResultFile $folder 'tuning-20261125-110000.json' @((Get-StoredTrial 'A' 'new'), (Get-StoredTrial 'batch-256' 'bad' -Problem @('2 of 2 long prompts failed.')), (Get-StoredTrial 'mmap-1' 'invalid' -Status 'invalid'))
        Write-ResultFile $folder 'tuning-20261125-120000.json' @((Get-StoredTrial 'kv-q8_0' 'other')) -Gpu 'Other card'
        Write-ResultFile $folder 'tuning-20261125-130000.json' @((Get-StoredTrial 'moe-8' 'v2')) -Version 2
        [System.IO.File]::WriteAllText((Join-Path $folder 'tuning-20261125-140000.json'), 'not JSON')

        $found = Get-GpuEarlierTrial -Folder $folder -GpuName 'Card'
        @($found.Keys | Sort-Object) | Should -Be @('m/A', 'm/batch-256', 'm/mmap-0')
        $found['m/A'].measured_at | Should -Be 'new'
        $found['m/A'].source_file | Should -Be 'tuning-20261125-110000.json'
        $found['m/batch-256'].measured_at | Should -Be 'old'
    }

    It 'returns nothing for a missing folder' {
        (Get-GpuEarlierTrial -Folder (Join-Path $TestDrive 'missing') -GpuName 'Card').Count | Should -Be 0
    }
}
