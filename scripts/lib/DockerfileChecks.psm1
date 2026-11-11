# Checks that every base image of the Git-tracked Dockerfiles is pinned by tag and sha256 digest.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

$script:PinnedReference = '^[^\s:@]+(?::\d+)?(?:/[^\s:@]+)*:[^\s:@]+@sha256:[0-9a-f]{64}$'

function Get-DockerfileBaseImage {
    <#
    Returns one record per FROM instruction of a Dockerfile text: Line (1-based), Reference (the image with
    ${NAME} and $NAME replaced by the default of a global ARG declared before the first FROM) and Pinned. A FROM
    that names an earlier stage or "scratch" is not a base image and is skipped. Pinned is true only when the
    reference carries a tag and a sha256 digest of 64 hex digits.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()] [string]$Text)

    $defaults = @{}
    $stages = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $found = New-Object System.Collections.Generic.List[object]
    $lines = $Text -split "`r?`n"
    $seenFrom = $false
    for ($i = 0; $i -lt $lines.Length; $i++) {
        $line = $lines[$i].Trim()
        if ($line -match '^ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$' -and -not $seenFrom) {
            $defaults[$Matches[1]] = $Matches[2]
            continue
        }
        if ($line -notmatch '^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$') { continue }
        $seenFrom = $true
        $reference = $Matches[1]
        $alias = $Matches[2]
        foreach ($name in @($defaults.Keys)) {
            $reference = $reference.Replace('${' + $name + '}', $defaults[$name]).Replace('$' + $name, $defaults[$name])
        }
        $isStage = $stages.Contains($reference)
        if ($alias) { [void]$stages.Add($alias) }
        if ($reference -eq 'scratch' -or $isStage) { continue }
        $found.Add([pscustomobject]@{
                Line      = $i + 1
                Reference = $reference
                Pinned    = [bool]($reference -cmatch $script:PinnedReference)
            })
    }
    return , $found
}

function Invoke-DockerfileCheck {
    <#
    Checks every Dockerfile that Git tracks under Root (see Get-RepositoryFile) and returns one result with Name,
    Unit, Checked (the base images seen) and Failures (each unpinned FROM, or a missing Dockerfile set). Throws
    when Git fails.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)] [string]$Root)

    $files = @(Get-RepositoryFile -Root $Root -Pattern @('Dockerfile', '*/Dockerfile', '*/*/Dockerfile', '*/*/*/Dockerfile'))
    $result = [pscustomobject]@{
        Name     = 'Dockerfile base images pinned by tag and sha256 digest'
        Unit     = 'unpinned'
        Checked  = 0
        Failures = New-Object System.Collections.Generic.List[string]
    }
    if ($files.Length -eq 0) {
        $result.Failures.Add('No Dockerfile is tracked: nothing was checked.')
    }
    foreach ($file in $files) {
        foreach ($image in (Get-DockerfileBaseImage -Text (Read-Utf8Text -Path (Join-Path $Root $file)))) {
            $result.Checked++
            if (-not $image.Pinned) {
                $result.Failures.Add("${file}:$($image.Line): base image '$($image.Reference)' is not pinned as name:tag@sha256:<digest>.")
            }
        }
    }
    return $result
}

Export-ModuleMember -Function Invoke-DockerfileCheck, Get-DockerfileBaseImage
