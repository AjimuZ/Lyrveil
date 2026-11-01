# ADR 0003: Five services and segmented internal networks

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements name five services. They also require offline operation, no exposed administrative ports and a limited Docker socket. Private text passes through the backend and the runtimes.

## Decision
Four Docker networks:

| Network | Internal | Members |
| --- | --- | --- |
| `lyrveil-edge` | No | frontend |
| `lyrveil-app` | Yes | frontend, backend |
| `lyrveil-control` | Yes | backend, orchestrator |
| `lyrveil-runtime` | Yes | backend, llm, image |

Only the frontend publishes a port, on `127.0.0.1:4200`. The orchestrator reads runtime readiness from Docker health status, so it needs no path to the runtimes.

## Alternatives
- The default single network: simple, but every service could reach the socket owner and the Internet.
- Firewall rules inside containers: fragile, and they need capabilities.
- A separate Docker socket proxy service: adds a service for a benefit the orchestrator's allowlist already gives ([ADR 0006](0006-docker-socket-confined-to-orchestrator.md)).

## Consequences
Privacy and offline behavior are enforced by the network, not by trust in each program. Tests check for no route out (T-DCK-08). A runtime can still reach the backend on its network, which [Security](../security.md#residual-risks) records as an accepted residual risk.
