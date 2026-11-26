# Builds the blind review sheet of the image default grid (T-QLT-02) and of the image identity protocol with its measured levers (T-QLT-07), reads the owner's filled sheet, and computes the verdicts and the default rule.

Set-StrictMode -Version Latest

# docs/image-generation.md (Image identity protocol, Measured levers, Grid), docs/models.md (Defaults and how they can
# change) and ADR 0036: the thresholds of each decision. The protocol gives no number for the grid, so ParameterMargin is
# this module's reading of "the defaults stay unless the grid shows a visible defect": an alternative replaces a default
# setting only when its share of images with a defect is lower by at least this many points.
$script:Limit = @{
    PagWinRate         = 0.60   # PAG: share of blind pairs the lever's image wins against scale 0
    PagP95Seconds      = 45.0   # PAG: the Alta render stage's 95th percentile, nearest rank
    IdentityAccuracy   = 0.90   # T-QLT-07: share of correct attribute checks
    IdentityWeightGain = 0.05   # Identity weight: rise of that share, in points
    ParameterMargin    = 0.10   # Grid: lead of an alternative setting over the default one
}

# The grid variants that change one parameter, and the parameter they change (tests/gpu_probe/image_quality.py).
$script:GridParameter = [ordered]@{
    'clip-skip-1' = 'clip_skip'; 'cfg-4.5' = 'cfg'; 'cfg-7' = 'cfg'; 'denoise-0.35' = 'denoise'; 'denoise-0.55' = 'denoise'
}

# The five attribute checks of an identity image: the key of Read-ImageReview and the label on the sheet.
$script:Attribute = [ordered]@{
    hair_color = 'Hair color'; hair_length = 'Hair length'; eyes = 'Eyes'; age = 'Age band'; outfit = 'Outfit'
}

function Get-ImageField {
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

function Get-ImageShuffle {
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

function ConvertTo-ImageCell {
    # Returns the cell Value (a record of the quality test or of renders.json) as a hashtable with every field set. Not exported.
    param($Value)

    $seconds = Get-ImageField $Value 'Seconds'
    $seed = Get-ImageField $Value 'Seed'
    return @{
        Id        = [string](Get-ImageField $Value 'Id')
        Section   = [string](Get-ImageField $Value 'Section')
        Variant   = [string](Get-ImageField $Value 'Variant')
        Profile   = [string](Get-ImageField $Value 'Profile')
        Character = [string](Get-ImageField $Value 'Character')
        Scene     = [string](Get-ImageField $Value 'Scene')
        Seed      = $(if ($null -eq $seed) { 0 } else { [int]$seed })
        Aspect    = [string](Get-ImageField $Value 'Aspect')
        Pair      = ((Get-ImageField $Value 'Pair') -eq $true)
        Expected  = Get-ImageField $Value 'Expected'
        Status    = [string](Get-ImageField $Value 'Status')
        Seconds   = $(if ($null -eq $seconds) { -1.0 } else { [double]$seconds })
        Path      = [string](Get-ImageField $Value 'Path')
    }
}

function Get-ImageNearestRank {
    <#
    Returns the nearest-rank percentile Percentile (above 0, at most 100) of Value, the smallest value with at least that
    share of the values at or below it, or -1 when there is none. With fewer than 20 values the 95th is the largest.
    #>
    [CmdletBinding()]
    [OutputType([double])]
    param([double[]]$Value = @(), [double]$Percentile = 95)

    $sorted = @($Value | Sort-Object)
    if ($sorted.Length -eq 0) { return -1.0 }
    $rank = [int][math]::Ceiling($Percentile / 100.0 * $sorted.Length)
    if ($rank -lt 1) { $rank = 1 }
    return [double]$sorted[$rank - 1]
}

function Get-ImageReviewSheet {
    <#
    Returns @{ Sheet; Key; Files } for the owner's blind review of T-QLT-02 and T-QLT-07. Cell holds the render records
    of the run, each @{ Id; Section ('grid', 'identity' or 'pag'); Variant; Profile; Character; Scene; Seed; Aspect; Pair;
    Expected (identity cells: hair_color, hair_length, eye_color, age_band, outfit); Status ('success' when rendered);
    Seconds; Path (the saved image) }. Only the cells that rendered and saved an image are used.

    The sheet is Markdown and names no profile, setting or lever. It has up to four parts, each in one random order under
    opaque ids: G (a grid image, answered "Defect:"), I (an identity image, answered on five attribute lines), P (a blind
    pair of a PAG image against its scale-0 baseline, answered "Better:") and W (a blind pair of an identity-weight image
    against its baseline, answered "Style change:"). A pair puts the lever's image on a random side. Files lists each
    image the sheet shows as @{ Name; Source } (Name is relative to the sheet's folder, under images/).

    Key is the only record of what each id is: protocol, version, sheet_id (a hash of the sheet, which Read-ImageReview
    checks), mode, seed, evidence, default_profile, profiles and items (id, kind, profile, variant, character, scene,
    seed, aspect, and for a pair lever_side and the lever image's render seconds). evidence is 'protocol' when Mode is Full
    and Planned cells all rendered, and 'smoke' otherwise. The same Seed and input give the same sheet. Throws when no cell
    gives an item.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [object[]]$Cell,
        [string]$DefaultProfile = '',
        [int]$Seed = 1,
        [string]$Mode = 'Full',
        [int]$Planned = -1
    )

    $random = New-Object System.Random $Seed
    $usable = @(@($Cell) | Where-Object { $null -ne $_ } | ForEach-Object { ConvertTo-ImageCell -Value $_ } | Where-Object { $_.Status -eq 'success' -and $_.Path })
    $baseline = @{}
    foreach ($cell in @($usable | Where-Object { $_.Section -eq 'identity' -and $_.Variant -eq 'baseline' })) {
        $baseline["$($cell.Character)|$($cell.Scene)|$($cell.Seed)"] = $cell
    }
    $pairOf = {
        param($Lever)
        $found = $baseline["$($Lever.Character)|$($Lever.Scene)|$($Lever.Seed)"]
        if ($null -eq $found) { return $null }
        return @{ Lever = $Lever; Base = $found; Flip = ($random.Next(2) -eq 1) }
    }

    $grid = @(Get-ImageShuffle -Item @($usable | Where-Object { $_.Section -eq 'grid' }) -Random $random)
    $identity = @(Get-ImageShuffle -Item @($usable | Where-Object { $_.Section -eq 'identity' }) -Random $random)
    $pag = @(Get-ImageShuffle -Item @($usable | Where-Object { $_.Section -eq 'pag' }) -Random $random | ForEach-Object { & $pairOf $_ } | Where-Object { $null -ne $_ })
    $weight = @(Get-ImageShuffle -Item @($usable | Where-Object { $_.Section -eq 'identity' -and $_.Variant -like 'identity-weight-*' -and $_.Pair }) -Random $random | ForEach-Object { & $pairOf $_ } | Where-Object { $null -ne $_ })

    $parts = @(
        @{ Kind = 'grid'; Prefix = 'G'; Entry = $grid; Title = 'Part 1: visible defects'
            Text = 'Each image is a character in a scene. Look for visible defects: wrong or extra limbs, hands, a distorted face or body, broken objects, smeared areas, text or marks that should not be there. Write "yes" after "Defect:" when the image has a visible defect, "no" when it has none.'
        },
        @{ Kind = 'identity'; Prefix = 'I'; Entry = $identity; Title = 'Part 2: does the image match the character'
            Text = 'The "Expected:" line says what the character looks like. For each attribute write "ok" when the image shows it and "wrong" when it does not. Age band is the look of that age, not a number.'
        },
        @{ Kind = 'pag'; Prefix = 'P'; Entry = $pag; Title = 'Part 3: which image is better'
            Text = 'Each item shows two images of the same character and scene, A and B. Write "A" or "B" after "Better:" for the one that looks better overall (structure, anatomy, detail), or "same" when you cannot tell.'
        },
        @{ Kind = 'weight'; Prefix = 'W'; Entry = $weight; Title = 'Part 4: does the style change'
            Text = 'Each item shows two images of the same character and scene, A and B. Write "yes" after "Style change:" when the two differ in style (colors, line, shading, rendering) and not only in details, "no" when they look like the same style.'
        }
    )
    $items = New-Object System.Collections.Generic.List[object]
    $files = New-Object System.Collections.Generic.List[object]
    $body = New-Object System.Text.StringBuilder
    foreach ($part in $parts) {
        if (@($part.Entry).Length -eq 0) { continue }
        $null = $body.AppendLine("## $($part.Title)")
        $null = $body.AppendLine()
        $null = $body.AppendLine($part.Text)
        $number = 0
        foreach ($entry in @($part.Entry)) {
            $number++
            $id = '{0}{1:D2}' -f $part.Prefix, $number
            $null = $body.AppendLine()
            $null = $body.AppendLine("### $id")
            if ($part.Kind -eq 'grid' -or $part.Kind -eq 'identity') {
                $name = "$id.jpg"
                $files.Add(@{ Name = $name; Source = $entry.Path })
                $null = $body.AppendLine("![$id](images/$name)")
                if ($part.Kind -eq 'grid') {
                    $null = $body.AppendLine('Defect:')
                }
                else {
                    $expected = $entry.Expected
                    $null = $body.AppendLine("Expected: $(Get-ImageField $expected 'hair_color'), $(Get-ImageField $expected 'hair_length'), $(Get-ImageField $expected 'eye_color'); age band: $(Get-ImageField $expected 'age_band') (age $(Get-ImageField $expected 'age')); outfit: $(Get-ImageField $expected 'outfit')")
                    foreach ($label in $script:Attribute.Values) { $null = $body.AppendLine("${label}:") }
                }
                $items.Add([ordered]@{
                        id = $id; kind = $part.Kind; profile = $entry.Profile; variant = $entry.Variant; character = $entry.Character
                        scene = $entry.Scene; seed = $entry.Seed; aspect = $entry.Aspect
                    })
                continue
            }
            $sideA = if ($entry.Flip) { $entry.Lever } else { $entry.Base }
            $sideB = if ($entry.Flip) { $entry.Base } else { $entry.Lever }
            $files.Add(@{ Name = "$id-A.jpg"; Source = $sideA.Path })
            $files.Add(@{ Name = "$id-B.jpg"; Source = $sideB.Path })
            $null = $body.AppendLine("A: ![$id A](images/$id-A.jpg)")
            $null = $body.AppendLine("B: ![$id B](images/$id-B.jpg)")
            $null = $body.AppendLine($(if ($part.Kind -eq 'pag') { 'Better:' } else { 'Style change:' }))
            $items.Add([ordered]@{
                    id = $id; kind = $part.Kind; profile = $entry.Lever.Profile; variant = $entry.Lever.Variant; character = $entry.Lever.Character
                    scene = $entry.Lever.Scene; seed = $entry.Lever.Seed; aspect = $entry.Lever.Aspect
                    lever_side = $(if ($entry.Flip) { 'A' } else { 'B' }); seconds = $entry.Lever.Seconds
                })
        }
        $null = $body.AppendLine()
    }
    if ($items.Count -eq 0) { throw 'No rendered cell gives an item of the review sheet.' }

    $text = $body.ToString()
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $hash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) }
    finally { $hash.Dispose() }
    $sheetId = (($digest | Select-Object -First 6 | ForEach-Object { $_.ToString('x2') }) -join '')
    $head = @(
        '# Lyrveil image quality: blind review sheet (T-QLT-02 and T-QLT-07)', '',
        "Sheet: $sheetId", '',
        'Fill in the answer lines of every item in this file and save it. The images are in the images folder beside it. Do not open key.json before you are done: it says which profile, setting and lever made each image. Then run: scripts\lyrveil.cmd test quality-rating <path of this file>', '')
    $profiles = [string[]]@($items | ForEach-Object { [string]$_.profile } | Select-Object -Unique | Sort-Object)
    $reached = ($Planned -lt 0 -or $usable.Length -ge $Planned)
    return @{
        Sheet = (($head -join "`n") + "`n" + $text)
        Files = $files.ToArray()
        Key   = [ordered]@{
            protocol = 'image'; version = 1; sheet_id = $sheetId; mode = $Mode; seed = $Seed
            evidence = $(if ($Mode -eq 'Full' -and $reached) { 'protocol' } else { 'smoke' })
            default_profile = $DefaultProfile; profiles = $profiles; items = $items.ToArray()
        }
    }
}

function Read-ImageReview {
    <#
    Reads SheetText, a sheet of Get-ImageReviewSheet filled in by the owner, against Key (the parsed key.json or the
    hashtable Get-ImageReviewSheet returned). Returns @{ Defect; Attribute; Better; Style; Missing; Problems }. Defect
    maps each grid id to $true when the owner saw a defect; Attribute maps each identity id to a hashtable of the five
    checks (hair_color, hair_length, eyes, age, outfit), each $true for "ok"; Better maps each PAG pair id to 'A', 'B' or
    'same'; Style maps each identity-weight pair id to $true for "yes". Answers are case-insensitive, and "si", also with
    its accent, is "yes". Missing lists the ids left (partly) blank, and Problems says what could not be read: a sheet that is not the
    key's (sheet id), an id the key does not know, an id twice, an answer outside the words above, and a key id the sheet
    lacks. A blank is only Missing, and an item with a blank or invalid answer is in no map, so nothing is guessed.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [AllowEmptyString()] [string]$SheetText = '',
        [Parameter(Mandatory = $true)] $Key
    )

    $defect = @{}
    $attribute = @{}
    $better = @{}
    $style = @{}
    $missing = New-Object System.Collections.Generic.List[string]
    $problems = New-Object System.Collections.Generic.List[string]
    $kinds = @{}
    foreach ($item in @(Get-ImageField $Key 'items')) { $kinds[[string](Get-ImageField $item 'id')] = [string](Get-ImageField $item 'kind') }

    $header = [regex]::Match($SheetText, '(?m)^Sheet:\s*(\S+)\s*$')
    $sheetId = [string](Get-ImageField $Key 'sheet_id')
    if (-not $header.Success -or $header.Groups[1].Value -cne $sheetId) {
        $problems.Add("The sheet is not the one of this key: its 'Sheet:' line is missing or differs from $sheetId.")
    }

    $labels = @{}
    foreach ($name in $script:Attribute.Keys) { $labels[$script:Attribute[$name]] = $name }
    $seen = @{}
    $answers = @{}
    $current = ''
    foreach ($line in @($SheetText -split '\r?\n')) {
        $heading = [regex]::Match($line, '^###\s+([GIPW]\d+)\s*$')
        if ($heading.Success) {
            $current = $heading.Groups[1].Value
            if ($seen.ContainsKey($current)) { $problems.Add("$current is on the sheet twice.") }
            $seen[$current] = $true
            $answers[$current] = @{}
            if (-not $kinds.ContainsKey($current)) { $problems.Add("$current is not an item of the key.") }
            continue
        }
        if (-not $current -or -not $kinds.ContainsKey($current)) { continue }
        $match = [regex]::Match($line, '^(Defect|Better|Style change|Hair color|Hair length|Eyes|Age band|Outfit):\s*(.*?)\s*$')
        if (-not $match.Success) { continue }
        $label = $match.Groups[1].Value
        $value = $match.Groups[2].Value
        $kind = $kinds[$current]
        $field = $label
        if ($labels.ContainsKey($label)) { $field = $labels[$label] }
        $wanted = @{ grid = 'Defect'; pag = 'Better'; weight = 'Style change' }
        if (($kind -eq 'identity' -and -not $labels.ContainsKey($label)) -or ($kind -ne 'identity' -and $wanted[$kind] -ne $label)) { continue }
        if (-not $value) { continue }
        $folded = $value.ToLowerInvariant().Replace([string][char]0xEC, 'i')
        $parsed = $null
        if ($kind -eq 'identity') {
            if ($folded -eq 'ok') { $parsed = $true }
            elseif ($folded -eq 'wrong') { $parsed = $false }
        }
        elseif ($kind -eq 'pag') {
            if ($folded -eq 'a') { $parsed = 'A' }
            elseif ($folded -eq 'b') { $parsed = 'B' }
            elseif ($folded -eq 'same') { $parsed = 'same' }
        }
        else {
            if (@('yes', 'si') -contains $folded) { $parsed = $true }
            elseif ($folded -eq 'no') { $parsed = $false }
        }
        if ($null -eq $parsed) {
            $problems.Add("$current has the answer '$value' on its '$label' line, which is not one of the answers the sheet asks for.")
            continue
        }
        $answers[$current][$field] = $parsed
    }
    foreach ($id in @($kinds.Keys | Sort-Object)) {
        if (-not $seen.ContainsKey($id)) {
            $problems.Add("$id is in the key but not on the sheet.")
            continue
        }
        $given = $answers[$id]
        switch ($kinds[$id]) {
            'grid' { if ($given.ContainsKey('Defect')) { $defect[$id] = [bool]$given['Defect'] } else { $missing.Add($id) } }
            'pag' { if ($given.ContainsKey('Better')) { $better[$id] = [string]$given['Better'] } else { $missing.Add($id) } }
            'weight' { if ($given.ContainsKey('Style change')) { $style[$id] = [bool]$given['Style change'] } else { $missing.Add($id) } }
            'identity' {
                if (@($script:Attribute.Keys | Where-Object { $given.ContainsKey($_) }).Length -eq $script:Attribute.Count) {
                    $checks = @{}
                    foreach ($name in $script:Attribute.Keys) { $checks[$name] = [bool]$given[$name] }
                    $attribute[$id] = $checks
                }
                else { $missing.Add($id) }
            }
        }
    }
    return @{ Defect = $defect; Attribute = $attribute; Better = $better; Style = $style; Missing = [string[]]$missing.ToArray(); Problems = [string[]]$problems.ToArray() }
}

function Get-ImageDefectRate {
    # Returns @{ Images; Answered; Defects; Rate; Complete } of the grid items Item of the key, with Rate the share of the answered images that have a defect. Not exported.
    param([object[]]$Item = @(), $Review)

    $answered = @(@($Item) | Where-Object { $Review.Defect.ContainsKey([string](Get-ImageField $_ 'id')) })
    $defects = @($answered | Where-Object { $Review.Defect[[string](Get-ImageField $_ 'id')] }).Length
    $rate = if ($answered.Length -gt 0) { [math]::Round($defects / $answered.Length, 4) } else { 0.0 }
    return @{ Images = @($Item).Length; Answered = $answered.Length; Defects = $defects; Rate = $rate; Complete = (@($Item).Length -gt 0 -and $answered.Length -eq @($Item).Length) }
}

function Get-ImageAttributeScore {
    # Returns @{ Images; Answered; Checks; Correct; Accuracy; Complete } of the identity items Item of the key. Not exported.
    param([object[]]$Item = @(), $Review)

    $answered = @(@($Item) | Where-Object { $Review.Attribute.ContainsKey([string](Get-ImageField $_ 'id')) })
    $correct = 0
    foreach ($entry in $answered) {
        $checks = $Review.Attribute[[string](Get-ImageField $entry 'id')]
        $correct += @($script:Attribute.Keys | Where-Object { $checks[$_] }).Length
    }
    $total = $answered.Length * $script:Attribute.Count
    $accuracy = if ($total -gt 0) { [math]::Round($correct / $total, 4) } else { 0.0 }
    return @{ Images = @($Item).Length; Answered = $answered.Length; Checks = $total; Correct = $correct; Accuracy = $accuracy; Complete = (@($Item).Length -gt 0 -and $answered.Length -eq @($Item).Length) }
}

function Get-ImageGridVerdict {
    # Returns the T-QLT-02 record: per profile the defect rate of its default settings and the decision for each changed parameter, the default rule, and the sizes. Not exported.
    param([object[]]$Item = @(), $Review, [string]$DefaultProfile)

    $grid = @(@($Item) | Where-Object { [string](Get-ImageField $_ 'kind') -eq 'grid' })
    $profiles = [ordered]@{}
    foreach ($profileId in @($grid | ForEach-Object { [string](Get-ImageField $_ 'profile') } | Select-Object -Unique | Sort-Object)) {
        $own = @($grid | Where-Object { [string](Get-ImageField $_ 'profile') -ceq $profileId })
        $default = Get-ImageDefectRate -Item @($own | Where-Object { [string](Get-ImageField $_ 'variant') -eq 'default' }) -Review $Review
        $record = [ordered]@{
            profile = $profileId; images = $default.Images; answered = $default.Answered; defects = $default.Defects
            defect_rate = $default.Rate; complete = $default.Complete; parameters = [ordered]@{}
        }
        foreach ($parameter in @($script:GridParameter.Values | Select-Object -Unique)) {
            $values = [ordered]@{}
            $complete = $default.Complete
            $best = $null
            foreach ($variant in @($script:GridParameter.Keys | Where-Object { $script:GridParameter[$_] -eq $parameter })) {
                $rate = Get-ImageDefectRate -Item @($own | Where-Object { [string](Get-ImageField $_ 'variant') -eq $variant }) -Review $Review
                $values[$variant] = [ordered]@{ images = $rate.Images; answered = $rate.Answered; defect_rate = $rate.Rate; complete = $rate.Complete }
                if (-not $rate.Complete) { $complete = $false }
                elseif ($default.Complete -and ($default.Rate - $rate.Rate) -ge ($script:Limit.ParameterMargin - 0.000001) -and ($null -eq $best -or $rate.Rate -lt $best.Rate)) {
                    $best = @{ Variant = $variant; Rate = $rate.Rate }
                }
            }
            $decision = 'incomplete'
            if ($complete) { $decision = 'keep' }
            if ($complete -and $null -ne $best) { $decision = $best.Variant }
            $record.parameters[$parameter] = [ordered]@{ default_rate = $default.Rate; variants = $values; decision = $decision }
        }
        $profiles[$profileId] = $record
    }

    $rule = [ordered]@{
        rule = 'the default image profile changes only if another profile has fewer visible defects than it in the owner''s blind review (docs/models.md), and a change needs an ADR'
        default_profile = $DefaultProfile; default_rate = $null; decision = 'undecided'; candidate = ''; reasons = @()
    }
    $reasons = New-Object System.Collections.Generic.List[string]
    $defaultRecord = $profiles[$DefaultProfile]
    if ($profiles.Count -lt 2) { $reasons.Add('Fewer than two image profiles were reviewed, so no profile can be compared with the default.') }
    elseif ($null -eq $defaultRecord) { $reasons.Add("The default image profile $DefaultProfile has no grid image on the sheet.") }
    elseif (@($profiles.Values | Where-Object { -not $_.complete }).Length -gt 0) {
        $rule.decision = 'incomplete'
        $reasons.Add('A profile has default-setting images without an answer.')
    }
    else {
        $rule.default_rate = $defaultRecord.defect_rate
        $fewer = @($profiles.Values | Where-Object { $_.profile -cne $DefaultProfile -and $_.defect_rate -lt $defaultRecord.defect_rate } | Sort-Object { $_.defect_rate }, { $_.profile })
        if ($fewer.Length -gt 0) {
            $rule.decision = 'change'
            $rule.candidate = [string]$fewer[0].profile
            $reasons.Add("$($fewer[0].profile) has a defect rate of $($fewer[0].defect_rate), below $($defaultRecord.defect_rate) of $DefaultProfile.")
        }
        else {
            $rule.decision = 'keep'
            $reasons.Add('No other profile has fewer visible defects than the default.')
        }
    }
    $rule.reasons = $reasons.ToArray()

    $sizes = [ordered]@{}
    $defaultOwn = @($grid | Where-Object { [string](Get-ImageField $_ 'profile') -ceq $DefaultProfile })
    foreach ($pair in @(@{ Size = 'tall'; Against = 'default'; Name = 'portrait' }, @{ Size = 'wide'; Against = 'landscape'; Name = 'landscape' })) {
        $own = Get-ImageDefectRate -Item @($defaultOwn | Where-Object { [string](Get-ImageField $_ 'variant') -eq $pair.Size }) -Review $Review
        $control = Get-ImageDefectRate -Item @($defaultOwn | Where-Object { [string](Get-ImageField $_ 'variant') -eq $pair.Against }) -Review $Review
        $decision = 'undecided'
        if ($own.Images -gt 0 -and $control.Images -gt 0) {
            if ($own.Complete -and $control.Complete) { $decision = if ($own.Rate -le $control.Rate) { 'keep' } else { 'remove' } }
            else { $decision = 'incomplete' }
        }
        $sizes[$pair.Size] = [ordered]@{ against = $pair.Name; defect_rate = $own.Rate; against_rate = $control.Rate; decision = $decision }
    }
    return @{ Profiles = $profiles; Rule = $rule; Sizes = $sizes }
}

function Get-ImageVerdict {
    <#
    Returns the verdict of T-QLT-02 and T-QLT-07 from Key (key.json parsed, or Get-ImageReviewSheet's) and Review (what
    Read-ImageReview returned). Nothing is guessed: a decision whose answers are not all there is 'incomplete'.

    grid (T-QLT-02): per profile the share of its default-setting images with a visible defect, and for CLIP skip, CFG
    and the second-pass denoise whether the default setting stays ('keep') or an alternative has a share lower by at
    least 10 points ('decision' is then that variant). default_rule: the default image profile changes ('change', with the
    candidate) only when another profile's share is strictly lower; a change needs an ADR, so it is a proposal.
    sizes: tall is kept when its share of defective images is at most that of portrait (the default setting) and wide
    when it is at most that of landscape, else 'remove'.

    identity (T-QLT-07): the share of correct attribute checks of the baseline images (target 90 %). identity_weight:
    'adopt' when the weighted images' share is at least 5 points higher and no pair shows a style change. pag: for each
    scale, the share of blind pairs its image wins against scale 0 (at least 60 %) and the nearest-rank 95th percentile of
    its render seconds (at most 45 s, an Alta render with the checkpoint loaded; it also holds the first render after
    the start); adopt_scale is the adoptable scale with the most wins, the smaller on a tie, or 0. text_profile_gap
    is 'not_run': the scenes are fixed, not written by each text profile.

    status: 'incomplete' when a decision lacks answers, 'missed' when the baseline accuracy is below 90 %, 'met'
    otherwise; passed holds for 'met'. evidence is 'protocol' only when the key says so.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.Specialized.OrderedDictionary])]
    param(
        [Parameter(Mandatory = $true)] $Key,
        [Parameter(Mandatory = $true)] $Review
    )

    $items = @(Get-ImageField $Key 'items')
    $defaultProfile = [string](Get-ImageField $Key 'default_profile')
    $grid = Get-ImageGridVerdict -Item $items -Review $Review -DefaultProfile $defaultProfile
    $reasons = New-Object System.Collections.Generic.List[string]
    $gridIncomplete = @($grid.Profiles.Values | Where-Object { -not $_.complete }).Length -gt 0
    foreach ($record in $grid.Profiles.Values) {
        foreach ($parameter in $record.parameters.Values) { if ($parameter.decision -eq 'incomplete') { $gridIncomplete = $true } }
    }
    if ($grid.Rule.decision -eq 'incomplete') { $gridIncomplete = $true }
    foreach ($size in $grid.Sizes.Values) { if ($size.decision -eq 'incomplete') { $gridIncomplete = $true } }
    if ($gridIncomplete) { $reasons.Add('T-QLT-02: a grid image has no answer, or a profile has no image at its default settings.') }
    $incomplete = $gridIncomplete

    $identity = @($items | Where-Object { [string](Get-ImageField $_ 'kind') -eq 'identity' })
    $base = Get-ImageAttributeScore -Item @($identity | Where-Object { [string](Get-ImageField $_ 'variant') -eq 'baseline' }) -Review $Review
    $weighted = Get-ImageAttributeScore -Item @($identity | Where-Object { [string](Get-ImageField $_ 'variant') -like 'identity-weight-*' }) -Review $Review
    $byAttribute = [ordered]@{}
    foreach ($name in $script:Attribute.Keys) {
        $own = @($identity | Where-Object { [string](Get-ImageField $_ 'variant') -eq 'baseline' -and $Review.Attribute.ContainsKey([string](Get-ImageField $_ 'id')) })
        $right = @($own | Where-Object { $Review.Attribute[[string](Get-ImageField $_ 'id')][$name] }).Length
        $byAttribute[$name] = $(if ($own.Length -gt 0) { [math]::Round($right / $own.Length, 4) } else { 0.0 })
    }
    $identityRecord = [ordered]@{
        target = 'at least 90 % of the attribute checks correct (hair color, hair length, eye color, age band, outfit)'
        images = $base.Images; answered = $base.Answered; checks = $base.Checks; correct = $base.Correct; accuracy = $base.Accuracy
        by_attribute = $byAttribute; complete = $base.Complete
        passed = ($base.Complete -and $base.Accuracy -ge ($script:Limit.IdentityAccuracy - 0.000001))
    }
    if ($identity.Length -eq 0) {
        $incomplete = $true
        $reasons.Add('T-QLT-07: the sheet has no identity image, which only the default image profile gets; name it in the run.')
    }
    elseif (-not $base.Complete) {
        $incomplete = $true
        $reasons.Add('T-QLT-07: an identity image without all five attribute answers.')
    }

    $styleItems = @($items | Where-Object { [string](Get-ImageField $_ 'kind') -eq 'weight' })
    $styleAnswered = @($styleItems | Where-Object { $Review.Style.ContainsKey([string](Get-ImageField $_ 'id')) })
    $styleChanges = @($styleAnswered | Where-Object { $Review.Style[[string](Get-ImageField $_ 'id')] }).Length
    $weightRecord = [ordered]@{
        rule = 'adopt weight 1.1 on hair_color and eye_color when the attribute accuracy rises by at least 5 points and no pair shows a style change'
        baseline_accuracy = $base.Accuracy; weighted_accuracy = $weighted.Accuracy; gain = [math]::Round($weighted.Accuracy - $base.Accuracy, 4)
        pairs = $styleItems.Length; answered = $styleAnswered.Length; style_changes = $styleChanges; decision = 'incomplete'
    }
    if ($weighted.Images -eq 0 -or $styleItems.Length -eq 0) { $weightRecord.decision = 'undecided' }
    elseif ($base.Complete -and $weighted.Complete -and $styleAnswered.Length -eq $styleItems.Length) {
        $weightRecord.decision = if ($weightRecord.gain -ge ($script:Limit.IdentityWeightGain - 0.000001) -and $styleChanges -eq 0) { 'adopt' } else { 'reject' }
    }
    else {
        $incomplete = $true
        $reasons.Add('T-QLT-07: the identity weight has an image or a pair without an answer.')
    }

    $pagRecord = [ordered]@{
        rule = 'adopt a PAG scale when its images win at least 60 % of the blind pairs against scale 0 and the 95th percentile of its Alta render seconds is at most 45 s'
        scales = [ordered]@{}; adopt_scale = 'undecided'
    }
    $pagItems = @($items | Where-Object { [string](Get-ImageField $_ 'kind') -eq 'pag' })
    $adoptable = New-Object System.Collections.Generic.List[object]
    $pagIncomplete = $false
    foreach ($variant in @($pagItems | ForEach-Object { [string](Get-ImageField $_ 'variant') } | Select-Object -Unique | Sort-Object)) {
        $own = @($pagItems | Where-Object { [string](Get-ImageField $_ 'variant') -eq $variant })
        $answered = @($own | Where-Object { $Review.Better.ContainsKey([string](Get-ImageField $_ 'id')) })
        $wins = @($answered | Where-Object { $Review.Better[[string](Get-ImageField $_ 'id')] -ceq [string](Get-ImageField $_ 'lever_side') }).Length
        $same = @($answered | Where-Object { $Review.Better[[string](Get-ImageField $_ 'id')] -ceq 'same' }).Length
        $rate = if ($answered.Length -gt 0) { [math]::Round($wins / $answered.Length, 4) } else { 0.0 }
        $seconds = [double[]]@($own | Where-Object { $null -ne (Get-ImageField $_ 'seconds') -and [double](Get-ImageField $_ 'seconds') -ge 0 } | ForEach-Object { [double](Get-ImageField $_ 'seconds') })
        $p95 = Get-ImageNearestRank -Value $seconds -Percentile 95
        $scale = [double]::Parse(($variant -replace '^pag-', ''), [System.Globalization.CultureInfo]::InvariantCulture)
        $entry = [ordered]@{ scale = $scale; pairs = $own.Length; answered = $answered.Length; wins = $wins; same = $same; losses = ($answered.Length - $wins - $same); win_rate = $rate; render_samples = $seconds.Length; p95_seconds = $p95; decision = 'incomplete' }
        if ($answered.Length -eq $own.Length -and $seconds.Length -gt 0) {
            $good = ($rate -ge ($script:Limit.PagWinRate - 0.000001) -and $p95 -le $script:Limit.PagP95Seconds)
            $entry.decision = if ($good) { 'adopt' } else { 'reject' }
            if ($good) { $adoptable.Add($entry) }
        }
        else { $pagIncomplete = $true }
        $pagRecord.scales[$variant] = $entry
    }
    if ($pagItems.Length -gt 0) {
        if ($pagIncomplete) {
            $pagRecord.adopt_scale = 'incomplete'
            $incomplete = $true
            $reasons.Add('T-QLT-07: a PAG pair has no answer, or a scale has no render time.')
        }
        elseif ($adoptable.Count -gt 0) { $pagRecord.adopt_scale = [double]@($adoptable.ToArray() | Sort-Object { -$_.win_rate }, { $_.scale })[0].scale }
        else { $pagRecord.adopt_scale = 0.0 }
    }

    $evidence = [string](Get-ImageField $Key 'evidence')
    $status = 'met'
    if ($incomplete) { $status = 'incomplete' }
    elseif (-not $identityRecord.passed) {
        $status = 'missed'
        $reasons.Add("T-QLT-07: the attribute accuracy is $($identityRecord.accuracy), below 0.9.")
    }
    return [ordered]@{
        test = 'T-QLT-02, T-QLT-07'; evidence = $(if ($evidence -eq 'protocol') { 'protocol' } else { 'smoke' })
        sheet_id = [string](Get-ImageField $Key 'sheet_id'); default_profile = $defaultProfile
        grid = $grid.Profiles; default_rule = $grid.Rule; sizes = $grid.Sizes
        identity = $identityRecord; identity_weight = $weightRecord; pag = $pagRecord
        text_profile_gap = [ordered]@{ status = 'not_run'; reason = 'the protocol asks for scenes written by every catalog text profile; this run uses fixed scenes, so the gap between a text profile and the default is not measured' }
        status = $status; reasons = [string[]]$reasons.ToArray(); passed = ($status -eq 'met')
    }
}

function Write-ImageArtifact {
    <#
    Writes sheet.md, key.json and renders.json of one image quality run into Folder (created when missing), as UTF-8
    without a byte order mark, copies every image of Blind.Files into Folder\images under its opaque name, and returns
    @{ Sheet; Key; Renders; Images }. Blind is what Get-ImageReviewSheet returned; Cell the render records, written
    without their paths. The folder holds the key beside the sheet, so the review is blind only as long as the owner does
    not open key.json.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)] [string]$Folder,
        [Parameter(Mandatory = $true)] [hashtable]$Blind,
        [object[]]$Cell = @()
    )

    $images = Join-Path $Folder 'images'
    foreach ($path in @($Folder, $images)) {
        if (-not (Test-Path -LiteralPath $path)) { $null = New-Item -ItemType Directory -Path $path }
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $paths = @{ Sheet = (Join-Path $Folder 'sheet.md'); Key = (Join-Path $Folder 'key.json'); Renders = (Join-Path $Folder 'renders.json'); Images = $images }
    foreach ($file in @($Blind.Files)) { Copy-Item -LiteralPath $file.Source -Destination (Join-Path $images $file.Name) -Force }
    $renders = @(@($Cell) | Where-Object { $null -ne $_ } | ForEach-Object {
            $record = ConvertTo-ImageCell -Value $_
            $record.Remove('Path')
            $record
        })
    [System.IO.File]::WriteAllText($paths.Sheet, [string]$Blind.Sheet, $utf8)
    [System.IO.File]::WriteAllText($paths.Key, (ConvertTo-Json -InputObject $Blind.Key -Depth 6), $utf8)
    [System.IO.File]::WriteAllText($paths.Renders, (ConvertTo-Json -InputObject $renders -Depth 6), $utf8)
    return $paths
}

function Test-ImageReviewSheet {
    <#
    Returns $true when the file SheetPath is a review sheet of Get-ImageReviewSheet, by its first heading, and $false for
    any other file, also one that does not exist. The command "test quality-rating" uses it to choose the import.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)] [string]$SheetPath)

    if (-not (Test-Path -LiteralPath $SheetPath -PathType Leaf)) { return $false }
    $first = @(Get-Content -LiteralPath $SheetPath -TotalCount 3 -Encoding UTF8)
    return (@($first | Where-Object { $_ -like '# Lyrveil image quality: blind review sheet*' }).Length -gt 0)
}

function Invoke-ImageReview {
    <#
    Imports the owner's filled sheet SheetPath and returns @{ Status; Verdict; Problems; VerdictPath }. key.json is read
    from Folder (the sheet's own folder when empty). Status is 0 only when the sheet read without a problem and the
    verdict is 'met', and 1 otherwise: unreadable files, a sheet that is not the key's, a blank or invalid answer, a
    baseline accuracy below target. The verdict is written to verdict.json beside the key, also when it is not met, and
    printed; no answer is invented for a blank one.
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
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($path in @($SheetPath, $keyPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $problems.Add("$path does not exist.") }
    }
    if ($problems.Count -gt 0) { return @{ Status = 1; Verdict = $null; Problems = $problems.ToArray(); VerdictPath = '' } }
    try { $key = [System.IO.File]::ReadAllText($keyPath, $utf8) | ConvertFrom-Json }
    catch { return @{ Status = 1; Verdict = $null; Problems = @("key.json could not be read: $($_.Exception.Message)"); VerdictPath = '' } }
    $review = Read-ImageReview -SheetText ([System.IO.File]::ReadAllText($SheetPath, $utf8)) -Key $key
    $verdict = Get-ImageVerdict -Key $key -Review $review
    $verdictPath = Join-Path $Folder 'verdict.json'
    [System.IO.File]::WriteAllText($verdictPath, (ConvertTo-Json -InputObject $verdict -Depth 8), $utf8)
    foreach ($problem in $review.Problems) { $problems.Add($problem) }
    foreach ($id in $review.Missing) { $problems.Add("$id was left blank.") }

    foreach ($record in $verdict.grid.Values) {
        $parameters = @($record.parameters.Keys | ForEach-Object { '{0} {1}' -f $_, $record.parameters[$_].decision })
        Write-Host ("  grid {0}: defect rate {1} ({2} of {3} images answered); {4}" -f $record.profile, $record.defect_rate, $record.answered, $record.images, ($parameters -join ', '))
    }
    Write-Host ("  default image profile {0}: {1}{2}" -f $verdict.default_profile, $verdict.default_rule.decision, $(if ($verdict.default_rule.candidate) { " to $($verdict.default_rule.candidate), which needs an ADR" } else { '' }))
    foreach ($reason in $verdict.default_rule.reasons) { Write-Host "      $reason" }
    foreach ($size in $verdict.sizes.Keys) { Write-Host ("  size {0}: {1} (defect rate {2} against {3} of {4})" -f $size, $verdict.sizes[$size].decision, $verdict.sizes[$size].defect_rate, $verdict.sizes[$size].against_rate, $verdict.sizes[$size].against) }
    Write-Host ("  identity: accuracy {0} over {1} checks, target met {2}" -f $verdict.identity.accuracy, $verdict.identity.checks, $verdict.identity.passed)
    Write-Host ("  identity weight 1.1: {0} (gain {1}, {2} style changes)" -f $verdict.identity_weight.decision, $verdict.identity_weight.gain, $verdict.identity_weight.style_changes)
    foreach ($scale in $verdict.pag.scales.Keys) {
        $entry = $verdict.pag.scales[$scale]
        Write-Host ("  {0}: {1} (wins {2} of {3}, p95 {4} s)" -f $scale, $entry.decision, $entry.wins, $entry.answered, $entry.p95_seconds)
    }
    Write-Host "  PAG scale to adopt: $($verdict.pag.adopt_scale)"
    Write-Host "  text profile gap: $($verdict.text_profile_gap.status)"
    Write-Host "  status: $($verdict.status)"
    foreach ($reason in $verdict.reasons) { Write-Host "      $reason" }
    if ($verdict.evidence -ne 'protocol') { Write-Host '  This sheet is a smoke run: its verdict is no evidence for the catalog.' }
    $status = if ($problems.Count -eq 0 -and $verdict.passed) { 0 } else { 1 }
    return @{ Status = $status; Verdict = $verdict; Problems = $problems.ToArray(); VerdictPath = $verdictPath }
}

Export-ModuleMember -Function Get-ImageNearestRank, Get-ImageReviewSheet, Read-ImageReview, Get-ImageVerdict, Write-ImageArtifact,
    Test-ImageReviewSheet, Invoke-ImageReview
