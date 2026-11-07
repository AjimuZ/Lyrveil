# Docker-level tests of the image runtime image docker/image (P05.1, docs/operations.md, Runtime images): the Dockerfile
# pins the ComfyUI commit of docs/technology.md, requirements.lock pins every package with hashes and PyTorch for CUDA
# 13.0, the image runs as 10001:10001 and holds no model or launch file, ComfyUI starts in CPU mode on a read-only root
# with only the tmpfs /tmp/comfy of compose.yaml and no network, and answers GET /system_stats of
# docs/runtime-contracts.md, the entrypoint turns a launch file into the documented command or exits 78, and the
# clip_tokens of the tag vocabulary match the tokenizer of the pinned tree (T-IMG-16). No test
# needs a GPU or a model. The image is tagged "lyrveil/image:test", so the ":local" image of an installation is never
# replaced. The first run downloads ComfyUI and about 4 GB of wheels; later runs reuse Docker's build cache.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $script:Image = 'lyrveil/image:test'
    $script:Dockerfile = 'docker/image/Dockerfile'
    $script:LockFile = Join-Path $script:RepositoryRoot 'docker/image/requirements.lock'
    # The container settings of lyrveil-image in compose.yaml that apply without a GPU: init, the user, no capabilities,
    # no new privileges, a read-only root and the 4 GiB tmpfs /tmp/comfy. "--network none" stands in for the internal
    # network, and a tmpfs /run/lyrveil for the read-only launch file mount.
    $script:Hardening = @('run', '--rm', '--init', '--user', '10001:10001', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--read-only', '--network', 'none',
        '--mount', 'type=tmpfs,destination=/tmp/comfy,tmpfs-size=4294967296', '--tmpfs', '/run/lyrveil')
    $script:ExampleLaunchText = [System.IO.File]::ReadAllText((Join-Path $script:RepositoryRoot 'config/examples/image-launch.env'))
    # Writes the launch file, unless the first argument is "-", then runs the entrypoint with the other arguments.
    # Single quotes only, so Windows PowerShell passes it to docker unchanged.
    $script:EntrypointScript = 'if [ x$1 != x- ]; then printf %s $1 | base64 -d > /run/lyrveil/image-launch.env; fi; ' +
        'shift; exec /usr/local/bin/lyrveil-image-entrypoint $@'
    # Writes the launch file, starts the entrypoint in CPU mode in the background, and pipes the poll script, the
    # second argument, to Python. The server's log is printed only when the poll fails.
    $script:ServeScript = 'printf %s $1 | base64 -d > /run/lyrveil/image-launch.env; ' +
        '/usr/local/bin/lyrveil-image-entrypoint --cpu > /tmp/comfy/server.log 2>&1 & server=$!; ' +
        'printf %s $2 | base64 -d | python - $server; status=$?; ' +
        'if [ $status -ne 0 ]; then cat /tmp/comfy/server.log >&2; fi; exit $status'
    # Prints the body of GET /system_stats once ComfyUI answers, and exits 1 when the server process ends first or
    # after three minutes, the start_period of the health check in compose.yaml.
    $script:PollScript = @'
import os, sys, time, urllib.request
for _ in range(180):
    if not os.path.exists('/proc/' + sys.argv[1]):
        sys.exit(1)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=2) as response:
            sys.stdout.write(response.read().decode())
        sys.exit(0)
    except OSError:
        time.sleep(1)
sys.exit(1)
'@

    function ConvertTo-Base64 {
        # Returns Text as UTF-8 in base64, so it reaches the container as one argument without quotes.
        param([string]$Text)
        return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Text))
    }

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
        # Runs the image's entrypoint in a hardened container. LaunchText becomes /run/lyrveil/image-launch.env unless
        # NoLaunchFile is set. Returns Status and Text as Invoke-DockerCapture.
        param([string]$LaunchText = '', [switch]$NoLaunchFile, [string[]]$Argument = @())
        $encoded = '-'
        if (-not $NoLaunchFile) { $encoded = ConvertTo-Base64 -Text $LaunchText }
        $arguments = $script:Hardening + @('--entrypoint', 'bash', $script:Image, '-c', $script:EntrypointScript,
            'entrypoint-test', $encoded) + $Argument
        return Invoke-DockerCapture -Arguments $arguments
    }

    function Get-ExpectedCommand {
        # Returns the ComfyUI command of docs/operations.md for the reserve Reserve, followed by Extra.
        param([string]$Reserve, [string[]]$Extra = @())
        return @('python', '/opt/ComfyUI/main.py', '--listen', '0.0.0.0', '--port', '8188', '--disable-auto-launch', '--offline',
            '--disable-all-custom-nodes', '--disable-metadata', '--preview-method', 'none', '--log-stdout',
            '--output-directory', '/tmp/comfy/output', '--input-directory', '/tmp/comfy/input',
            '--temp-directory', '/tmp/comfy/temp', '--user-directory', '/tmp/comfy/user', '--reserve-vram', $Reserve) + $Extra
    }

    function Get-LockRequirement {
        # Returns the requirements of requirements.lock, each on one line: continuation lines joined, comments and blank
        # lines dropped. The space before each continuation backslash goes with it, so the parts are joined by one space.
        $text = [System.IO.File]::ReadAllText($script:LockFile) -replace '\s*\\\r?\n\s*', ' '
        return @($text -split "\r?\n" | ForEach-Object { ($_ -replace '\s+#.*$', '').Trim() } |
                Where-Object { $_ -and -not $_.StartsWith('#') })
    }

    Push-Location $script:RepositoryRoot
    try {
        $null = Invoke-Docker @('build', '--quiet', '--file', $script:Dockerfile, '--tag', $script:Image, '.')
    }
    finally {
        Pop-Location
    }
}

Describe 'The pins of docker/image' {
    It 'clones the ComfyUI commit of docs/technology.md' {
        $row = @(Get-Content -LiteralPath (Join-Path $script:RepositoryRoot 'docs/technology.md') | Where-Object { $_ -like '| ComfyUI |*' })
        $row.Count | Should -Be 1
        $row[0] -match 'commit `([0-9a-f]{40})`' | Should -BeTrue
        $dockerfile = Get-Content -Raw -LiteralPath (Join-Path $script:RepositoryRoot $script:Dockerfile)
        $dockerfile | Should -Match "(?m)^ARG COMFYUI_COMMIT=$($Matches[1])$"
        $dockerfile | Should -Match '(?m)^ARG COMFYUI_REPOSITORY=https://github\.com/Comfy-Org/ComfyUI\.git$'
    }

    It 'pins every package of requirements.lock to one version with at least one SHA-256' {
        $requirements = @(Get-LockRequirement)
        $requirements.Count | Should -BeGreaterThan 50 -Because 'an empty lock would pass for the wrong reason'
        foreach ($requirement in $requirements) {
            $requirement | Should -Match '^[a-z0-9][a-z0-9._-]*==[A-Za-z0-9.+!_-]+( --hash=sha256:[0-9a-f]{64})+$'
        }
    }

    It 'takes PyTorch and torchvision from the CUDA 13.0 builds' {
        $requirements = @(Get-LockRequirement)
        foreach ($name in @('torch', 'torchvision')) {
            @($requirements | Where-Object { $_ -match "^$name==[^ ]+\+cu130 " }).Count | Should -Be 1 -Because "$name must be a +cu130 build"
        }
    }
}

Describe 'The image lyrveil/image:test' {
    It 'runs as 10001:10001 and starts with the entrypoint' {
        (Invoke-Docker @('image', 'inspect', '--format', '{{.Config.User}}', $script:Image)) -join '' | Should -Be '10001:10001'
        (Invoke-Docker @('image', 'inspect', '--format', '{{json .Config.Entrypoint}}', $script:Image)) -join '' |
            Should -Be '["/usr/local/bin/lyrveil-image-entrypoint"]'
    }

    It 'holds no model, launch file or .git folder' {
        # As root with only DAC_READ_SEARCH, so every folder of the image can be listed; -xdev skips /proc, /sys and /tmp.
        $run = Invoke-DockerCapture @('run', '--rm', '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'DAC_READ_SEARCH', '--read-only',
            '--network', 'none', '--entrypoint', 'find', $script:Image, '/', '-xdev', '(', '(', '-type', 'f', '(', '-name', '*.gguf',
            '-o', '-name', '*.safetensors', '-o', '-name', '*.sft', '-o', '-name', '*.ckpt', '-o', '-name', '*.pt', '-o', '-name', '*.pth',
            '-o', '-name', '*.env', ')', ')', '-o', '(', '-type', 'd', '-name', '.git', ')', ')', '-print')
        $run.Status | Should -Be 0 -Because $run.Text
        # A .pth file at the top of site-packages is a Python path file, such as setuptools' distutils-precedence.pth.
        @($run.Text -split "`n" | Where-Object { $_ -and $_ -notmatch '^/opt/venv/lib/python3\.12/site-packages/[^/]+\.pth$' }) |
            Should -BeNullOrEmpty
    }

    It 'keeps only the upstream placeholder in the checkpoints folder, the mount point of the models volume' {
        $run = Invoke-DockerCapture @('run', '--rm', '--network', 'none', '--entrypoint', 'ls', $script:Image, '-A',
            '/opt/ComfyUI/models/checkpoints')
        $run.Status | Should -Be 0 -Because $run.Text
        $run.Text | Should -Be 'put_checkpoints_here'
    }
}

Describe 'ComfyUI in CPU mode on a read-only root' {
    BeforeAll {
        $arguments = $script:Hardening + @('--entrypoint', 'bash', $script:Image, '-c', $script:ServeScript, 'serve-test',
            (ConvertTo-Base64 -Text $script:ExampleLaunchText), (ConvertTo-Base64 -Text $script:PollScript))
        $script:Serve = Invoke-DockerCapture -Arguments $arguments
    }

    It 'answers GET /system_stats with the pinned version, the CUDA 13.0 build of PyTorch and the CPU device' {
        $script:Serve.Status | Should -Be 0 -Because $script:Serve.Text
        $stats = $script:Serve.Text | ConvertFrom-Json
        $stats.system.comfyui_version | Should -Be '0.43.0'
        $stats.system.pytorch_version | Should -BeLike '*+cu130'
        @($stats.devices)[0].type | Should -Be 'cpu'
    }

    It 'started with the flags of docs/runtime-contracts.md and --cpu' {
        $stats = $script:Serve.Text | ConvertFrom-Json
        # ComfyUI reports sys.argv, which is the command without the interpreter.
        $expected = @(Get-ExpectedCommand -Reserve '1.5' -Extra @('--cpu') | Select-Object -Skip 1)
        (@($stats.system.argv) -join ' ') | Should -BeExactly ($expected -join ' ')
    }

    It 'mounts the root read-only and /tmp/comfy as a writable tmpfs' {
        $mounts = @(Invoke-Docker ($script:Hardening + @('--entrypoint', 'cat', $script:Image, '/proc/mounts')) |
                ForEach-Object { $fields = @($_ -split ' '); [pscustomobject]@{ Target = $fields[1]; Type = $fields[2]; Options = $fields[3] } })
        @($mounts | Where-Object Target -EQ '/')[-1].Options | Should -Match '^ro(,|$)'
        $state = @($mounts | Where-Object Target -EQ '/tmp/comfy')[-1]
        $state.Type | Should -Be 'tmpfs'
        $state.Options | Should -Match '^rw(,|$)'
        @($mounts | Where-Object Target -EQ '/tmp') | Should -BeNullOrEmpty -Because '/tmp itself stays read-only'
    }
}

Describe 'The tag vocabulary on the tokenizer of the pinned tree' {
    It 'records the CLIP token count ComfyUI gives every entry (T-IMG-16)' {
        # The build stage reuses the environment of the image; "cacheonly" skips exporting its 4 GB of layers.
        Push-Location $script:RepositoryRoot
        try {
            $run = Invoke-DockerCapture @('build', '--quiet', '--file', $script:Dockerfile, '--target', 'clip-tokens-check',
                '--output', 'type=cacheonly', '.')
        }
        finally {
            Pop-Location
        }
        $run.Status | Should -Be 0 -Because $run.Text
    }
}

Describe 'The entrypoint' {
    It 'turns the example launch file into the documented command' {
        $run = Invoke-Entrypoint -LaunchText $script:ExampleLaunchText -Argument @('--print-command')
        $run.Status | Should -Be 0 -Because $run.Text
        ($run.Text -split "`n") -join ' ' | Should -BeExactly ((Get-ExpectedCommand -Reserve '1.5') -join ' ')
    }

    It 'accepts the reserve <Value> and adds --cpu only when asked' -ForEach @(
        @{ Value = '0' }
        @{ Value = '0.5' }
        @{ Value = '4' }
    ) {
        $run = Invoke-Entrypoint -LaunchText "# A comment`n`nRESERVE_VRAM_GB=$Value" -Argument @('--cpu', '--print-command')
        $run.Status | Should -Be 0 -Because $run.Text
        ($run.Text -split "`n") -join ' ' | Should -BeExactly ((Get-ExpectedCommand -Reserve $Value -Extra @('--cpu')) -join ' ')
    }

    It 'exits 78 for <Name> and names the key, never the value' -ForEach @(
        @{ Name = 'an unknown key'; Text = "RESERVE_VRAM_GB=1.5`nEXTRA_FLAGS=--enable-manager`n"; Message = 'unknown key EXTRA_FLAGS' }
        @{ Name = 'a value above 4'; Text = "RESERVE_VRAM_GB=4.5`n"; Message = 'invalid value for RESERVE_VRAM_GB' }
        @{ Name = 'a value off the 0.5 step'; Text = "RESERVE_VRAM_GB=1.25`n"; Message = 'invalid value for RESERVE_VRAM_GB' }
        @{ Name = 'a command substitution'; Text = "RESERVE_VRAM_GB=`$(id)`n"; Message = 'invalid value for RESERVE_VRAM_GB' }
        @{ Name = 'a repeated key'; Text = "RESERVE_VRAM_GB=1.5`nRESERVE_VRAM_GB=2`n"; Message = 'repeats the key RESERVE_VRAM_GB' }
        @{ Name = 'a missing key'; Text = "# Nothing else`n"; Message = 'has no RESERVE_VRAM_GB' }
        @{ Name = 'Windows line endings'; Text = "RESERVE_VRAM_GB=1.5`r`n"; Message = 'invalid value for RESERVE_VRAM_GB' }
        @{ Name = 'a line that is not KEY=VALUE'; Text = "RESERVE_VRAM_GB=1.5`nexport RESERVE_VRAM_GB`n"; Message = 'is not KEY=VALUE' }
    ) {
        $run = Invoke-Entrypoint -LaunchText $Text -Argument @('--print-command')
        $run.Status | Should -Be 78 -Because $run.Text
        $run.Text | Should -Match ([regex]::Escape($Message))
        $run.Text | Should -Not -Match '\$\(id\)|--enable-manager|1\.25|4\.5'
    }

    It 'exits 78 when the launch file is missing' {
        $run = Invoke-Entrypoint -NoLaunchFile
        $run.Status | Should -Be 78 -Because $run.Text
        $run.Text | Should -Match 'the launch file /run/lyrveil/image-launch\.env is missing'
    }

    It 'exits 64 for <Name>' -ForEach @(
        @{ Name = 'an unknown argument'; Argument = @('--enable-manager') }
        @{ Name = 'a repeated argument'; Argument = @('--cpu', '--cpu') }
    ) {
        $run = Invoke-Entrypoint -LaunchText $script:ExampleLaunchText -Argument $Argument
        $run.Status | Should -Be 64 -Because $run.Text
    }
}
