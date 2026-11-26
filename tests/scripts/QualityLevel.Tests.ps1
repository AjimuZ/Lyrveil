# Tests scripts/lib/QualityLevel.psm1 without Docker or a GPU: the arguments of "test quality", the plan of T-QLT-05 from the real script, the probe and narration readers, every score at and around its target, and the SmartCache and SWA padding rules.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\QualityLevel.psm1') -Force
    $scriptPath = Join-Path $script:RepositoryRoot 'tests\quality\consistency\script.json'
    $script:ScriptDocument = [System.IO.File]::ReadAllText($scriptPath, (New-Object System.Text.UTF8Encoding $false)) | ConvertFrom-Json
    $script:Files = @('tests/quality/Consistency.Tests.ps1', 'tests/quality/Extraction.Tests.ps1')
    $script:Ids = @('rocinante-x-12b', 'rocinante-12b-v1.1')

    function Get-TestReply {
        # Returns one reply record as Consistency.Tests.ps1 builds it, with Change applied to its fields.
        param([hashtable]$Change = @{})
        $reply = @{
            Conversation = 'main'; Character = 'Nerea'; Number = 1; Kind = 'chat'; Correct = $null; Status = 'complete'
            Tokens = 20; Band = 'brevi'; Narration = 'nessuna'; Spans = 0; AfterChange = $false
            Checks = @{}; Normalizer = @{}
        }
        foreach ($key in $Change.Keys) { $reply[$key] = $Change[$key] }
        return $reply
    }

    function Get-TestRun {
        # Returns Count main replies of which the first Probes are probes, Correct of them correct; Hits of the
        # replies carry Check, and the first Cut were cut by N4. Tokens is the length of every reply.
        param([int]$Count = 20, [int]$Probes = 0, [int]$Correct = 0, [int]$Hits = 0, [string]$Check = 'V11', [int]$Cut = 0, [int]$Tokens = 20)
        $replies = New-Object System.Collections.Generic.List[object]
        for ($index = 0; $index -lt $Count; $index++) {
            $change = @{ Number = $index + 1; Tokens = $Tokens }
            if ($index -lt $Probes) {
                $change.Kind = 'probe'
                $change.Correct = ($index -lt $Correct)
            }
            if ($index -lt $Hits) { $change.Checks = @{ $Check = 1 } }
            if ($index -lt $Cut) { $change.Normalizer = @{ N4 = 1 } }
            $replies.Add((Get-TestReply -Change $change))
        }
        return $replies.ToArray()
    }
}

Describe 'Resolve-QualityArgument' {
    It 'sorts test files, short and catalog text profiles, each once and in argument order' {
        $resolved = Resolve-QualityArgument -Argument @('Extraction', 'rocinante-x-12b', 'short', 'Extraction', 'short') -TestFile $script:Files -TextProfileId $script:Ids
        $resolved.Files | Should -Be @('tests/quality/Extraction.Tests.ps1')
        $resolved.Items | Should -Be @('rocinante-x-12b', 'short')
        $resolved.Problems | Should -BeNullOrEmpty
    }

    It 'leaves the file list empty, meaning every file, when no file is named' {
        $resolved = Resolve-QualityArgument -Argument @('rocinante-12b-v1.1') -TestFile $script:Files -TextProfileId $script:Ids
        $resolved.Files | Should -BeNullOrEmpty
        $resolved.Items | Should -Be @('rocinante-12b-v1.1')
    }

    It 'refuses an unknown file, an unknown profile and any other argument' {
        $resolved = Resolve-QualityArgument -Argument @('Nothing', 'gemma-4-12b', 'rocinante-x-12b/A', 'SHORT') -TestFile $script:Files -TextProfileId $script:Ids
        $resolved.Problems.Length | Should -Be 4
        $resolved.Problems[0] | Should -Match "'Nothing' names no test file"
        $resolved.Problems[1] | Should -Match "'gemma-4-12b' is neither"
    }
}

Describe 'Get-QualitySelection and Get-QualityChangeProfile' {
    It 'runs every catalog profile in the full mode by default' {
        $selection = Get-QualitySelection -Select @() -TextProfileId $script:Ids
        $selection.Mode | Should -Be 'Full'
        $selection.Profiles | Should -Be $script:Ids
        $selection.Unknown | Should -BeNullOrEmpty
    }

    It 'runs only the named profiles, in the order named, and reads short' {
        $selection = Get-QualitySelection -Select @('rocinante-12b-v1.1', 'short', 'nosuch') -TextProfileId $script:Ids
        $selection.Mode | Should -Be 'Short'
        $selection.Profiles | Should -Be @('rocinante-12b-v1.1')
        $selection.Unknown | Should -Be @('nosuch')
    }

    It 'starts C6 on the next catalog profile, wrapping, and on none for a single profile' {
        Get-QualityChangeProfile -ProfileId 'rocinante-x-12b' -TextProfileId $script:Ids | Should -Be 'rocinante-12b-v1.1'
        Get-QualityChangeProfile -ProfileId 'rocinante-12b-v1.1' -TextProfileId $script:Ids | Should -Be 'rocinante-x-12b'
        Get-QualityChangeProfile -ProfileId 'rocinante-x-12b' -TextProfileId @('rocinante-x-12b') | Should -Be ''
    }
}

Describe 'Get-QualityConsistencyPlan' {
    It 'plans 120 messages per demo character with the probes ending at 10, 60 and 120 and the shared messages between' {
        $plan = Get-QualityConsistencyPlan -Document $script:ScriptDocument -Mode Full
        $plan.Characters.Length | Should -Be 3
        $plan.ChangeAt | Should -Be 60
        $plan.ChangeCharacter | Should -Be 'Nerea'
        foreach ($character in $plan.Characters) {
            $character.Steps.Length | Should -Be 120
            $probes = @($character.Steps | Where-Object { $_.Kind -eq 'probe' } | ForEach-Object { $_.Number })
            $probes | Should -Be @(7, 8, 9, 10, 57, 58, 59, 60, 117, 118, 119, 120)
            @($character.Steps | Where-Object { $_.Kind -eq 'probe' } | Where-Object { $_.Expect.Length -eq 0 }) | Should -BeNullOrEmpty
        }
        $nerea = $plan.Characters[0]
        $nerea.Steps[0].Text | Should -Be $script:ScriptDocument.messages[0].text
        $nerea.Steps[10].Text | Should -Be $script:ScriptDocument.messages[6].text
        $nerea.Steps[6].Topic | Should -Be 'name'
        @($nerea.Steps | Where-Object { $_.Kind -eq 'time_jump' }).Length | Should -BeGreaterOrEqual 2
        @($nerea.Steps | Where-Object { $_.Kind -eq 'correction' }).Length | Should -BeGreaterOrEqual 2
        @($nerea.Steps | Where-Object { $_.Kind -eq 'topic' }).Length | Should -BeGreaterOrEqual 20
    }

    It 'keeps the first probe block alone in the short mode, with the change after its second question' {
        $plan = Get-QualityConsistencyPlan -Document $script:ScriptDocument -Mode Short
        $plan.ChangeAt | Should -Be 2
        $plan.SmartCachePairs | Should -Be 1
        foreach ($character in $plan.Characters) {
            @($character.Steps | ForEach-Object { $_.Kind } | Select-Object -Unique) | Should -Be @('probe')
            @($character.Steps | ForEach-Object { $_.Topic }) | Should -Be @('name', 'age', 'occupation', 'place')
        }
    }

    It 'gives probes of the same topic the same fragments for one character, and covers every topic of C1' {
        $plan = Get-QualityConsistencyPlan -Document $script:ScriptDocument -Mode Full
        foreach ($character in $plan.Characters) {
            $probes = @($character.Steps | Where-Object { $_.Kind -eq 'probe' })
            @($probes | ForEach-Object { $_.Topic } | Sort-Object -Unique) | Should -Be @('age', 'fact', 'gender', 'main_trait', 'name', 'occupation', 'place')
            foreach ($group in ($probes | Group-Object -Property { $_.Topic })) {
                @($group.Group | ForEach-Object { (@($_.Expect) -join '|') + '/' + (@($_.Reject) -join '|') } | Select-Object -Unique).Length |
                    Should -Be 1 -Because "$($character.FirstName) asks '$($group.Name)' more than once"
            }
        }
    }

    It 'names demo characters and bands that the backend demo cards hold' {
        $plan = Get-QualityConsistencyPlan -Document $script:ScriptDocument -Mode Short
        $demo = [System.IO.File]::ReadAllText((Join-Path $script:RepositoryRoot 'backend\src\lyrveil\services\demo_characters.py'))
        foreach ($character in $plan.Characters) {
            $demo | Should -Match ('"first_name": "' + $character.FirstName + '"')
            $demo | Should -Match ('"message_length": "' + $character.Band + '"')
        }
    }

    It 'carries each character question frequency, as the backend demo cards hold it, and refuses an unknown one' {
        $plan = Get-QualityConsistencyPlan -Document $script:ScriptDocument -Mode Short
        @($plan.Characters | ForEach-Object { $_.QuestionFrequency }) | Should -Be @('a_volte', 'a_volte', 'spesso')
        $demo = [System.IO.File]::ReadAllText((Join-Path $script:RepositoryRoot 'backend\src\lyrveil\services\demo_characters.py'))
        $cards = [regex]::Matches($demo, '"first_name": "(\w+)"|"question_frequency": "(\w+)"')
        $order = @($cards | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } })
        ($order -join ',') | Should -Be 'Nerea,a_volte,Ilario,a_volte,Tessa,spesso'
        $broken = $script:ScriptDocument | ConvertTo-Json -Depth 8 | ConvertFrom-Json
        $broken.characters[0].question_frequency = 'sempre'
        { Get-QualityConsistencyPlan -Document $broken -Mode Short } | Should -Throw "*unknown question frequency 'sempre'*"
    }

    It 'refuses a character without 12 probes and too few shared messages' {
        $broken = $script:ScriptDocument | ConvertTo-Json -Depth 8 | ConvertFrom-Json
        $broken.characters[1].probes = @($broken.characters[1].probes | Select-Object -First 11)
        { Get-QualityConsistencyPlan -Document $broken -Mode Short } | Should -Throw '*Ilario has 11 probes*'
        $short = $script:ScriptDocument | ConvertTo-Json -Depth 8 | ConvertFrom-Json
        $short.messages = @($short.messages | Select-Object -First 107)
        { Get-QualityConsistencyPlan -Document $short -Mode Full } | Should -Throw '*107 shared messages*'
        Get-QualityConsistencyPlan -Document $short -Mode Short | Should -Not -BeNullOrEmpty
    }
}

Describe 'Probe answers and narration spans' {
    It 'accepts an expected fragment whatever the case and accents, and refuses a rejected one' {
        $step = @{ Expect = @('ventitre', '23'); Reject = @('sono un ragazzo') }
        # The file stays ASCII for Windows PowerShell 5.1, so the accented letters are written as code points.
        Test-QualityProbeAnswer -Step $step -Text ('Ho VENTITR' + [char]0x00C9 + ' anni, giuro!') | Should -BeTrue
        Test-QualityProbeAnswer -Step $step -Text 'Ne ho 31.' | Should -BeFalse
        Test-QualityProbeAnswer -Step @{ Expect = @('ragazza'); Reject = @('sono un ragazzo') } -Text 'Sono un ragazzo, non una ragazza.' | Should -BeFalse
        Test-QualityProbeAnswer -Step $step -Text '' | Should -BeFalse
    }

    It 'folds typographic apostrophes and runs of whitespace' {
        ConvertTo-QualityFoldedText -Text ('Com' + [char]0x2019 + [char]0x00E8 + '   la  CITT' + [char]0x00C0) | Should -Be "com'e la citta"
    }

    It 'counts the asterisk spans of one line' {
        Measure-QualityNarrationSpan -Text '*sorride* ciao *si siede*' | Should -Be 2
        Measure-QualityNarrationSpan -Text "un * solo`n e un altro *" | Should -Be 0
        Measure-QualityNarrationSpan -Text '' | Should -Be 0
    }

    It 'gives the median of an odd and an even count, and -1 for none' {
        Get-QualityMedian -Value @(3, 1, 2) | Should -Be 2
        Get-QualityMedian -Value @(4, 1, 3, 2) | Should -Be 2.5
        Get-QualityMedian -Value @() | Should -Be -1
    }
}

Describe 'Get-QualityConsistencyScore' {
    It 'passes C1 at 95 % of the probes and fails below' {
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 20 -Correct 19)).C1.passed | Should -BeTrue
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 20 -Correct 18)
        $score.C1.passed | Should -BeFalse
        $score.HardPassed | Should -BeFalse
    }

    It 'fails C1 for one delivered wrong name or age, even with every probe right' {
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 4 -Correct 4 -Hits 1 -Check 'V10')
        $score.C1.wrong_name_or_age | Should -Be 1
        $score.C1.passed | Should -BeFalse
    }

    It 'passes C2 at 1 delivered V11 hit per 200 replies and fails above' {
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 200 -Probes 4 -Correct 4 -Hits 1)).C2.passed | Should -BeTrue
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 200 -Probes 4 -Correct 4 -Hits 2)).C2.passed | Should -BeFalse
    }

    It 'records C3 and C5 without letting them decide the hard targets' {
        $run = @(Get-TestRun -Count 20 -Probes 4 -Correct 4 -Hits 5 -Check 'V13')
        $run[5].Spans = 2
        $run[5].Normalizer = @{ N3 = 3 }
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply $run
        $score.C3.passed | Should -BeFalse
        $score.C5.delivered_spans | Should -Be 2
        $score.C5.removed_spans_n3 | Should -Be 3
        $score.C5.passed | Should -BeFalse
        $score.C7.status | Should -Be 'not_run'
        $score.HardPassed | Should -BeTrue
    }

    It 'passes C4 at the band target and 10 % cut, and fails just above either' {
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 4 -Correct 4 -Tokens 70 -Cut 2)).C4.passed | Should -BeTrue
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 4 -Correct 4 -Tokens 71)).C4.passed | Should -BeFalse
        $cut = Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 4 -Correct 4 -Cut 3)
        $cut.C4.bands.brevi.cut_rate | Should -Be 0.15
        $cut.C4.passed | Should -BeFalse
    }

    It 'fails the run when a planned reply did not complete' {
        $run = @(Get-TestRun -Count 20 -Probes 4 -Correct 4)
        $run[10].Status = 'timeout'
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply $run
        $score.not_complete | Should -Be 1
        $score.HardPassed | Should -BeFalse
    }

    It 'judges C6 on the change conversation after the change only, and marks it not applicable without one' {
        $run = New-Object System.Collections.Generic.List[object]
        foreach ($reply in (Get-TestRun -Count 20 -Probes 4 -Correct 4)) { $run.Add($reply) }
        $run.Add((Get-TestReply -Change @{ Conversation = 'change'; Kind = 'probe'; Correct = $false; AfterChange = $false; Tokens = 500 }))
        $run.Add((Get-TestReply -Change @{ Conversation = 'change'; Kind = 'probe'; Correct = $true; AfterChange = $true }))
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply $run.ToArray() -ChangeFrom 'q'
        $score.C6.applicable | Should -BeTrue
        $score.C6.change_from | Should -Be 'q'
        $score.C6.c1.probes | Should -Be 1
        $score.C6.passed | Should -BeTrue
        $run.Add((Get-TestReply -Change @{ Conversation = 'change'; Kind = 'probe'; Correct = $false; AfterChange = $true }))
        (Get-QualityConsistencyScore -ProfileId 'p' -Reply $run.ToArray() -ChangeFrom 'q').C6.passed | Should -BeFalse
        $alone = Get-QualityConsistencyScore -ProfileId 'p' -Reply (Get-TestRun -Count 20 -Probes 4 -Correct 4)
        $alone.C6.applicable | Should -BeFalse
        $alone.HardPassed | Should -BeTrue
    }

    It 'never passes a run without replies' {
        $score = Get-QualityConsistencyScore -ProfileId 'p' -Reply @()
        $score.all_complete | Should -BeFalse
        $score.HardPassed | Should -BeFalse
    }
}

Describe 'Get-QualityTrialLaunchLine' {
    It 'changes one lever in place and marks the trial CUSTOM' {
        $lines = @('MODEL_PROFILE=m', 'SWA_PADDING=0', 'SMART_CACHE=0', 'LEVEL=A')
        Get-QualityTrialLaunchLine -Line $lines -Setting @{ SMART_CACHE = '2' } | Should -Be @('MODEL_PROFILE=m', 'SWA_PADDING=0', 'SMART_CACHE=2', 'LEVEL=CUSTOM')
        Get-QualityTrialLaunchLine -Line @('MODEL_PROFILE=m', 'LEVEL=A') -Setting @{ SWA_PADDING = '1024' } | Should -Be @('MODEL_PROFILE=m', 'SWA_PADDING=1024', 'LEVEL=CUSTOM')
    }
}

Describe 'Get-QualitySmartCacheDecision' {
    BeforeAll {
        function Get-TestTrial {
            param([int]$Value, [double[]]$Second, [double]$Peak = 6000, [double]$Docker = 16000, [string[]]$Problems = @())
            return @{ Value = $Value; ProcessValue = $Second; PeakRamMiB = $Peak; DockerMemoryMiB = $Docker; Problems = $Problems }
        }
    }

    It 'takes 2 at a 50 % fall with 4096 MiB of headroom, and keeps 0 just short of either' {
        $decision = Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4, 4, 4)), (Get-TestTrial 2 @(2, 2, 2) -Peak 11904))
        $decision.decision | Should -Be 2
        $decision.reduction | Should -Be 0.5
        $decision.headroom_mib | Should -Be 4096
        (Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4, 4, 4)), (Get-TestTrial 2 @(2.04, 2.04, 2.04)))).decision | Should -Be 0
        $tight = Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4)), (Get-TestTrial 2 @(1) -Peak 11905))
        $tight.decision | Should -Be 0
        @($tight.reasons).Length | Should -Be 1
    }

    It 'stays undecided without both trials, with a problem or with an unknown amount' {
        (Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4)))).decision | Should -Be 'undecided'
        (Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4)), (Get-TestTrial 2 @()))).decision | Should -Be 'undecided'
        (Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4)), (Get-TestTrial 2 @(1) -Problems @('failed')))).decision | Should -Be 'undecided'
        (Get-QualitySmartCacheDecision -Trial @((Get-TestTrial 0 @(4)), (Get-TestTrial 2 @(1) -Docker -1))).decision | Should -Be 'undecided'
        (Get-QualitySmartCacheDecision -Trial @()).decision | Should -Be 'undecided'
    }
}

Describe 'SWA padding' {
    It 'plans 0, 1024 and 2048 for a gemma4 profile only' {
        (Get-QualitySwaPaddingPlan -TextProfile ([pscustomobject]@{ template = 'gemma4' })).Values | Should -Be @(0, 1024, 2048)
        $plan = Get-QualitySwaPaddingPlan -TextProfile ([pscustomobject]@{ template = 'mistral_v3_tekken' })
        $plan.Applicable | Should -BeFalse
        $plan.Reason | Should -Match 'ADR 0045'
    }

    It 'keeps the smallest value within 10 % of the best median that holds the margin and speed rules' {
        $trials = @(
            @{ Value = 0; ProcessValue = @(2.0); MarginMiB = 900; SpeedOk = $true },
            @{ Value = 1024; ProcessValue = @(1.1); MarginMiB = 700; SpeedOk = $true },
            @{ Value = 2048; ProcessValue = @(1.0); MarginMiB = 600; SpeedOk = $true })
        (Get-QualitySwaPaddingDecision -Trial $trials).decision | Should -Be 1024
        $trials[1].MarginMiB = 400
        (Get-QualitySwaPaddingDecision -Trial $trials).decision | Should -Be 2048
        $trials[2].SpeedOk = $false
        (Get-QualitySwaPaddingDecision -Trial $trials).decision | Should -Be 'undecided'
        (Get-QualitySwaPaddingDecision -Trial @($trials[0])).decision | Should -Be 'undecided'
    }
}
