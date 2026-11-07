# Tests of scripts/lib/DataFiles.psm1 and scripts/lib/ToolContainer.psm1 that need no Docker: the one-off containers
# have no network and mount the Windows folder read-only, the backup folder comes from LYRVEIL_BACKUP_DIR or the
# default, the backup copy of start and stop turns an invalid folder into a warning, and import-model and restore
# refuse a wrong argument, extension or name before any Docker call.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Module = (Resolve-Path (Join-Path $PSScriptRoot '..\..\scripts\lib\DataFiles.psm1')).Path
    Import-Module $script:Module -Force
    # DataFiles.psm1 has loaded ToolContainer.psm1; without -Force its instance is reused and only made visible here.
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\ToolContainer.psm1')
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

    function Invoke-Captured {
        # Runs the command Name with Parameters and returns its Status and the Text it printed with Write-Host.
        param([string]$Name, [hashtable]$Parameters)
        $items = @(& $Name @Parameters 6>&1)
        $status = @($items | Where-Object { $_ -is [int] })
        $lines = @($items | Where-Object { $_ -isnot [int] } | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ Status = $status[-1]; Text = ($lines -join "`n") }
    }
}

Describe 'Get-ToolContainerArgument' {
    It 'runs the tool with no network, a read-only root, no capability and a read-only Windows folder' {
        $mounts = @(
            @{ Type = 'bind'; Source = 'C:\Users\someone\My models'; Target = '/import'; ReadOnly = $true },
            @{ Type = 'volume'; Source = 'lyrveil-test-models'; Target = '/models'; ReadOnly = $false }
        )
        $arguments = Get-ToolContainerArgument -Image 'lyrveil/backend:test' -Mount $mounts -Module 'lyrveil.tools.import_model' -ToolArgument @('/import/a.gguf')
        $line = $arguments -join ' '
        $line | Should -Match '^run --rm --pull never --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --user 10001:10001 '
        $arguments | Should -Contain 'type=bind,source=C:\Users\someone\My models,target=/import,readonly'
        $arguments | Should -Contain 'type=volume,source=lyrveil-test-models,target=/models'
        $line | Should -Match '--entrypoint python lyrveil/backend:test -m lyrveil\.tools\.import_model /import/a\.gguf$'
        @($arguments | Where-Object { $_ -eq '--network' }).Length | Should -Be 1
    }
}

Describe 'Get-BackupFolder' {
    It 'uses %USERPROFILE%\Documents\Lyrveil\backups without a .env or with an empty value' {
        $expected = Join-Path $env:USERPROFILE 'Documents\Lyrveil\backups'
        Get-BackupFolder -EnvPath (Join-Path $TestDrive 'absent.env') | Should -Be $expected
        $empty = Join-Path $TestDrive 'empty.env'
        Set-Content -LiteralPath $empty -Value 'LYRVEIL_BACKUP_DIR=' -Encoding Ascii
        Get-BackupFolder -EnvPath $empty | Should -Be $expected
    }

    It 'uses LYRVEIL_BACKUP_DIR with its variables expanded' {
        $file = Join-Path $TestDrive 'set.env'
        Set-Content -LiteralPath $file -Value @('LYRVEIL_HTTP_PORT=4200', 'LYRVEIL_BACKUP_DIR=%TEMP%\lyrveil-backups\') -Encoding Ascii
        Get-BackupFolder -EnvPath $file | Should -Be ([System.IO.Path]::GetFullPath((Join-Path $env:TEMP 'lyrveil-backups')))
    }

    It 'refuses a relative folder and one that a mount cannot carry' {
        $file = Join-Path $TestDrive 'relative.env'
        Set-Content -LiteralPath $file -Value 'LYRVEIL_BACKUP_DIR=backups' -Encoding Ascii
        { Get-BackupFolder -EnvPath $file } | Should -Throw '*absolute folder on a drive*'
        Set-Content -LiteralPath $file -Value 'LYRVEIL_BACKUP_DIR=D:\a,b' -Encoding Ascii
        { Get-BackupFolder -EnvPath $file } | Should -Throw '*without a comma*'
    }
}

Describe 'Copy-BackupToWindows' {
    It 'returns an invalid LYRVEIL_BACKUP_DIR as a warning reason, without throwing' {
        # The folder is resolved first, so this case ends before any Docker call.
        $file = Join-Path $TestDrive 'invalid.env'
        Set-Content -LiteralPath $file -Value 'LYRVEIL_BACKUP_DIR=backups' -Encoding Ascii
        $context = @{ InstallationEnvPath = $file }
        $reason = Copy-BackupToWindows -Context $context 6>$null
        $reason | Should -Match '^not copied: LYRVEIL_BACKUP_DIR .* must be an absolute folder on a drive'
    }
}

Describe 'Get-ModelImportFolder' {
    It 'sends a .gguf to llm and a .safetensors to image/checkpoints' {
        Get-ModelImportFolder -Name 'custom-12b.Q4_K_M.gguf' 6>$null | Should -Be 'llm'
        Get-ModelImportFolder -Name 'my_checkpoint.safetensors' 6>$null | Should -Be 'image/checkpoints'
    }

    It 'refuses other extensions and names outside the pattern' {
        foreach ($name in @('model.bin', 'model.GGUF', 'with space.gguf', '.gguf', ('a' * 129) + '.gguf')) {
            Get-ModelImportFolder -Name $name 6>$null | Should -BeNullOrEmpty -Because $name
        }
    }
}

Describe 'Refusals before any Docker call' {
    BeforeAll {
        Mock -ModuleName DataFiles Test-DockerReachable { throw 'No Docker call was expected.' }
        Mock -ModuleName DataFiles Invoke-ToolContainer { throw 'No container was expected.' }
        $script:Folder = Join-Path $TestDrive 'files'
        $null = New-Item -ItemType Directory -Force -Path $script:Folder
        foreach ($name in @('model.bin', 'with space.gguf', 'lyrveil-20261107T120000Z.tar')) {
            Set-Content -LiteralPath (Join-Path $script:Folder $name) -Value 'x' -Encoding Ascii
        }
    }

    It 'import-model refuses another extension, a name outside the pattern, a missing file and a wrong argument count' {
        foreach ($case in @(
                @{ Argument = @((Join-Path $script:Folder 'model.bin')); Text = 'only \.gguf and \.safetensors' },
                @{ Argument = @((Join-Path $script:Folder 'with space.gguf')); Text = 'the name must match' },
                @{ Argument = @((Join-Path $script:Folder 'absent.gguf')); Text = 'is not a file' },
                @{ Argument = @(); Text = 'Usage: scripts\\lyrveil\.cmd import-model <file>' },
                @{ Argument = @('-Force'); Text = "unknown argument '-Force'" }
            )) {
            $run = Invoke-Captured -Name 'Invoke-LyrveilImportModel' -Parameters @{ Root = $script:Root; Argument = $case.Argument }
            $run.Status | Should -Be 1 -Because $run.Text
            $run.Text | Should -Match $case.Text
        }
    }

    It 'restore refuses a missing file and a wrong argument count' {
        $two = @((Join-Path $script:Folder 'lyrveil-20261107T120000Z.tar'), (Join-Path $script:Folder 'model.bin'))
        foreach ($case in @(
                @{ Argument = @((Join-Path $script:Folder 'absent.tar')); Text = 'is not a file' },
                @{ Argument = $two; Text = 'Usage: scripts\\lyrveil\.cmd restore <file>' }
            )) {
            $run = Invoke-Captured -Name 'Invoke-LyrveilRestore' -Parameters @{ Root = $script:Root; Argument = $case.Argument }
            $run.Status | Should -Be 1 -Because $run.Text
            $run.Text | Should -Match $case.Text
        }
    }

    It 'backup takes no argument' {
        $run = Invoke-Captured -Name 'Invoke-LyrveilBackup' -Parameters @{ Root = $script:Root; Argument = @('-Now') }
        $run.Status | Should -Be 1
        $run.Text | Should -Match "unknown argument '-Now'"
    }
}
