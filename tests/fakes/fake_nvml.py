"""Fake GpuProbe: a card whose memory and failures a test scripts, with memory held by programs other than Lyrveil.

The card has `total_mib`. Its used memory is `external_mib`, held by other programs such as the desktop, plus
`runtime_mib`, held by the runtime a test pretends is loaded. This is the whole-card view that NVML gives on WSL2.
"""

from collections import deque
from dataclasses import dataclass, field

from lyrveil_orchestrator.gpu_monitor import CudaVersion, GpuFacts, GpuProbeError, VramReading

# The lowest driver the preflight accepts, and the reference machine's.
CUDA_13_0 = CudaVersion(13, 0)


@dataclass
class FakeGpuProbe:
    """A GpuProbe over scripted memory. Every read raises GpuProbeError while a failure is pending or `error` is set.

    `script_external` queues readings of `external_mib`: each memory read takes the next value, and the last one stays.
    `fail_next` makes the next reads fail, and `error` makes every read fail until it is set back to None. `reads`
    counts the attempts, failed or not, and `closes` the calls to `close`.
    """

    total_mib: int = 12282
    external_mib: int = 1489
    runtime_mib: int = 0
    device_name: str = "NVIDIA GeForce RTX 4070"
    driver_version: str = "596.36"
    cuda_driver_version: CudaVersion = CUDA_13_0
    error: str | None = None
    reads: int = 0
    closes: int = 0
    _external_script: deque[int] = field(default_factory=deque, init=False, repr=False)
    _failures: deque[str] = field(default_factory=deque, init=False, repr=False)

    def script_external(self, *values_mib: int) -> None:
        """Queues the external memory use that the next reads report, one value per read."""
        self._external_script.extend(values_mib)

    def fail_next(self, message: str = "nvmlDeviceGetMemoryInfo failed: Unknown Error", times: int = 1) -> None:
        """Makes the next `times` reads raise GpuProbeError with `message`."""
        self._failures.extend([message] * times)

    def read_facts(self) -> GpuFacts:
        return GpuFacts(self.device_name, self.driver_version, self.cuda_driver_version, self.read_memory())

    def read_memory(self) -> VramReading:
        self.reads += 1
        if self._failures:
            raise GpuProbeError(self._failures.popleft())
        if self.error is not None:
            raise GpuProbeError(self.error)
        if self._external_script:
            self.external_mib = self._external_script.popleft()
        used = min(self.total_mib, self.external_mib + self.runtime_mib)
        return VramReading(self.total_mib, used, self.total_mib - used)

    def close(self) -> None:
        self.closes += 1
