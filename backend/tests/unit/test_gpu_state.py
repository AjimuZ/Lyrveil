"""Tests of lyrveil/orchestration/gpu_state.py: the interface label of every GPU state of docs/gpu-orchestration.md, and
the follower that publishes `gpu.state` (docs/api.md, Event WebSocket).

The Italian text of each label key is checked by the frontend's translator spec, against the same table of
docs/gpu-orchestration.md (Interface labels).
"""

import pytest

from lyrveil.events.hub import EventHub
from lyrveil.orchestration.client import DesiredMode, GpuStatus, OrchestratorUnavailableError
from lyrveil.orchestration.gpu_state import GpuStateFollower, label_key

BOOT = "backend-boot"
# The states of docs/gpu-orchestration.md (State machine), written out again so that a new state must get a label.
DOCUMENTED_STATES = {
    "IDLE",
    "LOADING_LLM",
    "LLM_READY",
    "GENERATING_TEXT",
    "UNLOADING_LLM",
    "LOADING_IMAGE",
    "IMAGE_READY",
    "GENERATING_IMAGE",
    "UNLOADING_IMAGE",
    "RECOVERING",
    "ERROR",
}
# Each state as an image request meets it, with the mode the orchestrator moves to at that point, and its label key.
EVERY_STATE: list[tuple[str, DesiredMode, str | None]] = [
    ("RECOVERING", "llm", "gpu.state.recovering"),
    ("IDLE", "llm", "gpu.state.returning"),
    ("LOADING_LLM", "llm", "gpu.state.returning"),
    ("LLM_READY", "llm", None),
    ("GENERATING_TEXT", "llm", None),
    ("UNLOADING_LLM", "image", "gpu.state.waiting"),
    ("IDLE", "image", "gpu.state.waiting"),
    ("LOADING_IMAGE", "image", "gpu.state.waiting"),
    ("IMAGE_READY", "image", "gpu.state.drawing"),
    ("GENERATING_IMAGE", "image", "gpu.state.drawing"),
    ("IMAGE_READY", "llm", "gpu.state.returning"),
    ("UNLOADING_IMAGE", "llm", "gpu.state.returning"),
    ("ERROR", "llm", "gpu.state.error"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def status(
    state: str,
    desired: DesiredMode = "llm",
    seq: int | None = None,
    boot_id: str | None = "orchestrator-1",
    *,
    draining: bool = False,
    blocked: str | None = None,
) -> GpuStatus:
    return GpuStatus(state, desired, draining, seq=seq, boot_id=boot_id, blocked_reason=blocked)


def test_the_table_covers_every_documented_state() -> None:
    assert {state for state, _, _ in EVERY_STATE} == DOCUMENTED_STATES


@pytest.mark.parametrize(("state", "desired", "key"), EVERY_STATE)
def test_every_state_has_its_documented_label(state: str, desired: DesiredMode, key: str | None) -> None:
    assert label_key(status(state, desired)) == key


def test_no_label_follows_a_switch_that_leads_nowhere_and_a_blocked_load_names_its_cause() -> None:
    # With mode none or a drain nothing loads after IDLE, and a ready image runtime is about to stop.
    assert label_key(status("IDLE", "none")) is None
    assert label_key(status("IDLE", "image", draining=True)) is None
    assert label_key(status("IMAGE_READY", "none")) is None
    # A profile change stops the text runtime with mode none: the documented label of the state still holds.
    assert label_key(status("UNLOADING_LLM", "none")) == "gpu.state.waiting"
    # A load held back by other programs on the card has its own label, whatever the state.
    assert label_key(status("IDLE", "image", blocked="GPU_INSUFFICIENT_VRAM")) == "gpu.blocked.insufficientVram"
    assert label_key(status("IDLE", "image", blocked="SOMETHING_ELSE")) == "gpu.state.waiting"
    assert label_key(status("SOME_FUTURE_STATE")) is None


class Source:
    """A GpuStateSource that answers with the scripted states in order, raising each exception of the script instead
    of answering, and records each (since, wait_s) it was asked."""

    def __init__(self, *answers: GpuStatus | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[int, float]] = []

    def wait_for_state(self, since: int, wait_s: float) -> GpuStatus:
        self.calls.append((since, wait_s))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.anyio
async def test_each_new_state_is_published_once_with_its_label_the_queue_and_the_blocked_reason() -> None:
    source = Source(
        status("LLM_READY", "llm", 3),
        status("LLM_READY", "llm", 3),
        status("UNLOADING_LLM", "image", 4),
        # The IDLE of seq 5 came and went within one answer, so it is folded into LOADING_IMAGE.
        status("LOADING_IMAGE", "image", 6),
        OrchestratorUnavailableError("the control API did not answer"),
        # The orchestrator restarted: its sequence starts again.
        status("RECOVERING", "llm", 1, "orchestrator-2"),
        status("IDLE", "image", 2, "orchestrator-2", blocked="GPU_INSUFFICIENT_VRAM"),
    )
    hub = EventHub(BOOT)
    depths = iter([0, 1, 1, 2, 2])
    follower = GpuStateFollower(source, hub, lambda: next(depths), wait_s=25.0)

    followed = [await follower.follow_once() for _ in range(7)]

    assert [None if item is None else item.state for item in followed] == [
        "LLM_READY",
        "LLM_READY",
        "UNLOADING_LLM",
        "LOADING_IMAGE",
        None,
        "RECOVERING",
        "IDLE",
    ]
    # The first read and the read after the failure answer at once; the others wait for the next transition.
    assert source.calls == [(0, 0.0), (3, 25.0), (3, 25.0), (4, 25.0), (6, 25.0), (0, 0.0), (1, 25.0)]
    events = hub.missed(0, BOOT)
    assert events is not None
    assert {event.type for event in events} == {"gpu.state"}
    assert [dict(event.data) for event in events] == [
        {"state": "LLM_READY", "label_key": None, "queue": 0, "blocked_reason": None},
        {"state": "UNLOADING_LLM", "label_key": "gpu.state.waiting", "queue": 1, "blocked_reason": None},
        {"state": "LOADING_IMAGE", "label_key": "gpu.state.waiting", "queue": 1, "blocked_reason": None},
        {"state": "RECOVERING", "label_key": "gpu.state.recovering", "queue": 2, "blocked_reason": None},
        {
            "state": "IDLE",
            "label_key": "gpu.blocked.insufficientVram",
            "queue": 2,
            "blocked_reason": "GPU_INSUFFICIENT_VRAM",
        },
    ]
