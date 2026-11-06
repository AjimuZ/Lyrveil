# Orchestrator package and state machine, 2026-11-06

Scope: roadmap item P01.4a, the orchestrator package, its lock and test stage, the transition table of [GPU orchestration](../gpu-orchestration.md#state-machine), and the orchestrator's place in `scripts\lyrveil.cmd test` ([Testing](../testing.md#the-lint-level)).

Machine: Windows 11 Pro, Windows PowerShell 5.1, Docker Engine 29.9.2. Commands ran from Git Bash in the repository root.

## Lock and image

| Command | Result |
| --- | --- |
| `docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .` | uv 0.13.6 on CPython 3.12.16: `Resolved 15 packages`. Wrote `orchestrator/uv.lock` |
| `docker build --quiet -f orchestrator/Dockerfile --target test -t lyrveil/orchestrator-test:local .` | Built |

The package has no runtime dependency yet. The `dev` group pins Hypothesis 6.170.14, mypy 2.4.1, pytest 9.2.0 and Ruff 0.17.3, as in [Technology](../technology.md#infrastructure-and-tools). The lock also resolved sortedcontainers 2.4.0, the one dependency of Hypothesis.

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `scripts/lyrveil.cmd test lint`, first run | 1 | Orchestrator `ruff format --check` exit 1: one missing blank line in `state_machine.py`. Every other check passed |
| `scripts/lyrveil.cmd test lint` after the fix, with these documents | 0 | 78 Markdown files, 656 links and anchors, 0 broken. PSScriptAnalyzer: 7 scripts, 0 findings. Backend and orchestrator: `ruff check`, `ruff format --check` and `mypy --strict src tests` exit 0. Orchestrator: 4 source files |
| `scripts/lyrveil.cmd test unit tests/unit/test_state_machine.py tests/unit/test_locked_environment.py -q` | 0 | The backend was skipped, since it holds neither file. Orchestrator: 132 passed |

T-ORC-01 is covered by `orchestrator/tests/unit/test_state_machine.py`:

- the 11 state names and the 25 edges of the diagram, written out again in the test, match the table exactly, each edge once;
- each of the 121 ordered pairs of states: the 25 edges are accepted, and the other 96, including every self-transition, raise `IllegalTransition` with the source and target;
- the exclusivity rules the table can hold: `LOADING_*` only from `IDLE`, `IDLE` only from the two unloading states and `RECOVERING`, and no edge from a state of one runtime to a state of the other;
- two Hypothesis properties, 300 examples each: random sequences of targets from `RECOVERING` apply exactly the legal moves and refuse the rest, and random legal walks of 80 steps change runtime only through a state without one;
- the module imports only `collections.abc`, `dataclasses`, `enum` and `types`, and calls none of `open`, `print`, `input`, `exec`, `eval`, `__import__`.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| The full `scripts/lyrveil.cmd test unit` and `test integration` | The session ran only the orchestrator's unit tests. The backend's tests did not change | Next full run of both levels |
| The three new Pester cases of `Python component selection` in `tests/scripts/TestCommand.Tests.ps1` | Pester runs only within the full `test unit`, which this session did not run | Next full run of `test unit` |
| T-ORC-02, the property that no event sequence runs both runtimes | It needs the container gate and the supervisor, P06 | P06 |
