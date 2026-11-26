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

    It 'quality-rating names its usage and runs nothing without a sheet' {
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test', 'quality-rating')
        $run.Output | Should -Match 'Usage: scripts\\lyrveil.cmd test quality-rating <filled sheet.md>'
        $run.Output | Should -Match 'Nothing ran'
        $run.ExitCode | Should -Be 1
    }

    It 'quality-rating imports a filled blind sheet, prints the verdict per profile and exits by it' {
        Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\ItalianQuality.psm1') -Force
        $replies = New-Object System.Collections.Generic.List[object]
        foreach ($character in @('Nerea', 'Ilario', 'Tessa')) {
            foreach ($number in 1..40) {
                $replies.Add(@{ Conversation = 'main'; Character = $character; Number = $number; Kind = 'chat'; Status = 'complete'; Question = "Domanda $number"; Text = "Risposta numero $number di $character." })
            }
        }
        $blind = Get-ItalianBlindSheet -ReplyByProfile @{ 'small-model' = $replies.ToArray() } -CharacterName @('Nerea', 'Ilario', 'Tessa') -Seed 3
        $passing = @{ M1 = @{ passed = $true }; M2 = @{ passed = $true }; M3 = @{ passed = $true }; M4 = @{ passed = $true } }
        $folder = Join-Path $TestDrive 'rating-cli'
        $paths = Write-ItalianArtifact -Folder $folder -Blind $blind -Metric @{ 'small-model' = $passing }
        $byId = @{}
        foreach ($item in $blind.Key.items) { $byId[$item.id] = $item }
        $current = ''
        $filled = foreach ($line in ($blind.Sheet -split '\r?\n')) {
            if ($line -match '^### ([NA]\d+)') { $current = $Matches[1] }
            if ($current -and $line -match '^Rating:\s*$') { 'Rating: 5' }
            elseif ($current -and $line -match '^Author:\s*$') { "Author: $($byId[$current].character)" }
            else { $line }
        }
        [System.IO.File]::WriteAllText($paths.Sheet, ($filled -join "`n"), (New-Object System.Text.UTF8Encoding($false)))
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test', 'quality-rating', $paths.Sheet)
        $run.Output | Should -Match 'small-model: met'
        $run.Output | Should -Match 'Verdict: .*verdict\.json'
        $run.Output | Should -Match 'test quality-rating: passed'
        $run.ExitCode | Should -Be 0

        [System.IO.File]::WriteAllText($paths.Sheet, $blind.Sheet, (New-Object System.Text.UTF8Encoding($false)))
        $blank = Invoke-EntryPoint $script:RepositoryRoot @('test', 'quality-rating', $paths.Sheet)
        $blank.Output | Should -Match 'small-model: incomplete'
        $blank.Output | Should -Match 'N01 was left blank'
        $blank.Output | Should -Match 'test quality-rating: FAILED'
        $blank.ExitCode | Should -Be 1
    }

    It 'quality-rating imports a filled image review sheet and exits by its verdict' {
        Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\ImageQuality.psm1') -Force
        $cells = New-Object System.Collections.Generic.List[object]
        $expected = @{ hair_color = 'red hair'; hair_length = 'short hair'; eye_color = 'green eyes'; age = 27; age_band = 'woman'; outfit = 'canvas jacket' }
        $made = @(@('grid', 'default'), @('grid', 'clip-skip-1'), @('grid', 'cfg-4.5'), @('grid', 'cfg-7'), @('grid', 'denoise-0.35'), @('grid', 'denoise-0.55'), @('identity', 'baseline'))
        foreach ($pair in $made) {
            $path = Join-Path $TestDrive "image-$($pair[1]).jpg"
            [System.IO.File]::WriteAllBytes($path, [byte[]](1, 2, 3))
            $cells.Add(@{
                    Id = "$($pair[0])/$($pair[1])"; Section = $pair[0]; Variant = $pair[1]; Profile = 'big'; Character = 'Nerea'; Scene = 'cafe'; Seed = 1
                    Aspect = 'portrait'; Expected = $expected; Status = 'success'; Seconds = 20.0; Path = $path
                })
        }
        $blind = Get-ImageReviewSheet -Cell $cells.ToArray() -DefaultProfile 'big' -Seed 2 -Mode 'Full'
        $paths = Write-ImageArtifact -Folder (Join-Path $TestDrive 'image-cli') -Blind $blind -Cell $cells.ToArray()
        $filled = foreach ($line in ($blind.Sheet -split '\r?\n')) {
            if ($line -match '^Defect:\s*$') { 'Defect: no' }
            elseif ($line -match '^(Hair color|Hair length|Eyes|Age band|Outfit):\s*$') { "$($Matches[1]): ok" }
            else { $line }
        }
        [System.IO.File]::WriteAllText($paths.Sheet, ($filled -join "`n"), (New-Object System.Text.UTF8Encoding($false)))
        $run = Invoke-EntryPoint $script:RepositoryRoot @('test', 'quality-rating', $paths.Sheet)
        $run.Output | Should -Match 'identity: accuracy 1 over 5 checks, target met True'
        $run.Output | Should -Match 'Verdict: .*verdict\.json'
        $run.Output | Should -Match 'test quality-rating: passed'
        $run.ExitCode | Should -Be 0

        [System.IO.File]::WriteAllText($paths.Sheet, $blind.Sheet, (New-Object System.Text.UTF8Encoding($false)))
        $blank = Invoke-EntryPoint $script:RepositoryRoot @('test', 'quality-rating', $paths.Sheet)
        $blank.Output | Should -Match 'was left blank'
        $blank.Output | Should -Match 'status: incomplete'
        $blank.Output | Should -Match 'test quality-rating: FAILED'
        $blank.ExitCode | Should -Be 1
    }
}

Describe 'The e2e level' {
    # Stand-ins for docker, npm and npx, first on PATH, print the arguments of "compose" and "npx" calls. FAKE_UP_EXIT
    # makes "compose up" fail and FAKE_TEST_EXIT makes "playwright test" exit with that status, so these cases check the
    # order of the steps and that the project is removed whatever happens, without Docker or a browser.
    BeforeAll {
        $script:E2eBin = Join-Path $TestDrive 'fake-e2e-bin'
        Write-FixtureFile $script:E2eBin 'docker.cmd' (@(
                '@echo off',
                'if not "%1"=="compose" exit /b 0',
                'echo fake-docker %*',
                'for %%a in (%*) do if "%%a"=="up" if defined FAKE_UP_EXIT exit /b %FAKE_UP_EXIT%',
                'exit /b 0', '') -join "`r`n")
        Write-FixtureFile $script:E2eBin 'npm.cmd' "@echo off`r`necho fake-npm %*`r`nexit /b 0`r`n"
        Write-FixtureFile $script:E2eBin 'npx.cmd' (@(
                '@echo off', 'echo fake-npx %*',
                'if "%2"=="test" if defined FAKE_TEST_EXIT exit /b %FAKE_TEST_EXIT%',
                'exit /b 0', '') -join "`r`n")
        $script:E2eRepository = Initialize-FixtureRepository 'cli-e2e' -WithScripts
        Write-FixtureFile $script:E2eRepository 'tests/e2e/package.json' "{}`n"
        Write-FixtureFile $script:E2eRepository 'tests/e2e/package-lock.json' "{}`n"
        Invoke-FixtureGit $script:E2eRepository @('add', '-A')
        $script:SavedE2ePath = $env:PATH
        $env:PATH = "$script:E2eBin;$env:PATH"
    }

    AfterAll {
        $env:PATH = $script:SavedE2ePath
        Remove-Item Env:FAKE_UP_EXIT, Env:FAKE_TEST_EXIT, Env:LYRVEIL_TEST_HTTP_PORT -ErrorAction SilentlyContinue
    }

    BeforeEach {
        Remove-Item Env:FAKE_UP_EXIT, Env:FAKE_TEST_EXIT, Env:LYRVEIL_TEST_HTTP_PORT -ErrorAction SilentlyContinue
    }

    It 'refuses to pass when the repository has no tests/e2e' {
        $run = Invoke-EntryPoint (Initialize-FixtureRepository 'cli-no-e2e' -WithScripts) @('test', 'e2e')
        $run.Output | Should -Match "Test level 'e2e' has no tests yet"
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses the port of a real Lyrveil before it starts anything' {
        $env:LYRVEIL_TEST_HTTP_PORT = '4200'
        $run = Invoke-EntryPoint $script:E2eRepository @('test', 'e2e')
        $run.Output | Should -Match 'LYRVEIL_TEST_HTTP_PORT is 4200'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'starts the test project, runs the suite, then removes the project and its volumes' {
        $run = Invoke-EntryPoint $script:E2eRepository @('test', 'e2e')
        $run.Output | Should -Match 'fake-docker compose -p lyrveil-test -f compose.test.yaml up --build --detach --wait lyrveil-frontend'
        $run.Output | Should -Match 'fake-npx playwright test'
        $run.Output | Should -Match 'fake-docker compose -p lyrveil-test -f compose.test.yaml down --volumes --remove-orphans'
        $run.Output.LastIndexOf('fake-docker compose -p lyrveil-test -f compose.test.yaml down') |
            Should -BeGreaterThan $run.Output.IndexOf('fake-npx playwright test')
        $run.ExitCode | Should -Be 0
    }

    It 'removes the project and fails when the suite fails' {
        $env:FAKE_TEST_EXIT = '3'
        $run = Invoke-EntryPoint $script:E2eRepository @('test', 'e2e')
        $run.Output | Should -Match 'FAIL\] end-to-end: playwright test \(exit 3\)'
        $run.Output.LastIndexOf('fake-docker compose -p lyrveil-test -f compose.test.yaml down --volumes') |
            Should -BeGreaterThan $run.Output.IndexOf('fake-npx playwright test')
        $run.ExitCode | Should -Not -Be 0
    }

    It 'removes the project, runs no test and fails when the project does not start' {
        $env:FAKE_UP_EXIT = '1'
        $run = Invoke-EntryPoint $script:E2eRepository @('test', 'e2e')
        $run.Output | Should -Match 'the test project did not start'
        $run.Output | Should -Not -Match 'fake-npx playwright test'
        $run.Output | Should -Match 'fake-docker compose -p lyrveil-test -f compose.test.yaml down --volumes'
        $run.ExitCode | Should -Not -Be 0
    }
}

Describe 'The gpu level' {
    # Stand-ins for nvidia-smi and docker, first on PATH. nvidia-smi reports one GPU, or fails with FAKE_NVIDIA_EXIT as
    # it does without a driver. docker prints the arguments of every call except "ps" and "volume ls", which list
    # nothing (or a running runtime of an installation with FAKE_RUNNING_RUNTIME); it answers "config" with the file
    # FAKE_CONFIG and fails "build" with FAKE_BUILD_EXIT. The fixture's tests/gpu file passes, or fails with
    # FAKE_GPU_TEST_FAIL, so these cases check the skip, the order of the steps and the cleanup without Docker or a GPU.
    BeforeAll {
        Import-Module (Join-Path $script:RepositoryRoot 'scripts\lib\GpuLevel.psm1') -Force
        $script:PreviousGpuTools = $env:LYRVEIL_TOOLS_DIR
        if (-not $env:LYRVEIL_TOOLS_DIR) { $env:LYRVEIL_TOOLS_DIR = Join-Path $script:RepositoryRoot '.tools' }
        $script:GpuBin = Join-Path $TestDrive 'fake-gpu-bin'
        Write-FixtureFile $script:GpuBin 'nvidia-smi.cmd' (@(
                '@echo off',
                'if defined FAKE_NVIDIA_EXIT echo NVIDIA-SMI has failed because it could not communicate with the NVIDIA driver.',
                'if defined FAKE_NVIDIA_EXIT exit /b %FAKE_NVIDIA_EXIT%',
                'echo 0, NVIDIA Stand-in GPU, 12282, 512',
                'exit /b 0', '') -join "`r`n")
        Write-FixtureFile $script:GpuBin 'docker.cmd' (@(
                '@echo off',
                'if "%1"=="ps" if defined FAKE_RUNNING_RUNTIME echo lyrveil-image',
                'if "%1"=="ps" exit /b 0',
                'if "%1"=="volume" if "%2"=="ls" exit /b 0',
                'for %%a in (%*) do if "%%a"=="config" (type "%FAKE_CONFIG%" & exit /b 0)',
                'echo fake-docker %*',
                'for %%a in (%*) do if "%%a"=="build" if defined FAKE_BUILD_EXIT exit /b %FAKE_BUILD_EXIT%',
                'exit /b 0', '') -join "`r`n")

        function Get-GpuConfigurationJson {
            # Returns the JSON of a resolved test configuration that the level accepts, changed by Change when given.
            param([scriptblock]$Change)
            $config = @{
                name     = 'lyrveil-gpu-test'
                services = @{
                    'lyrveil-llm'   = @{ image = 'lyrveil/llm:gpu-test'; volumes = @(
                            @{ type = 'volume'; source = 'lyrveil-models'; target = '/models/llm'; read_only = $true },
                            @{ type = 'volume'; source = 'lyrveil-data'; target = '/run/lyrveil'; read_only = $true }) }
                    'lyrveil-image' = @{ image = 'lyrveil/image:gpu-test'; volumes = @(
                            @{ type = 'volume'; source = 'lyrveil-models'; target = '/opt/ComfyUI/models/checkpoints'; read_only = $true },
                            @{ type = 'tmpfs'; target = '/tmp/comfy' }) }
                    'lyrveil-orchestrator' = @{ image = 'lyrveil/orchestrator:gpu-test'
                        environment = @{ LYRVEIL_CONTROL_TOKEN = ('7e57a11c' * 8); LYRVEIL_LOG_LEVEL = 'INFO' }
                        volumes = @(@{ type = 'bind'; source = '/var/run/docker.sock'; target = '/var/run/docker.sock' }) }
                    'lyrveil-backend' = @{ image = 'lyrveil/backend:gpu-test'
                        environment = @{ LYRVEIL_CONTROL_TOKEN = ('7e57a11c' * 8); LYRVEIL_LOG_LEVEL = 'INFO'; LYRVEIL_TZ = 'Europe/Rome' }
                        volumes = @(
                            @{ type = 'volume'; source = 'lyrveil-data'; target = '/data' },
                            @{ type = 'volume'; source = 'lyrveil-models'; target = '/models'; read_only = $true }) }
                }
                volumes  = @{
                    'lyrveil-data'   = @{ name = 'lyrveil-gpu-test-data'; external = $true }
                    'lyrveil-models' = @{ name = 'lyrveil-models'; external = $true }
                }
            }
            if ($Change) { & $Change $config }
            return ($config | ConvertTo-Json -Depth 8)
        }

        $script:GoodConfig = Join-Path $TestDrive 'gpu-config-good.json'
        Write-FixtureFile $TestDrive 'gpu-config-good.json' (Get-GpuConfigurationJson)
        $script:WritableConfig = Join-Path $TestDrive 'gpu-config-writable.json'
        Write-FixtureFile $TestDrive 'gpu-config-writable.json' (Get-GpuConfigurationJson {
                param($c) $c.services['lyrveil-llm'].volumes[0].read_only = $false })

        $script:GpuRepository = Initialize-FixtureRepository 'cli-gpu' -WithScripts
        Write-FixtureFile $script:GpuRepository 'config/model-catalog.json' (
            '{"text": [{"id": "big-model", "file": "big.gguf", "bytes": 200}, {"id": "small-model", "file": "small.gguf", "bytes": 100}]}')
        Write-FixtureFile $script:GpuRepository 'tests/gpu/StandIn.Tests.ps1' (@(
                "Describe 'stand-in gpu test' {",
                "    It 'runs' {",
                "        Write-Host 'stand-in gpu test ran'",
                '        $env:FAKE_GPU_TEST_FAIL | Should -BeNullOrEmpty',
                '    }',
                '}', '') -join "`n")
        Write-FixtureFile $script:GpuRepository 'tests/gpu/Other.Tests.ps1' (@(
                "Describe 'other gpu test' {",
                "    It 'runs' { Write-Host 'other gpu test ran' }",
                '}', '') -join "`n")
        Write-FixtureFile $script:GpuRepository 'tests/gpu/Selected.Tests.ps1' (@(
                'param([string[]]$Select = @())',
                'BeforeAll { $script:Seen = @($Select) -join "," }',
                "Describe 'selected gpu test' {",
                '    It "runs" { Write-Host "selected gpu test ran for [$script:Seen]" }',
                '}', '') -join "`n")
        # The opt-in file of T-GPU-13: a plain "test gpu" must leave it out, and a command that names it must run it.
        Write-FixtureFile $script:GpuRepository 'tests/gpu/Session.Tests.ps1' (@(
                'param([string[]]$Select = @())',
                'BeforeAll { $script:Seen = @($Select) -join "," }',
                "Describe 'opt-in session test' {",
                '    It "runs" { Write-Host "opt-in session test ran for [$script:Seen]" }',
                '}', '') -join "`n")
        # The two files of the quality level, which runs on the same test project and passes its items as Select.
        foreach ($name in @('Consistency', 'Extraction')) {
            Write-FixtureFile $script:GpuRepository "tests/quality/$name.Tests.ps1" (@(
                    'param([string[]]$Select = @())',
                    'BeforeAll { $script:Seen = @($Select) -join "," }',
                    "Describe 'quality $name test' {",
                    "    It 'runs' { Write-Host ""quality $name test ran for [`$script:Seen]"" }",
                    '}', '') -join "`n")
        }
        Invoke-FixtureGit $script:GpuRepository @('add', '-A')
        $script:SavedGpuPath = $env:PATH
        $env:PATH = "$script:GpuBin;$env:PATH"
        $script:Project = 'compose -p lyrveil-gpu-test -f compose.yaml -f tests/gpu/compose.gpu-test.yaml --profile ai'
    }

    AfterAll {
        $env:PATH = $script:SavedGpuPath
        Remove-Item Env:FAKE_NVIDIA_EXIT, Env:FAKE_RUNNING_RUNTIME, Env:FAKE_CONFIG, Env:FAKE_BUILD_EXIT, Env:FAKE_GPU_TEST_FAIL -ErrorAction SilentlyContinue
        if ($null -eq $script:PreviousGpuTools) { Remove-Item Env:LYRVEIL_TOOLS_DIR -ErrorAction SilentlyContinue }
        else { $env:LYRVEIL_TOOLS_DIR = $script:PreviousGpuTools }
    }

    BeforeEach {
        Remove-Item Env:FAKE_NVIDIA_EXIT, Env:FAKE_RUNNING_RUNTIME, Env:FAKE_BUILD_EXIT, Env:FAKE_GPU_TEST_FAIL -ErrorAction SilentlyContinue
        $env:FAKE_CONFIG = $script:GoodConfig
    }

    It 'skips with a clear message, touches no Docker and never passes when no GPU is present' {
        $env:FAKE_NVIDIA_EXIT = '9'
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $run.Output | Should -Match "Test level 'gpu' skipped: nvidia-smi exited with status 9"
        $run.Output | Should -Match 'test gpu: SKIPPED \(gpu\)\. No test ran'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.Output | Should -Not -Match 'stand-in gpu test ran'
        $run.Output | Should -Not -Match 'test gpu: passed'
        $run.ExitCode | Should -Be 1
    }

    It 'refuses a malformed trial selector before any GPU or Docker call' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'rocinante-x-12b')
        $run.Output | Should -Match "'rocinante-x-12b' is not a trial selector"
        $run.Output | Should -Match 'Nothing ran\.'
        $run.Output | Should -Not -Match 'reading GPU 0'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.ExitCode | Should -Be 1
    }

    It 'refuses trial selectors when the tuning plan cannot be read from the catalog' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'rocinante-x-12b/A')
        $run.Output | Should -Match 'The tuning plan could not be read'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.Output | Should -Not -Match 'stand-in gpu test ran'
        $run.ExitCode | Should -Be 1
    }

    It 'clears, builds, prepares the data volume, runs the tests, then removes the project and the volume' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $out = $run.Output
        $firstDown = $out.IndexOf("fake-docker $script:Project down --volumes --remove-orphans")
        $build = $out.IndexOf("fake-docker $script:Project build lyrveil-llm lyrveil-image")
        $create = $out.IndexOf('fake-docker volume create --label com.lyrveil.test=gpu lyrveil-gpu-test-data')
        $launch = $out.IndexOf('MODEL_FILE=small.gguf')
        $tests = $out.IndexOf('stand-in gpu test ran')
        $lastDown = $out.LastIndexOf("fake-docker $script:Project down --volumes --remove-orphans")
        $lastVolume = $out.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data')
        @($firstDown, $build, $create, $launch, $tests) | Should -Not -Contain -1
        $build | Should -BeGreaterThan $firstDown
        $create | Should -BeGreaterThan $build
        $launch | Should -BeGreaterThan $create
        $tests | Should -BeGreaterThan $launch
        $lastDown | Should -BeGreaterThan $tests
        $lastVolume | Should -BeGreaterThan $lastDown
        $out | Should -Match 'lyrveil-gpu-test-data:/data --entrypoint /bin/sh lyrveil/llm:gpu-test'
        $out | Should -Match 'test gpu: passed'
        $run.ExitCode | Should -Be 0
    }

    It 'leaves the opt-in session test out of a plain run, and runs it only when named, with or without its item' {
        $plain = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $plain.Output | Should -Match 'stand-in gpu test ran'
        $plain.Output | Should -Match 'other gpu test ran'
        $plain.Output | Should -Not -Match 'opt-in session test ran'
        $named = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'Session')
        $named.Output | Should -Match 'GPU: only tests/gpu/Session\.Tests\.ps1'
        $named.Output | Should -Match 'opt-in session test ran for \[\]'
        $named.Output | Should -Not -Match 'stand-in gpu test ran'
        $short = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'Session', 'short')
        $short.Output | Should -Match 'opt-in session test ran for \[short\]'
        $short.Output.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data') |
            Should -BeGreaterThan $short.Output.IndexOf('opt-in session test ran')
        $short.ExitCode | Should -Be 0
    }

    It 'runs only the test file an argument names, with the same steps and cleanup' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'StandIn')
        $run.Output | Should -Match 'GPU: only tests/gpu/StandIn\.Tests\.ps1'
        $run.Output | Should -Match 'stand-in gpu test ran'
        $run.Output | Should -Not -Match 'other gpu test ran'
        $run.Output.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data') |
            Should -BeGreaterThan $run.Output.IndexOf('stand-in gpu test ran')
        $run.Output | Should -Match 'test gpu: passed'
        $run.ExitCode | Should -Be 0
    }

    It 'runs one test file for the items that follow its name, with the same cleanup' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu', 'Selected', 'noobai-xl-1.1', 'illustrious-xl-v2.0')
        $run.Output | Should -Match 'GPU: only tests/gpu/Selected\.Tests\.ps1, for noobai-xl-1\.1 illustrious-xl-v2\.0'
        $run.Output | Should -Match 'selected gpu test ran for \[noobai-xl-1\.1,illustrious-xl-v2\.0\]'
        $run.Output | Should -Not -Match 'stand-in gpu test ran'
        $run.Output.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data') |
            Should -BeGreaterThan $run.Output.IndexOf('selected gpu test ran')
        $run.ExitCode | Should -Be 0
    }

    It 'runs every quality file on the gpu level steps, passing short and the catalog profiles named, then cleans up' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'quality', 'small-model', 'short')
        $out = $run.Output
        $out | Should -Match 'Quality: tests/quality/Consistency\.Tests\.ps1, tests/quality/Extraction\.Tests\.ps1, for small-model short'
        $out | Should -Match 'quality Consistency test ran for \[small-model,short\]'
        $out | Should -Match 'quality Extraction test ran for \[small-model,short\]'
        $out | Should -Not -Match 'stand-in gpu test ran'
        $build = $out.IndexOf("fake-docker $script:Project build lyrveil-llm lyrveil-image")
        $tests = $out.IndexOf('quality Consistency test ran')
        $build | Should -BeGreaterThan -1
        $tests | Should -BeGreaterThan $build
        $out.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data') | Should -BeGreaterThan $out.IndexOf('quality Extraction test ran')
        $out | Should -Match '\[ok  \] quality: no container or volume of the test project is left'
        $out | Should -Match 'test quality: passed'
        $run.ExitCode | Should -Be 0
    }

    It 'runs only the quality file an argument names, with every catalog profile when none is named' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'quality', 'Extraction')
        $run.Output | Should -Match 'Quality: tests/quality/Extraction\.Tests\.ps1\r?\n'
        $run.Output | Should -Match 'quality Extraction test ran for \[\]'
        $run.Output | Should -Not -Match 'quality Consistency test ran'
        $run.ExitCode | Should -Be 0
    }

    It 'refuses a quality argument that is no test file, short or catalog profile before any GPU or Docker call' {
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'quality', 'rocinante-x-12b', 'Nothing')
        $run.Output | Should -Match "'rocinante-x-12b' is neither a test file of tests/quality, 'short', nor a text profile of the catalog"
        $run.Output | Should -Match "'Nothing' names no test file of tests/quality"
        $run.Output | Should -Match 'catalog text profiles \(big-model, small-model\)\. Nothing ran\.'
        $run.Output | Should -Not -Match 'reading GPU 0'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.ExitCode | Should -Be 1
    }

    It 'skips the quality level without a GPU, touching no Docker, and never passes' {
        $env:FAKE_NVIDIA_EXIT = '9'
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'quality')
        $run.Output | Should -Match "Test level 'quality' skipped: nvidia-smi exited with status 9"
        $run.Output | Should -Match 'test quality: SKIPPED \(quality\)\. No test ran'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.Output | Should -Not -Match 'quality Consistency test ran'
        $run.ExitCode | Should -Be 1
    }

    It 'removes the project and the volume and fails when a test fails' {
        $env:FAKE_GPU_TEST_FAIL = '1'
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $run.Output | Should -Match 'FAIL\] gpu: tests \(exit 1\)'
        $run.Output.LastIndexOf('fake-docker volume rm --force lyrveil-gpu-test-data') |
            Should -BeGreaterThan $run.Output.IndexOf('stand-in gpu test ran')
        $run.Output | Should -Match 'test gpu: FAILED \(gpu\)'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'runs no test and still removes everything when the images do not build' {
        $env:FAKE_BUILD_EXIT = '1'
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $run.Output | Should -Match 'the runtime images did not build \(exit 1\)'
        $run.Output | Should -Not -Match 'stand-in gpu test ran'
        $run.Output | Should -Not -Match 'volume create'
        $run.Output.LastIndexOf("fake-docker $script:Project down --volumes") |
            Should -BeGreaterThan $run.Output.IndexOf("fake-docker $script:Project build")
        $run.ExitCode | Should -Not -Be 0
    }

    It 'builds and starts nothing when the configuration mounts the models writable' {
        $env:FAKE_CONFIG = $script:WritableConfig
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $run.Output | Should -Match "lyrveil-llm mounts 'lyrveil-models' writable at /models/llm"
        $run.Output | Should -Not -Match "$([regex]::Escape($script:Project)) build"
        $run.Output | Should -Not -Match 'stand-in gpu test ran'
        $run.Output | Should -Match 'fake-docker volume rm --force lyrveil-gpu-test-data'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'refuses to run, touching nothing, while a runtime of an installation is running' {
        $env:FAKE_RUNNING_RUNTIME = '1'
        $run = Invoke-EntryPoint $script:GpuRepository @('test', 'gpu')
        $run.Output | Should -Match 'the container lyrveil-image of an installation is running'
        $run.Output | Should -Not -Match 'fake-docker'
        $run.ExitCode | Should -Not -Be 0
    }

    It 'accepts only the test volumes, mounted read-only, in the resolved configuration' {
        Test-GpuTestConfiguration -Text (Get-GpuConfigurationJson) | Should -BeNullOrEmpty
        $real = Test-GpuTestConfiguration -Text (Get-GpuConfigurationJson { param($c) $c.volumes['lyrveil-data'].name = 'lyrveil-data' })
        $real | Should -Contain "The configuration names the volume 'lyrveil-data'."
        $real | Should -Contain "lyrveil-llm mounts the volume 'lyrveil-data' at /run/lyrveil."
        $named = Test-GpuTestConfiguration -Text (Get-GpuConfigurationJson {
                param($c)
                $c.services['lyrveil-image'].container_name = 'lyrveil-image'
                $c.services['lyrveil-image'].volumes += @{ type = 'bind'; source = 'C:\data'; target = '/data' }
            })
        $named | Should -Contain 'lyrveil-image sets container_name.'
        $named | Should -Contain 'lyrveil-image mounts a bind at /data.'
    }

    It 'allows the Docker socket only to the orchestrator and keeps the .env and its token out of the test stack' {
        $stack = Test-GpuTestConfiguration -Text (Get-GpuConfigurationJson {
                param($c)
                $c.services['lyrveil-backend'].volumes += @{ type = 'bind'; source = '/var/run/docker.sock'; target = '/var/run/docker.sock' }
                $c.services['lyrveil-backend'].volumes[1].read_only = $false
                $c.services['lyrveil-orchestrator'].volumes += @{ type = 'bind'; source = 'C:\Users'; target = '/host' }
                $c.services['lyrveil-orchestrator'].environment.LYRVEIL_BACKUP_DIR = 'C:\Backups'
                $c.services['lyrveil-backend'].environment.LYRVEIL_CONTROL_TOKEN = 'f' * 64
                $c.services['lyrveil-backend'].env_file = @('.env')
            })
        $stack | Should -Contain 'lyrveil-backend mounts a bind at /var/run/docker.sock.'
        $stack | Should -Contain "lyrveil-backend mounts 'lyrveil-models' writable at /models."
        $stack | Should -Contain 'lyrveil-orchestrator mounts a bind at /host.'
        $stack | Should -Contain 'lyrveil-orchestrator sets LYRVEIL_BACKUP_DIR, which the override does not.'
        $stack | Should -Contain "lyrveil-backend does not carry the level's own control token."
        $stack | Should -Contain 'lyrveil-backend reads an env_file.'
        @($stack | Where-Object { $_ -match 'f{64}' }) | Should -BeNullOrEmpty
        $missing = Test-GpuTestConfiguration -Text (Get-GpuConfigurationJson { param($c) $c.services.Remove('lyrveil-orchestrator') })
        $missing | Should -Contain "The services are 'lyrveil-backend, lyrveil-image, lyrveil-llm', not 'lyrveil-backend, lyrveil-image, lyrveil-llm, lyrveil-orchestrator'."
    }

    It 'writes request bodies as ASCII JSON that keeps every character' {
        $text = "Com'$([char]0x00E8) <|turn>`n$([char]0xD83D)$([char]0xDE00)"
        $json = ConvertTo-GpuRequestJson -Body @{ prompt = $text }
        $json | Should -Match '^[\x00-\x7F]*$'
        ($json | ConvertFrom-Json).prompt | Should -BeExactly $text
    }

    It 'reads the memory amount of docker stats in MiB' {
        ConvertFrom-DockerMemoryText -Text '4.5GiB / 23.47GiB' | Should -Be 4608
        ConvertFrom-DockerMemoryText -Text '512MiB / 1GiB' | Should -Be 512
        ConvertFrom-DockerMemoryText -Text '2GB / 8GB' | Should -Be 1907.3
        ConvertFrom-DockerMemoryText -Text '-- / --' | Should -Be -1
    }

    It 'gives the level-A launch lines that the backend writes for a catalog profile' {
        $textProfile = [pscustomobject]@{
            id = 'rocinante-x-12b'; file = 'r.gguf'; context_size = 12288; batch_size = 512; use_mmap = $false
            kv_quant = 'f16'; swa_padding = 0; smart_cache = 0
        }
        Get-GpuLaunchLine -TextProfile $textProfile -Level A | Should -Be @(
            'MODEL_PROFILE=rocinante-x-12b', 'MODEL_FILE=r.gguf', 'CONTEXT_SIZE=12288', 'GPU_LAYERS=-1', 'BATCH_SIZE=512',
            'MOE_CPU_LAYERS=0', 'USE_MMAP=0', 'KV_QUANT=f16', 'SWA_PADDING=0', 'SMART_CACHE=0', 'LEVEL=A')
        (Get-GpuLaunchLine -TextProfile $textProfile)[-1] | Should -Be 'LEVEL=CUSTOM'
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
