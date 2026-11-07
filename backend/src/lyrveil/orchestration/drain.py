"""The GPU drain before a shutdown: step 2 of docs/gpu-orchestration.md (Safe shutdown).

`stop.cmd` runs `python -m lyrveil.cli drain-gpu` in the backend container, after the queue drain, because the backend
holds the control token and the orchestrator publishes no port. POST /v1/drain makes the orchestrator stop the runtime
once the current lease ends and verify its release; the command then follows the state until IDLE.
"""

import time
from collections.abc import Callable
from typing import Protocol

from lyrveil.orchestration.client import MAX_STATE_WAIT_S, GpuStatus

# The states a drain ends in: IDLE once the release is verified, ERROR when the orchestrator could not verify it.
FINAL_STATES = frozenset({"IDLE", "ERROR"})


class GpuDrainControl(Protocol):
    """The drain and the long poll of OrchestratorClient. Both block."""

    def drain(self) -> GpuStatus: ...
    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus: ...


def drain_gpu(
    control: GpuDrainControl, timeout_s: float, *, monotonic: Callable[[], float] = time.monotonic
) -> GpuStatus:
    """Starts the drain and returns the GPU state once it is IDLE or ERROR, or the last state read when `timeout_s`
    has passed first. IDLE means that no runtime holds the GPU and its release was verified.

    Raises OrchestratorUnavailableError when the control API does not answer. Blocks. Raises ValueError for a negative
    `timeout_s`.
    """
    if timeout_s < 0:
        raise ValueError("timeout_s must be at least 0")
    deadline = monotonic() + timeout_s
    status = control.drain()
    while status.state not in FINAL_STATES:
        left = deadline - monotonic()
        if left <= 0:
            break
        status = control.wait_for_state(status.seq or 0, min(left, MAX_STATE_WAIT_S))
    return status
