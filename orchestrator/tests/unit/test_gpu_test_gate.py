"""Tests of tests/gpu_probe/gpu_test_orchestrator.py, the orchestrator of the gpu level's mode switch tests, without
Docker.

Its gate drives real runtimes through the host's Docker socket, so it must send only the documented calls, only to the
test project's two runtime containers, and refuse an installation's lyrveil-llm and lyrveil-image.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import respx

from lyrveil_orchestrator.container_gate import (
    CALLS,
    INSPECT,
    LOGS,
    START,
    STOP,
    TOP,
    ContainerName,
    ContainerNotAllowedError,
    DockerCall,
    ForeignContainerError,
)

PROBE_PATH = Path(__file__).resolve().parents[1] / "gpu_probe" / "gpu_test_orchestrator.py"
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
QUERIES: dict[DockerCall, dict[str, str]] = {
    INSPECT: {},
    START: {},
    STOP: {"t": "20"},
    TOP: {},
    LOGS: {"stdout": "1", "stderr": "1", "tail": "200"},
}


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gpu_test_orchestrator", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """Runs the async tests on asyncio, the event loop of the orchestrator."""
    return "asyncio"


def answer(name: str, project: str, service: str) -> dict[str, Any]:
    """An inspect answer of a running, healthy container `name` with the two Compose labels."""
    return {
        "Id": "f" * 64,
        "Name": f"/{name}",
        "Config": {"Labels": {PROJECT: project, SERVICE: service}, "Tty": False},
        "State": {"Status": "running", "Running": True, "ExitCode": 0, "Health": {"Status": "healthy"}},
    }


def test_each_runtime_maps_to_the_test_project_container_of_its_service() -> None:
    assert dict(probe.TEST_CONTAINERS) == {
        ContainerName.LLM: "lyrveil-gpu-test-lyrveil-llm-1",
        ContainerName.IMAGE: "lyrveil-gpu-test-lyrveil-image-1",
    }


@pytest.mark.anyio
async def test_every_request_goes_to_a_test_project_container_and_never_to_an_installation() -> None:
    assert set(QUERIES) == CALLS
    built: set[tuple[str, str]] = set()
    async with probe.GpuTestContainerGate() as gate:
        for call in CALLS:
            for name in ContainerName:
                request = gate._request(call, name, QUERIES[call])
                built.add((request.method, request.url.path))
    assert built == {
        (call.method, f"/v1.44/containers/lyrveil-gpu-test-{name.value}-1/{call.action}")
        for call in CALLS
        for name in ContainerName
    }
    assert not any(f"/containers/{name.value}/" in path for _, path in built for name in ContainerName)


@pytest.mark.anyio
async def test_a_plain_name_is_still_refused_before_a_request_exists() -> None:
    async with probe.GpuTestContainerGate() as gate:
        with pytest.raises(ContainerNotAllowedError):
            gate._request(INSPECT, "lyrveil-gpu-test-lyrveil-llm-1")


@pytest.mark.anyio
async def test_the_test_project_runtime_is_started_after_its_identity_check() -> None:
    path = "/v1.44/containers/lyrveil-gpu-test-lyrveil-llm-1"
    with respx.mock(base_url="http://docker", assert_all_mocked=True) as router:
        router.get(f"{path}/json").respond(
            200, json=answer("lyrveil-gpu-test-lyrveil-llm-1", "lyrveil-gpu-test", "lyrveil-llm")
        )
        router.post(f"{path}/start").respond(204)
        async with probe.GpuTestContainerGate() as gate:
            assert await gate.start(ContainerName.LLM) is True
        assert [(call.request.method, call.request.url.path) for call in router.calls] == [
            ("GET", f"{path}/json"),
            ("POST", f"{path}/start"),
        ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "project", "service"),
    [
        ("lyrveil-llm", "lyrveil", "lyrveil-llm"),
        ("lyrveil-gpu-test-lyrveil-llm-1", "lyrveil", "lyrveil-llm"),
        ("lyrveil-gpu-test-lyrveil-llm-1", "lyrveil-gpu-test", "lyrveil-image"),
        ("lyrveil-gpu-test-lyrveil-image-1", "lyrveil-gpu-test", "lyrveil-llm"),
    ],
    ids=["an installation's runtime", "the installation's project", "another service", "another name"],
)
async def test_any_other_container_is_refused_and_never_stopped(name: str, project: str, service: str) -> None:
    path = "/v1.44/containers/lyrveil-gpu-test-lyrveil-llm-1"
    with respx.mock(base_url="http://docker", assert_all_called=False, assert_all_mocked=True) as router:
        router.get(f"{path}/json").respond(200, json=answer(name, project, service))
        stop = router.post(f"{path}/stop")
        async with probe.GpuTestContainerGate() as gate:
            with pytest.raises(ForeignContainerError):
                await gate.stop(ContainerName.LLM, 20)
        assert not stop.called
