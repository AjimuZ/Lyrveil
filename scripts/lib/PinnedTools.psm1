# Fetches the PowerShell modules pinned in tools.psd1 into a Git-ignored folder, verified by hash, without any machine-wide install.

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'RepositoryFiles.psm1') -Force

$script:GalleryPackageUrl = 'https://www.powershellgallery.com/api/v2/package/{0}/{1}'

function Get-ToolsFolder {
    <#
    Returns the folder that holds fetched tools: LYRVEIL_TOOLS_DIR when set, otherwise .tools under Root, which
    .gitignore excludes. The folder may not exist yet.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)] [string]$Root)

    if ($env:LYRVEIL_TOOLS_DIR) { return $env:LYRVEIL_TOOLS_DIR }
    return (Join-Path $Root '.tools')
}

function Get-ToolPin {
    # Returns the Version and Sha256 pinned for Name in tools.psd1. Throws when Name has no pin.
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)] [string]$Name)

    $pins = Import-PowerShellDataFile -Path (Join-Path $PSScriptRoot 'tools.psd1')
    if (-not $pins.ContainsKey($Name)) { throw "No pinned version of '$Name' in scripts/lib/tools.psd1." }
    return $pins[$Name]
}

function Install-PinnedModule {
    <#
    Returns the path of the manifest (.psd1) of module Name at its pinned version, fetching it first when it is not
    in the tools folder yet. A fetch downloads the .nupkg from the PowerShell Gallery, checks its SHA-256 against the
    pin before extracting anything, and only then moves the extracted folder into place, so the folder never holds a
    partial or unverified copy. Nothing is written outside the tools folder.

    Throws when the download fails or the hash differs; the message gives both hashes and nothing is kept.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)] [string]$Name,
        [Parameter(Mandatory = $true)] [string]$Root
    )

    $pin = Get-ToolPin -Name $Name
    $tools = Get-ToolsFolder -Root $Root
    $target = Join-Path $tools "$Name\$($pin.Version)"
    $manifest = Join-Path $target "$Name.psd1"
    $stamp = Join-Path $target '.lyrveil-sha256'

    if ((Test-Path -LiteralPath $manifest) -and (Test-Path -LiteralPath $stamp)) {
        $recorded = (Read-Utf8Text -Path $stamp).Trim()
        if ($recorded -eq $pin.Sha256) { return $manifest }
    }

    $null = New-Item -ItemType Directory -Force -Path $tools
    $package = Join-Path $tools "$Name.$($pin.Version).nupkg.part"
    $staging = Join-Path $tools "$Name.$($pin.Version).staging"
    foreach ($leftover in @($package, $staging, $target)) {
        if (Test-Path -LiteralPath $leftover) { Remove-Item -LiteralPath $leftover -Recurse -Force }
    }

    $url = $script:GalleryPackageUrl -f $Name, $pin.Version
    Write-Host "Fetching $Name $($pin.Version) from the PowerShell Gallery into $tools"
    # Windows PowerShell 5.1 may default to TLS 1.0, which the Gallery refuses; the progress bar slows the download
    # by orders of magnitude. Both settings are restored afterwards.
    $previousProtocol = [System.Net.ServicePointManager]::SecurityProtocol
    $previousProgress = $ProgressPreference
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = $previousProtocol -bor [System.Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $package -UseBasicParsing
    }
    finally {
        [System.Net.ServicePointManager]::SecurityProtocol = $previousProtocol
        $ProgressPreference = $previousProgress
    }

    $actual = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $pin.Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $package -Force
        throw "SHA-256 of $Name $($pin.Version) from $url is $actual, but scripts/lib/tools.psd1 pins $($pin.Sha256). Nothing was installed."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $staging)
    [System.IO.File]::WriteAllText((Join-Path $staging '.lyrveil-sha256'), $actual, (New-Object System.Text.UTF8Encoding($false)))
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target)
    Move-Item -LiteralPath $staging -Destination $target
    Remove-Item -LiteralPath $package -Force

    if (-not (Test-Path -LiteralPath $manifest)) {
        throw "The package of $Name $($pin.Version) has no $Name.psd1 at its root."
    }
    return $manifest
}

Export-ModuleMember -Function Install-PinnedModule, Get-ToolsFolder
