"""Fake ContainerGate: the two runtime containers as scripted state, with failures, and no Docker socket.

The fake keeps the real gate's promises that the orchestrator relies on: only a ContainerName member is accepted, a
container that does not exist or is not Lyrveil's is refused, start and stop report whether anything changed, and a stop
needs a grace period of 1 to MAX_STOP_GRACE_S seconds. Failures are queued per call with `fail`.
"""

from collections import defaultdict, deque
from dataclasses import dataclass

from lyrveil_orchestrator.container_gate import (
    MAX_STOP_GRACE_S,
    ContainerCallError,
    ContainerGate,
    ContainerMissingError,
    ContainerName,
    ContainerNotAllowedError,
    ContainerState,
    ContainerStatus,
    DockerUnavailableError,
    ForeignContainerError,
    HealthStatus,
    ProcessTable,
)

_ENDED = frozenset({ContainerState.EXITED, ContainerState.DEAD})
_ONE_PROCESS = ProcessTable(("PID", "CMD"), (("1", "runtime"),))


@dataclass
class FakeContainer:
    """The scripted state of one runtime container.

    `exists` and `foreign` make the gate answer as for a missing or a foreign container. `health_after_start` is the
    health a start gives the container: None for no health check, STARTING to let a test move it to healthy later.
    """

    state: ContainerState = ContainerState.CREATED
    health: HealthStatus | None = None
    exit_code: int = 0
    logs: str = ""
    processes: ProcessTable = _ONE_PROCESS
    exists: bool = True
    foreign: bool = False
    health_after_start: HealthStatus | None = HealthStatus.HEALTHY


class FakeContainerGate(ContainerGate):
    """A ContainerGate over `containers`, one FakeContainer per ContainerName, that never opens the Docker socket.

    It does not call ContainerGate.__init__, which would build the socket client, so the inherited private request
    methods are unusable; every public method is overridden. `calls` lists each call as (operation, name) in order,
    including the ones that failed. `closed` becomes True after `aclose`.
    """

    def __init__(self) -> None:
        # The parent's constructor would build the socket client, so it is deliberately not called.
        self.containers = {name: FakeContainer() for name in ContainerName}
        self.calls: list[tuple[str, ContainerName]] = []
        self.closed = False
        self._failures: defaultdict[tuple[str, ContainerName], deque[ContainerCallError]] = defaultdict(deque)

    def fail(
        self, name: ContainerName, operation: str, error: ContainerCallError | None = None, times: int = 1
    ) -> None:
        """Makes the next `times` calls of `operation` on `name` raise `error`, DockerUnavailableError by default."""
        for _ in range(times):
            self._failures[(operation, name)].append(
                error or DockerUnavailableError(name, operation, "Docker Engine did not answer")
            )

    def crash(self, name: ContainerName, exit_code: int = 137, logs: str | None = None) -> None:
        """Ends the container as if its process had died: exited with `exit_code`, and `logs` when given."""
        container = self.containers[name]
        container.state = ContainerState.EXITED
        container.health = None
        container.exit_code = exit_code
        if logs is not None:
            container.logs = logs

    async def aclose(self) -> None:
        self.closed = True

    async def inspect(self, name: ContainerName) -> ContainerStatus:
        container = self._enter("inspect", name)
        return ContainerStatus(
            name=name,
            state=container.state,
            running=container.state is ContainerState.RUNNING,
            health=container.health,
            exit_code=container.exit_code if container.state in _ENDED else None,
        )

    async def start(self, name: ContainerName) -> bool:
        container = self._enter("start", name)
        if container.state is ContainerState.RUNNING:
            return False
        container.state = ContainerState.RUNNING
        container.health = container.health_after_start
        container.exit_code = 0
        return True

    async def stop(self, name: ContainerName, grace_s: int) -> bool:
        if isinstance(grace_s, bool) or not isinstance(grace_s, int) or not 1 <= grace_s <= MAX_STOP_GRACE_S:
            raise ValueError(f"the stop grace period must be an int from 1 to {MAX_STOP_GRACE_S} seconds")
        container = self._enter("stop", name)
        if container.state is not ContainerState.RUNNING:
            return False
        container.state = ContainerState.EXITED
        container.health = None
        container.exit_code = 0
        return True

    async def top(self, name: ContainerName) -> ProcessTable:
        container = self._enter("top", name)
        if container.state is not ContainerState.RUNNING:
            return ProcessTable((), ())
        return container.processes

    async def logs(self, name: ContainerName) -> str:
        return self._enter("logs", name).logs

    def _enter(self, operation: str, name: ContainerName) -> FakeContainer:
        """Records the call, then raises what the real gate would: a refused name, a queued failure, or a refusal."""
        if not isinstance(name, ContainerName):
            raise ContainerNotAllowedError()
        self.calls.append((operation, name))
        queued = self._failures[(operation, name)]
        if queued:
            raise queued.popleft()
        container = self.containers[name]
        if not container.exists:
            raise ContainerMissingError(name, operation, "no such container")
        if container.foreign:
            raise ForeignContainerError(name, operation, "identity check failed on the name")
        return container
