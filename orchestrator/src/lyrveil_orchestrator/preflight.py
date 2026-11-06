"""The orchestrator's rows of the preflight table in docs/operations.md: NVML, CUDA driver, total and free VRAM.

`python -m lyrveil_orchestrator.preflight` prints one line per row, or the report as JSON with `--json`, and exits 0
when no row failed and 1 otherwise. Warnings never change the exit status: start.cmd shows them and goes on.
`GET /v1/preflight` of the control API returns the same report.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lyrveil_orchestrator.gpu_monitor import CudaVersion, GpuFacts, GpuProbeError, GPUResourceMonitor, NvmlProbe

# The thresholds of docs/operations.md, Preflight checks.
MIN_CUDA_DRIVER = CudaVersion(13, 0)
TOTAL_VRAM_PASS_MIB = 11000
TOTAL_VRAM_WARNING_MIB = 10000
FREE_VRAM_PASS_MIB = 9000
# Below this the text model waits for memory before it loads, so startup still goes on.
FREE_VRAM_WAIT_MIB = 6000

EXIT_OK = 0
EXIT_FAILED = 1


class CheckStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAILURE = "failure"


class CheckId(StrEnum):
    """The rows this module reports, in report order. The labels are the names of the table's rows."""

    GPU = "gpu_in_container"
    CUDA_DRIVER = "cuda_driver_version"
    TOTAL_VRAM = "total_vram"
    FREE_VRAM = "free_vram"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    CheckId.GPU: "GPU in container",
    CheckId.CUDA_DRIVER: "CUDA driver version",
    CheckId.TOTAL_VRAM: "Total VRAM",
    CheckId.FREE_VRAM: "Free VRAM",
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One row of the report. `detail` holds the measured value or the reason, never user data."""

    check: CheckId
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """The four rows, always all of them and in CheckId order, plus the facts they were judged on.

    `facts` is None when NVML failed; the other rows then fail as not checked, because an unread value cannot pass.
    """

    checks: tuple[CheckResult, ...]
    facts: GpuFacts | None

    @property
    def ok(self) -> bool:
        """True when no row failed. Warnings count as ok."""
        return all(result.status is not CheckStatus.FAILURE for result in self.checks)

    def to_dict(self) -> dict[str, Any]:
        """Returns the report as JSON-ready data: `ok`, `checks` with check, label, status and detail, and `gpu`."""
        gpu: dict[str, Any] | None = None
        if self.facts is not None:
            gpu = {
                "device_name": self.facts.device_name,
                "driver_version": self.facts.driver_version,
                "cuda_driver_version": str(self.facts.cuda_driver_version),
                "vram": {
                    "total_mib": self.facts.memory.total_mib,
                    "used_mib": self.facts.memory.used_mib,
                    "free_mib": self.facts.memory.free_mib,
                },
            }
        return {
            "ok": self.ok,
            "checks": [
                {"check": r.check.value, "label": r.check.label, "status": r.status.value, "detail": r.detail}
                for r in self.checks
            ],
            "gpu": gpu,
        }


def judge(facts: GpuFacts) -> PreflightReport:
    """Returns the report for facts NVML returned, judged at the documented thresholds."""
    cuda = facts.cuda_driver_version
    total = facts.memory.total_mib
    free = facts.memory.free_mib

    if cuda >= MIN_CUDA_DRIVER:
        cuda_row = CheckResult(CheckId.CUDA_DRIVER, CheckStatus.PASS, f"CUDA {cuda}, driver {facts.driver_version}")
    else:
        cuda_row = CheckResult(
            CheckId.CUDA_DRIVER,
            CheckStatus.FAILURE,
            f"CUDA {cuda}, driver {facts.driver_version}: at least CUDA {MIN_CUDA_DRIVER} is required. Update the "
            "NVIDIA driver on Windows",
        )

    if total >= TOTAL_VRAM_PASS_MIB:
        total_row = CheckResult(CheckId.TOTAL_VRAM, CheckStatus.PASS, f"{total} MiB")
    elif total >= TOTAL_VRAM_WARNING_MIB:
        total_row = CheckResult(
            CheckId.TOTAL_VRAM, CheckStatus.WARNING, f"{total} MiB, under the {TOTAL_VRAM_PASS_MIB} MiB of a pass"
        )
    else:
        total_row = CheckResult(
            CheckId.TOTAL_VRAM, CheckStatus.FAILURE, f"{total} MiB: at least {TOTAL_VRAM_WARNING_MIB} MiB is required"
        )

    if free >= FREE_VRAM_PASS_MIB:
        free_row = CheckResult(CheckId.FREE_VRAM, CheckStatus.PASS, f"{free} MiB")
    elif free >= FREE_VRAM_WAIT_MIB:
        free_row = CheckResult(
            CheckId.FREE_VRAM,
            CheckStatus.WARNING,
            f"{free} MiB, under {FREE_VRAM_PASS_MIB} MiB: other programs use the GPU, so replies may be slower",
        )
    else:
        # Never a failure: other programs can free memory later, and the model waits for it.
        free_row = CheckResult(
            CheckId.FREE_VRAM,
            CheckStatus.WARNING,
            f"{free} MiB, under {FREE_VRAM_WAIT_MIB} MiB: the model waits until other programs free memory",
        )

    gpu_row = CheckResult(CheckId.GPU, CheckStatus.PASS, f"NVML works: {facts.device_name}")
    return PreflightReport(checks=(gpu_row, cuda_row, total_row, free_row), facts=facts)


def nvml_failed(error: GpuProbeError) -> PreflightReport:
    """Returns the report when NVML failed: the GPU row fails with the error and the other rows fail as not checked."""
    not_checked = "Not checked, because NVML failed"
    return PreflightReport(
        checks=(
            CheckResult(CheckId.GPU, CheckStatus.FAILURE, f"NVML fails: {error}"),
            CheckResult(CheckId.CUDA_DRIVER, CheckStatus.FAILURE, not_checked),
            CheckResult(CheckId.TOTAL_VRAM, CheckStatus.FAILURE, not_checked),
            CheckResult(CheckId.FREE_VRAM, CheckStatus.FAILURE, not_checked),
        ),
        facts=None,
    )


def run_preflight(monitor: GPUResourceMonitor) -> PreflightReport:
    """Reads the facts once through `monitor` and returns the report. Never raises for an NVML failure."""
    try:
        facts = monitor.facts()
    except GpuProbeError as error:
        return nvml_failed(error)
    return judge(facts)


def format_report(report: PreflightReport) -> str:
    """Returns one line per row, such as `PASS     Total VRAM: 12282 MiB`."""
    return "\n".join(f"{r.status.value.upper():<8} {r.check.label}: {r.detail}" for r in report.checks)


def main(argv: Sequence[str] | None = None, monitor: GPUResourceMonitor | None = None) -> int:
    """Runs the preflight, prints it, and returns EXIT_OK when no row failed and EXIT_FAILED otherwise.

    `monitor` defaults to one over NVML, and is closed before returning either way.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lyrveil_orchestrator.preflight", description="Checks the GPU as seen from the container."
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    arguments = parser.parse_args(argv)
    monitor = monitor if monitor is not None else GPUResourceMonitor(NvmlProbe())
    try:
        report = run_preflight(monitor)
    finally:
        monitor.close()
    print(json.dumps(report.to_dict(), indent=2) if arguments.json else format_report(report))
    return EXIT_OK if report.ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
