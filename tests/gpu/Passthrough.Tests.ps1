# T-GPU-01: the real text and image runtimes of compose.yaml reach the GPU, started one after the other in the project lyrveil-gpu-test.
#
# Run only by "scripts\lyrveil.cmd test gpu": the level builds the ":gpu-test" images, creates the data volume
# lyrveil-gpu-test-data with the launch files, runs this file, then removes the project and the volume whatever happened.
# Never both runtimes at once: the image runtime starts only after the text runtime is removed and its VRAM released.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    $script:Setting = Get-GpuTestSetting
    $script:TextProfile = Get-GpuTestProfile -Root $script:Root
    $script:Card = Get-GpuSample

    function Get-MountProblem {
        # Returns a message for every mount of Service that is not a tmpfs or a read-only mount of the test data volume
        # or the models volume, and one when the models volume is not mounted at all.
        param([string]$Service)
        $mounts = @(Get-GpuTestContainerFact -Root $script:Root -Service $Service -Template '{{json .Mounts}}')
        $problems = @()
        foreach ($mount in $mounts) {
            if ($mount.Type -eq 'tmpfs') { continue }
            if ($mount.Type -ne 'volume') { $problems += "a $($mount.Type) mount at $($mount.Destination)"; continue }
            if (@($script:Setting.DataVolume, $script:Setting.ModelsVolume) -notcontains $mount.Name) {
                $problems += "the volume $($mount.Name) at $($mount.Destination)"
            }
            elseif ($mount.RW) { $problems += "$($mount.Name) writable at $($mount.Destination)" }
        }
        if (@($mounts | Where-Object { $_.Type -eq 'volume' -and $_.Name -eq $script:Setting.ModelsVolume }).Length -eq 0) {
            $problems += "no mount of $($script:Setting.ModelsVolume)"
        }
        return $problems
    }

    function Test-GpuRequest {
        # Returns $true when the container of Service asked Docker for every GPU, as "gpus: all" of compose.yaml does.
        param([string]$Service)
        $requests = @(Get-GpuTestContainerFact -Root $script:Root -Service $Service -Template '{{json .HostConfig.DeviceRequests}}')
        foreach ($request in $requests) {
            if ($null -eq $request) { continue }
            $capabilities = @($request.Capabilities | ForEach-Object { $_ })
            # The property, not the Count that PowerShell adds to every object.
            $count = $request.PSObject.Properties['Count'].Value
            if ($count -eq -1 -and $capabilities -contains 'gpu') { return $true }
        }
        return $false
    }

    function Test-ModelWrite {
        # Tries to create a file in Folder inside the container of Service and returns the exit status of the attempt,
        # which must not be 0 for a read-only mount.
        param([string]$Service, [string]$Folder)
        return (Invoke-GpuCompose -Root $script:Root -Argument @('exec', '-T', $Service, 'touch', "$Folder/.lyrveil-gpu-write-test")).Status
    }
}

Describe 'T-GPU-01: GPU passthrough' {
    It 'has a GPU to test' {
        $script:Card.Available | Should -BeTrue -Because $script:Card.Reason
    }

    Context 'lyrveil-llm, the text runtime, with the smallest catalog text profile on the GPU' {
        BeforeAll {
            $script:LlmBefore = Get-GpuSample
            $script:LlmUp = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
            $script:LlmAfter = Get-GpuSample
        }

        AfterAll {
            $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-llm'
        }

        It 'becomes healthy with the GPU request of compose.yaml' {
            $script:LlmUp | Should -Be 0
            Test-GpuRequest 'lyrveil-llm' | Should -BeTrue
        }

        It 'mounts the models read-only and no volume but the test ones' {
            Get-MountProblem 'lyrveil-llm' | Should -BeNullOrEmpty
            Test-ModelWrite 'lyrveil-llm' '/models/llm' | Should -Not -Be 0
        }

        It 'serves the model of the launch file over its HTTP API' {
            $answer = Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-llm' -Path '/api/v1/model'
            $answer.Status | Should -Be 0
            $answer.Body.result | Should -Be ('koboldcpp/' + [System.IO.Path]::GetFileNameWithoutExtension($script:TextProfile.file))
        }

        It 'finds the host GPU through CUDA' {
            $logs = (Invoke-GpuCompose -Root $script:Root -Argument @('logs', '--no-color', 'lyrveil-llm') -Capture).Output -join "`n"
            $logs | Should -Match ([regex]::Escape($script:Card.Name))
        }

        It 'holds at least half of the model file in VRAM, as NVML shows' {
            $grownMiB = $script:LlmAfter.UsedMiB - $script:LlmBefore.UsedMiB
            Write-Host "    NVML: $($script:LlmBefore.UsedMiB) MiB before, $($script:LlmAfter.UsedMiB) MiB with $($script:TextProfile.id) loaded"
            $grownMiB | Should -BeGreaterThan ([int]([long]$script:TextProfile.bytes / 1MB / 2))
        }
    }

    Context 'lyrveil-image, the image runtime, after the text runtime is gone' {
        BeforeAll {
            $script:Released = Wait-GpuMemoryBelow -LimitMiB ($script:LlmBefore.UsedMiB + 1024) -TimeoutSecond 60
            $script:LlmLeft = @((Invoke-GpuCompose -Root $script:Root -Argument @('ps', '--all', '--quiet', 'lyrveil-llm') -Capture).Output |
                    Where-Object { $_.Trim() })
            $script:ImageBefore = Get-GpuSample
            $script:ImageUp = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
            $script:ImageAfter = Get-GpuSample
        }

        AfterAll {
            $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
        }

        It 'starts only after the text runtime is removed and its VRAM released' {
            $script:LlmLeft | Should -BeNullOrEmpty
            $script:Released | Should -BeTrue
        }

        It 'becomes healthy with the GPU request of compose.yaml' {
            $script:ImageUp | Should -Be 0
            Test-GpuRequest 'lyrveil-image' | Should -BeTrue
        }

        It 'mounts the checkpoints read-only and no volume but the test ones' {
            Get-MountProblem 'lyrveil-image' | Should -BeNullOrEmpty
            Test-ModelWrite 'lyrveil-image' '/opt/ComfyUI/models/checkpoints' | Should -Not -Be 0
        }

        It 'reports the host GPU as its CUDA device in /system_stats' {
            $answer = Invoke-GpuRuntimeRequest -Root $script:Root -Service 'lyrveil-image' -Path '/system_stats'
            $answer.Status | Should -Be 0
            $device = @($answer.Body.devices)[0]
            $device.type | Should -Be 'cuda'
            $device.name | Should -Match ([regex]::Escape($script:Card.Name))
            Write-Host "    ComfyUI: $($device.name), $([long]$device.vram_total / 1MB) MiB; NVML: $($script:Card.TotalMiB) MiB"
            [math]::Abs(([long]$device.vram_total / 1MB) - $script:Card.TotalMiB) | Should -BeLessThan 128
        }

        It 'holds a CUDA context in VRAM, as NVML shows' {
            Write-Host "    NVML: $($script:ImageBefore.UsedMiB) MiB before, $($script:ImageAfter.UsedMiB) MiB with ComfyUI started"
            ($script:ImageAfter.UsedMiB - $script:ImageBefore.UsedMiB) | Should -BeGreaterThan 100
        }
    }
}
