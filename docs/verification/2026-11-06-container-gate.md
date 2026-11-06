# Container gate, 2026-11-06

Scope: roadmap item P01.4b, `orchestrator/src/lyrveil_orchestrator/container_gate.py`, its HTTPX dependency, the T-ORC-07 tests, and the scans that keep the Docker socket out of every other module of the orchestrator and of the backend ([Security](../security.md#docker-socket), [Runtime contracts](../runtime-contracts.md#docker-engine-api)).

Machine: Windows 11 Pro. `docker version` reports Engine 29.9.2, API 1.56, minimum API 1.40, so the pinned `v1.44` is in range. Commands ran from Git Bash in the repository root.

## Lock and image

| Command | Result |
| --- | --- |
| `docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .` | Run by an earlier session of this item, when HTTPX and respx were added: uv 0.13.6 on CPython 3.12.16, `Resolved 22 packages`, and it wrote `orchestrator/uv.lock`. Not repeated, since later changes touched only tests and documents. The lock holds 22 packages, against 15 in the previous commit |

The lock adds HTTPX 0.28.1 as the one runtime dependency and respx 0.23.1 to the `dev` group, as in [Technology](../technology.md#backend-and-orchestrator). It also resolved the dependencies of HTTPX: anyio 4.16.0, certifi 2026.10.23, h11 0.16.0, httpcore 1.0.9 and idna 3.20. `scripts\lyrveil.cmd test lint` and `test unit` built both test stages before the runs below, with `uv sync --locked`, which refuses a lock that does not match `pyproject.toml`.

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `scripts/lyrveil.cmd test lint`, last run after these documents | 0 | 79 Markdown files, 670 links and anchors, 0 broken. PSScriptAnalyzer: 7 scripts, 0 findings. Backend: `ruff check`, `ruff format --check` and `mypy --strict src tests` exit 0, 23 source files. Orchestrator: the same three exit 0, 7 source files |
| `scripts/lyrveil.cmd test unit tests/unit/test_container_gate.py tests/unit/test_socket_confinement.py tests/unit/test_locked_environment.py -q` | 0 | Backend skipped, since it holds only the last file. Orchestrator: 316 passed in 1.78 s |
| `scripts/lyrveil.cmd test unit tests/unit/test_docker_socket.py tests/unit/test_import_rules.py -q` | 0 | Backend: 35 passed in 0.05 s. Orchestrator skipped, since it holds neither file |
| `scripts/lyrveil.cmd test unit` with the four files of both components in one command | 1 | `nothing ran`: a component runs only when it holds every named file, so each component needs its own command |

T-ORC-07 is covered by `orchestrator/tests/unit/test_container_gate.py`:

- the allowlist holds exactly `lyrveil-llm` and `lyrveil-image`, and the public methods of `ContainerGate` are exactly `inspect`, `start`, `stop`, `top`, `logs` and `aclose`;
- 17 names, run through each of the 5 methods, raise `ContainerNotAllowedError` with no request sent. They include plain strings that spell an allowed name, the other services, paths with `..`, a container ID and values that are not strings;
- 8 foreign identities, for both containers and through each method, raise `ForeignContainerError` after the inspect, and nothing else is sent: another project, the project in another case, no project label, the other runtime's service, another service, no service label, no labels and another name;
- the builder, for its 5 calls and 2 names, builds exactly the 10 documented method and path pairs, written out again in the test, and none of them is forbidden. The forbidden check has its own 14 cases: create, remove, exec, attach, kill, prune, images, build, volumes and networks;
- 9 calls outside the list and 7 query parameters that a call does not document are refused before a request exists;
- every method sends the inspect, then its own documented call, and nothing else;
- the module's strings name no forbidden endpoint or method. It imports only HTTPX, as a module, and `collections.abc`, `dataclasses`, `enum`, `struct`, `types` and `typing`. It makes one HTTPX client over one transport, uses the client through exactly one `build_request`, one `send` and `aclose`, and never calls the request functions of HTTPX. With the builder's refusals, this is why the 10 pairs are every request the gate can send.

The same file checks the rest of the contract: the stop grace period from 1 to 120 s, and a stop request that waits longer than it; the logs query `stdout=1&stderr=1&tail=200`; `204` and `304`; the inspect fields and malformed `State` values; answers with another status, a body that is not a JSON object, or a redirect, which is not followed; `404`; connection errors and timeouts; error messages without text from Docker; top with `409`; logs demultiplexed in order, raw logs of a TTY, and malformed streams. One test sends an inspect over a real Unix socket in the test container and checks the request line that arrives.

`orchestrator/tests/unit/test_socket_confinement.py` scans `orchestrator/src` and finds only `container_gate.py`, where it reports the socket's path and the `uds` argument. Its 18 cases show that each pattern is found, including the `docker` command alone, by path, with a subcommand and with an option. One case shows that ordinary HTTP code and the `DOCKER_UNAVAILABLE` code are not reported, 4 show that messages naming Docker, `dockerd` and `docker composer` are not taken for the command, and a fixture package with a second module that passes `uds` fails the scan.

`backend/tests/unit/test_docker_socket.py` runs a copy of the same scan on the backend's `src/` and `migrations/`, which both hold modules and give no finding. It repeats the 18 pattern cases, the 5 cases of code that is not reported, and a fixture package whose `import docker` fails the scan.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| Calls against the real Docker Engine | The runtime containers do not exist yet, so the readings of [Runtime contracts](../runtime-contracts.md#docker-engine-api), the `409` of top among them, are not measured | P02, when the supervisor starts `lyrveil-llm` through the gate |
| The full `scripts/lyrveil.cmd test unit` and `test integration` | Only the files above ran, plus the backend's import rules | Next full run of both levels |
| A scan of the frontend and the scripts | The frontend runs in the browser and cannot open a Unix socket. The scripts run the `docker` command on the host by design | None |
