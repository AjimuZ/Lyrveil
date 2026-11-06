"""Tests of NvmlProbe and GPUResourceMonitor, with nvidia-ml-py's functions replaced, since no test sees a GPU."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pynvml
import pytest

from lyrveil_orchestrator.gpu_monitor import (
    CudaVersion,
    GpuFacts,
    GpuProbeError,
    GPUResourceMonitor,
    NvmlProbe,
    VramReading,
)

MIB = 1024 * 1024


@dataclass
class FakeNvml:
    """Stands in for the NVML calls the probe makes, and records init and shutdown calls."""

    init_error: int | None = None
    handle_error: int | None = None
    calls: list[str] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pynvml, "nvmlInit", self.init)
        monkeypatch.setattr(pynvml, "nvmlShutdown", lambda: self.calls.append("shutdown"))
        monkeypatch.setattr(pynvml, "nvmlDeviceGetHandleByIndex", self.handle)
        monkeypatch.setattr(pynvml, "nvmlDeviceGetName", lambda handle: b"NVIDIA GeForce RTX 4070")
        monkeypatch.setattr(pynvml, "nvmlSystemGetDriverVersion", lambda: "596.36")
        monkeypatch.setattr(pynvml, "nvmlSystemGetCudaDriverVersion", lambda: 13020)
        monkeypatch.setattr(
            pynvml,
            "nvmlDeviceGetMemoryInfo",
            lambda handle: SimpleNamespace(total=12282 * MIB, used=1489 * MIB + 5, free=10522 * MIB + 7),
        )

    def init(self) -> None:
        self.calls.append("init")
        if self.init_error is not None:
            raise pynvml.NVMLError(self.init_error)

    def handle(self, index: int) -> str:
        self.calls.append(f"handle {index}")
        if self.handle_error is not None:
            raise pynvml.NVMLError(self.handle_error)
        return "device-0"


def test_cuda_versions_decode_from_nvml_and_order_by_major_then_minor() -> None:
    assert CudaVersion.from_nvml(13000) == CudaVersion(13, 0)
    assert CudaVersion.from_nvml(12080) == CudaVersion(12, 8)
    assert str(CudaVersion.from_nvml(13020)) == "13.2"
    assert CudaVersion(12, 9) < CudaVersion(13, 0) < CudaVersion(13, 1)


def test_facts_are_read_in_mib_rounded_down(monkeypatch: pytest.MonkeyPatch) -> None:
    nvml = FakeNvml()
    nvml.install(monkeypatch)
    probe = NvmlProbe()
    assert probe.read_facts() == GpuFacts(
        device_name="NVIDIA GeForce RTX 4070",
        driver_version="596.36",
        cuda_driver_version=CudaVersion(13, 2),
        memory=VramReading(total_mib=12282, used_mib=1489, free_mib=10522),
    )
    assert probe.read_memory() == VramReading(total_mib=12282, used_mib=1489, free_mib=10522)
    probe.close()
    probe.close()
    assert nvml.calls == ["init", "handle 0", "shutdown"]


def test_a_missing_library_raises_a_probe_error_and_the_next_read_tries_again(monkeypatch: pytest.MonkeyPatch) -> None:
    nvml = FakeNvml(init_error=pynvml.NVML_ERROR_LIBRARY_NOT_FOUND)
    nvml.install(monkeypatch)
    probe = NvmlProbe()
    with pytest.raises(GpuProbeError, match=r"^nvmlInit failed: "):
        probe.read_facts()
    nvml.init_error = None
    assert probe.read_memory().total_mib == 12282
    assert nvml.calls == ["init", "init", "handle 0"]


def test_a_failed_device_lookup_shuts_nvml_down_again(monkeypatch: pytest.MonkeyPatch) -> None:
    nvml = FakeNvml(handle_error=pynvml.NVML_ERROR_GPU_IS_LOST)
    nvml.install(monkeypatch)
    probe = NvmlProbe()
    with pytest.raises(GpuProbeError, match=r"^nvmlDeviceGetHandleByIndex failed: "):
        probe.read_memory()
    probe.close()
    assert nvml.calls == ["init", "handle 0", "shutdown"]


def test_the_monitor_reads_through_its_probe_and_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    nvml = FakeNvml()
    nvml.install(monkeypatch)
    monitor = GPUResourceMonitor(NvmlProbe())
    assert monitor.memory().free_mib == 10522
    assert monitor.facts().device_name == "NVIDIA GeForce RTX 4070"
    monitor.close()
    assert nvml.calls[-1] == "shutdown"
