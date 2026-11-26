# Tests scripts/lib/ItalianQuality.psm1 without Docker or a GPU: the metrics M1 to M4 at and around their targets, the blind sheet (sampling, mixing, no profile name), the import of a filled sheet and the T-QLT-01 verdict, all on synthetic replies and ratings.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\ItalianQuality.psm1') -Force
    $script:Names = @('Nerea', 'Ilario', 'Tessa')
    $script:Frequency = @{ Nerea = 'a_volte'; Ilario = 'a_volte'; Tessa = 'spesso' }

    function Get-Corpus {
        # Returns Count replies for each of Characters on ProfileId, shaped as Consistency.Tests.ps1 records them. Every
        # text starts with three words no other reply shares, so M3 is 0. The first Questions[name] replies of a
        # character hold a question mark (30 % of 40 for Nerea and Ilario, 50 % for Tessa by default), and the first
        # Probes replies are identity probes.
        param(
            [int]$Count = 40, [string[]]$Characters = $script:Names, [int]$Probes = 0,
            [hashtable]$Questions = @{ Nerea = 12; Ilario = 12; Tessa = 20 }
        )
        $replies = New-Object System.Collections.Generic.List[object]
        foreach ($character in $Characters) {
            for ($number = 1; $number -le $Count; $number++) {
                $mark = if ($Questions.ContainsKey($character) -and $number -le $Questions[$character]) { '?' } else { '.' }
                $replies.Add(@{
                        Conversation = 'main'; Character = $character; Number = $number; Kind = $(if ($number -le $Probes) { 'probe' } else { 'chat' })
                        Status = 'complete'; Question = "Domanda $number"; Text = "Risposta numero $number di $character$mark"
                        Checks = @{}; Normalizer = @{}
                    })
            }
        }
        return $replies.ToArray()
    }

    function Get-SingleCharacterRun {
        # Returns 100 replies of Nerea of which 30 hold a question mark, so M4 passes and the other metrics are the test's own.
        return (Get-Corpus -Count 100 -Characters @('Nerea') -Questions @{ Nerea = 30 })
    }

    function Get-PassingMetric {
        # Returns metrics in which M1 to M4 pass, for a profile named in a verdict test.
        return @{ M1 = @{ passed = $true }; M2 = @{ passed = $true }; M3 = @{ passed = $true }; M4 = @{ passed = $true } }
    }

    function Get-TestBlind {
        # Returns the blind sheet of two profiles with 40 replies for each of three characters.
        param([int]$Seed = 7, [string]$Mode = 'Full', [int]$Probes = 0, [int]$Count = 40)
        $by = @{ 'profile-alpha' = (Get-Corpus -Count $Count -Probes $Probes); 'profile-beta' = (Get-Corpus -Count $Count -Probes $Probes) }
        return (Get-ItalianBlindSheet -ReplyByProfile $by -CharacterName $script:Names -Seed $Seed -Mode $Mode)
    }

    function Get-FilledSheet {
        # Returns Blind's sheet with its Rating: and Author: lines filled in. Rating maps a profile to the ratings its
        # naturalness items take in turn (4 when absent); Wrong maps a profile to the number of its authorship items
        # answered with another character; the ids in Blank stay empty.
        param($Blind, [hashtable]$Rating = @{}, [hashtable]$Wrong = @{}, [string[]]$Blank = @())
        $byId = @{}
        foreach ($item in $Blind.Key.items) { $byId[$item.id] = $item }
        $count = @{}
        $current = ''
        $lines = New-Object System.Collections.Generic.List[string]
        foreach ($line in ($Blind.Sheet -split '\r?\n')) {
            $heading = [regex]::Match($line, '^### ([NA]\d+)\s*$')
            if ($heading.Success) { $current = $heading.Groups[1].Value }
            $field = [regex]::Match($line, '^(Rating|Author):\s*$')
            if ($current -and $field.Success -and $Blank -notcontains $current) {
                $item = $byId[$current]
                $slot = "$($item.profile)|$($item.kind)"
                $index = if ($count.ContainsKey($slot)) { $count[$slot] } else { 0 }
                $count[$slot] = $index + 1
                if ($item.kind -eq 'naturalness') {
                    $values = @(if ($Rating.ContainsKey($item.profile)) { $Rating[$item.profile] } else { 4 })
                    $lines.Add("Rating: $($values[$index % $values.Length])")
                }
                else {
                    $wrongCount = if ($Wrong.ContainsKey($item.profile)) { $Wrong[$item.profile] } else { 0 }
                    $answer = if ($index -lt $wrongCount) { @($Blind.Key.characters | Where-Object { $_ -cne $item.character })[0] } else { $item.character }
                    $lines.Add("Author: $answer")
                }
                continue
            }
            $lines.Add($line)
        }
        return ($lines -join "`n")
    }

    function Get-Verdict {
        # Reads Blind's sheet filled as Fill says and returns the verdict with passing automatic metrics.
        param($Blind, [hashtable]$Fill = @{})
        $sheet = Get-FilledSheet -Blind $Blind @Fill
        $rating = Read-ItalianRating -SheetText $sheet -Key $Blind.Key
        $metric = @{ 'profile-alpha' = (Get-PassingMetric); 'profile-beta' = (Get-PassingMetric) }
        return (Get-ItalianVerdict -Key $Blind.Key -Rating $rating -Metric $metric)
    }
}

Describe 'Get-ItalianExpectedQuestionRate' {
    It 'maps the three card values and reads an unknown one as the default' {
        Get-ItalianExpectedQuestionRate -Frequency 'raramente' | Should -Be 0.1
        Get-ItalianExpectedQuestionRate -Frequency 'a_volte' | Should -Be 0.3
        Get-ItalianExpectedQuestionRate -Frequency 'spesso' | Should -Be 0.5
        Get-ItalianExpectedQuestionRate -Frequency 'sempre' | Should -Be 0.3
    }
}

Describe 'Get-ItalianMetricScore' {
    It 'passes M1 to M4 on a clean run and leaves M5 and M6 pending' {
        $score = Get-ItalianMetricScore -ProfileId 'profile-alpha' -Reply (Get-Corpus) -QuestionFrequency $script:Frequency
        $score.replies | Should -Be 120
        $score.M1.passed | Should -BeTrue
        $score.M2.passed | Should -BeTrue
        $score.M3.passed | Should -BeTrue
        $score.M4.passed | Should -BeTrue
        $score.automatic_passed | Should -BeTrue
        $score.M5.status | Should -Be 'pending'
        $score.M6.status | Should -Be 'pending'
    }

    It 'M1 allows 2 replies in 100 with a stop-word hit of another language and refuses 3' {
        $run = Get-SingleCharacterRun
        foreach ($index in 0..1) { $run[$index].Checks = @{ V8 = 1 } }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $score.M1.italian_rate | Should -Be 0.98
        $score.M1.passed | Should -BeTrue
        $run[2].Checks = @{ V8 = 1 }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $score.M1.italian_rate | Should -Be 0.97
        $score.M1.passed | Should -BeFalse
        $score.automatic_passed | Should -BeFalse
    }

    It 'M2 allows 1 calque or artifact hit per 100 replies and counts every hit of a reply' {
        $run = Get-SingleCharacterRun
        $run[0].Checks = @{ V9 = 1 }
        (Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency).M2.passed | Should -BeTrue
        $run[1].Checks = @{ V9 = 1 }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $score.M2.per_100 | Should -Be 2
        $score.M2.passed | Should -BeFalse
        $run[1].Checks = @{ V9 = 2 }
        (Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency).M2.hits | Should -Be 3
    }

    It 'M3 counts a reply that shares its first three words with one of the previous 10 replies, and no older one' {
        $run = Get-Corpus -Count 20 -Characters @('Nerea') -Questions @{ Nerea = 6 }
        # Replies 2, 8 and 9 share a lead: 8 sees 2 within its previous 10 and 9 sees 2 and 8. Reply 20 shares it with
        # none of replies 10 to 19, so the older ones are out of its window. ASCII only: the lead is folded.
        $run[1].Text = 'Va bene cosi, dimmi.'
        $run[7].Text = 'Va bene cosi, andiamo.'
        $near = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $near.M3.repeated | Should -Be 1
        $near.M3.rate | Should -Be 0.05
        $near.M3.passed | Should -BeTrue
        $run[19].Text = 'VA BENE COSI, ancora.'
        (Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency).M3.repeated | Should -Be 1
        $run[8].Text = 'Va bene cosi, di nuovo.'
        $more = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $more.M3.repeated | Should -Be 2
        $more.M3.rate | Should -Be 0.1
        $more.M3.passed | Should -BeFalse
    }

    It 'M4 allows a question rate 20 points from the expected one and refuses a larger distance' {
        $edge = Get-Corpus -Count 20 -Characters @('Nerea') -Questions @{ Nerea = 10 }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $edge -QuestionFrequency @{ Nerea = 'a_volte' }
        $score.M4.characters['Nerea'].distance | Should -Be 0.2
        $score.M4.passed | Should -BeTrue
        $far = Get-Corpus -Count 20 -Characters @('Nerea') -Questions @{ Nerea = 11 }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $far -QuestionFrequency @{ Nerea = 'a_volte' }
        $score.M4.characters['Nerea'].distance | Should -Be 0.25
        $score.M4.passed | Should -BeFalse
    }

    It 'M4 fails when one character is out of range even if the others are in range' {
        $run = Get-Corpus -Questions @{ Nerea = 12; Ilario = 12; Tessa = 0 }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $score.M4.characters['Tessa'].passed | Should -BeFalse
        $score.M4.characters['Nerea'].passed | Should -BeTrue
        $score.M4.passed | Should -BeFalse
    }

    It 'counts only the complete replies of the main conversations' {
        $run = Get-SingleCharacterRun
        $run += @{ Conversation = 'change'; Character = 'Nerea'; Number = 1; Kind = 'chat'; Status = 'complete'; Text = 'Altro testo.'; Checks = @{ V8 = 1 }; Normalizer = @{} }
        $run += @{ Conversation = 'main'; Character = 'Nerea'; Number = 101; Kind = 'chat'; Status = 'timeout'; Text = ''; Checks = $null; Normalizer = $null }
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency
        $score.replies | Should -Be 100
        $score.M1.other_language | Should -Be 0
    }

    It 'fails every metric for a profile with no complete reply' {
        $score = Get-ItalianMetricScore -ProfileId 'p' -Reply @()
        $score.replies | Should -Be 0
        $score.automatic_passed | Should -BeFalse
        $score.M1.passed | Should -BeFalse
        $score.M4.passed | Should -BeFalse
    }

    It 'reads the check counts of a record parsed from JSON' {
        $run = Get-SingleCharacterRun
        $run[0].Checks = '{"V9": 2}' | ConvertFrom-Json
        (Get-ItalianMetricScore -ProfileId 'p' -Reply $run -QuestionFrequency $script:Frequency).M2.hits | Should -Be 2
    }
}

Describe 'Get-ItalianBlindSheet' {
    It 'samples 30 naturalness and 20 authorship replies per profile, balanced by character, none twice' {
        $blind = Get-TestBlind
        foreach ($profileId in @('profile-alpha', 'profile-beta')) {
            $own = @($blind.Key.items | Where-Object { $_.profile -eq $profileId })
            @($own | Where-Object { $_.kind -eq 'naturalness' }).Length | Should -Be 30
            $written = @($own | Where-Object { $_.kind -eq 'authorship' })
            $written.Length | Should -Be 20
            @($written | Where-Object { $_.character -eq 'Nerea' }).Length | Should -Be 7
            @($written | Where-Object { $_.character -eq 'Ilario' }).Length | Should -Be 7
            @($written | Where-Object { $_.character -eq 'Tessa' }).Length | Should -Be 6
            @($own | ForEach-Object { "$($_.character)#$($_.number)" } | Select-Object -Unique).Length | Should -Be 50
        }
        $blind.Key.evidence | Should -Be 'protocol'
    }

    It 'never shows a profile id, and its ids reveal neither profile nor character' {
        $blind = Get-TestBlind
        $blind.Sheet | Should -Not -Match 'profile-alpha'
        $blind.Sheet | Should -Not -Match 'profile-beta'
        $blind.Sheet | Should -Match "(?m)^Sheet: $($blind.Key.sheet_id)\s*$"
        $ids = @($blind.Key.items | ForEach-Object { $_.id })
        $ids.Length | Should -Be 100
        @($ids | Where-Object { $_ -notmatch '^[NA]\d{2}$' }) | Should -BeNullOrEmpty
        $blind.Sheet | Should -Match '### N01'
        $blind.Sheet | Should -Match '### A20'
    }

    It 'mixes the profiles in one order, so the first ten items of a part are not all of one profile' {
        $blind = Get-TestBlind
        foreach ($kind in @('naturalness', 'authorship')) {
            $firstTen = @($blind.Key.items | Where-Object { $_.kind -eq $kind } | Select-Object -First 10)
            @($firstTen | ForEach-Object { $_.profile } | Select-Object -Unique).Length | Should -Be 2
        }
    }

    It 'gives the same sheet for the same seed and another sheet for another seed' {
        (Get-TestBlind -Seed 5).Sheet | Should -Be (Get-TestBlind -Seed 5).Sheet
        (Get-TestBlind -Seed 5).Sheet | Should -Not -Be (Get-TestBlind -Seed 6).Sheet
        (Get-TestBlind -Seed 5).Key.seed | Should -Be 5
    }

    It 'shows the question and the character for naturalness, and only the reply for authorship' {
        $blind = Get-TestBlind
        $blind.Sheet | Should -Match '(?m)^User: Domanda \d+\s*$'
        $blind.Sheet | Should -Match '(?m)^> Risposta numero \d+ di (Nerea|Ilario|Tessa)'
        $part = ($blind.Sheet -split '## Part 2')[1]
        $part | Should -Not -Match '(?m)^User: '
        $part | Should -Not -Match '(?m)^Character: '
    }

    It 'is a smoke sheet in the short mode or when a sample cannot be filled' {
        (Get-TestBlind -Mode 'Short').Key.evidence | Should -Be 'smoke'
        $small = Get-TestBlind -Count 12
        $small.Key.evidence | Should -Be 'smoke'
        @($small.Key.items | Where-Object { $_.profile -eq 'profile-alpha' }).Length | Should -Be 36
    }

    It 'uses probe replies only to fill what the other replies cannot' {
        $enough = Get-TestBlind -Probes 4
        @($enough.Key.items | Where-Object { $_.probe }) | Should -BeNullOrEmpty
        $only = Get-ItalianBlindSheet -ReplyByProfile @{ 'profile-alpha' = (Get-Corpus -Count 4 -Probes 4) } -CharacterName $script:Names -Seed 1 -Mode 'Short'
        @($only.Key.items | Where-Object { $_.probe }).Length | Should -Be 12
        $only.Key.evidence | Should -Be 'smoke'
    }

    It 'leaves out replies that are empty, incomplete or from the change conversation' {
        $run = Get-Corpus -Count 40
        $run += @{ Conversation = 'change'; Character = 'Nerea'; Number = 900; Kind = 'chat'; Status = 'complete'; Text = 'Cambio.'; Question = 'q'; Checks = @{}; Normalizer = @{} }
        $run += @{ Conversation = 'main'; Character = 'Nerea'; Number = 901; Kind = 'chat'; Status = 'failed'; Text = 'Rotto.'; Question = 'q'; Checks = @{}; Normalizer = @{} }
        $run += @{ Conversation = 'main'; Character = 'Nerea'; Number = 902; Kind = 'chat'; Status = 'complete'; Text = '  '; Question = 'q'; Checks = @{}; Normalizer = @{} }
        $blind = Get-ItalianBlindSheet -ReplyByProfile @{ 'profile-alpha' = $run } -CharacterName $script:Names -Seed 2
        @($blind.Key.items | Where-Object { $_.number -ge 900 }) | Should -BeNullOrEmpty
    }

    It 'refuses a run with no reply to sample' {
        { Get-ItalianBlindSheet -ReplyByProfile @{ 'profile-alpha' = @() } -CharacterName $script:Names } | Should -Throw 'No profile has a complete reply*'
    }
}

Describe 'Read-ItalianRating' {
    It 'reads every rating and author of a filled sheet, ignoring case and accents in names' {
        $blind = Get-TestBlind
        $sheet = (Get-FilledSheet -Blind $blind -Rating @{ 'profile-alpha' = @(5, 3) }) -replace '(?m)^Author: Nerea$', 'Author: NEREA'
        $rating = Read-ItalianRating -SheetText $sheet -Key $blind.Key
        $rating.Problems | Should -BeNullOrEmpty
        $rating.Missing | Should -BeNullOrEmpty
        $rating.Naturalness.Count | Should -Be 60
        $rating.Authorship.Count | Should -Be 40
        @($rating.Authorship.Values | Where-Object { $_ -ceq 'Nerea' }).Length | Should -Be 14
        $first = @($blind.Key.items | Where-Object { $_.kind -eq 'naturalness' -and $_.profile -eq 'profile-alpha' })[0]
        $rating.Naturalness[$first.id] | Should -Be 5
    }

    It 'lists the blank values as missing and never invents a rating' {
        $blind = Get-TestBlind
        $sheet = Get-FilledSheet -Blind $blind -Blank @('N03', 'A02')
        $rating = Read-ItalianRating -SheetText $sheet -Key $blind.Key
        $rating.Missing | Should -Be @('N03', 'A02')
        $rating.Naturalness.ContainsKey('N03') | Should -BeFalse
        $rating.Authorship.ContainsKey('A02') | Should -BeFalse
        $rating.Problems | Should -BeNullOrEmpty
    }

    It 'reports a rating outside 1 to 5, a rating that is no whole number and an author who is no character' {
        $blind = Get-TestBlind
        $sheet = Get-FilledSheet -Blind $blind
        $sheet = $sheet -replace '(?s)(### N01\r?\n.*?)Rating: 4', '${1}Rating: 6'
        $sheet = $sheet -replace '(?s)(### N02\r?\n.*?)Rating: 4', '${1}Rating: 4,5'
        $sheet = $sheet -replace '(?s)(### A01\r?\n.*?)Author: \w+', '${1}Author: Marco'
        $rating = Read-ItalianRating -SheetText $sheet -Key $blind.Key
        $rating.Problems.Length | Should -Be 3
        ($rating.Problems -join ' ') | Should -Match "N01 has the rating '6'"
        ($rating.Problems -join ' ') | Should -Match "N02 has the rating '4,5'"
        ($rating.Problems -join ' ') | Should -Match "A01 names 'Marco'"
        $rating.Naturalness.ContainsKey('N01') | Should -BeFalse
    }

    It 'reports a sheet of another key, an item twice and an item the sheet lacks' {
        $blind = Get-TestBlind
        $sheet = Get-FilledSheet -Blind $blind
        $other = Read-ItalianRating -SheetText ($sheet -replace '(?m)^Sheet: \S+', 'Sheet: 000000000000') -Key $blind.Key
        ($other.Problems -join ' ') | Should -Match 'not the one of this key'
        $twice = Read-ItalianRating -SheetText ($sheet + "`n### N01`nRating: 5`n") -Key $blind.Key
        ($twice.Problems -join ' ') | Should -Match 'N01 is on the sheet twice'
        $lacking = Read-ItalianRating -SheetText ($sheet -replace '(?s)### A20.*$', '') -Key $blind.Key
        ($lacking.Problems -join ' ') | Should -Match 'A20 is in the key but not on the sheet'
    }

    It 'ignores the instruction lines of the sheet, which mention Rating: and Author:' {
        $blind = Get-TestBlind
        $rating = Read-ItalianRating -SheetText $blind.Sheet -Key $blind.Key
        $rating.Problems | Should -BeNullOrEmpty
        $rating.Missing.Length | Should -Be 100
    }
}

Describe 'Get-ItalianVerdict' {
    It 'meets T-QLT-01 for a profile with passing metrics, an average of 4 and 80 % of the authors found' {
        $verdict = Get-Verdict -Blind (Get-TestBlind) -Fill @{ Rating = @{ 'profile-alpha' = @(5, 3); 'profile-beta' = @(4) }; Wrong = @{ 'profile-alpha' = 4; 'profile-beta' = 0 } }
        $alpha = $verdict.profiles['profile-alpha']
        $alpha.M5.average | Should -Be 4
        $alpha.M5.passed | Should -BeTrue
        $alpha.M6.correct | Should -Be 16
        $alpha.M6.rate | Should -Be 0.8
        $alpha.M6.passed | Should -BeTrue
        $alpha.status | Should -Be 'met'
        $verdict.profiles['profile-beta'].status | Should -Be 'met'
        $verdict.passed | Should -BeTrue
        $verdict.evidence | Should -Be 'protocol'
    }

    It 'misses M5 below an average of 4 and M6 below 80 %, naming each reason' {
        $ratings = @(3) + @(1..29 | ForEach-Object { 4 })
        $verdict = Get-Verdict -Blind (Get-TestBlind) -Fill @{ Rating = @{ 'profile-alpha' = $ratings }; Wrong = @{ 'profile-beta' = 5 } }
        $alpha = $verdict.profiles['profile-alpha']
        $alpha.M5.average | Should -BeLessThan 4
        $alpha.status | Should -Be 'missed'
        ($alpha.reasons -join ' ') | Should -Match 'M5 average is'
        $beta = $verdict.profiles['profile-beta']
        $beta.M6.rate | Should -Be 0.75
        $beta.M6.passed | Should -BeFalse
        $beta.status | Should -Be 'missed'
        ($beta.reasons -join ' ') | Should -Match 'M6 is 0.75'
        $verdict.passed | Should -BeFalse
    }

    It 'decides each profile on its own items, so one met profile does not save the other' {
        $verdict = Get-Verdict -Blind (Get-TestBlind) -Fill @{ Wrong = @{ 'profile-beta' = 10 } }
        $verdict.profiles['profile-alpha'].status | Should -Be 'met'
        $verdict.profiles['profile-beta'].status | Should -Be 'missed'
        $verdict.passed | Should -BeFalse
    }

    It 'is incomplete, not met, when a rating or an author is blank' {
        $blind = Get-TestBlind
        $someRated = @($blind.Key.items | Where-Object { $_.kind -eq 'naturalness' -and $_.profile -eq 'profile-alpha' })[0].id
        $verdict = Get-Verdict -Blind $blind -Fill @{ Blank = @($someRated) }
        $verdict.profiles['profile-alpha'].status | Should -Be 'incomplete'
        $verdict.profiles['profile-alpha'].M5.rated | Should -Be 29
        $verdict.profiles['profile-beta'].status | Should -Be 'met'
        $verdict.passed | Should -BeFalse
    }

    It 'is incomplete for a profile without recorded metrics' {
        $blind = Get-TestBlind
        $rating = Read-ItalianRating -SheetText (Get-FilledSheet -Blind $blind) -Key $blind.Key
        $verdict = Get-ItalianVerdict -Key $blind.Key -Rating $rating -Metric @{ 'profile-alpha' = (Get-PassingMetric) }
        $verdict.profiles['profile-alpha'].status | Should -Be 'met'
        $verdict.profiles['profile-beta'].status | Should -Be 'incomplete'
        ($verdict.profiles['profile-beta'].reasons -join ' ') | Should -Match 'No automatic metric M1'
    }

    It 'misses a profile whose automatic metric misses its target even when the owner rates it well' {
        $blind = Get-TestBlind
        $rating = Read-ItalianRating -SheetText (Get-FilledSheet -Blind $blind) -Key $blind.Key
        $failing = Get-PassingMetric
        $failing.M2 = @{ passed = $false }
        $verdict = Get-ItalianVerdict -Key $blind.Key -Rating $rating -Metric @{ 'profile-alpha' = (Get-PassingMetric); 'profile-beta' = $failing }
        $verdict.profiles['profile-beta'].status | Should -Be 'missed'
        ($verdict.profiles['profile-beta'].reasons -join ' ') | Should -Match 'M2 misses its target'
    }

    It 'marks the verdict of a smoke sheet as no evidence' {
        $verdict = Get-Verdict -Blind (Get-TestBlind -Mode 'Short')
        $verdict.evidence | Should -Be 'smoke'
    }
}

Describe 'Invoke-ItalianRating' {
    BeforeAll {
        function Initialize-RunFolder {
            # Writes the artifacts of two passing profiles into a new folder of the test drive and returns it with the blind sheet.
            param([string]$Name)
            $folder = Join-Path $TestDrive $Name
            $blind = Get-TestBlind
            $metric = @{ 'profile-alpha' = (Get-ItalianMetricScore -ProfileId 'profile-alpha' -Reply (Get-Corpus) -QuestionFrequency $script:Frequency); 'profile-beta' = (Get-ItalianMetricScore -ProfileId 'profile-beta' -Reply (Get-Corpus) -QuestionFrequency $script:Frequency) }
            $null = Write-ItalianArtifact -Folder $folder -Blind $blind -Metric $metric
            return @{ Folder = $folder; Blind = $blind }
        }
    }

    It 'writes the sheet, key and metrics and imports a filled sheet into a verdict file' {
        $run = Initialize-RunFolder 'rating-ok'
        foreach ($name in @('sheet.md', 'key.json', 'metrics.json')) { Test-Path -LiteralPath (Join-Path $run.Folder $name) | Should -BeTrue }
        (Get-Content -LiteralPath (Join-Path $run.Folder 'sheet.md') -Raw) | Should -Not -Match 'profile-alpha'
        $sheetPath = Join-Path $run.Folder 'sheet.md'
        [System.IO.File]::WriteAllText($sheetPath, (Get-FilledSheet -Blind $run.Blind), (New-Object System.Text.UTF8Encoding $false))
        $result = Invoke-ItalianRating -SheetPath $sheetPath 6>$null
        $result.Problems | Should -BeNullOrEmpty
        $result.Status | Should -Be 0
        $result.Verdict.passed | Should -BeTrue
        $written = (Get-Content -LiteralPath (Join-Path $run.Folder 'verdict.json') -Raw) | ConvertFrom-Json
        $written.profiles.'profile-alpha'.status | Should -Be 'met'
        $written.test | Should -Be 'T-QLT-01'
    }

    It 'fails, with the verdict written, when an author is wrong too often' {
        $run = Initialize-RunFolder 'rating-missed'
        $sheetPath = Join-Path $run.Folder 'sheet.md'
        [System.IO.File]::WriteAllText($sheetPath, (Get-FilledSheet -Blind $run.Blind -Wrong @{ 'profile-alpha' = 10 }), (New-Object System.Text.UTF8Encoding $false))
        $result = Invoke-ItalianRating -SheetPath $sheetPath 6>$null
        $result.Status | Should -Be 1
        $result.Verdict.profiles['profile-alpha'].status | Should -Be 'missed'
        Test-Path -LiteralPath $result.VerdictPath | Should -BeTrue
    }

    It 'fails and names the blank ids when the sheet is not filled in' {
        $run = Initialize-RunFolder 'rating-blank'
        $result = Invoke-ItalianRating -SheetPath (Join-Path $run.Folder 'sheet.md') 6>$null
        $result.Status | Should -Be 1
        $result.Problems | Should -Contain 'N01 was left blank.'
        $result.Verdict.profiles['profile-alpha'].status | Should -Be 'incomplete'
    }

    It 'reads the key and metrics from another folder when one is given' {
        $run = Initialize-RunFolder 'rating-elsewhere'
        $copy = Join-Path $TestDrive 'rating-copy'
        $null = New-Item -ItemType Directory -Path $copy
        $sheetPath = Join-Path $copy 'my-sheet.md'
        [System.IO.File]::WriteAllText($sheetPath, (Get-FilledSheet -Blind $run.Blind), (New-Object System.Text.UTF8Encoding $false))
        (Invoke-ItalianRating -SheetPath $sheetPath -Folder $run.Folder 6>$null).Status | Should -Be 0
        Test-Path -LiteralPath (Join-Path $run.Folder 'verdict.json') | Should -BeTrue
    }

    It 'fails without reading anything when the sheet, the key or the metrics are missing' {
        $run = Initialize-RunFolder 'rating-gone'
        Remove-Item -LiteralPath (Join-Path $run.Folder 'key.json')
        $result = Invoke-ItalianRating -SheetPath (Join-Path $run.Folder 'sheet.md') 6>$null
        $result.Status | Should -Be 1
        $result.Verdict | Should -BeNullOrEmpty
        ($result.Problems -join ' ') | Should -Match 'key\.json does not exist'
    }
}
