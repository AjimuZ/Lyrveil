# Tests of scripts/lib/GpuResilience.psm1: the readers of docker top, the host process probe, the route probe and the orchestrator's log, and the judges of T-GPU-11 and T-GPU-12, with the launch lines and CUDA request of T-GPU-10.

BeforeAll {
    Set-StrictMode -Version Latest
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\GpuResilience.psm1') -Force

    # Routing tables as the probe prints them. A container on an internal network has only its subnet; one on a
    # bridge with a route out also has the default route. Every container has the IPv6 reject route on lo.
    $script:InternalRoute = @(
        "route:Iface`tDestination`tGateway `tFlags`tRefCnt`tUse`tMetric`tMask`t`tMTU`tWindow`tIRTT",
        "route:eth0`t0000FEAC`t00000000`t0001`t0`t0`t0`t0000FFFF`t0`t0`t0")
    $script:BridgeRoute = @(
        'route:Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT',
        'route:eth0 00000000 010011AC 0003 0 0 0 00000000 0 0 0',
        'route:eth0 000011AC 00000000 0001 0 0 0 0000FFFF 0 0 0')
    $zero = '0' * 32
    $script:LoopbackRoute6 = "route6:$zero 00 $zero 00 $zero ffffffff 00000001 00000000 00200200 lo"
    $script:Isolated = @($script:InternalRoute) + @($script:LoopbackRoute6, 'connect=unreachable', 'resolve=unresolved')

    function Get-NetworkRecord {
        # Returns one record of T-GPU-12 as the test builds it: a known container on one internal network, publishing
        # nothing, with the probe of Line.
        param([string[]]$Line = $script:Isolated, [bool]$Internal = $true, [string[]]$Published = @())
        return @{
            Service = 'lyrveil-backend'; Known = $true; Published = $Published
            Networks = @(@{ Name = 'lyrveil-gpu-test_lyrveil-app'; Internal = $Internal })
            Probe = (ConvertFrom-GpuRouteProbe -Line $Line)
        }
    }

    $script:Listed = @(
        [pscustomobject]@{ Service = 'lyrveil-llm'; Pid = 100; Command = '/sbin/docker-init -- /usr/local/bin/lyrveil-llm-entrypoint' },
        [pscustomobject]@{ Service = 'lyrveil-llm'; Pid = 101; Command = '/opt/koboldcpp/koboldcpp-launcher --model /models/llm/a.gguf' },
        [pscustomobject]@{ Service = 'lyrveil-backend'; Pid = 200; Command = 'python -m lyrveil.main' },
        [pscustomobject]@{ Service = 'lyrveil-backend'; Pid = 201; Command = "python -c import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)" })
    # The probe saw the long-lived processes; the health check had ended already.
    $script:SeenBefore = @($script:Listed | Where-Object { $_.Pid -ne 201 } | ForEach-Object { [pscustomobject]@{ Pid = $_.Pid; Command = $_.Command } })

    function Get-State {
        # Returns the state of one container after the stop: exited 143 by default.
        param([string]$Service = 'lyrveil-llm', [bool]$Running = $false, [string]$Status = 'exited', [bool]$Ran = $true,
            [string]$StartedAt = '2026-11-26T18:32:00Z', [bool]$OOMKilled = $false, $ExitCode = 143)
        return [ordered]@{
            Service = $Service; Known = $true; Ran = $Ran; Running = $Running; Restarting = $false; Status = $Status
            ExitCode = $ExitCode; OOMKilled = $OOMKilled; StartedAt = $StartedAt; FinishedAt = '2026-11-26T18:33:00Z'
        }
    }
}

Describe 'ConvertFrom-DockerTopText' {
    It 'reads the PID and the whole command of each process line and leaves out the header' {
        $processes = @(ConvertFrom-DockerTopText -Line @(
                'UID                 PID                 PPID                C                   STIME               TTY                 TIME                CMD',
                '10001               13187               13166               0                   18:30               ?                   00:00:00            /sbin/docker-init -- python -m lyrveil.main',
                '10001               13210               13187               2                   18:30               ?                   00:00:04            python -m lyrveil.main'))
        $processes.Length | Should -Be 2
        $processes[0].Pid | Should -Be 13187
        $processes[0].Command | Should -Be '/sbin/docker-init -- python -m lyrveil.main'
        $processes[1].Command | Should -Be 'python -m lyrveil.main'
    }

    It 'gives no process for an error text' {
        @(ConvertFrom-DockerTopText -Line @('Error response from daemon: container 1234 is not running')).Length | Should -Be 0
    }
}

Describe 'ConvertFrom-GpuHostProcessText' {
    It 'reads "<pid> <command line>" lines and leaves out the others' {
        $processes = @(ConvertFrom-GpuHostProcessText -Line @('27904 /opt/koboldcpp/koboldcpp-launcher --model /models/llm/a.gguf', 'Traceback (most recent call last):', ''))
        $processes.Length | Should -Be 1
        $processes[0].Pid | Should -Be 27904
        $processes[0].Command | Should -Be '/opt/koboldcpp/koboldcpp-launcher --model /models/llm/a.gguf'
    }
}

Describe 'ConvertFrom-GpuRouteProbe' {
    It 'reads a container of an internal network as isolated, tabs or spaces alike' {
        $probe = ConvertFrom-GpuRouteProbe -Line $script:Isolated
        $probe.DefaultRoute | Should -BeFalse
        $probe.Connect | Should -Be 'unreachable'
        $probe.Resolve | Should -Be 'unresolved'
        $probe.Complete | Should -BeTrue
    }

    It 'sees the default route of a bridge network' {
        (ConvertFrom-GpuRouteProbe -Line $script:BridgeRoute).DefaultRoute | Should -BeTrue
    }

    It 'sees an IPv6 default route on an interface other than lo' {
        $zero = '0' * 32
        $line = @($script:InternalRoute) + @("route6:$zero 00 $zero 00 fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0")
        (ConvertFrom-GpuRouteProbe -Line $line).DefaultRoute | Should -BeTrue
    }

    It 'is not complete without the connection or the lookup line' {
        (ConvertFrom-GpuRouteProbe -Line (@($script:InternalRoute) + @('connect=unreachable'))).Complete | Should -BeFalse
        (ConvertFrom-GpuRouteProbe -Line @('connect=unreachable', 'resolve=unresolved')).Complete | Should -BeFalse
    }
}

Describe 'Get-GpuOfflineProblem' {
    It 'finds nothing for a known container on internal networks with an isolated probe' {
        Get-GpuOfflineProblem -Network @(Get-NetworkRecord) | Should -BeNullOrEmpty
    }

    It 'names a network that is not internal and a published port' {
        $problems = @(Get-GpuOfflineProblem -Network @(Get-NetworkRecord -Internal $false -Published @('8000/tcp on 127.0.0.1:8000')))
        $problems.Length | Should -Be 2
        ($problems -join ' ') | Should -Match 'not internal'
        ($problems -join ' ') | Should -Match 'publishes 8000/tcp'
    }

    It 'names a default route, a connection out and a resolved name' {
        $problems = @(Get-GpuOfflineProblem -Network @(Get-NetworkRecord -Line (@($script:BridgeRoute) + @('connect=connected', 'resolve=resolved'))))
        $problems.Length | Should -Be 3
    }

    It 'refuses a probe that did not report everything, an unknown container and one with no network' {
        @(Get-GpuOfflineProblem -Network @(Get-NetworkRecord -Line @('connect=unreachable'))).Length | Should -Be 1
        @(Get-GpuOfflineProblem -Network @(@{ Service = 'lyrveil-llm'; Known = $false })).Length | Should -Be 1
        $empty = Get-NetworkRecord
        $empty.Networks = @()
        @(Get-GpuOfflineProblem -Network @($empty)) | Should -Match 'joins no network'
        @(Get-GpuOfflineProblem -Network @()).Length | Should -Be 1
    }

    It 'judges a record without a probe on its networks alone' {
        $record = Get-NetworkRecord
        $record.Probe = $null
        Get-GpuOfflineProblem -Network @($record) | Should -BeNullOrEmpty
    }
}

Describe 'Get-GpuOrphanProblem' {
    It 'finds nothing when the probe saw the processes before the stop and none after' {
        Get-GpuOrphanProblem -Listed $script:Listed -SeenBefore $script:SeenBefore -SeenAfter @() | Should -BeNullOrEmpty
    }

    It 'names a process of the stack that still runs after the stop' {
        $problems = @(Get-GpuOrphanProblem -Listed $script:Listed -SeenBefore $script:SeenBefore -SeenAfter @($script:SeenBefore[2]))
        $problems.Length | Should -Be 1
        $problems[0] | Should -Match 'Process 200 of lyrveil-backend still runs'
    }

    It 'ignores a PID that another program took, unless it is a runtime program' {
        Get-GpuOrphanProblem -Listed $script:Listed -SeenBefore $script:SeenBefore -SeenAfter @([pscustomobject]@{ Pid = 200; Command = 'sleep 60' }) | Should -BeNullOrEmpty
        $problems = @(Get-GpuOrphanProblem -Listed $script:Listed -SeenBefore $script:SeenBefore -SeenAfter @([pscustomobject]@{ Pid = 555; Command = 'python /opt/ComfyUI/main.py --listen 0.0.0.0' }))
        $problems.Length | Should -Be 1
        $problems[0] | Should -Match 'runtime process'
    }

    It 'refuses a service of which the probe saw no process before the stop' {
        $problems = @(Get-GpuOrphanProblem -Listed $script:Listed -SeenBefore @($script:SeenBefore | Where-Object { $_.Pid -ge 200 }) -SeenAfter @())
        $problems.Length | Should -Be 1
        $problems[0] | Should -Match 'no process of lyrveil-llm'
    }

    It 'refuses an empty list from docker top' {
        @(Get-GpuOrphanProblem -Listed @() -SeenBefore @() -SeenAfter @()).Length | Should -Be 1
    }
}

Describe 'Get-GpuStoppedProblem' {
    It 'finds nothing for exited containers, one of them never started, that stay stopped' {
        $stopped = @((Get-State), (Get-State -Service 'lyrveil-image' -Ran $false -Status 'created' -StartedAt '0001-01-01T00:00:00Z' -ExitCode 0))
        Get-GpuStoppedProblem -Stopped $stopped -Later $stopped | Should -BeNullOrEmpty
    }

    It 'names a container still running, one killed for memory and one without an exit code' {
        @(Get-GpuStoppedProblem -Stopped @(Get-State -Running $true)).Length | Should -Be 1
        @(Get-GpuStoppedProblem -Stopped @(Get-State -OOMKilled $true)) | Should -Match 'killed for memory'
        @(Get-GpuStoppedProblem -Stopped @(Get-State -ExitCode $null)) | Should -Match 'no exit code'
    }

    It 'names a container that started again after the stop' {
        @(Get-GpuStoppedProblem -Stopped @(Get-State) -Later @(Get-State -Running $true)) | Should -Match 'started again'
        @(Get-GpuStoppedProblem -Stopped @(Get-State) -Later @(Get-State -StartedAt '2026-11-26T18:34:00Z')) | Should -Match 'started again'
    }

    It 'refuses a missing container and an empty check' {
        @(Get-GpuStoppedProblem -Stopped @(@{ Service = 'lyrveil-llm'; Known = $false })).Length | Should -Be 1
        @(Get-GpuStoppedProblem -Stopped @()).Length | Should -Be 1
    }
}

Describe 'ConvertFrom-GpuEventLog and Get-GpuDrainEvent' {
    BeforeAll {
        $prefix = 'lyrveil-orchestrator-1  | 2026-11-26T18:32:42.81Z 2026-11-26 18:32:42,816 INFO lyrveil_orchestrator.lifecycle '
        $script:Log = @(
            'lyrveil-orchestrator-1  | INFO:     127.0.0.1:40000 - "GET /v1/state HTTP/1.1" 200 OK',
            $prefix + '{"seq": 19, "at": "2026-11-26T18:32:35.318+00:00", "from": "GENERATING_TEXT", "to": "LLM_READY", "reason": "lease released", "job_id": "j", "code": null, "details": {}}',
            $prefix + '{"seq": 20, "at": "2026-11-26T18:32:42.816+00:00", "from": "LLM_READY", "to": "UNLOADING_LLM", "reason": "drain", "job_id": null, "code": null, "details": {}}',
            $prefix + '{"seq": 21, "at": "2026-11-26T18:32:44.147+00:00", "from": "UNLOADING_LLM", "to": "IDLE", "reason": "release verified", "job_id": null, "code": null, "details": {"exit_code": 143}}',
            $prefix + '{"seq": 22, "at": broken')
    }

    It 'reads every event of the log and leaves out the other lines' {
        $events = @(ConvertFrom-GpuEventLog -Line $script:Log)
        $events.Length | Should -Be 3
        $events[2].details.exit_code | Should -Be 143
    }

    It 'finds the drain and its verified release' {
        $drain = Get-GpuDrainEvent -Transition @(ConvertFrom-GpuEventLog -Line $script:Log)
        $drain.Unloading.seq | Should -Be 20
        $drain.Released.seq | Should -Be 21
    }

    It 'gives no release when the drain did not verify one' {
        $drain = Get-GpuDrainEvent -Transition @(ConvertFrom-GpuEventLog -Line ($script:Log | Select-Object -First 3))
        $drain.Unloading.seq | Should -Be 20
        $drain.Released | Should -BeNullOrEmpty
        (Get-GpuDrainEvent -Transition @()).Unloading | Should -BeNullOrEmpty
    }
}

Describe 'Get-GpuCudaRequest and Get-GpuOomLaunchLine' {
    It 'reads the free memory and the CUDA0 buffers of a KoboldCpp load' {
        $request = Get-GpuCudaRequest -Line @(
            'lyrveil-llm-1  | llama_prepare_model_devices: using device CUDA0 (NVIDIA GeForce RTX 4070) (0000:01:00.0) - 11090 MiB free',
            'lyrveil-llm-1  | load_tensors:        CUDA0 model buffer size =  6763.30 MiB',
            'lyrveil-llm-1  | load_tensors:    CUDA_Host model buffer size =   360.00 MiB',
            'lyrveil-llm-1  | llama_kv_cache:      CUDA0 KV buffer size =  3880.00 MiB',
            'lyrveil-llm-1  | sched_reserve:      CUDA0 compute buffer size =  1281.06 MiB')
        $request.FreeMiB | Should -Be 11090
        $request.Buffer.Count | Should -Be 3
        $request.RequestedMiB | Should -Be 11924.4
        (Get-GpuCudaRequest -Line @('nothing')).FreeMiB | Should -BeNullOrEmpty
    }

    It 'forces every layer on the GPU with the given context and batch, keeping the other lines' {
        $lines = Get-GpuOomLaunchLine -Line @('MODEL_PROFILE=p', 'CONTEXT_SIZE=12288', 'GPU_LAYERS=-1', 'BATCH_SIZE=512', 'LEVEL=A') -ContextSize 24576 -BatchSize 2048
        $lines | Should -Be @('MODEL_PROFILE=p', 'CONTEXT_SIZE=24576', 'GPU_LAYERS=99', 'BATCH_SIZE=2048', 'LEVEL=A')
        { Get-GpuOomLaunchLine -Line @('MODEL_PROFILE=p', 'LEVEL=A') -ContextSize 24576 -BatchSize 2048 } | Should -Throw
    }
}
