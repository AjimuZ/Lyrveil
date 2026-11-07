"""GpuStateFollower: follows the orchestrator's GPU state and publishes each change as `gpu.state`, with its label.

docs/api.md (Event WebSocket) and docs/gpu-orchestration.md (Interface labels). The follower long-polls GET /v1/state
through the OrchestratorClient, so it hears of a transition as soon as the orchestrator makes it, and publishes the
state, the key of its Italian label in frontend/src/i18n/it.json, the queue depth and the blocked reason. Transitions
closer together than one answer are folded into the latest one: the momentary IDLE between the stop of one runtime and
the start of the other is usually not published. Like every event, `gpu.state` is a hint, and losing one loses no data.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

import anyio
from anyio import to_thread

from lyrveil.logs import get_logger
from lyrveil.orchestration.client import MAX_STATE_WAIT_S, GpuStatus, OrchestratorUnavailableError
from lyrveil.ports.events import GPU_STATE, EventPublisher

# The label keys of frontend/src/i18n/it.json, with their text from docs/gpu-orchestration.md (Interface labels).
WAITING = "gpu.state.waiting"  # In attesa della scheda video…
DRAWING = "gpu.state.drawing"  # Sta disegnando…
RETURNING = "gpu.state.returning"  # Sta tornando in chat…
RECOVERING = "gpu.state.recovering"  # Sto riprendendo il controllo della scheda video…
ERROR = "gpu.state.error"  # La scheda video non risponde. Riprova
INSUFFICIENT_VRAM = "gpu.blocked.insufficientVram"  # Altri programmi stanno usando la scheda video. Chiudili e riprova

# The states whose label does not depend on the desired mode. LLM_READY and GENERATING_TEXT have none: the
# conversation shows only the typing indicator.
_STATE_LABELS: Mapping[str, str | None] = MappingProxyType(
    {
        "LLM_READY": None,
        "GENERATING_TEXT": None,
        "UNLOADING_LLM": WAITING,
        "LOADING_IMAGE": WAITING,
        "GENERATING_IMAGE": DRAWING,
        "UNLOADING_IMAGE": RETURNING,
        "LOADING_LLM": RETURNING,
        "RECOVERING": RECOVERING,
        "ERROR": ERROR,
    }
)
# IDLE and IMAGE_READY, which the table of docs/gpu-orchestration.md leaves out, take the label of the switch they are
# part of, by the mode the orchestrator moves to: toward the image (steps 7, 9, 12 and 13 of the 18 steps) or back to
# text (step 15, and the moment between steps 13 and 14). With mode none or a drain no load follows, and no text shows.
_SWITCH_LABELS: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("IDLE", "image"): WAITING,
        ("IDLE", "llm"): RETURNING,
        ("IMAGE_READY", "image"): DRAWING,
        ("IMAGE_READY", "llm"): RETURNING,
    }
)
# Blocked reasons with a label of their own, which wins over the state's.
_BLOCKED_LABELS: Mapping[str, str] = MappingProxyType({"GPU_INSUFFICIENT_VRAM": INSUFFICIENT_VRAM})
# How long the follower waits after a failed read before it asks again.
RETRY_S = 5.0

logger = get_logger(__name__)


def label_key(status: GpuStatus) -> str | None:
    """Returns the it.json key of the interface label for `status`, or None when the interface shows no GPU text.

    A blocked reason with its own label wins over the state. A state the backend does not know has no label.
    """
    blocked = None if status.blocked_reason is None else _BLOCKED_LABELS.get(status.blocked_reason)
    if blocked is not None:
        return blocked
    if status.state in _STATE_LABELS:
        return _STATE_LABELS[status.state]
    target = "none" if status.draining else status.desired_mode
    return _SWITCH_LABELS.get((status.state, target))


class GpuStateSource(Protocol):
    """The long poll of OrchestratorClient. It blocks, so the follower runs it in a worker thread."""

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus: ...


class GpuStateFollower:
    """Publishes `gpu.state` on `events` once for each GPU state `source` reports with a new `boot_id` or `seq`.

    The event's data holds `state`, `label_key`, `queue` and `blocked_reason`. `queue_depth` returns the number of
    queued and running jobs; it blocks, so it runs in a worker thread. The first read, and the first after a failure,
    does not wait, since the orchestrator may have restarted with a `seq` below the last one seen; later reads wait up
    to `wait_s` for the next transition.
    """

    def __init__(
        self,
        source: GpuStateSource,
        events: EventPublisher,
        queue_depth: Callable[[], int],
        *,
        wait_s: float = MAX_STATE_WAIT_S,
        retry_s: float = RETRY_S,
    ) -> None:
        self._source = source
        self._events = events
        self._queue_depth = queue_depth
        self._wait_s = wait_s
        self._retry_s = retry_s
        # (boot_id, seq) of the last state published, None before the first.
        self._published: tuple[str | None, int | None] | None = None
        self._resync = True

    async def run_forever(self) -> None:
        """Follows the state until cancelled. After a failed read, or an unexpected error that is logged, it waits
        `retry_s` and reads again without waiting for a change."""
        while True:
            try:
                followed = await self.follow_once()
            except Exception:
                logger.error("gpu_state_follow_failed", exc_info=True)
                self._resync = True
                followed = None
            if followed is None:
                await anyio.sleep(self._retry_s)

    async def follow_once(self) -> GpuStatus | None:
        """Reads the next GPU state and publishes it when it is new. Returns the state read, or None when the
        orchestrator did not answer, which makes the next read a resync."""
        last_seq = None if self._published is None else self._published[1]
        since, wait_s = (0, 0.0) if self._resync or last_seq is None else (last_seq, self._wait_s)
        try:
            status = await to_thread.run_sync(self._source.wait_for_state, since, wait_s)
        except OrchestratorUnavailableError:
            if not self._resync:
                logger.info("gpu_state_unavailable")
            self._resync = True
            return None
        self._resync = False
        seen = (status.boot_id, status.seq)
        if seen != self._published:
            queue = await to_thread.run_sync(self._queue_depth)
            self._events.publish(
                GPU_STATE,
                {
                    "state": status.state,
                    "label_key": label_key(status),
                    "queue": queue,
                    "blocked_reason": status.blocked_reason,
                },
            )
            self._published = seen
        return status
