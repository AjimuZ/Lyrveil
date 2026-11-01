# ADR 0006: Docker socket only in the orchestrator, behind an allowlist

Status: accepted (2026-11-01). Supersedes: none.

## Context
Something must start and stop the runtimes, and Docker's socket grants root-equivalent control. The requirements forbid exposing it to untrusted services, and ask for reduced authority with an explicit allowlist and documented residual risks. On Docker Desktop the socket appears in containers as `root:root` with mode 660, measured on 2026-11-01.

## Decision
- The orchestrator is the reduced-authority controller. It is the only service with the socket.
- `ContainerGate` is the only module that uses it, over HTTPX. It exposes `inspect`, `start`, `stop`, `top` and `logs`, for the names `lyrveil-llm` and `lyrveil-image`, after checking Compose labels. No create, exec or generic request exists.
- The control API accepts only enums, identifiers and numbers, needs a bearer token, and is reachable only from the backend.
- The container runs as UID 10001 with supplementary group 0, all capabilities dropped, `no-new-privileges` and a read-only root.
- The backend, which handles user input and model output, has no socket.

## Alternatives
- A socket proxy such as docker-socket-proxy: a sixth service. Common configurations filter API sections rather than specific containers, so the allowlist would still be needed.
- Socket in the backend: the service with the largest attack surface would hold root-equivalent power.
- A host-side daemon: more moving parts on Windows and WSL2, with a privileged process outside Docker.
- Mounting the socket `:ro`: it does not restrict API calls, and gives false comfort.

## Consequences
Exploiting the orchestrator would still give control of Docker. [Security](../security.md#residual-risks) records this and the other residual risks. T-ORC-07 and a source scan protect the allowlist. Keeping the service's dependencies minimal is a standing requirement.
