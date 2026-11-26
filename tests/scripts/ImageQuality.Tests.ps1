# Tests scripts/lib/ImageQuality.psm1 without Docker or a GPU: the blind review sheet of T-QLT-02 and T-QLT-07, the reader of the owner's answers, and every verdict and decision at and around its threshold, with synthetic renders and answers.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\ImageQuality.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false

    function Get-TestCellRecord {
        # Returns one render record as ImageGrid.Tests.ps1 builds it, rendered and saved.
        param([string]$Section, [string]$Variant, [string]$ProfileId, [string]$Character, [string]$Scene, [int]$Seed, [bool]$Pair = $false, [double]$Seconds = 25.0)
        return @{
            Id = "$Section/$Variant/$ProfileId/$Character/$Scene/$Seed"; Section = $Section; Variant = $Variant; Profile = $ProfileId
            Character = $Character; Scene = $Scene; Seed = $Seed; Aspect = 'portrait'; Pair = $Pair
            Expected = @{ hair_color = 'red hair'; hair_length = 'short hair'; eye_color = 'green eyes'; age = 27; age_band = 'woman'; outfit = 'canvas jacket' }
            Status = 'success'; Seconds = $Seconds; Path = (Join-Path $TestDrive "render-$Section-$Variant-$ProfileId-$Character-$Scene-$Seed.jpg")
        }
    }

    function Get-TestCell {
        <#
        Returns the renders of a run of two profiles: the grid of 'big' (the default: its six settings and the sizes) and
        'small' (six settings), ten seeds each, then for 'big' the identity renders (two characters, five scenes, two
        seeds, baseline and weighted) and the PAG renders of the first seed. SlowVariant's first render takes SlowSeconds.
        #>
        param([string]$SlowVariant = '', [double]$SlowSeconds = 25.0)
        $cells = New-Object System.Collections.Generic.List[object]
        $settings = @('default', 'clip-skip-1', 'cfg-4.5', 'cfg-7', 'denoise-0.35', 'denoise-0.55')
        foreach ($variant in ($settings + @('landscape', 'tall', 'wide'))) {
            foreach ($seed in 1..10) { $cells.Add((Get-TestCellRecord 'grid' $variant 'big' 'Nerea' 'cafe' $seed)) }
        }
        foreach ($variant in $settings) {
            foreach ($seed in 1..10) { $cells.Add((Get-TestCellRecord 'grid' $variant 'small' 'Nerea' 'cafe' $seed)) }
        }
        $slowLeft = $SlowVariant
        foreach ($character in @('Nerea', 'Ilario')) {
            foreach ($scene in @('s1', 's2', 's3', 's4', 's5')) {
                foreach ($seed in 1, 2) {
                    $cells.Add((Get-TestCellRecord 'identity' 'baseline' 'big' $character $scene $seed))
                    $cells.Add((Get-TestCellRecord 'identity' 'identity-weight-1.1' 'big' $character $scene $seed ($seed -eq 1)))
                }
                foreach ($scale in @('pag-1.5', 'pag-3')) {
                    $seconds = 25.0
                    if ($scale -eq $slowLeft) {
                        $seconds = $SlowSeconds
                        $slowLeft = ''
                    }
                    $cells.Add((Get-TestCellRecord 'pag' $scale 'big' $character $scene 1 $true $seconds))
                }
            }
        }
        return $cells.ToArray()
    }

    function Get-FilledSheet {
        <#
        Returns Sheet with every answer line filled the way Answer says. DefectCount maps "profile|variant" to the number
        of its images (by seed, from 1) that have a defect; BaselineWrong and WeightedWrong are how many attribute checks of
        the baseline and of the weighted identity images are "wrong"; StyleChanges how many weight pairs answer "yes";
        PagWins maps a PAG variant to the number of its pairs the lever's image wins (the others are lost).
        #>
        param(
            [string]$Sheet, $Key, [hashtable]$DefectCount = @{}, [int]$BaselineWrong = 0, [int]$WeightedWrong = 0, [int]$StyleChanges = 0,
            [hashtable]$PagWins = @{}
        )
        $byId = @{}
        foreach ($item in $Key.items) { $byId[$item.id] = $item }
        $wrong = @{ baseline = $BaselineWrong; weighted = $WeightedWrong }
        $wins = @{} + $PagWins
        $styleLeft = $StyleChanges
        $current = ''
        $lines = foreach ($line in ($Sheet -split '\r?\n')) {
            if ($line -match '^### ([GIPW]\d+)') {
                $current = $Matches[1]
                $line
                continue
            }
            $item = if ($current) { $byId[$current] } else { $null }
            if ($item -and $line -match '^Defect:\s*$') {
                $count = 0
                if ($DefectCount.ContainsKey("$($item.profile)|$($item.variant)")) { $count = $DefectCount["$($item.profile)|$($item.variant)"] }
                "Defect: $(if ($item.seed -le $count) { 'yes' } else { 'no' })"
            }
            elseif ($item -and $line -match '^(Hair color|Hair length|Eyes|Age band|Outfit):\s*$') {
                $group = if ($item.variant -eq 'baseline') { 'baseline' } else { 'weighted' }
                $answer = 'ok'
                if ($wrong[$group] -gt 0) {
                    $answer = 'wrong'
                    $wrong[$group]--
                }
                "$($Matches[1]): $answer"
            }
            elseif ($item -and $line -match '^Better:\s*$') {
                $other = if ($item.lever_side -eq 'A') { 'B' } else { 'A' }
                $pick = $other
                if ($wins.ContainsKey($item.variant) -and $wins[$item.variant] -gt 0) {
                    $pick = $item.lever_side
                    $wins[$item.variant]--
                }
                "Better: $pick"
            }
            elseif ($item -and $line -match '^Style change:\s*$') {
                $answer = 'no'
                if ($styleLeft -gt 0) {
                    $answer = 'yes'
                    $styleLeft--
                }
                "Style change: $answer"
            }
            else { $line }
        }
        return ($lines -join "`n")
    }

    function Get-TestVerdict {
        # Returns the verdict of a filled synthetic sheet; Cell and Seed pick the run, the other arguments are those of Get-FilledSheet.
        param([hashtable]$Answer = @{}, [object[]]$Cell = $null)
        if ($null -eq $Cell) { $Cell = Get-TestCell }
        $blind = Get-ImageReviewSheet -Cell $Cell -DefaultProfile 'big' -Seed 5 -Mode 'Full' -Planned $Cell.Length
        $filled = Get-FilledSheet -Sheet $blind.Sheet -Key $blind.Key @Answer
        $review = Read-ImageReview -SheetText $filled -Key $blind.Key
        return @{ Verdict = (Get-ImageVerdict -Key $blind.Key -Review $review); Review = $review; Blind = $blind; Filled = $filled }
    }

    # A run in which everything passes: the default profile has the fewest defects, the identity checks are all right.
    $script:Good = @{ DefectCount = @{ 'big|default' = 1; 'small|default' = 2 }; PagWins = @{ 'pag-1.5' = 6; 'pag-3' = 3 } }
}

Describe 'Get-ImageReviewSheet' {
    It 'shows no profile, setting or lever and gives every image an opaque name' {
        $blind = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5 -Mode 'Full'
        $blind.Sheet | Should -Not -Match 'big|small|clip|cfg|denoise|pag|identity-weight|baseline|landscape'
        $blind.Sheet | Should -Match '(?m)^Sheet: [0-9a-f]{12}$'
        $blind.Sheet | Should -Match '(?m)^# Lyrveil image quality: blind review sheet'
        @($blind.Files | Where-Object { $_.Name -notmatch '^[GIPW]\d{2,}(-[AB])?\.jpg$' }).Length | Should -Be 0
    }

    It 'has one G item per grid render, one I item per identity render and a pair per PAG render and weight pair' {
        $blind = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5 -Mode 'Full'
        @($blind.Key.items | Where-Object { $_.kind -eq 'grid' }).Length | Should -Be 150
        @($blind.Key.items | Where-Object { $_.kind -eq 'identity' }).Length | Should -Be 40
        @($blind.Key.items | Where-Object { $_.kind -eq 'pag' }).Length | Should -Be 20
        @($blind.Key.items | Where-Object { $_.kind -eq 'weight' }).Length | Should -Be 10
        @($blind.Files).Length | Should -Be (150 + 40 + 2 * 20 + 2 * 10)
    }

    It 'puts the lever image of a pair on a random side and records which in the key only' {
        $blind = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5 -Mode 'Full'
        $sides = @($blind.Key.items | Where-Object { $_.kind -eq 'pag' } | ForEach-Object { $_.lever_side } | Select-Object -Unique)
        $sides.Length | Should -Be 2
        foreach ($item in @($blind.Key.items | Where-Object { $_.kind -eq 'pag' })) {
            $leverFile = @($blind.Files | Where-Object { $_.Name -eq "$($item.id)-$($item.lever_side).jpg" })
            $leverFile.Length | Should -Be 1
            $leverFile[0].Source | Should -Match 'render-pag-'
        }
    }

    It 'gives the same sheet for the same seed and another for another seed' {
        $first = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5
        $again = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5
        $other = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 6
        $again.Sheet | Should -Be $first.Sheet
        $other.Sheet | Should -Not -Be $first.Sheet
    }

    It 'leaves out a render that failed and a PAG render without its baseline, and says smoke for a short or partial run' {
        $cells = @(Get-TestCell)
        $failed = $cells[0]
        $failed.Status = 'ComfyUI status error'
        $cells = @($cells | Where-Object { -not ($_.Section -eq 'identity' -and $_.Variant -eq 'baseline' -and $_.Character -eq 'Nerea' -and $_.Scene -eq 's1' -and $_.Seed -eq 1) })
        $blind = Get-ImageReviewSheet -Cell $cells -DefaultProfile 'big' -Seed 5 -Mode 'Full' -Planned (@(Get-TestCell).Length)
        @($blind.Key.items | Where-Object { $_.kind -eq 'grid' }).Length | Should -Be 149
        @($blind.Key.items | Where-Object { $_.kind -eq 'pag' }).Length | Should -Be 18
        $blind.Key.evidence | Should -Be 'smoke'
        (Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Mode 'Short').Key.evidence | Should -Be 'smoke'
        (Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Mode 'Full').Key.evidence | Should -Be 'protocol'
    }

    It 'throws when no render gives an item' {
        $cell = Get-TestCellRecord 'grid' 'default' 'big' 'Nerea' 'cafe' 1
        $cell.Status = 'client exit 1'
        { Get-ImageReviewSheet -Cell @($cell) -DefaultProfile 'big' } | Should -Throw '*No rendered cell*'
    }
}

Describe 'Read-ImageReview' {
    BeforeAll {
        $script:Cells = @(Get-TestCell)
        $script:Blind = Get-ImageReviewSheet -Cell $script:Cells -DefaultProfile 'big' -Seed 5 -Mode 'Full'
    }

    It 'reads a complete sheet into one answer per item' {
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key -DefectCount @{ 'big|default' = 3 } -BaselineWrong 4 -StyleChanges 1 -PagWins @{ 'pag-1.5' = 5 }
        $review = Read-ImageReview -SheetText $filled -Key $script:Blind.Key
        $review.Problems | Should -BeNullOrEmpty
        $review.Missing | Should -BeNullOrEmpty
        $review.Defect.Count | Should -Be 150
        @($review.Defect.Values | Where-Object { $_ }).Length | Should -Be 3
        $review.Attribute.Count | Should -Be 40
        $review.Better.Count | Should -Be 20
        $review.Style.Count | Should -Be 10
        @($review.Style.Values | Where-Object { $_ }).Length | Should -Be 1
    }

    It 'reads answers in any case, "si" for yes, and a lower-case pair side' {
        $sheet = $script:Blind.Sheet
        $grid = @($script:Blind.Key.items | Where-Object { $_.kind -eq 'grid' })[0].id
        $weight = @($script:Blind.Key.items | Where-Object { $_.kind -eq 'weight' })[0].id
        $pair = @($script:Blind.Key.items | Where-Object { $_.kind -eq 'pag' })[0].id
        $sheet = $sheet -replace "(?ms)(### $grid\r?\n.*?Defect:)", '$1 YES'
        $sheet = $sheet -replace "(?ms)(### $weight\r?\n.*?Style change:)", "`$1 S$([char]0xEC)"
        $sheet = $sheet -replace "(?ms)(### $pair\r?\n.*?Better:)", '$1 a'
        $review = Read-ImageReview -SheetText $sheet -Key $script:Blind.Key
        $review.Defect[$grid] | Should -BeTrue
        $review.Style[$weight] | Should -BeTrue
        $review.Better[$pair] | Should -Be 'A'
    }

    It 'lists a blank answer as missing, also one of five attribute lines, and guesses nothing' {
        $identity = @($script:Blind.Key.items | Where-Object { $_.kind -eq 'identity' })[0].id
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key
        $partial = $filled -replace "(?ms)(### $identity\r?\n.*?)Eyes: ok", '$1Eyes:'
        $review = Read-ImageReview -SheetText $partial -Key $script:Blind.Key
        $review.Missing | Should -Be @($identity)
        $review.Attribute.ContainsKey($identity) | Should -BeFalse
        $review.Attribute.Count | Should -Be 39
        (Read-ImageReview -SheetText $script:Blind.Sheet -Key $script:Blind.Key).Missing.Length | Should -Be (150 + 40 + 20 + 10)
    }

    It 'reports an answer outside the words the sheet asks for' {
        $grid = @($script:Blind.Key.items | Where-Object { $_.kind -eq 'grid' })[0].id
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key
        $bad = $filled -replace "(?ms)(### $grid\r?\n.*?Defect:) no", '$1 maybe'
        $review = Read-ImageReview -SheetText $bad -Key $script:Blind.Key
        $review.Problems | Should -Contain "$grid has the answer 'maybe' on its 'Defect' line, which is not one of the answers the sheet asks for."
        $review.Defect.ContainsKey($grid) | Should -BeFalse
        $review.Missing | Should -Contain $grid
    }

    It 'reports a sheet that is not the key''s, an id twice, an unknown id and a key id the sheet lacks' {
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key
        (Read-ImageReview -SheetText ($filled -replace 'Sheet: [0-9a-f]+', 'Sheet: 000000000000') -Key $script:Blind.Key).Problems |
            Should -Contain "The sheet is not the one of this key: its 'Sheet:' line is missing or differs from $($script:Blind.Key.sheet_id)."
        (Read-ImageReview -SheetText ($filled + "`n### G01`nDefect: no`n") -Key $script:Blind.Key).Problems | Should -Contain 'G01 is on the sheet twice.'
        (Read-ImageReview -SheetText ($filled + "`n### G999`nDefect: no`n") -Key $script:Blind.Key).Problems | Should -Contain 'G999 is not an item of the key.'
        $lacking = $filled -replace '(?ms)### W10\r?\n.*?Style change: \w+', ''
        (Read-ImageReview -SheetText $lacking -Key $script:Blind.Key).Problems | Should -Contain 'W10 is in the key but not on the sheet.'
    }

    It 'reads a key that was written to key.json and parsed back' {
        $key = ConvertTo-Json -InputObject $script:Blind.Key -Depth 6 | ConvertFrom-Json
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key -DefectCount @{ 'big|default' = 2 }
        $review = Read-ImageReview -SheetText $filled -Key $key
        $review.Problems | Should -BeNullOrEmpty
        $review.Defect.Count | Should -Be 150
    }
}

Describe 'Get-ImageVerdict: the grid and the default rule (T-QLT-02)' {
    It 'keeps the default image profile when no other profile has fewer defects, also on a tie' {
        $tie = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3; 'small|default' = 3 } }).Verdict
        $tie.default_rule.decision | Should -Be 'keep'
        $tie.grid['big'].defect_rate | Should -Be 0.3
        $worse = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3; 'small|default' = 5 } }).Verdict
        $worse.default_rule.decision | Should -Be 'keep'
    }

    It 'proposes the profile with strictly fewer defects, naming it, and says a change needs an ADR' {
        $verdict = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3; 'small|default' = 2 } }).Verdict
        $verdict.default_rule.decision | Should -Be 'change'
        $verdict.default_rule.candidate | Should -Be 'small'
        $verdict.default_rule.rule | Should -Match 'ADR'
    }

    It 'compares nothing when only one profile was reviewed' {
        $only = @(Get-TestCell | Where-Object { $_.Profile -eq 'big' })
        $verdict = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3 } } $only).Verdict
        $verdict.default_rule.decision | Should -Be 'undecided'
    }

    It 'replaces a default setting only when an alternative has at least 10 points fewer defective images' {
        $verdict = (Get-TestVerdict @{ DefectCount = @{
                    'big|default' = 3; 'big|cfg-7' = 1; 'big|cfg-4.5' = 2; 'big|denoise-0.35' = 1; 'big|denoise-0.55' = 3; 'big|clip-skip-1' = 3
                } }).Verdict
        $verdict.grid['big'].parameters['cfg'].decision | Should -Be 'cfg-7'
        $verdict.grid['big'].parameters['denoise'].decision | Should -Be 'denoise-0.35'
        $verdict.grid['big'].parameters['clip_skip'].decision | Should -Be 'keep'
        $verdict.grid['big'].parameters['cfg'].variants['cfg-4.5'].defect_rate | Should -Be 0.2
        # An alternative that is not listed has no defective image, so cfg-4.5 is held at the default's rate to leave cfg-7 alone.
        $just = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3; 'big|cfg-7' = 2; 'big|cfg-4.5' = 3 } }).Verdict
        $just.grid['big'].parameters['cfg'].decision | Should -Be 'cfg-7'
        $below = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 3; 'big|cfg-7' = 3; 'big|cfg-4.5' = 3 } }).Verdict
        $below.grid['big'].parameters['cfg'].decision | Should -Be 'keep'
    }

    It 'keeps tall and wide when they have no more defective images than portrait and landscape, else removes them' {
        $verdict = (Get-TestVerdict @{ DefectCount = @{ 'big|default' = 2; 'big|tall' = 2; 'big|landscape' = 1; 'big|wide' = 2 } }).Verdict
        $verdict.sizes['tall'].decision | Should -Be 'keep'
        $verdict.sizes['wide'].decision | Should -Be 'remove'
        $verdict.sizes['wide'].against | Should -Be 'landscape'
        $verdict.sizes['wide'].defect_rate | Should -Be 0.2
        $verdict.sizes['wide'].against_rate | Should -Be 0.1
    }

    It 'is incomplete, with its reason, when a grid image has no answer' {
        $blind = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Seed 5 -Mode 'Full'
        $filled = (Get-FilledSheet -Sheet $blind.Sheet -Key $blind.Key) -replace 'Defect: no', 'Defect:'
        $review = Read-ImageReview -SheetText $filled -Key $blind.Key
        $verdict = Get-ImageVerdict -Key $blind.Key -Review $review
        $verdict.status | Should -Be 'incomplete'
        $verdict.default_rule.decision | Should -Be 'incomplete'
        $verdict.reasons | Should -Contain 'T-QLT-02: a grid image has no answer, or a profile has no image at its default settings.'
        $verdict.passed | Should -BeFalse
    }
}

Describe 'Get-ImageVerdict: the identity protocol and the measured levers (T-QLT-07)' {
    It 'meets the attribute target at 90 % of the checks and misses it at 89 %' {
        $met = (Get-TestVerdict (@{ BaselineWrong = 10; WeightedWrong = 10 } + $script:Good)).Verdict
        $met.identity.accuracy | Should -Be 0.9
        $met.identity.checks | Should -Be 100
        $met.identity.passed | Should -BeTrue
        $met.status | Should -Be 'met'
        $met.passed | Should -BeTrue
        $missed = (Get-TestVerdict (@{ BaselineWrong = 11; WeightedWrong = 11 } + $script:Good)).Verdict
        $missed.identity.passed | Should -BeFalse
        $missed.status | Should -Be 'missed'
        $missed.reasons | Should -Contain 'T-QLT-07: the attribute accuracy is 0.89, below 0.9.'
    }

    It 'breaks the baseline accuracy down by attribute' {
        $verdict = (Get-TestVerdict (@{ BaselineWrong = 20 } + $script:Good)).Verdict
        # The first twenty "wrong" lines of the baseline images fall on the first attribute lines of four images.
        $verdict.identity.by_attribute.Keys | Should -Be @('hair_color', 'hair_length', 'eyes', 'age', 'outfit')
        foreach ($name in @('hair_color', 'hair_length', 'eyes', 'age', 'outfit')) { $verdict.identity.by_attribute[$name] | Should -Be 0.8 }
    }

    It 'adopts the identity weight at a gain of 5 points with no style change, and not at 4 points or with one' {
        $adopt = (Get-TestVerdict (@{ BaselineWrong = 20; WeightedWrong = 15 } + $script:Good)).Verdict
        $adopt.identity_weight.gain | Should -Be 0.05
        $adopt.identity_weight.decision | Should -Be 'adopt'
        $small = (Get-TestVerdict (@{ BaselineWrong = 20; WeightedWrong = 16 } + $script:Good)).Verdict
        $small.identity_weight.decision | Should -Be 'reject'
        $styled = (Get-TestVerdict (@{ BaselineWrong = 20; WeightedWrong = 10; StyleChanges = 1 } + $script:Good)).Verdict
        $styled.identity_weight.gain | Should -Be 0.1
        $styled.identity_weight.style_changes | Should -Be 1
        $styled.identity_weight.decision | Should -Be 'reject'
    }

    It 'adopts a PAG scale that wins 60 % of the pairs within 45 s, and the one with more wins' {
        $verdict = (Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 6; 'pag-3' = 7 } }).Verdict
        $verdict.pag.scales['pag-1.5'].win_rate | Should -Be 0.6
        $verdict.pag.scales['pag-1.5'].decision | Should -Be 'adopt'
        $verdict.pag.scales['pag-3'].decision | Should -Be 'adopt'
        $verdict.pag.adopt_scale | Should -Be 3
        $tie = (Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 7; 'pag-3' = 7 } }).Verdict
        $tie.pag.adopt_scale | Should -Be 1.5
    }

    It 'rejects a PAG scale below 60 % of wins or above 45 s at the 95th percentile, and adopts none' {
        $few = (Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 5; 'pag-3' = 5 } }).Verdict
        $few.pag.scales['pag-1.5'].decision | Should -Be 'reject'
        $few.pag.adopt_scale | Should -Be 0
        $edge = (Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 8; 'pag-3' = 8 } } (Get-TestCell -SlowVariant 'pag-3' -SlowSeconds 45)).Verdict
        $edge.pag.scales['pag-3'].p95_seconds | Should -Be 45
        $edge.pag.scales['pag-3'].decision | Should -Be 'adopt'
        $slow = (Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 6; 'pag-3' = 9 } } (Get-TestCell -SlowVariant 'pag-3' -SlowSeconds 45.5)).Verdict
        $slow.pag.scales['pag-3'].decision | Should -Be 'reject'
        $slow.pag.scales['pag-3'].win_rate | Should -Be 0.9
        $slow.pag.adopt_scale | Should -Be 1.5
    }

    It 'counts a "same" answer as no win and records the pairs lost and tied' {
        $run = Get-TestVerdict @{ DefectCount = $script:Good.DefectCount; PagWins = @{ 'pag-1.5' = 6 } }
        $filled = $run.Filled -replace '(?m)^Better: [AB]$', 'Better: same'
        $review = Read-ImageReview -SheetText $filled -Key $run.Blind.Key
        $verdict = Get-ImageVerdict -Key $run.Blind.Key -Review $review
        $verdict.pag.scales['pag-1.5'].same | Should -Be 10
        $verdict.pag.scales['pag-1.5'].wins | Should -Be 0
        $verdict.pag.adopt_scale | Should -Be 0
    }

    It 'says the text profile gap was not run, since the scenes are fixed' {
        (Get-TestVerdict $script:Good).Verdict.text_profile_gap.status | Should -Be 'not_run'
    }

    It 'is incomplete, not missed, when the sheet has no identity image' {
        $grid = @(Get-TestCell | Where-Object { $_.Section -eq 'grid' })
        $verdict = (Get-TestVerdict $script:Good $grid).Verdict
        $verdict.status | Should -Be 'incomplete'
        $verdict.reasons | Should -Contain 'T-QLT-07: the sheet has no identity image, which only the default image profile gets; name it in the run.'
    }

    It 'carries the evidence of the key: protocol only for a complete full run' {
        (Get-TestVerdict $script:Good).Verdict.evidence | Should -Be 'protocol'
        $short = Get-ImageReviewSheet -Cell (Get-TestCell) -DefaultProfile 'big' -Mode 'Short'
        (Get-ImageVerdict -Key $short.Key -Review (Read-ImageReview -SheetText $short.Sheet -Key $short.Key)).evidence | Should -Be 'smoke'
    }
}

Describe 'Get-ImageNearestRank' {
    It 'is the largest of fewer than 20 values at the 95th percentile, and -1 for none' {
        Get-ImageNearestRank -Value @(3.0, 1.0, 2.0) -Percentile 95 | Should -Be 3
        Get-ImageNearestRank -Value ([double[]](1..20)) -Percentile 95 | Should -Be 19
        Get-ImageNearestRank -Value @() | Should -Be -1
    }
}

Describe 'Write-ImageArtifact, Test-ImageReviewSheet and Invoke-ImageReview' {
    BeforeAll {
        $script:Folder = Join-Path $TestDrive 'image-run'
        $script:Cells = @(Get-TestCell)
        foreach ($cell in $script:Cells) { [System.IO.File]::WriteAllBytes($cell.Path, [byte[]](1, 2, 3)) }
        $script:Blind = Get-ImageReviewSheet -Cell $script:Cells -DefaultProfile 'big' -Seed 9 -Mode 'Full'
        $script:Paths = Write-ImageArtifact -Folder $script:Folder -Blind $script:Blind -Cell $script:Cells
    }

    It 'writes the sheet, the key and the renders, and copies each image under its opaque name' {
        Test-Path -LiteralPath $script:Paths.Sheet | Should -BeTrue
        Test-Path -LiteralPath $script:Paths.Key | Should -BeTrue
        @(Get-ChildItem -LiteralPath $script:Paths.Images -Filter '*.jpg').Length | Should -Be $script:Blind.Files.Length
        $renders = [System.IO.File]::ReadAllText($script:Paths.Renders, $script:Utf8) | ConvertFrom-Json
        @($renders).Length | Should -Be $script:Cells.Length
        $renders[0].PSObject.Properties['Path'] | Should -BeNullOrEmpty
        [System.IO.File]::ReadAllText($script:Paths.Sheet, $script:Utf8) | Should -Not -Match 'big|small|baseline'
    }

    It 'recognizes its own sheet and no other file' {
        Test-ImageReviewSheet -SheetPath $script:Paths.Sheet | Should -BeTrue
        $other = Join-Path $TestDrive 'italian.md'
        [System.IO.File]::WriteAllText($other, "# Lyrveil Italian quality: blind rating sheet (T-QLT-01)`n", $script:Utf8)
        Test-ImageReviewSheet -SheetPath $other | Should -BeFalse
        Test-ImageReviewSheet -SheetPath (Join-Path $TestDrive 'missing.md') | Should -BeFalse
    }

    It 'imports a filled sheet from the written key, writes verdict.json and exits 0 when the verdict is met' {
        $filled = Get-FilledSheet -Sheet $script:Blind.Sheet -Key $script:Blind.Key -DefectCount $script:Good.DefectCount -BaselineWrong 5 -WeightedWrong 5 -PagWins $script:Good.PagWins
        [System.IO.File]::WriteAllText($script:Paths.Sheet, $filled, $script:Utf8)
        $result = Invoke-ImageReview -SheetPath $script:Paths.Sheet 6>$null
        $result.Problems | Should -BeNullOrEmpty
        $result.Status | Should -Be 0
        $result.Verdict.default_rule.decision | Should -Be 'keep'
        $written = [System.IO.File]::ReadAllText($result.VerdictPath, $script:Utf8) | ConvertFrom-Json
        $written.status | Should -Be 'met'
        $written.default_rule.default_profile | Should -Be 'big'
    }

    It 'exits 1 and lists every blank id when the sheet is not filled in, and writes no guess' {
        [System.IO.File]::WriteAllText($script:Paths.Sheet, $script:Blind.Sheet, $script:Utf8)
        $result = Invoke-ImageReview -SheetPath $script:Paths.Sheet 6>$null
        $result.Status | Should -Be 1
        $result.Problems | Should -Contain 'G01 was left blank.'
        $result.Verdict.status | Should -Be 'incomplete'
    }

    It 'exits 1 for a missing sheet or key without reading anything' {
        (Invoke-ImageReview -SheetPath (Join-Path $TestDrive 'nothing\sheet.md') 6>$null).Status | Should -Be 1
    }
}
