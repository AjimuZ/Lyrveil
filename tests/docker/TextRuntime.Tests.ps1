# Docker-level tests of the text runtime image docker/llm (P02.1, docs/operations.md, Runtime images): the build checks
# the size and SHA-256 of the pinned KoboldCpp binary, the image holds the unpacked launcher and no model, launch file or
# single-file binary, the launcher runs on a read-only root with only the tmpfs /tmp of compose.yaml, and the entrypoint
# turns a launch file into the documented command or exits 78. No test needs a GPU or a model: a model file is an empty
# stand-in and the entrypoint's --print-command stops before the launcher starts. The image is tagged
# "lyrveil/llm:test", so the ":local" image of an installation is never replaced. The first run downloads the
# 628,981,944-byte binary from GitHub; later runs reuse Docker's build cache, also for the two mismatch builds.

BeforeDiscovery {
    # A valid launch file, one KEY=VALUE line per key in the order of docs/configuration.md. Discovery only: the data
    # of the -ForEach cases below.
    $script:ValidLines = @(
        'MODEL_PROFILE=gemma-4-26b-a4b'
        'MODEL_FILE=test-model.gguf'
        'CONTEXT_SIZE=16384'
        'GPU_LAYERS=-1'
        'BATCH_SIZE=512'
        'MOE_CPU_LAYERS=0'
        'USE_MMAP=0'
        'KV_QUANT=f16'
        'SWA_PADDING=0'
        'SMART_CACHE=0'
        'LEVEL=A'
    )
}

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:Image = 'lyrveil/llm:test'
    $script:Dockerfile = 'docker/llm/Dockerfile'
    $script:Launcher = '/opt/koboldcpp/koboldcpp-launcher'
    # The container settings of lyrveil-llm in compose.yaml that apply without a GPU: init, the user, no capabilities,
    # no new privileges, a read-only root and the tmpfs /tmp. "--network none" stands in for the internal network.
    $script:Hardening = @('run', '--rm', '--init', '--user', '10001:10001', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--read-only', '--tmpfs', '/tmp', '--network', 'none')
    # The reference launch file, whose model is gemma-4-26B_q4_0-it.gguf.
    $script:ExampleLaunchText = [System.IO.File]::ReadAllText((Join-Path $script:RepositoryRoot 'config/examples/llm-launch.env'))
    # The flags of docs/runtime-contracts.md that the entrypoint passes.
    $script:ContractFlags = @('--model', '--usecuda', '--contextsize', '--gpulayers', '--batchsize', '--quantkv', '--moecpu',
        '--usemmap', '--swapadding', '--smartcache', '--host', '--port', '--skiplauncher', '--quiet')
    # Writes the launch file and the stand-in model into tmpfs folders, then runs the entrypoint. Arguments: the launch
    # file in base64 or "-" for none, the model file name or "-" for none, then the entrypoint's own arguments. Single
    # quotes only, so Windows PowerShell passes it to docker unchanged.
    $script:EntrypointScript = 'if [ x$1 != x- ]; then printf %s $1 | base64 -d > /run/lyrveil/llm-launch.env; fi; ' +
        'if [ x$2 != x- ]; then : > /models/llm/$2; fi; shift 2; exec /usr/local/bin/lyrveil-llm-entrypoint $@'

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Invoke-DockerCapture {
        # Runs Docker and returns its exit Status and every line it printed, the error stream included, as Text.
        # Never throws, so a test can expect a failure.
        param([string[]]$Arguments)
        $ErrorActionPreference = 'Continue'
        $lines = @(& docker @Arguments 2>&1 | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ Status = $LASTEXITCODE; Text = ($lines -join "`n") }
    }

    function Invoke-Entrypoint {
        <#
        Runs the image's entrypoint in a hardened container, with tmpfs folders in place of the read-only mounts
        /run/lyrveil and /models/llm. LaunchText becomes /run/lyrveil/llm-launch.env unless NoLaunchFile is set, and an
        empty ModelFile is created in /models/llm unless it is empty. Returns Status and Text as Invoke-DockerCapture.
        #>
        param([string]$LaunchText = '', [switch]$NoLaunchFile, [string]$ModelFile = '', [string[]]$Argument = @())
        $encoded = '-'
        if (-not $NoLaunchFile) { $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($LaunchText)) }
        $model = '-'
        if ($ModelFile) { $model = $ModelFile }
        $arguments = $script:Hardening + @('--tmpfs', '/run/lyrveil', '--tmpfs', '/models/llm', '--entrypoint', 'bash',
            $script:Image, '-c', $script:EntrypointScript, 'entrypoint-test', $encoded, $model) + $Argument
        return Invoke-DockerCapture -Arguments $arguments
    }

    function Get-ExpectedCommand {
        # Returns the launcher command of docs/operations.md for the model file Model, with Optional placed between
        # --quantkv and --host as the entrypoint places it.
        param([string]$Model, [string]$Context, [string]$Layers, [string]$Batch, [string]$Kv, [string[]]$Optional = @())
        return @($script:Launcher, '--model', "/models/llm/$Model", '--usecuda', '--contextsize', $Context, '--gpulayers',
            $Layers, '--batchsize', $Batch, '--quantkv', $Kv) + $Optional + @('--host', '0.0.0.0', '--port', '5001', '--skiplauncher', '--quiet')
    }

    Push-Location $script:RepositoryRoot
    try {
        $null = Invoke-Docker @('build', '--quiet', '--file', $script:Dockerfile, '--tag', $script:Image, '.')
    }
    finally {
        Pop-Location
    }
}

Describe 'The build of docker/llm' {
    It 'pins the KoboldCpp version, size and SHA-256 of docs/technology.md' {
        $dockerfile = Get-Content -Raw -LiteralPath (Join-Path $script:RepositoryRoot $script:Dockerfile)
        $row = @(Get-Content -LiteralPath (Join-Path $script:RepositoryRoot 'docs/technology.md') | Where-Object { $_ -like '| KoboldCpp |*' })
        $row.Count | Should -Be 1
        $row[0] -match '`(v[\d.]+)`.*\(([\d,]+) bytes\).*SHA-256 `([0-9a-f]{64})`' | Should -BeTrue
        $version, $size, $hash = $Matches[1], ($Matches[2] -replace ',', ''), $Matches[3]
        $dockerfile | Should -Match "(?m)^ARG KOBOLDCPP_VERSION=$([regex]::Escape($version))$"
        $dockerfile | Should -Match "(?m)^ARG KOBOLDCPP_SIZE=$size$"
        $dockerfile | Should -Match "(?m)^ARG KOBOLDCPP_SHA256=$hash$"
    }

    It 'fails when the downloaded file has another <Name>' -ForEach @(
        @{ Name = 'size'; BuildArg = 'KOBOLDCPP_SIZE=628981943'; Message = 'koboldcpp-linux-x64 is 628981944 bytes, expected 628981943' }
        @{ Name = 'SHA-256'; BuildArg = "KOBOLDCPP_SHA256=$('0' * 64)"; Message = 'koboldcpp-linux-x64: FAILED' }
    ) {
        Push-Location $script:RepositoryRoot
        try {
            $run = Invoke-DockerCapture @('build', '--progress', 'plain', '--target', 'download', '--build-arg', $BuildArg,
                '--file', $script:Dockerfile, '.')
        }
        finally {
            Pop-Location
        }
        $run.Status | Should -Not -Be 0
        $run.Text | Should -Match ([regex]::Escape($Message))
    }
}

Describe 'The image lyrveil/llm:test' {
    It 'runs as 10001:10001 and starts with the entrypoint' {
        (Invoke-Docker @('image', 'inspect', '--format', '{{.Config.User}}', $script:Image)) -join '' | Should -Be '10001:10001'
        (Invoke-Docker @('image', 'inspect', '--format', '{{json .Config.Entrypoint}}', $script:Image)) -join '' |
            Should -Be '["/usr/local/bin/lyrveil-llm-entrypoint"]'
    }

    It 'holds no model, launch file or single-file binary' {
        # As root with only DAC_READ_SEARCH, so every folder of the image can be listed, including the 0700 apt cache
        # folder owned by _apt that the base image ships; -xdev skips /proc, /sys and /tmp.
        $run = Invoke-DockerCapture @('run', '--rm', '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'DAC_READ_SEARCH', '--read-only', '--network', 'none',
            '--entrypoint', 'find', $script:Image, '/', '-xdev', '-type', 'f', '(', '-name', '*.gguf', '-o', '-name', '*.safetensors',
            '-o', '-name', '*.ckpt', '-o', '-name', '*.env', '-o', '-name', 'koboldcpp-linux-x64', ')', '-print')
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -BeNullOrEmpty
    }

    It 'finds a .gguf with the same name filter in a control run, so the listing can fail' {
        $run = Invoke-DockerCapture @('run', '--rm', '--user', '0:0', '--cap-drop', 'ALL', '--read-only', '--network', 'none',
            '--tmpfs', '/models/llm', '--entrypoint', 'sh', $script:Image, '-c',
            ': > /models/llm/probe.gguf && find /models -type f -name ''*.gguf'' -print')
        $run.Text | Should -Be '/models/llm/probe.gguf'
    }
}

Describe 'The unpacked launcher on a read-only root' {
    It 'prints the pinned version 1.124.2' {
        $run = Invoke-DockerCapture ($script:Hardening + @('--entrypoint', $script:Launcher, $script:Image, '--version'))
        $run.Status | Should -Be 0 -Because $run.Text
        @($run.Text -split "`n" | Where-Object { $_.Trim() })[-1].Trim() | Should -Be '1.124.2'
    }

    It 'lists every flag the entrypoint passes in its help' {
        $run = Invoke-DockerCapture ($script:Hardening + @('--entrypoint', $script:Launcher, $script:Image, '--help'))
        $run.Status | Should -Be 0 -Because $run.Text
        foreach ($flag in $script:ContractFlags) {
            $run.Text | Should -Match "(^|[\s\[,])$([regex]::Escape($flag))([\s\],=]|$)" -Because "the launcher must accept $flag"
        }
    }

    It 'mounts the root read-only and /tmp as a writable tmpfs' {
        $mounts = @(Invoke-Docker ($script:Hardening + @('--entrypoint', 'cat', $script:Image, '/proc/mounts')) |
                ForEach-Object { $fields = @($_ -split ' '); [pscustomobject]@{ Target = $fields[1]; Type = $fields[2]; Options = $fields[3] } })
        @($mounts | Where-Object Target -EQ '/')[-1].Options | Should -Match '^ro(,|$)'
        $tmp = @($mounts | Where-Object Target -EQ '/tmp')[-1]
        $tmp.Type | Should -Be 'tmpfs'
        $tmp.Options | Should -Match '^rw(,|$)'
    }

    It 'refuses a write to the root even from root, and accepts one in /tmp' {
        # Root owns /opt/koboldcpp, so only the read-only mount, not a file mode, can refuse the first write.
        $run = Invoke-DockerCapture @('run', '--rm', '--user', '0:0', '--cap-drop', 'ALL', '--read-only', '--tmpfs', '/tmp',
            '--network', 'none', '--entrypoint', 'sh', $script:Image, '-c',
            'touch /opt/koboldcpp/probe 2>/dev/null && echo root-writable || echo root-read-only; touch /tmp/probe && echo tmp-writable')
        (@($run.Text -split "`n" | ForEach-Object { $_.Trim() }) -join ',') | Should -Be 'root-read-only,tmp-writable'
    }
}

Describe 'The entrypoint' {
    It 'turns the example launch file into the documented command' {
        $run = Invoke-Entrypoint -LaunchText $script:ExampleLaunchText -ModelFile 'gemma-4-26B_q4_0-it.gguf' -Argument @('--print-command')
        $run.Status | Should -Be 0 -Because $run.Text
        ($run.Text -split "`n") -join ' ' |
            Should -BeExactly ((Get-ExpectedCommand -Model 'gemma-4-26B_q4_0-it.gguf' -Context '16384' -Layers '-1' -Batch '512' -Kv 'f16') -join ' ')
    }

    It 'adds the optional flags only when their keys ask for them' {
        $text = (@('# A comment', '', 'MODEL_PROFILE=custom-0123456789ab', 'MODEL_FILE=custom.Q4_K_M.gguf', 'CONTEXT_SIZE=8192',
                'GPU_LAYERS=40', 'BATCH_SIZE=2048', 'MOE_CPU_LAYERS=12', 'USE_MMAP=1', 'KV_QUANT=q8_0', 'SWA_PADDING=1024',
                'SMART_CACHE=2', 'LEVEL=CUSTOM') -join "`n")
        $run = Invoke-Entrypoint -LaunchText $text -ModelFile 'custom.Q4_K_M.gguf' -Argument @('--print-command')
        $run.Status | Should -Be 0 -Because $run.Text
        $expected = Get-ExpectedCommand -Model 'custom.Q4_K_M.gguf' -Context '8192' -Layers '40' -Batch '2048' -Kv 'q8_0' `
            -Optional @('--moecpu', '12', '--usemmap', '--swapadding', '1024', '--smartcache', '2')
        ($run.Text -split "`n") -join ' ' | Should -BeExactly ($expected -join ' ')
    }

    It 'exits 78 for <Name> and names the key, never the value' -ForEach @(
        @{ Name = 'an unknown key'; Lines = $script:ValidLines + 'EXTRA_FLAGS=--admin'; NewLine = "`n"; Message = 'unknown key EXTRA_FLAGS' }
        @{ Name = 'a value outside the allowed set'; Lines = $script:ValidLines -replace '^LEVEL=A$', 'LEVEL=D'; NewLine = "`n"; Message = 'invalid value for LEVEL' }
        @{ Name = 'a repeated key'; Lines = $script:ValidLines + 'LEVEL=B'; NewLine = "`n"; Message = 'repeats the key LEVEL' }
        @{ Name = 'a missing key'; Lines = @($script:ValidLines | Where-Object { $_ -notlike 'LEVEL=*' }); NewLine = "`n"; Message = 'has no LEVEL' }
        @{ Name = 'Windows line endings'; Lines = $script:ValidLines; NewLine = "`r`n"; Message = 'invalid value for MODEL_PROFILE' }
        @{ Name = 'a command substitution'; Lines = $script:ValidLines -replace '^MODEL_FILE=.*$', 'MODEL_FILE=$(id).gguf'; NewLine = "`n"; Message = 'invalid value for MODEL_FILE' }
        @{ Name = 'a path in MODEL_FILE'; Lines = $script:ValidLines -replace '^MODEL_FILE=.*$', 'MODEL_FILE=../test-model.gguf'; NewLine = "`n"; Message = 'invalid value for MODEL_FILE' }
        @{ Name = 'a line that is not KEY=VALUE'; Lines = $script:ValidLines + 'export LEVEL'; NewLine = "`n"; Message = 'is not KEY=VALUE' }
    ) {
        $run = Invoke-Entrypoint -LaunchText (($Lines -join $NewLine) + $NewLine) -ModelFile 'test-model.gguf' -Argument @('--print-command')
        $run.Status | Should -Be 78 -Because $run.Text
        $run.Text | Should -Match ([regex]::Escape($Message))
        $run.Text | Should -Not -Match '\$\(id\)|--admin|\.\./'
    }

    It 'exits 78 when the launch file is missing' {
        $run = Invoke-Entrypoint -NoLaunchFile -ModelFile 'gemma-4-26B_q4_0-it.gguf'
        $run.Status | Should -Be 78 -Because $run.Text
        $run.Text | Should -Match 'the launch file /run/lyrveil/llm-launch\.env is missing'
    }

    It 'exits 78 when the model file is missing' {
        $run = Invoke-Entrypoint -LaunchText $script:ExampleLaunchText
        $run.Status | Should -Be 78 -Because $run.Text
        $run.Text | Should -Match 'the model file named by MODEL_FILE is missing from /models/llm'
    }

    It 'exits 64 for an unknown argument' {
        $run = Invoke-Entrypoint -LaunchText $script:ExampleLaunchText -ModelFile 'gemma-4-26B_q4_0-it.gguf' -Argument @('--admin')
        $run.Status | Should -Be 64 -Because $run.Text
    }
}
