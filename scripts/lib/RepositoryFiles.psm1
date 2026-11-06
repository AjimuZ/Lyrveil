# Lists the files of a Lyrveil repository as Git sees them, so every check scans exactly what a commit would contain.

Set-StrictMode -Version Latest

function Get-RepositoryFile {
    <#
    Returns repository-relative paths, with forward slashes, of the files that match the Git pathspecs in Pattern:
    tracked files that still exist on disk, plus untracked files that .gitignore does not exclude. Untracked files are
    included so a check run before a commit sees the new files that commit will add; ignored files (tools, data,
    dependencies) are never returned. Paths are unique and sorted ordinally.

    Throws when Git fails, so a broken Git setup never turns into an empty scan that passes.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string[]]$Pattern = @()
    )

    $arguments = @('-C', $Root, '-c', 'core.quotepath=off', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--')
    $arguments += $Pattern
    $raw = & git @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files failed in '$Root' with exit code $LASTEXITCODE."
    }

    $unique = New-Object 'System.Collections.Generic.SortedSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($chunk in @($raw)) {
        foreach ($path in ([string]$chunk).Split([char]0)) {
            if ($path.Length -eq 0) { continue }
            # A tracked file deleted from the working tree is still listed by --cached.
            if (Test-Path -LiteralPath (Join-Path $Root $path) -PathType Leaf) {
                [void]$unique.Add($path)
            }
        }
    }
    return [string[]]@($unique)
}

function Read-Utf8Text {
    <#
    Returns the content of a file decoded as UTF-8, whatever the console code page is. Windows PowerShell 5.1 would
    otherwise read files without a BOM as ANSI. Throws when the file cannot be read.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Path)

    return [System.IO.File]::ReadAllText($Path, (New-Object System.Text.UTF8Encoding($false)))
}

Export-ModuleMember -Function Get-RepositoryFile, Read-Utf8Text
