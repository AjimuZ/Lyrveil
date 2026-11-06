# Checks the repository's Markdown: relative links and anchors, unfinished-work markers, requirement and test traceability, ADR structure and JSON samples.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

# The markers are assembled from fragments so that this module never contains them literally and never matches its
# own pattern if a later gate scans source files too.
$script:MarkerPattern = '(?<![A-Za-z0-9_])(' + (@(('TO' + 'DO'), ('FIX' + 'ME'), ('T' + 'BD'), ('X' + 'XX')) -join '|') + ')(?![A-Za-z0-9_])'
$script:FillerPattern = '(?i)' + 'lorem' + ' ' + 'ipsum'

# Identifier families of the documentation: requirements, acceptance criteria and catalog tests.
$script:IdPattern = '(?<![A-Za-z0-9-])((?:R-[A-Z]+|AC|T-[A-Z]{3})-)(\d+)(?![A-Za-z0-9])'
$script:RangePattern = '(?<![A-Za-z0-9-])((?:R-[A-Z]+|AC|T-[A-Z]{3})-)(\d+)\s+to\s+\1(\d+)(?![A-Za-z0-9])'

$script:AdrSections = @('## Context', '## Decision', '## Alternatives', '## Consequences')

function Initialize-CheckResult {
    # Returns an empty, mutable result for one named check. Not exported.
    param([string]$Name, [string]$Unit)
    return [pscustomobject]@{
        Name     = $Name
        Unit     = $Unit
        Checked  = 0
        Failures = New-Object System.Collections.Generic.List[string]
    }
}

function Expand-IdList {
    <#
    Returns every identifier written in Text without duplicates: the members of ranges such as "T-DCK-01 to
    T-DCK-11" first, keeping the zero padding of the first bound, then the single identifiers, each group in order
    of appearance. A range whose end is
    below its start yields only its two bounds, so a typo is never silently dropped.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([AllowEmptyString()] [string]$Text)

    $found = New-Object System.Collections.Generic.List[string]
    $rest = $Text
    foreach ($range in [regex]::Matches($Text, $script:RangePattern)) {
        $prefix = $range.Groups[1].Value
        $first = $range.Groups[2].Value
        $start = [int]$first
        $end = [int]$range.Groups[3].Value
        if ($end -lt $start) {
            $found.Add($prefix + $first)
            $found.Add($prefix + $range.Groups[3].Value)
        }
        else {
            for ($n = $start; $n -le $end; $n++) {
                $found.Add($prefix + $n.ToString().PadLeft($first.Length, '0'))
            }
        }
        $rest = $rest.Replace($range.Value, ' ')
    }
    foreach ($single in [regex]::Matches($rest, $script:IdPattern)) {
        $found.Add($single.Value)
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $ordered = New-Object System.Collections.Generic.List[string]
    foreach ($id in $found) {
        if ($seen.Add($id)) { $ordered.Add($id) }
    }
    return [string[]]$ordered.ToArray()
}

function ConvertTo-HeadingAnchor {
    <#
    Returns the anchor GitHub generates for a heading text: link targets dropped, lower case, every character that is
    not a letter, digit, mark, space, hyphen or underscore removed, then each space turned into a hyphen. Duplicate
    numbering is the caller's job.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()] [string]$Heading)

    $text = [regex]::Replace($Heading, '!?\[([^\]]*)\]\([^)]*\)', '$1')
    $text = $text.Trim().ToLowerInvariant()
    $text = [regex]::Replace($text, '[^\p{L}\p{N}\p{M}\s_-]', '')
    return [regex]::Replace($text, '\s', '-')
}

function Get-MarkdownStructure {
    <#
    Returns the anchors, the links and the JSON samples of a Markdown text. Fenced code blocks contribute neither
    headings nor links, and inline code spans contribute no links, as on GitHub. Each link and sample carries its
    1-based line number. Repeated headings get the suffixes -1, -2 and so on, as on GitHub.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()] [string]$Text)

    $anchors = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $anchorCounts = @{}
    $links = New-Object System.Collections.Generic.List[object]
    $samples = New-Object System.Collections.Generic.List[object]

    $lines = $Text -split "`r?`n"
    $fence = $null
    $fenceInfo = ''
    $fenceStart = 0
    $fenceBody = New-Object System.Text.StringBuilder

    for ($i = 0; $i -lt $lines.Length; $i++) {
        $line = $lines[$i]
        $fenceMatch = [regex]::Match($line, '^\s{0,3}(`{3,}|~{3,})\s*([^`\s]*)')
        if ($null -eq $fence) {
            if ($fenceMatch.Success) {
                $fence = $fenceMatch.Groups[1].Value
                $fenceInfo = $fenceMatch.Groups[2].Value.ToLowerInvariant()
                $fenceStart = $i + 1
                [void]$fenceBody.Clear()
                continue
            }
        }
        else {
            $closing = $fenceMatch.Success -and
                $fenceMatch.Groups[1].Value[0] -eq $fence[0] -and
                $fenceMatch.Groups[1].Value.Length -ge $fence.Length -and
                $fenceMatch.Groups[2].Value.Length -eq 0
            if ($closing) {
                if ($fenceInfo -eq 'json') {
                    $samples.Add([pscustomobject]@{ Line = $fenceStart; Text = $fenceBody.ToString() })
                }
                $fence = $null
            }
            else {
                [void]$fenceBody.AppendLine($line)
            }
            continue
        }

        $heading = [regex]::Match($line, '^\s{0,3}#{1,6}(?:\s+(.*?))?(?:\s+#+)?\s*$')
        if ($heading.Success) {
            $anchor = ConvertTo-HeadingAnchor -Heading $heading.Groups[1].Value
            if ($anchorCounts.ContainsKey($anchor)) {
                $anchorCounts[$anchor] += 1
                [void]$anchors.Add($anchor + '-' + $anchorCounts[$anchor])
            }
            else {
                $anchorCounts[$anchor] = 0
                [void]$anchors.Add($anchor)
            }
        }

        $prose = [regex]::Replace($line, '`+[^`]*`+', '')
        foreach ($link in [regex]::Matches($prose, '!?\[[^\]]*\]\(\s*<?([^)\s>]*)>?(?:\s+"[^"]*")?\s*\)')) {
            $links.Add([pscustomobject]@{ Line = $i + 1; Target = $link.Groups[1].Value })
        }
    }

    return [pscustomobject]@{
        Anchors = $anchors
        Links   = $links
        Samples = $samples
    }
}

function Get-TableRow {
    <#
    Returns the cells of every Markdown table row in Text, trimmed, with their 1-based line number. Separator rows
    are skipped. Cells are split on unescaped pipes.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()] [string]$Text)

    $rows = New-Object System.Collections.Generic.List[object]
    $lines = $Text -split "`r?`n"
    for ($i = 0; $i -lt $lines.Length; $i++) {
        $line = $lines[$i].Trim()
        if (-not ($line.StartsWith('|') -and $line.EndsWith('|') -and $line.Length -ge 2)) { continue }
        if ($line -match '^\|[\s:|-]+\|$') { continue }
        $inner = $line.Substring(1, $line.Length - 2)
        $cells = @([regex]::Split($inner, '(?<!\\)\|') | ForEach-Object { $_.Trim() })
        $rows.Add([pscustomobject]@{ Line = $i + 1; Cells = $cells })
    }
    return , $rows
}

function Test-RelativeLink {
    # Checks every relative link and heading anchor of the Markdown files against the repository's file set. Not exported.
    param([string]$Root, [string[]]$MarkdownFiles, [hashtable]$Documents, [string[]]$AllFiles)

    $result = Initialize-CheckResult -Name 'Relative links and heading anchors' -Unit 'broken'
    $files = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $folders = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    [void]$folders.Add('')
    foreach ($file in $AllFiles) {
        [void]$files.Add($file)
        $parent = $file
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
            [void]$folders.Add($parent)
        }
    }

    foreach ($source in $MarkdownFiles) {
        $sourceFolder = if ($source.Contains('/')) { $source.Substring(0, $source.LastIndexOf('/')) } else { '' }
        foreach ($link in $Documents[$source].Links) {
            $target = $link.Target
            # Absolute URLs (http:, https:, mailto:) are outside the scope of a repository check.
            if ($target.Length -eq 0 -or $target -match '^[A-Za-z][A-Za-z0-9+.-]*:') { continue }
            $result.Checked++
            $hash = $target.IndexOf('#')
            $pathPart = if ($hash -ge 0) { $target.Substring(0, $hash) } else { $target }
            $anchor = if ($hash -ge 0) { [System.Uri]::UnescapeDataString($target.Substring($hash + 1)) } else { '' }
            $where = "${source}:$($link.Line)"

            if ($pathPart.Length -eq 0) {
                $resolved = $source
            }
            else {
                $relative = [System.Uri]::UnescapeDataString($pathPart)
                $segments = New-Object System.Collections.Generic.List[string]
                if (-not $relative.StartsWith('/') -and $sourceFolder.Length -gt 0) {
                    $segments.AddRange([string[]]$sourceFolder.Split('/'))
                }
                $escapes = $false
                foreach ($segment in $relative.Split('/')) {
                    if ($segment -eq '' -or $segment -eq '.') { continue }
                    if ($segment -eq '..') {
                        if ($segments.Count -eq 0) { $escapes = $true; break }
                        $segments.RemoveAt($segments.Count - 1)
                    }
                    else {
                        $segments.Add($segment)
                    }
                }
                $resolved = $segments -join '/'
                if ($escapes) {
                    $result.Failures.Add("${where}: link '$target' leaves the repository.")
                    continue
                }
                if (-not ($files.Contains($resolved) -or $folders.Contains($resolved))) {
                    $result.Failures.Add("${where}: link '$target' points to '$resolved', which is not a repository file or folder.")
                    continue
                }
            }

            if ($anchor.Length -gt 0 -and $resolved.EndsWith('.md')) {
                if (-not $Documents.ContainsKey($resolved)) {
                    $Documents[$resolved] = Get-MarkdownStructure -Text (Read-Utf8Text -Path (Join-Path $Root $resolved))
                }
                if (-not $Documents[$resolved].Anchors.Contains($anchor)) {
                    $result.Failures.Add("${where}: link '$target' names anchor '#$anchor', which no heading of '$resolved' produces.")
                }
            }
        }
    }
    return $result
}

function Test-UnfinishedMarker {
    # Reports every unfinished-work marker or filler text in the Markdown files. Not exported.
    param([string[]]$MarkdownFiles, [hashtable]$Texts)

    $result = Initialize-CheckResult -Name 'Unfinished-work markers' -Unit 'found'
    foreach ($source in $MarkdownFiles) {
        $result.Checked++
        $lines = $Texts[$source] -split "`r?`n"
        for ($i = 0; $i -lt $lines.Length; $i++) {
            foreach ($pattern in @($script:MarkerPattern, $script:FillerPattern)) {
                $hit = [regex]::Match($lines[$i], $pattern)
                if ($hit.Success) {
                    $result.Failures.Add("${source}:$($i + 1): unfinished-work marker '$($hit.Value)'.")
                }
            }
        }
    }
    return $result
}

function Get-DocumentText {
    # Returns the text of a required document, or $null after recording a failure when it is missing. Not exported.
    param([string]$Path, [hashtable]$Texts, $Result)

    if ($Texts.ContainsKey($Path)) { return $Texts[$Path] }
    $Result.Failures.Add("${Path}: required document is missing or not tracked.")
    return $null
}

function Test-Traceability {
    <#
    Checks that every requirement and acceptance criterion of docs/requirements.md heads a row of
    docs/traceability.md whose other cells are all filled, and that the traceability rows name no undefined
    identifier. Returns two results: requirements and acceptance criteria. Not exported.
    #>
    param([hashtable]$Texts)

    $requirements = Initialize-CheckResult -Name 'Requirements traced in docs/traceability.md' -Unit 'untraced'
    $criteria = Initialize-CheckResult -Name 'Acceptance criteria traced in docs/traceability.md' -Unit 'untraced'

    $requirementText = Get-DocumentText -Path 'docs/requirements.md' -Texts $Texts -Result $requirements
    $traceText = Get-DocumentText -Path 'docs/traceability.md' -Texts $Texts -Result $requirements
    if ($null -eq $requirementText -or $null -eq $traceText) { return @($requirements, $criteria) }

    $defined = New-Object System.Collections.Generic.List[string]
    $definedSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($row in (Get-TableRow -Text $requirementText)) {
        $first = $row.Cells[0]
        if ($first -match '^(R-[A-Z]+-\d+|AC-\d+)$') {
            if ($definedSet.Add($first)) {
                $defined.Add($first)
            }
            else {
                $requirements.Failures.Add("docs/requirements.md:$($row.Line): '$first' is defined twice.")
            }
        }
    }

    $traced = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($row in (Get-TableRow -Text $traceText)) {
        $ids = @(Expand-IdList -Text $row.Cells[0] | Where-Object { $_ -notlike 'T-*' })
        if ($ids.Length -eq 0) { continue }
        $empty = @($row.Cells | Select-Object -Skip 1 | Where-Object { $_.Length -eq 0 })
        if ($row.Cells.Length -lt 2 -or $empty.Length -gt 0) {
            $requirements.Failures.Add("docs/traceability.md:$($row.Line): row for '$($row.Cells[0])' has an empty cell.")
            continue
        }
        foreach ($id in $ids) {
            [void]$traced.Add($id)
            if (-not $definedSet.Contains($id)) {
                $requirements.Failures.Add("docs/traceability.md:$($row.Line): '$id' is not defined in docs/requirements.md.")
            }
        }
    }

    foreach ($id in $defined) {
        $result = if ($id.StartsWith('AC-')) { $criteria } else { $requirements }
        $result.Checked++
        if (-not $traced.Contains($id)) {
            $result.Failures.Add("docs/requirements.md: '$id' has no row in docs/traceability.md.")
        }
    }
    return @($requirements, $criteria)
}

function Test-TestCatalog {
    <#
    Checks the test catalog of docs/testing.md against the rest of the documentation: every test identifier used in
    any Markdown file is in the catalog, and every catalog test is named by an "Exit:" line of docs/roadmap.md.
    Returns two results. Not exported.
    #>
    param([string[]]$MarkdownFiles, [hashtable]$Texts)

    $used = Initialize-CheckResult -Name 'Test identifiers used in the docs and present in the catalog' -Unit 'missing'
    $exits = Initialize-CheckResult -Name 'Catalog tests named by a roadmap exit' -Unit 'unnamed'

    $testingText = Get-DocumentText -Path 'docs/testing.md' -Texts $Texts -Result $used
    $roadmapText = Get-DocumentText -Path 'docs/roadmap.md' -Texts $Texts -Result $exits
    if ($null -eq $testingText -or $null -eq $roadmapText) { return @($used, $exits) }

    $catalog = New-Object System.Collections.Generic.List[string]
    $catalogSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($row in (Get-TableRow -Text $testingText)) {
        if ($row.Cells[0] -match '^T-[A-Z]{3}-\d+$') {
            if ($catalogSet.Add($row.Cells[0])) {
                $catalog.Add($row.Cells[0])
            }
            else {
                $used.Failures.Add("docs/testing.md:$($row.Line): '$($row.Cells[0])' is in the catalog twice.")
            }
        }
    }

    $reported = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($source in $MarkdownFiles) {
        $lines = $Texts[$source] -split "`r?`n"
        for ($i = 0; $i -lt $lines.Length; $i++) {
            foreach ($id in @(Expand-IdList -Text $lines[$i] | Where-Object { $_ -like 'T-*' })) {
                if ($catalogSet.Contains($id)) { continue }
                if ($reported.Add("$source|$id")) {
                    $used.Failures.Add("${source}:$($i + 1): test '$id' is not in the catalog of docs/testing.md.")
                }
            }
        }
    }
    $used.Checked = $catalog.Count

    $named = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($line in ($roadmapText -split "`r?`n")) {
        if ($line -notmatch '^\s*Exit:') { continue }
        foreach ($id in @(Expand-IdList -Text $line)) { [void]$named.Add($id) }
    }
    foreach ($id in $catalog) {
        $exits.Checked++
        if (-not $named.Contains($id)) {
            $exits.Failures.Add("docs/testing.md: catalog test '$id' is not named by any 'Exit:' line of docs/roadmap.md.")
        }
    }
    return @($used, $exits)
}

function Test-DecisionRecord {
    # Checks that each ADR has a status line and the four sections, and is linked from docs/decisions/README.md. Not exported.
    param([string[]]$MarkdownFiles, [hashtable]$Texts)

    $result = Initialize-CheckResult -Name 'ADRs with all sections and listed in the index' -Unit 'incomplete'
    $records = @($MarkdownFiles | Where-Object { $_ -match '^docs/decisions/\d{4}-[^/]+\.md$' })
    if ($records.Length -eq 0) { return $result }
    $index = Get-DocumentText -Path 'docs/decisions/README.md' -Texts $Texts -Result $result
    foreach ($record in $records) {
        $result.Checked++
        $lines = $Texts[$record] -split "`r?`n"
        if (-not @($lines | Where-Object { $_ -match '^Status: \S' })) {
            $result.Failures.Add("${record}: no 'Status:' line.")
        }
        foreach ($section in $script:AdrSections) {
            if ($lines -notcontains $section) {
                $result.Failures.Add("${record}: no '$section' section.")
            }
        }
        $name = $record.Substring('docs/decisions/'.Length)
        if ($null -ne $index -and -not $index.Contains("]($name)")) {
            $result.Failures.Add("${record}: not linked from docs/decisions/README.md.")
        }
    }
    return $result
}

function Test-JsonSample {
    # Checks that every fenced block with the json info string parses. Not exported.
    param([string[]]$MarkdownFiles, [hashtable]$Documents)

    $result = Initialize-CheckResult -Name 'JSON samples that parse' -Unit 'invalid'
    foreach ($source in $MarkdownFiles) {
        foreach ($sample in $Documents[$source].Samples) {
            $result.Checked++
            try {
                $null = ConvertFrom-Json -InputObject $sample.Text -ErrorAction Stop
            }
            catch {
                $result.Failures.Add("${source}:$($sample.Line): JSON sample does not parse: $($_.Exception.Message)")
            }
        }
    }
    return $result
}

function Invoke-DocumentationCheck {
    <#
    Runs every documentation check on the Markdown files Git tracks under Root (see Get-RepositoryFile) and returns
    one result per check, each with Name, Unit, Checked and Failures. The documentation passes only when every
    result has no failure. Requires Git and a Git working tree at Root. Throws when Git fails.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $allFiles = @(Get-RepositoryFile -Root $Root)
    $markdown = @($allFiles | Where-Object { $_.EndsWith('.md') })
    $texts = @{}
    $documents = @{}
    foreach ($file in $markdown) {
        $texts[$file] = Read-Utf8Text -Path (Join-Path $Root $file)
        $documents[$file] = Get-MarkdownStructure -Text $texts[$file]
    }

    $files = Initialize-CheckResult -Name 'Markdown files scanned' -Unit 'unreadable'
    $files.Checked = $markdown.Length
    if ($markdown.Length -eq 0) {
        $files.Failures.Add('No Markdown file is tracked: nothing was checked.')
    }

    $results = New-Object System.Collections.Generic.List[object]
    $results.Add($files)
    $results.Add((Test-RelativeLink -Root $Root -MarkdownFiles $markdown -Documents $documents -AllFiles $allFiles))
    $results.Add((Test-UnfinishedMarker -MarkdownFiles $markdown -Texts $texts))
    $results.AddRange([object[]](Test-Traceability -Texts $texts))
    $results.AddRange([object[]](Test-TestCatalog -MarkdownFiles $markdown -Texts $texts))
    $results.Add((Test-DecisionRecord -MarkdownFiles $markdown -Texts $texts))
    $results.Add((Test-JsonSample -MarkdownFiles $markdown -Documents $documents))
    return , $results
}

Export-ModuleMember -Function Invoke-DocumentationCheck, Expand-IdList, ConvertTo-HeadingAnchor
