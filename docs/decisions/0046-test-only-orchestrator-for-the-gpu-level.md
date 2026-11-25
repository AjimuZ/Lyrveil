# ADR 0046: A test-only orchestrator drives the gpu level's real runtimes

Status: accepted (2026-11-26). Extends [ADR 0006](0006-docker-socket-confined-to-orchestrator.md) to a test image; the
production gate and its allowlist do not change.

## Context
- T-GPU-07 to T-GPU-09 (GPU.6) need the real orchestrator on the real GPU: its switches between the two runtimes, its
  release verification, its VRAM hold, and the backend's scheduler on top of it.
- The gpu level runs in its own Compose project, `lyrveil-gpu-test`, so it never touches an installation
  ([The `gpu` level](../testing.md#the-gpu-level)). Its runtime containers are `lyrveil-gpu-test-lyrveil-llm-1` and
  `lyrveil-gpu-test-lyrveil-image-1`, with the project label `lyrveil-gpu-test`.
- `ContainerGate` (ADR 0006) acts only on the containers named `lyrveil-llm` and `lyrveil-image` with the project label
  `lyrveil`, so the production orchestrator refuses the test project's runtimes. Giving the test runtimes those names
  would collide with an installation's containers.
- The end-to-end level already runs the real control API and lifecycle manager in a test-only stage of
  `orchestrator/Dockerfile` (`fake-runtime`), over fakes.

## Decision
- A new test-only stage of `orchestrator/Dockerfile`, `gpu-test-runtime`, tagged `lyrveil/orchestrator:gpu-test`, runs
  `orchestrator/tests/gpu_probe/gpu_test_orchestrator.py`: `lyrveil_orchestrator.main.run` with the real `NvmlProbe`
  and `GpuTestContainerGate`.
- `GpuTestContainerGate` is `ContainerGate` with two overrides. The parent's builder still checks the call, the
  `ContainerName` member and the query; only the container's segment of the path becomes the test project's container
  of the same service. The identity check is the parent's with the name `lyrveil-gpu-test-<service>-1` and the project
  label `lyrveil-gpu-test`, so an installation's `lyrveil-llm` and `lyrveil-image` are neither addressed nor accepted.
  `orchestrator/tests/unit/test_gpu_test_gate.py` checks both.
- `tests/gpu/compose.gpu-test.yaml` puts that orchestrator and the production backend image, both `:gpu-test`, in the
  test project, with a control token of the level's own, no `env_file`, and the test data volume. The orchestrator
  mounts the host's Docker socket, as in `compose.yaml`. The level's configuration check refuses any other host mount,
  a socket in another service, an environment variable the override does not set, and another token.
- Production code is unchanged: `compose.yaml` never names the stage, the runtime stage copies nothing from it, and
  `main.main` still takes no setting that selects a gate.

## Alternatives
- A constructor argument or a setting that names the project and containers of the production gate: one code path, but
  it widens ADR 0006's allowlist in production for the sake of a test.
- Name the test runtimes `lyrveil-llm` and `lyrveil-image` with the label `lyrveil`: collides with an installation's
  containers, which the level must never touch.
- Drive the switches from the test with `docker compose stop` and `start`: no orchestrator would run, so T-GPU-07 to
  T-GPU-09 would test the script, not the exclusivity rules, the release verification or the VRAM hold.

## Consequences
- The gpu level now starts a container with the host's Docker socket. Its gate can address only the test project's two
  runtimes, and the level refuses to start while an installation's runtime or orchestrator runs, so an installation's
  orchestrator never races the test for the GPU.
- `GpuTestContainerGate` overrides two private methods of `ContainerGate` (`_request`, `_identified`). A change to them
  breaks its unit test before it breaks the gpu level.
- The real-GPU tests exercise every production module of the orchestrator except the production gate's two names,
  which T-ORC-07 covers without Docker.
