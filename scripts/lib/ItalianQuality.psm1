# Computes the automatic metrics M1 to M4 of the Italian quality protocol T-QLT-01, builds its blind rating sheet, and turns a filled sheet into the per-profile verdict on M5 and M6.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'QualityLevel.psm1') -Force

# docs/prompting.md (Italian quality protocol): the targets of M1 to M6 and the sizes of the owner's samples.
$script:Limit = @{
    ItalianRate        = 0.98   # M1: share of replies mainly in Italian
    CalquesPer100      = 1.0    # M2: calque and artifact hits per 100 replies
    RepeatRate         = 0.05   # M3: share of replies repeating the first three words of one of the previous 10
    QuestionTolerance  = 0.20   # M4: distance, in share of replies, from the expected question rate
    NaturalnessAverage = 4.0    # M5: average owner rating out of 5
    AuthorshipRate     = 0.80   # M6: share of replies attributed to the right character
}
$script:NaturalnessSample = 30
$script:AuthorshipSample = 20
$script:RepeatWindow = 10
$script:RepeatWords = 3

# The expected share of replies that ask a question, for each card value of communication.question_frequency. The
# protocol states "the expected value" without numbers, so these are this module's reading of the three card values.
$script:ExpectedQuestionRate = @{ raramente = 0.10; a_volte = 0.30; spesso = 0.50 }

function Get-ItalianField {
    # Returns the member Name of Value (a hashtable, ordered dictionary or parsed JSON object), or $null when it has none. Not exported.
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

function Get-ItalianCount {
    # Returns the count Name of Table (a check count table of one reply), 0 when absent. Not exported.
    param($Table, [string]$Name)

    $value = Get-ItalianField -Value $Table -Name $Name
    if ($null -eq $value) { return 0 }
    return [int]$value
}

function Get-ItalianExpectedQuestionRate {
    <#
    Returns the expected share of replies with a question mark for a card's question_frequency (raramente 0.10, a_volte
    0.30, spesso 0.50). An unknown value reads as the card default, a_volte.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param([string]$Frequency = 'a_volte')

    if ($script:ExpectedQuestionRate.ContainsKey($Frequency)) { return [double]$script:ExpectedQuestionRate[$Frequency] }
    return [double]$script:ExpectedQuestionRate['a_volte']
}

function Get-ItalianLeadWord {
    # Returns the first RepeatWords folded words of Text, joined by a space, or '' when it has fewer. Not exported.
    param([AllowNull()] [AllowEmptyString()] [string]$Text)

    $folded = ConvertTo-QualityFoldedText -Text $Text
    $words = @([regex]::Matches($folded, '[^\W_]+') | ForEach-Object { $_.Value })
    if ($words.Length -lt $script:RepeatWords) { return '' }
    return ($words | Select-Object -First $script:RepeatWords) -join ' '
}

function Get-ItalianMetricScore {
    <#
    Returns the automatic metrics M1 to M4 of T-QLT-01 for ProfileId from Reply, the reply records of Consistency.Tests.ps1
    (Conversation, Character, Number, Status, Text, Checks). Only the complete replies of the "main" conversations count,
    because the change conversation of C6 starts on another profile.

    M1 is the share of replies without a V8 hit: V8 is the backend's stop-word check, which fires when more words are
    stop words of another supported language than of Italian, and it reads only replies of 8 words or more. M2 is the
    V9 hits (calques and artifacts, with the list's own opening and repetition rules) per 100 replies. M3 is computed
    from the texts: the share of replies whose first three words equal those of one of the 10 previous replies of the
    same conversation. M4 is, for each character, the share of replies holding a question mark against
    Get-ItalianExpectedQuestionRate of QuestionFrequency (character first name to card value), and passes when every
    character is within 20 points. M5 and M6 need the owner and are reported as pending. automatic_passed holds when
    M1 to M4 pass; a profile with no complete reply fails them all.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory = $true)] [string]$ProfileId,
        [object[]]$Reply = @(),
        [hashtable]$QuestionFrequency = @{}
    )

    $main = @(@($Reply) | Where-Object { $null -ne $_ -and $_.Conversation -eq 'main' -and $_.Status -eq 'complete' })
    $total = $main.Length

    $other = @($main | Where-Object { (Get-ItalianCount $_.Checks 'V8') -gt 0 }).Length
    $italian = if ($total -gt 0) { [math]::Round(1.0 - ($other / $total), 4) } else { 0.0 }
    $m1 = [ordered]@{
        target = 'at least 98 % of replies mainly in Italian, by stop-word ratio (no V8 hit)'; replies = $total
        other_language = $other; italian_rate = $italian
        passed = ($total -gt 0 -and $italian -ge $script:Limit.ItalianRate)
    }

    $calques = 0
    foreach ($item in $main) { $calques += Get-ItalianCount $item.Checks 'V9' }
    $per100 = if ($total -gt 0) { [math]::Round($calques * 100.0 / $total, 3) } else { 0.0 }
    $m2 = [ordered]@{
        target = 'at most 1 calque or artifact hit (V9) per 100 replies'; replies = $total; hits = $calques; per_100 = $per100
        passed = ($total -gt 0 -and $per100 -le $script:Limit.CalquesPer100)
    }

    $repeated = 0
    foreach ($character in @($main | ForEach-Object { [string]$_.Character } | Select-Object -Unique)) {
        $leads = New-Object System.Collections.Generic.List[string]
        foreach ($item in @($main | Where-Object { [string]$_.Character -ceq $character } | Sort-Object { [int]$_.Number })) {
            $lead = Get-ItalianLeadWord -Text ([string]$item.Text)
            if ($lead) {
                $from = [math]::Max(0, $leads.Count - $script:RepeatWindow)
                for ($index = $from; $index -lt $leads.Count; $index++) {
                    if ($leads[$index] -ceq $lead) {
                        $repeated++
                        break
                    }
                }
            }
            # A reply too short to have a lead still occupies a place among the previous 10.
            $leads.Add($lead)
        }
    }
    $repeatRate = if ($total -gt 0) { [math]::Round($repeated / $total, 4) } else { 0.0 }
    $m3 = [ordered]@{
        target = 'at most 5 % of replies share their first three words with one of the previous 10'; replies = $total
        repeated = $repeated; rate = $repeatRate; passed = ($total -gt 0 -and $repeatRate -le $script:Limit.RepeatRate)
    }

    $characters = [ordered]@{}
    $questionOk = $total -gt 0
    foreach ($character in @($main | ForEach-Object { [string]$_.Character } | Select-Object -Unique)) {
        $own = @($main | Where-Object { [string]$_.Character -ceq $character })
        $questions = @($own | Where-Object { ([string]$_.Text).Contains('?') }).Length
        $rate = [math]::Round($questions / $own.Length, 4)
        $frequency = 'a_volte'
        if ($QuestionFrequency.ContainsKey($character)) { $frequency = [string]$QuestionFrequency[$character] }
        $expected = Get-ItalianExpectedQuestionRate -Frequency $frequency
        $distance = [math]::Round([math]::Abs($rate - $expected), 4)
        $ok = ($distance -le $script:Limit.QuestionTolerance)
        if (-not $ok) { $questionOk = $false }
        $characters[$character] = [ordered]@{
            replies = $own.Length; questions = $questions; rate = $rate; question_frequency = $frequency
            expected_rate = $expected; distance = $distance; passed = $ok
        }
    }
    $m4 = [ordered]@{
        target = "question rate within 20 points of the card's expected value, for every character"
        expected_rates = 'raramente 0.10, a_volte 0.30, spesso 0.50 (share of replies holding a question mark)'
        characters = $characters; passed = $questionOk
    }

    $pending = [ordered]@{ status = 'pending'; reason = 'the owner rates the blind sheet' }
    return [ordered]@{
        profile = $ProfileId; replies = $total
        M1 = $m1; M2 = $m2; M3 = $m3; M4 = $m4; M5 = $pending; M6 = $pending
        automatic_passed = ($m1.passed -and $m2.passed -and $m3.passed -and $m4.passed)
    }
}

function Get-ItalianShuffle {
    # Returns Item in a random order drawn from Random (Fisher-Yates), so one seed gives one order. Not exported.
    param([object[]]$Item = @(), [System.Random]$Random)

    $list = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($Item)) { $list.Add($entry) }
    for ($index = $list.Count - 1; $index -gt 0; $index--) {
        $swap = $Random.Next($index + 1)
        $held = $list[$index]
        $list[$index] = $list[$swap]
        $list[$swap] = $held
    }
    return $list.ToArray()
}

function Select-ItalianSample {
    # Returns up to Size entries of Pool (non-probe replies, shuffled) and, when that is not enough, of Spare (probe
    # replies), as @{ Chosen; Spare }: Spare keeps the probe replies not chosen so that the next sample can use them. Not exported.
    param([object[]]$Pool = @(), [object[]]$Spare = @(), [int]$Size)

    $chosen = @(@($Pool) | Select-Object -First $Size)
    $rest = @(@($Pool) | Select-Object -Skip $chosen.Length)
    $missing = $Size - $chosen.Length
    $extra = @()
    if ($missing -gt 0) {
        $extra = @(@($Spare) | Select-Object -First $missing)
        $Spare = @(@($Spare) | Select-Object -Skip $extra.Length)
    }
    return @{ Chosen = @(@($chosen) + @($extra)); Rest = $rest; Spare = @($Spare) }
}

function Get-ItalianBlindSheet {
    <#
    Returns @{ Sheet; Key } for the owner's blind rating of T-QLT-01. ReplyByProfile maps each profile id to its reply
    records (as Get-ItalianMetricScore reads them, with Question, Kind and Text added). Per profile it samples
    NaturalnessSize replies for M5 and AuthorshipSize for M6, from the complete replies of the main conversations: M6
    takes the same number per character (the remainder to the first characters, and at most half of a character's
    replies), M5 takes the rest of the pool, and no reply is in both. Probe replies, which answer a scripted identity question, are used only to fill a sample
    that the other replies cannot (the short mode has no other).

    The sheet is Markdown, shows no profile id and holds the replies of every profile mixed in one random order per
    part, under opaque ids N01... (naturalness) and A01... (authorship). Key is the only record of which profile and
    character wrote each id: sheet_id (a hash of the sheet, which Read-ItalianRating checks), mode, evidence, seed, the
    sample sizes asked and items (id, kind, profile, character, number, probe). evidence is 'protocol' when Mode is
    Full and every sample reached its size, and 'smoke' otherwise. The same Seed and input give the same sheet.
    Throws when no profile has a reply to sample.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [hashtable]$ReplyByProfile,
        [string[]]$CharacterName = @(),
        [hashtable]$CharacterNote = @{},
        [int]$Seed = 1,
        [string]$Mode = 'Full',
        [int]$NaturalnessSize = $script:NaturalnessSample,
        [int]$AuthorshipSize = $script:AuthorshipSample
    )

    $random = New-Object System.Random $Seed
    $naturalness = New-Object System.Collections.Generic.List[object]
    $authorship = New-Object System.Collections.Generic.List[object]
    $reached = $true
    foreach ($profileId in @($ReplyByProfile.Keys | Sort-Object)) {
        $all = @(@($ReplyByProfile[$profileId]) | Where-Object { $null -ne $_ -and $_.Conversation -eq 'main' -and $_.Status -eq 'complete' -and ([string]$_.Text).Trim() })
        $names = [string[]]@(if ($CharacterName.Length -gt 0) { $CharacterName } else { $all | ForEach-Object { [string]$_.Character } | Select-Object -Unique })
        $pool = @{}
        $spare = @{}
        foreach ($name in $names) {
            $own = @($all | Where-Object { [string]$_.Character -ceq $name })
            $pool[$name] = @(Get-ItalianShuffle -Item @($own | Where-Object { $_.Kind -ne 'probe' }) -Random $random)
            $spare[$name] = @(Get-ItalianShuffle -Item @($own | Where-Object { $_.Kind -eq 'probe' }) -Random $random)
        }
        $chosenAuthorship = New-Object System.Collections.Generic.List[object]
        for ($index = 0; $index -lt $names.Length; $index++) {
            $name = $names[$index]
            $quota = [math]::Floor($AuthorshipSize / $names.Length)
            if ($index -lt ($AuthorshipSize % $names.Length)) { $quota++ }
            # A character with few replies gives at most half of them to M6, so M5 keeps some (the short mode).
            $quota = [math]::Min($quota, [math]::Floor((@($pool[$name]).Length + @($spare[$name]).Length) / 2))
            $taken = Select-ItalianSample -Pool $pool[$name] -Spare $spare[$name] -Size $quota
            $pool[$name] = $taken.Rest
            $spare[$name] = $taken.Spare
            foreach ($item in $taken.Chosen) { $chosenAuthorship.Add($item) }
        }
        $remaining = @($names | ForEach-Object { $pool[$_] })
        $remainingProbe = @($names | ForEach-Object { $spare[$_] })
        $takenNaturalness = Select-ItalianSample -Pool @(Get-ItalianShuffle -Item $remaining -Random $random) -Spare @(Get-ItalianShuffle -Item $remainingProbe -Random $random) -Size $NaturalnessSize
        if ($chosenAuthorship.Count -lt $AuthorshipSize -or $takenNaturalness.Chosen.Length -lt $NaturalnessSize) { $reached = $false }
        foreach ($item in $chosenAuthorship) { $authorship.Add(@{ Profile = $profileId; Reply = $item }) }
        foreach ($item in $takenNaturalness.Chosen) { $naturalness.Add(@{ Profile = $profileId; Reply = $item }) }
    }
    if ($naturalness.Count + $authorship.Count -eq 0) { throw 'No profile has a complete reply to put on the sheet.' }

    $ordered = @(
        @{ Kind = 'naturalness'; Prefix = 'N'; Entry = @(Get-ItalianShuffle -Item $naturalness.ToArray() -Random $random) },
        @{ Kind = 'authorship'; Prefix = 'A'; Entry = @(Get-ItalianShuffle -Item $authorship.ToArray() -Random $random) })
    $items = New-Object System.Collections.Generic.List[object]
    $body = New-Object System.Text.StringBuilder
    $null = $body.AppendLine('## Part 1: naturalness')
    $null = $body.AppendLine()
    $null = $body.AppendLine('Rate how natural each reply sounds as Italian in that conversation: 1 = clearly artificial or awkward, 5 = fully natural. Write one whole number from 1 to 5 after "Rating:". The lines that start with ">" are the reply.')
    $partTwo = $false
    foreach ($group in $ordered) {
        if ($group.Kind -eq 'authorship') {
            $partTwo = $true
            $null = $body.AppendLine()
            $null = $body.AppendLine('## Part 2: who wrote it')
            $null = $body.AppendLine()
            $null = $body.AppendLine('Each reply was written by one of the characters below. Write that character''s first name after "Author:". The lines that start with ">" are the reply.')
            $null = $body.AppendLine()
            foreach ($name in $CharacterName) {
                $note = if ($CharacterNote.ContainsKey($name)) { " - $($CharacterNote[$name])" } else { '' }
                $null = $body.AppendLine("- $name$note")
            }
        }
        $number = 0
        foreach ($entry in $group.Entry) {
            $number++
            $id = '{0}{1:D2}' -f $group.Prefix, $number
            $reply = $entry.Reply
            $null = $body.AppendLine()
            $null = $body.AppendLine("### $id")
            if ($group.Kind -eq 'naturalness') {
                $null = $body.AppendLine("Character: $($reply.Character)")
                $question = ([string](Get-ItalianField $reply 'Question')).Trim()
                if ($question) { $null = $body.AppendLine("User: $question") }
            }
            foreach ($line in @(([string]$reply.Text).Trim() -split '\r?\n')) { $null = $body.AppendLine("> $line") }
            $null = $body.AppendLine($(if ($group.Kind -eq 'naturalness') { 'Rating:' } else { 'Author:' }))
            $items.Add([ordered]@{
                    id = $id; kind = $group.Kind; profile = [string]$entry.Profile; character = [string]$reply.Character
                    number = [int]$reply.Number; probe = ($reply.Kind -eq 'probe')
                })
        }
    }
    if (-not $partTwo) { throw 'The sheet has no second part.' }

    $text = $body.ToString()
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $hash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) }
    finally { $hash.Dispose() }
    $sheetId = (($digest | Select-Object -First 6 | ForEach-Object { $_.ToString('x2') }) -join '')
    $head = @(
        '# Lyrveil Italian quality: blind rating sheet (T-QLT-01)', '',
        "Sheet: $sheetId", '',
        'Fill in the "Rating:" and "Author:" lines in this file and save it. Do not open key.json before you are done: it says which profile wrote each reply. Then run: scripts\lyrveil.cmd test quality-rating <path of this file>', '')
    return @{
        Sheet = (($head -join "`n") + "`n" + $text)
        Key   = [ordered]@{
            version = 1; sheet_id = $sheetId; mode = $Mode; seed = $Seed
            evidence = $(if ($Mode -eq 'Full' -and $reached) { 'protocol' } else { 'smoke' })
            naturalness_size = $NaturalnessSize; authorship_size = $AuthorshipSize
            characters = [string[]]@($CharacterName); items = $items.ToArray()
        }
    }
}

function Read-ItalianRating {
    <#
    Reads SheetText, a sheet of Get-ItalianBlindSheet filled in by the owner, against Key (the parsed key.json or the
    hashtable Get-ItalianBlindSheet returned). Returns @{ Naturalness; Authorship; Missing; Problems }: Naturalness maps
    each naturalness id to its rating (1 to 5), Authorship maps each authorship id to the character named, in the
    spelling of Key.characters; Missing lists the ids left blank, and Problems says what could not be read: a sheet
    that is not the key's (sheet id), an id the key does not know, an id twice, a rating that is no whole number from
    1 to 5, an author who is no character of the key, and a key id the sheet lacks. A blank value is only Missing.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [AllowEmptyString()] [string]$SheetText = '',
        [Parameter(Mandatory = $true)] $Key
    )

    $naturalness = @{}
    $authorship = @{}
    $missing = New-Object System.Collections.Generic.List[string]
    $problems = New-Object System.Collections.Generic.List[string]
    $kinds = @{}
    foreach ($item in @(Get-ItalianField $Key 'items')) { $kinds[[string](Get-ItalianField $item 'id')] = [string](Get-ItalianField $item 'kind') }
    $names = @{}
    foreach ($name in @(Get-ItalianField $Key 'characters')) { $names[(ConvertTo-QualityFoldedText -Text ([string]$name))] = [string]$name }

    $header = [regex]::Match($SheetText, '(?m)^Sheet:\s*(\S+)\s*$')
    $sheetId = [string](Get-ItalianField $Key 'sheet_id')
    if (-not $header.Success -or $header.Groups[1].Value -cne $sheetId) {
        $problems.Add("The sheet is not the one of this key: its 'Sheet:' line is missing or differs from $sheetId.")
    }

    $seen = @{}
    $current = ''
    foreach ($line in @($SheetText -split '\r?\n')) {
        $heading = [regex]::Match($line, '^###\s+([NA]\d+)\s*$')
        if ($heading.Success) {
            $current = $heading.Groups[1].Value
            if ($seen.ContainsKey($current)) { $problems.Add("$current is on the sheet twice.") }
            $seen[$current] = $true
            if (-not $kinds.ContainsKey($current)) { $problems.Add("$current is not an item of the key.") }
            continue
        }
        if (-not $current -or -not $kinds.ContainsKey($current)) { continue }
        $rating = [regex]::Match($line, '^Rating:\s*(.*?)\s*$')
        $author = [regex]::Match($line, '^Author:\s*(.*?)\s*$')
        if ($rating.Success -and $kinds[$current] -eq 'naturalness') {
            $value = $rating.Groups[1].Value
            if (-not $value) { $missing.Add($current) }
            elseif ($value -match '^[1-5]$') { $naturalness[$current] = [int]$value }
            else { $problems.Add("$current has the rating '$value', which is no whole number from 1 to 5.") }
        }
        elseif ($author.Success -and $kinds[$current] -eq 'authorship') {
            $value = $author.Groups[1].Value
            $folded = ConvertTo-QualityFoldedText -Text $value
            if (-not $value) { $missing.Add($current) }
            elseif ($names.ContainsKey($folded)) { $authorship[$current] = $names[$folded] }
            else { $problems.Add("$current names '$value', who is none of: $((@($names.Values) | Sort-Object) -join ', ').") }
        }
    }
    foreach ($id in @($kinds.Keys | Sort-Object)) {
        if (-not $seen.ContainsKey($id)) { $problems.Add("$id is in the key but not on the sheet.") }
    }
    return @{ Naturalness = $naturalness; Authorship = $authorship; Missing = [string[]]$missing.ToArray(); Problems = [string[]]$problems.ToArray() }
}

function Get-ItalianVerdict {
    <#
    Returns the T-QLT-01 verdict of every profile named in Key.items, from Key, Rating (what Read-ItalianRating
    returned) and Metric (profile id to the record of Get-ItalianMetricScore, parsed from metrics.json or not).

    Per profile: M1 to M4 as computed, M5 (the average rating of its naturalness items against 4 out of 5) and M6
    (the share of its authorship items attributed to the right character against 80 %), and a status: 'incomplete'
    when it has no metrics, no item of a part, or an item without a valid value (nothing is guessed), 'met' when
    M1 to M6 all pass, and 'missed' otherwise, with the reasons. The record's evidence is 'protocol' only when the key
    says so; a smoke or short sheet is computed but is no evidence. passed holds when every profile is met.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory = $true)] $Key,
        [Parameter(Mandatory = $true)] $Rating,
        $Metric = $null
    )

    $items = @(Get-ItalianField $Key 'items')
    $profiles = [ordered]@{}
    $allMet = ($items.Length -gt 0)
    foreach ($profileId in @($items | ForEach-Object { [string](Get-ItalianField $_ 'profile') } | Select-Object -Unique | Sort-Object)) {
        $reasons = New-Object System.Collections.Generic.List[string]
        $incomplete = $false
        $metrics = Get-ItalianField $Metric $profileId
        $record = [ordered]@{ profile = $profileId }
        foreach ($name in @('M1', 'M2', 'M3', 'M4')) {
            $value = Get-ItalianField $metrics $name
            $record[$name] = $value
            if ($null -eq $value) {
                $incomplete = $true
                $reasons.Add("No automatic metric $name was recorded for $profileId.")
            }
            elseif ((Get-ItalianField $value 'passed') -ne $true) { $reasons.Add("$name misses its target.") }
        }

        $own = @($items | Where-Object { [string](Get-ItalianField $_ 'profile') -ceq $profileId })
        $natural = @($own | Where-Object { [string](Get-ItalianField $_ 'kind') -eq 'naturalness' })
        $rated = @($natural | Where-Object { $Rating.Naturalness.ContainsKey([string](Get-ItalianField $_ 'id')) })
        $average = if ($rated.Length -gt 0) { [math]::Round((@($rated | ForEach-Object { [double]$Rating.Naturalness[[string](Get-ItalianField $_ 'id')] }) | Measure-Object -Average).Average, 3) } else { 0.0 }
        $m5 = [ordered]@{ target = 'average naturalness rating of at least 4 out of 5'; items = $natural.Length; rated = $rated.Length; average = $average; passed = $false }
        if ($natural.Length -eq 0 -or $rated.Length -lt $natural.Length) {
            $incomplete = $true
            $reasons.Add("M5 has $($rated.Length) of $($natural.Length) ratings.")
        }
        else {
            $m5.passed = ($average -ge $script:Limit.NaturalnessAverage)
            if (-not $m5.passed) { $reasons.Add("M5 average is $average, below 4.") }
        }
        $record.M5 = $m5

        $written = @($own | Where-Object { [string](Get-ItalianField $_ 'kind') -eq 'authorship' })
        $guessed = @($written | Where-Object { $Rating.Authorship.ContainsKey([string](Get-ItalianField $_ 'id')) })
        $correct = @($guessed | Where-Object { $Rating.Authorship[[string](Get-ItalianField $_ 'id')] -ceq [string](Get-ItalianField $_ 'character') }).Length
        $share = if ($guessed.Length -gt 0) { [math]::Round($correct / $guessed.Length, 4) } else { 0.0 }
        $m6 = [ordered]@{ target = 'at least 80 % of replies attributed to the right character'; items = $written.Length; rated = $guessed.Length; correct = $correct; rate = $share; passed = $false }
        if ($written.Length -eq 0 -or $guessed.Length -lt $written.Length) {
            $incomplete = $true
            $reasons.Add("M6 has $($guessed.Length) of $($written.Length) answers.")
        }
        else {
            $m6.passed = ($share -ge $script:Limit.AuthorshipRate)
            if (-not $m6.passed) { $reasons.Add("M6 is $share, below 0.8.") }
        }
        $record.M6 = $m6

        $status = if ($incomplete) { 'incomplete' } elseif ($reasons.Count -eq 0) { 'met' } else { 'missed' }
        if ($status -ne 'met') { $allMet = $false }
        $record.status = $status
        $record.reasons = [string[]]$reasons.ToArray()
        $profiles[$profileId] = $record
    }
    $evidence = [string](Get-ItalianField $Key 'evidence')
    return [ordered]@{
        test = 'T-QLT-01'; evidence = $(if ($evidence -eq 'protocol') { 'protocol' } else { 'smoke' })
        sheet_id = [string](Get-ItalianField $Key 'sheet_id'); profiles = $profiles; passed = $allMet
    }
}

function Write-ItalianArtifact {
    <#
    Writes sheet.md, key.json and metrics.json of one T-QLT-01 run into Folder (created when missing), as UTF-8 without a
    byte order mark, and returns the three paths. Blind is what Get-ItalianBlindSheet returned; Metric maps each
    profile id to its Get-ItalianMetricScore record. The folder holds the key beside the sheet, so the sheet is blind
    only as long as the owner does not open key.json.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Folder,
        [Parameter(Mandatory = $true)] [hashtable]$Blind,
        [Parameter(Mandatory = $true)] [hashtable]$Metric
    )

    if (-not (Test-Path -LiteralPath $Folder)) { $null = New-Item -ItemType Directory -Path $Folder }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $paths = @{
        Sheet = (Join-Path $Folder 'sheet.md'); Key = (Join-Path $Folder 'key.json'); Metric = (Join-Path $Folder 'metrics.json')
    }
    [System.IO.File]::WriteAllText($paths.Sheet, [string]$Blind.Sheet, $utf8)
    [System.IO.File]::WriteAllText($paths.Key, (ConvertTo-Json -InputObject $Blind.Key -Depth 6), $utf8)
    [System.IO.File]::WriteAllText($paths.Metric, (ConvertTo-Json -InputObject $Metric -Depth 8), $utf8)
    return $paths
}

function Invoke-ItalianRating {
    <#
    Imports the owner's filled sheet SheetPath and returns @{ Status; Verdict; Problems; VerdictPath }. key.json and
    metrics.json are read from Folder (the sheet's own folder when empty). Status is 0 only when the sheet read without a
    problem and every profile is met, and 1 otherwise: unreadable files, a sheet that is not the key's, a blank or
    invalid value, an unmet target. The verdict is written to verdict.json beside the key, also when a target is missed,
    and printed per profile; no rating is invented for a blank one.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$SheetPath,
        [string]$Folder = ''
    )

    $utf8 = New-Object System.Text.UTF8Encoding $false
    # Relative paths are read from the PowerShell location, which is not the process directory .NET resolves against.
    $SheetPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SheetPath)
    $Folder = if ($Folder) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Folder) } else { Split-Path -Parent $SheetPath }
    $keyPath = Join-Path $Folder 'key.json'
    $metricPath = Join-Path $Folder 'metrics.json'
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($path in @($SheetPath, $keyPath, $metricPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $problems.Add("$path does not exist.") }
    }
    if ($problems.Count -gt 0) { return @{ Status = 1; Verdict = $null; Problems = $problems.ToArray(); VerdictPath = '' } }
    try {
        $key = [System.IO.File]::ReadAllText($keyPath, $utf8) | ConvertFrom-Json
        $metric = [System.IO.File]::ReadAllText($metricPath, $utf8) | ConvertFrom-Json
    }
    catch {
        return @{ Status = 1; Verdict = $null; Problems = @("key.json or metrics.json could not be read: $($_.Exception.Message)"); VerdictPath = '' }
    }
    $rating = Read-ItalianRating -SheetText ([System.IO.File]::ReadAllText($SheetPath, $utf8)) -Key $key
    $verdict = Get-ItalianVerdict -Key $key -Rating $rating -Metric $metric
    $verdictPath = Join-Path $Folder 'verdict.json'
    [System.IO.File]::WriteAllText($verdictPath, (ConvertTo-Json -InputObject $verdict -Depth 8), $utf8)
    foreach ($problem in $rating.Problems) { $problems.Add($problem) }
    foreach ($id in $rating.Missing) { $problems.Add("$id was left blank.") }

    foreach ($profileId in @($verdict.profiles.Keys)) {
        $entry = $verdict.profiles[$profileId]
        $flags = @('M1', 'M2', 'M3', 'M4') | ForEach-Object { '{0} {1}' -f $_, (Get-ItalianField (Get-ItalianField $entry $_) 'passed') }
        Write-Host ("  {0}: {1}; {2}, M5 {3} (average {4}), M6 {5} (rate {6})" -f $profileId, $entry.status, ($flags -join ', '),
            $entry.M5.passed, $entry.M5.average, $entry.M6.passed, $entry.M6.rate)
        foreach ($reason in $entry.reasons) { Write-Host "      $reason" }
    }
    if ($verdict.evidence -ne 'protocol') { Write-Host '  This sheet is a smoke run: its verdict is no evidence for the catalog.' }
    $status = if ($problems.Count -eq 0 -and $verdict.passed) { 0 } else { 1 }
    return @{ Status = $status; Verdict = $verdict; Problems = $problems.ToArray(); VerdictPath = $verdictPath }
}

Export-ModuleMember -Function Get-ItalianExpectedQuestionRate, Get-ItalianMetricScore, Get-ItalianBlindSheet, Read-ItalianRating,
    Get-ItalianVerdict, Write-ItalianArtifact, Invoke-ItalianRating
