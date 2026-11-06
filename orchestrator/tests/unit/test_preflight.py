"""Tests of the preflight's GPU rows at each threshold of docs/operations.md, with a fake GpuProbe."""

import json
from dataclasses import dataclass

import pytest

from lyrveil_orchestrator.gpu_monitor import CudaVersion, GpuFacts, GpuProbeError, GPUResourceMonitor, VramReading
from lyrveil_orchestrator.preflight import EXIT_FAILED, EXIT_OK, CheckId, CheckStatus, main, run_preflight

PASS, WARNING, FAILURE = CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.FAILURE
CUDA_13_0 = CudaVersion(13, 0)


@dataclass
class FakeProbe:
    """A GpuProbe that returns fixed facts, or raises GpuProbeError when `error` is set."""

    total_mib: int = 12282
    free_mib: int = 10522
    cuda: CudaVersion = CUDA_13_0
    error: str | None = None
    closed: bool = False

    def read_facts(self) -> GpuFacts:
        if self.error is not None:
            raise GpuProbeError(self.error)
        return GpuFacts("NVIDIA GeForce RTX 4070", "596.36", self.cuda, self.read_memory())

    def read_memory(self) -> VramReading:
        return VramReading(self.total_mib, self.total_mib - self.free_mib, self.free_mib)

    def close(self) -> None:
        self.closed = True


def status_of(check: CheckId, probe: FakeProbe) -> CheckStatus:
    report = run_preflight(GPUResourceMonitor(probe))
    return next(result.status for result in report.checks if result.check is check)


@pytest.mark.parametrize(
    ("cuda", "expected"),
    [(CudaVersion(13, 0), PASS), (CudaVersion(13, 2), PASS), (CudaVersion(14, 0), PASS), (CudaVersion(12, 9), FAILURE)],
)
def test_the_cuda_driver_needs_at_least_13_0(cuda: CudaVersion, expected: CheckStatus) -> None:
    assert status_of(CheckId.CUDA_DRIVER, FakeProbe(cuda=cuda)) is expected


@pytest.mark.parametrize(
    ("total_mib", "expected"),
    [(12282, PASS), (11000, PASS), (10999, WARNING), (10000, WARNING), (9999, FAILURE), (8192, FAILURE)],
)
def test_total_vram_passes_from_11000_and_fails_under_10000(total_mib: int, expected: CheckStatus) -> None:
    assert status_of(CheckId.TOTAL_VRAM, FakeProbe(total_mib=total_mib, free_mib=0)) is expected


@pytest.mark.parametrize(
    ("free_mib", "expected"),
    [(10522, PASS), (9000, PASS), (8999, WARNING), (6000, WARNING), (5999, WARNING), (0, WARNING)],
)
def test_free_vram_passes_from_9000_and_never_fails(free_mib: int, expected: CheckStatus) -> None:
    assert status_of(CheckId.FREE_VRAM, FakeProbe(free_mib=free_mib)) is expected


def test_free_vram_under_6000_says_that_the_model_waits() -> None:
    report = run_preflight(GPUResourceMonitor(FakeProbe(free_mib=5999)))
    assert "the model waits" in report.checks[3].detail
    assert "the model waits" not in run_preflight(GPUResourceMonitor(FakeProbe(free_mib=6000))).checks[3].detail


def test_a_working_card_passes_every_row_in_table_order() -> None:
    report = run_preflight(GPUResourceMonitor(FakeProbe()))
    assert [(r.check, r.status) for r in report.checks] == [
        (CheckId.GPU, PASS),
        (CheckId.CUDA_DRIVER, PASS),
        (CheckId.TOTAL_VRAM, PASS),
        (CheckId.FREE_VRAM, PASS),
    ]
    assert report.ok


def test_an_nvml_failure_fails_every_row_and_names_the_error() -> None:
    report = run_preflight(GPUResourceMonitor(FakeProbe(error="nvmlInit failed: NVML Shared Library Not Found")))
    assert [r.status for r in report.checks] == [FAILURE] * 4
    assert report.checks[0].detail == "NVML fails: nvmlInit failed: NVML Shared Library Not Found"
    assert report.facts is None
    assert not report.ok


def test_warnings_alone_keep_the_report_ok() -> None:
    assert run_preflight(GPUResourceMonitor(FakeProbe(total_mib=10240, free_mib=4000))).ok


def test_main_exits_0_with_warnings_and_closes_the_monitor(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe(free_mib=7000)
    assert main([], GPUResourceMonitor(probe)) == EXIT_OK
    assert probe.closed
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "PASS     GPU in container: NVML works: NVIDIA GeForce RTX 4070"
    assert lines[3].startswith("WARNING  Free VRAM: 7000 MiB")


def test_main_exits_1_on_a_failure_and_prints_json_on_request(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe(total_mib=8192, free_mib=8000)
    assert main(["--json"], GPUResourceMonitor(probe)) == EXIT_FAILED
    assert probe.closed
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert [row["status"] for row in report["checks"]] == ["pass", "pass", "failure", "warning"]
    assert report["gpu"]["vram"] == {"total_mib": 8192, "used_mib": 192, "free_mib": 8000}
    assert report["gpu"]["cuda_driver_version"] == "13.0"
