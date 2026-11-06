# Tests the documentation checks and the "test" command of scripts\lyrveil.cmd on generated fixture repositories.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\DocumentationChecks.psm1') -Force

    function Write-FixtureFile {
        # Writes Text as UTF-8 without BOM to Path under Root, creating folders as needed.
        param([string]$Root, [string]$Path, [string]$Text)
        $full = Join-Path $Root $Path
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $full)
        [System.IO.File]::WriteAllText($full, $Text, (New-Object System.Text.UTF8Encoding($false)))
    }

    function Invoke-FixtureGit {
        # Runs Git in the fixture with line-ending conversion off, so no warning depends on the user's settings.
        param([string]$Root, [string[]]$Arguments)
        & git -C $Root -c core.autocrlf=false -c core.safecrlf=false @Arguments
        if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed in $Root" }
    }

    function Initialize-FixtureRepository {
        <#
        Creates a Git repository under the test drive whose documentation passes every check, stages all its files,
        and returns its path. With WithScripts, it also holds a copy of the scripts so the entry point can run on it.
        #>
        param([string]$Name, [switch]$WithScripts)
        $root = Join-Path $TestDrive $Name
        $null = New-Item -ItemType Directory -Path $root
        & git init -q $root
        if ($LASTEXITCODE -ne 0) { throw "git init failed in $root" }

        Write-FixtureFile $root 'README.md' "# Fixture`n`nSee [requirements](docs/requirements.md#1-product) and [testing](docs/testing.md).`n"
        Write-FixtureFile $root 'docs/requirements.md' (@(
                '# Requirements', '', '## 1. Product', '',
                '| ID | Requirement |', '| --- | --- |', '| R-ABC-01 | One. |', '| R-ABC-02 | Two. |', '',
                '## 2. Acceptance criteria', '',
                '| ID | Criterion |', '| --- | --- |', '| AC-01 | Works. |', ''
            ) -join "`n")
        Write-FixtureFile $root 'docs/traceability.md' (@(
                '# Traceability', '',
                '| Requirements | Topic | Design | Phase | Tests |', '| --- | --- | --- | --- | --- |',
                '| R-ABC-01 to R-ABC-02 | Topic | [Testing](testing.md#catalog) | P01 | T-ABC-01 |', '',
                '| Criterion | Verified by |', '| --- | --- |', '| AC-01 | T-ABC-02 |', ''
            ) -join "`n")
        Write-FixtureFile $root 'docs/testing.md' (@(
                '# Testing', '', '## Catalog', '',
                '| ID | Test | Level | Phase |', '| --- | --- | --- | --- |',
                '| T-ABC-01 | First | Unit | P01 |', '| T-ABC-02 | Second | Unit | P01 |', ''
            ) -join "`n")
        Write-FixtureFile $root 'docs/roadmap.md' "# Roadmap`n`n## P01`n`nExit: T-ABC-01 to T-ABC-02 pass.`n"

        if ($WithScripts) {
            $scripts = Join-Path $root 'scripts'
            $null = New-Item -ItemType Directory -Path $scripts
            foreach ($file in @('lyrveil.cmd', 'lyrveil.ps1', 'PSScriptAnalyzerSettings.psd1')) {
                Copy-Item -LiteralPath (Join-Path $script:RepositoryRoot "scripts\$file") -Destination $scripts
            }
            Copy-Item -LiteralPath (Join-Path $script:RepositoryRoot 'scripts\lib') -Destination $scripts -Recurse
        }

        Invoke-FixtureGit $root @('add', '-A')
        return $root
    }

    function Get-Failure {
        # Returns every failure message of a documentation check run.
        param($Results)
        return @($Results | ForEach-Object { $_.Failures })
    }

    function Invoke-EntryPoint {
        # Runs scripts\lyrveil.cmd of the repository at Root and returns its exit code and combined output.
        param([string]$Root, [string[]]$Arguments)
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & (Join-Path $Root 'scripts\lyrveil.cmd') @Arguments 2>&1 | Out-String
            $code = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previous
        }
        return [pscustomobject]@{ ExitCode = $code; Output = $output }
    }
}

Describe 'Invoke-DocumentationCheck' {
    It 'passes a consistent fixture and reports counts' {
        $root = Initialize-FixtureRepository 'valid'
        $results = Invoke-DocumentationCheck -Root $root
        Get-Failure $results | Should -BeNullOrEmpty
        ($results | Where-Object { $_.Name -like 'Relative links*' }).Checked | Should -Be 3
        ($results | Where-Object { $_.Name -like 'Requirements traced*' }).Checked | Should -Be 2
        ($results | Where-Object { $_.Name -like 'Catalog tests named*' }).Checked | Should -Be 2
    }

    It 'reports a tracked file with a broken relative link' {
        $root = Initialize-FixtureRepository 'link'
        Write-FixtureFile $root 'docs/extra.md' "# Extra`n`nSee [missing](missing.md).`n"
        Invoke-FixtureGit $root @('add', '-A')
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -Match "docs/extra.md:3: link 'missing.md'"
    }

    It 'reports a broken heading anchor' {
        $root = Initialize-FixtureRepository 'anchor'
        Write-FixtureFile $root 'docs/extra.md' "# Extra`n`nSee [catalog](testing.md#no-such-heading).`n"
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -Match "anchor '#no-such-heading'"
    }

    It 'skips links in code and Markdown that .gitignore excludes' {
        $root = Initialize-FixtureRepository 'ignored'
        Write-FixtureFile $root '.gitignore' "ignored/`n"
        Write-FixtureFile $root 'ignored/notes.md' "# Notes`n`n[broken](nowhere.md)`n"
        $fence = '`' * 3
        Write-FixtureFile $root 'docs/code.md' (@('# Code', '', '`[a](nowhere.md)`', '', ($fence + 'text'), '[b](nowhere.md)', $fence, '') -join "`n")
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -BeNullOrEmpty
    }

    It 'reports an unfinished-work marker' {
        $root = Initialize-FixtureRepository 'marker'
        Write-FixtureFile $root 'docs/extra.md' ("# Extra`n`n" + 'TO' + 'DO' + ": finish.`n")
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -Match 'docs/extra.md:3: unfinished-work marker'
    }

    It 'reports a requirement and an acceptance criterion with no trace' {
        $root = Initialize-FixtureRepository 'trace'
        $path = Join-Path $root 'docs/requirements.md'
        $text = [System.IO.File]::ReadAllText($path).Replace("| R-ABC-02 | Two. |`n", "| R-ABC-02 | Two. |`n| R-ABC-03 | Three. |`n")
        Write-FixtureFile $root 'docs/requirements.md' ($text + "| AC-02 | Also works. |`n")
        $failures = Get-Failure (Invoke-DocumentationCheck -Root $root)
        $failures | Should -Contain "docs/requirements.md: 'R-ABC-03' has no row in docs/traceability.md."
        $failures | Should -Contain "docs/requirements.md: 'AC-02' has no row in docs/traceability.md."
    }

    It 'reports a test identifier missing from the catalog' {
        $root = Initialize-FixtureRepository 'unknown-test'
        Write-FixtureFile $root 'docs/extra.md' "# Extra`n`nCovered by T-ABC-09.`n"
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -Match "test 'T-ABC-09' is not in the catalog"
    }

    It 'reports a catalog test that no roadmap exit names' {
        $root = Initialize-FixtureRepository 'unnamed-test'
        $path = Join-Path $root 'docs/testing.md'
        Write-FixtureFile $root 'docs/testing.md' ([System.IO.File]::ReadAllText($path) + "| T-ABC-03 | Third | Unit | P01 |`n")
        Get-Failure (Invoke-DocumentationCheck -Root $root) | Should -Match "catalog test 'T-ABC-03' is not named by any 'Exit:' line"
    }
}

Describe 'Identifier and anchor helpers' {
    It 'expands ranges and keeps single identifiers' {
        (Expand-IdList -Text 'T-DCK-01 to T-DCK-03, T-DCK-13 and R-PUB-09') -join ',' |
            Should -Be 'T-DCK-01,T-DCK-02,T-DCK-03,T-DCK-13,R-PUB-09'
    }

    It 'builds heading anchors as GitHub does' {
        ConvertTo-HeadingAnchor -Heading '21. Acceptance criteria' | Should -Be '21-acceptance-criteria'
        ConvertTo-HeadingAnchor -Heading '`scripts\lyrveil.cmd import-model`' | Should -Be 'scriptslyrveilcmd-import-model'
    }
}

Describe 'scripts\lyrveil.cmd test' {
    BeforeAll {
        # Fixture runs share the repository's fetched tools instead of downloading them again.
        $script:PreviousTools = $env:LYRVEIL_TOOLS_DIR
        if (-not $env:LYRVEIL_TOOLS_DIR) { $env:LYRVEIL_TOOLS_DIR = Join-Path $script:RepositoryRoot '.tools' }
    }

    AfterAll {
        if ($null -eq $script:PreviousTools) { Remove-Item Env:LYRVEIL_TOOLS_DIR -ErrorAction SilentlyContinue }
        else { $env:LYRVEIL_TOOLS_DIR = $script:PreviousTools }
    }

    It 'lint exits 0 on a consistent repository' {
        $run = Invoke-EntryPoint (Initialize-FixtureRepository 'cli-valid' -WithScripts) @('test', 'lint')
        $run.Output | Should -Match 'test lint: passed'
        $run.ExitCode | Should -Be 0
    }

    It 'lint exits non-zero when a tracked Markdown file has a broken link' {
        $root = Initialize-FixtureRepository 'cli-link' -WithScripts
        Write-FixtureFile $root 'docs/extra.md' "# Extra`n`nSee [missing](missing.md#part).`n"
        Invoke-FixtureGit $root @('add', '-A')
        $run = Invoke-EntryPoint $root @('test', 'lint')
        $run.Output | Should -Match "docs/extra.md:3: link 'missing.md#part'"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'lint exits non-zero on a PSScriptAnalyzer finding' {
        $root = Initialize-FixtureRepository 'cli-script' -WithScripts
        Write-FixtureFile $root 'tools/bad.ps1' "gci`n"
        Invoke-FixtureGit $root @('add', '-A')
        $run = Invoke-EntryPoint $root @('test', 'lint')
        $run.Output | Should -Match 'tools/bad.ps1:1: \[Warning\] PSAvoidUsingCmdletAliases'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses an unknown level' {
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test', 'nosuch')
        $run.Output | Should -Match "Unknown test level 'nosuch'"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses a missing level' {
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test')
        $run.Output | Should -Match 'Usage: scripts\\lyrveil.cmd test <level>'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses arguments for a level that does not pass them to pytest' {
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test', 'lint', '-k', 'name')
        $run.Output | Should -Match "Test level 'lint' takes no arguments"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses pytest arguments when no Python component exists' {
        $run = Invoke-EntryPoint (Initialize-FixtureRepository 'cli-no-python' -WithScripts) @('test', 'unit', '-k', 'name')
        $run.Output | Should -Match "Test level 'unit' has no tests yet"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses to pass a level that has no tests' {
        $run = Invoke-EntryPoint (Initialize-FixtureRepository 'cli-empty' -WithScripts) @('test', 'unit')
        $run.Output | Should -Match "Test level 'unit' has no tests yet"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses to pass the integration level when no Python component exists' {
        $run = Invoke-EntryPoint (Initialize-FixtureRepository 'cli-no-integration' -WithScripts) @('test', 'integration')
        $run.Output | Should -Match "Test level 'integration' has no tests yet"
        $run.ExitCode | Should -Not -Be 0
    }
}

Describe 'Python component selection' {
    # A stand-in docker.cmd, first on PATH, prints its arguments and succeeds, so these cases check only which
    # component runs pytest and with what, without Docker.
    BeforeAll {
        $script:FakeBin = Join-Path $TestDrive 'fake-docker'
        Write-FixtureFile $script:FakeBin 'docker.cmd' "@echo off`r`necho fake-docker %*`r`nexit /b 0`r`n"
        $script:Components = Initialize-FixtureRepository 'cli-components' -WithScripts
        Write-FixtureFile $script:Components 'backend/Dockerfile' "FROM scratch`n"
        Write-FixtureFile $script:Components 'backend/tests/unit/test_a.py' "def test_a() -> None:`n    pass`n"
        Write-FixtureFile $script:Components 'backend/tests/integration/test_c.py' "def test_c() -> None:`n    pass`n"
        Write-FixtureFile $script:Components 'orchestrator/Dockerfile' "FROM scratch`n"
        Write-FixtureFile $script:Components 'orchestrator/tests/unit/test_b.py' "def test_b() -> None:`n    pass`n"
        Invoke-FixtureGit $script:Components @('add', '-A')
        $script:SavedPath = $env:PATH
        $env:PATH = "$script:FakeBin;$env:PATH"
    }

    AfterAll {
        $env:PATH = $script:SavedPath
    }

    It 'skips a component without an integration folder' {
        $run = Invoke-EntryPoint $script:Components @('test', 'integration')
        $run.Output | Should -Match 'fake-docker run --rm --network none lyrveil/backend-test:local pytest tests/integration'
        $run.Output | Should -Match "Python component 'orchestrator': skipped, it has no tests/integration"
        $run.Output | Should -Not -Match 'orchestrator-test:local pytest'
        $run.ExitCode | Should -Be 0
    }

    It 'runs a named test only in the component that holds it' {
        $run = Invoke-EntryPoint $script:Components @('test', 'unit', 'tests/unit/test_b.py::test_b', '-x')
        $run.Output | Should -Match 'lyrveil/orchestrator-test:local pytest tests/unit/test_b.py::test_b -x'
        $run.Output | Should -Match "Python component 'backend': skipped, it has no tests/unit/test_b.py"
        $run.ExitCode | Should -Be 0
    }

    It 'fails when no component holds the named test' {
        $run = Invoke-EntryPoint $script:Components @('test', 'unit', 'tests/unit/test_missing.py')
        $run.Output | Should -Match 'no Python component has tests/unit/test_missing.py: nothing ran'
        $run.Output | Should -Not -Match 'fake-docker run'
        $run.ExitCode | Should -Not -Be 0
    }
}
