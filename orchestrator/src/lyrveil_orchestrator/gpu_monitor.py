"""GPU facts and VRAM readings over NVML, behind the GpuProbe port, for the preflight and the lifecycle manager.

On WSL2, NVML is a device-level gauge only (docs/runtime-contracts.md, NVML and WSL2): it reports the device name, the
driver versions and the memory of the whole card, never which process holds it. The probe therefore makes only the
documented calls: nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetName, nvmlDeviceGetMemoryInfo,
nvmlSystemGetDriverVersion and nvmlSystemGetCudaDriverVersion, plus nvmlShutdown.

NVML calls block, briefly. Callers on the event loop run them in a worker thread.
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

import pynvml

# The card the runtimes use: the hardware tiers describe a machine with one GPU.
DEVICE_INDEX = 0
_BYTES_PER_MIB = 1024 * 1024


class GpuProbeError(Exception):
    """NVML could not be loaded, initialized or read.

    The message names the NVML call that failed and NVML's own error text, which holds no user data, so it may be
    logged.
    """


@dataclass(frozen=True, slots=True)
class VramReading:
    """The memory of the whole card, in MiB rounded down. `used_mib` includes memory held by other programs."""

    total_mib: int
    used_mib: int
    free_mib: int


@dataclass(frozen=True, slots=True, order=True)
class CudaVersion:
    """The highest CUDA version the installed driver supports, such as 13.0. Orders by major, then minor."""

    major: int
    minor: int

    @classmethod
    def from_nvml(cls, value: int) -> "CudaVersion":
        """Decodes NVML's encoding, 1000 * major + 10 * minor, so 13000 is 13.0 and 12080 is 12.8."""
        return cls(major=value // 1000, minor=(value % 1000) // 10)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True, slots=True)
class GpuFacts:
    """What the preflight needs to know about the card and its driver, read in one pass."""

    device_name: str
    driver_version: str
    cuda_driver_version: CudaVersion
    memory: VramReading


class GpuProbe(Protocol):
    """The port the orchestrator reads the GPU through. NvmlProbe is the real one; tests use fakes.

    Every method raises GpuProbeError when NVML fails, and never another exception for an NVML problem.
    """

    def read_facts(self) -> GpuFacts:
        """Returns the device name, the driver versions and a memory reading."""
        ...

    def read_memory(self) -> VramReading:
        """Returns a fresh memory reading of the card."""
        ...

    def close(self) -> None:
        """Releases NVML. Safe to call more than once, and on a probe that never initialized."""
        ...


def _text(value: object) -> str:
    # Older bindings return bytes for names and versions, newer ones str.
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


class NvmlProbe:
    """GpuProbe over nvidia-ml-py, on device DEVICE_INDEX.

    NVML is initialized on the first read and stays initialized until close(). A failed initialization leaves the
    probe closed, so the next read tries again: the GPU may become visible after Docker Desktop restarts.
    """

    def __init__(self) -> None:
        self._handle: object | None = None

    def read_facts(self) -> GpuFacts:
        handle = self._open()
        name = _call("nvmlDeviceGetName", pynvml.nvmlDeviceGetName, handle)
        driver = _call("nvmlSystemGetDriverVersion", pynvml.nvmlSystemGetDriverVersion)
        cuda = _call("nvmlSystemGetCudaDriverVersion", pynvml.nvmlSystemGetCudaDriverVersion)
        return GpuFacts(
            device_name=_text(name),
            driver_version=_text(driver),
            cuda_driver_version=CudaVersion.from_nvml(int(cuda)),
            memory=self._memory(handle),
        )

    def read_memory(self) -> VramReading:
        return self._memory(self._open())

    def close(self) -> None:
        if self._handle is None:
            return
        self._handle = None
        # Nothing is left to release when the shutdown itself fails, and close() promises not to raise.
        with suppress(pynvml.NVMLError):
            pynvml.nvmlShutdown()

    def _open(self) -> object:
        if self._handle is None:
            _call("nvmlInit", pynvml.nvmlInit)
            try:
                self._handle = _call("nvmlDeviceGetHandleByIndex", pynvml.nvmlDeviceGetHandleByIndex, DEVICE_INDEX)
            except GpuProbeError:
                # Keeps the init and shutdown calls paired, since NVML counts initializations.
                with suppress(pynvml.NVMLError):
                    pynvml.nvmlShutdown()
                raise
        return self._handle

    @staticmethod
    def _memory(handle: object) -> VramReading:
        info = _call("nvmlDeviceGetMemoryInfo", pynvml.nvmlDeviceGetMemoryInfo, handle)
        return VramReading(
            total_mib=int(info.total) // _BYTES_PER_MIB,
            used_mib=int(info.used) // _BYTES_PER_MIB,
            free_mib=int(info.free) // _BYTES_PER_MIB,
        )


def _call[T](name: str, function: Callable[..., T], *args: object) -> T:
    """Returns `function(*args)`, turning NVML's errors, and a missing library, into GpuProbeError naming `name`."""
    try:
        return function(*args)
    except pynvml.NVMLError as error:
        raise GpuProbeError(f"{name} failed: {error}") from None


class GPUResourceMonitor:
    """Reads the card through a GpuProbe for the preflight now, and for release verification and VRAM checks later.

    Owns the probe: close() closes it.
    """

    def __init__(self, probe: GpuProbe) -> None:
        self._probe = probe

    def facts(self) -> GpuFacts:
        """Returns the card's facts. Raises GpuProbeError when NVML fails."""
        return self._probe.read_facts()

    def memory(self) -> VramReading:
        """Returns a fresh memory reading. Raises GpuProbeError when NVML fails."""
        return self._probe.read_memory()

    def close(self) -> None:
        """Closes the probe. Never raises."""
        self._probe.close()
