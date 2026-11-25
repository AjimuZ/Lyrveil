"""The orchestrator of the gpu level's mode switch tests (T-GPU-07 to T-GPU-09): the real control API, lifecycle
manager, supervisor and NVML probe, with the container gate addressed to the runtimes of the test project.

`python gpu_probe/gpu_test_orchestrator.py` runs it. It exists only in the `gpu-test-runtime` stage of
orchestrator/Dockerfile, the image "lyrveil/orchestrator:gpu-test" of tests/gpu/compose.gpu-test.yaml, which
compose.yaml never names. The production gate refuses the test project's runtimes, whose Compose project is not
"lyrveil" and whose names are not "lyrveil-llm" and "lyrveil-image"; GpuTestContainerGate refuses an installation's
runtimes in turn, so the tests can never start, stop or read them.
"""

import sys
from collections.abc import Mapping
from types import MappingProxyType

import httpx

from lyrveil_orchestrator.container_gate import (
    API_VERSION,
    INSPECT,
    PROJECT_LABEL,
    SERVICE_LABEL,
    ContainerGate,
    ContainerName,
    DockerCall,
    ForeignContainerError,
    _json,
)
from lyrveil_orchestrator.gpu_monitor import NvmlProbe
from lyrveil_orchestrator.main import run

# The Compose project of the gpu level (scripts/lib/GpuLevel.psm1).
TEST_PROJECT = "lyrveil-gpu-test"
# The container of each runtime in that project. The override sets no container_name, so Compose names the single
# container of each service "<project>-<service>-1", and the service is the production container's name.
TEST_CONTAINERS: Mapping[ContainerName, str] = MappingProxyType(
    {name: f"{TEST_PROJECT}-{name.value}-1" for name in ContainerName}
)


class GpuTestContainerGate(ContainerGate):
    """ContainerGate with the same five calls on the same two ContainerName members, each sent to the member's
    container in TEST_CONTAINERS and accepted only when Docker reports the test project's labels for it.

    The parent's builder still checks the call, the member and the query before a request exists; only the
    container's segment of the path changes. The identity check is the parent's with the test project's name and
    project label, so an installation's lyrveil-llm and lyrveil-image raise ForeignContainerError even if Docker ever
    answered for them at these paths.
    """

    def _request(
        self,
        call: DockerCall,
        container: ContainerName,
        query: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> httpx.Request:
        request = super()._request(call, container, query, timeout_s)
        path = f"/{API_VERSION}/containers/{TEST_CONTAINERS[container]}/{call.action}"
        request.url = request.url.copy_with(path=path)
        return request

    async def _identified(self, container: ContainerName, call: DockerCall) -> Mapping[str, object]:
        response = await self._send(INSPECT, container)
        document = _json(container, INSPECT, response)
        check_test_identity(container, call.operation, document)
        return document


def check_test_identity(container: ContainerName, operation: str, document: Mapping[str, object]) -> None:
    """Raises ForeignContainerError unless the inspect document describes the test project's container of
    `container`: its name in TEST_CONTAINERS, the project label TEST_PROJECT and the service label of the runtime."""
    if document.get("Name") != f"/{TEST_CONTAINERS[container]}":
        raise ForeignContainerError(container, operation, "identity check failed on the name")
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ForeignContainerError(container, operation, "identity check failed: no labels")
    if labels.get(PROJECT_LABEL) != TEST_PROJECT:
        raise ForeignContainerError(container, operation, f"identity check failed on {PROJECT_LABEL}")
    if labels.get(SERVICE_LABEL) != container.value:
        raise ForeignContainerError(container, operation, f"identity check failed on {SERVICE_LABEL}")


def main() -> int:
    """Starts the orchestrator over GpuTestContainerGate and the real NVML probe and returns its exit status, as
    lyrveil_orchestrator.main.run does."""
    return run(GpuTestContainerGate, NvmlProbe)


if __name__ == "__main__":
    sys.exit(main())
