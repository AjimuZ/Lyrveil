# Pinned PowerShell modules that the test command fetches from the PowerShell Gallery into the Git-ignored tools
# folder. Sha256 is the hash of the .nupkg file, recorded at the first download. A changed package is refused.
@{
    PSScriptAnalyzer = @{
        Version = '1.24.0'
        Sha256  = 'e86c97d44bb1bc8a1de35e753b85ea1d938f6f9f881639a181507e079bca4556'
    }
    Pester           = @{
        Version = '5.7.1'
        Sha256  = '4a27904c6814a5fbe4758f8e49861f6a1994aee77b71165a5c43c0371ba6c580'
    }
}
