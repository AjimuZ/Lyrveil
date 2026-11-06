"""The GPU state machine of docs/gpu-orchestration.md as a pure transition table, with no I/O.

The table is the only authority on which state may follow which. Callers such as the lifecycle manager decide when a
transition happens, perform the container and memory work around it, and log and emit the event; this module only
answers whether a move is legal and refuses the ones that are not.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class GpuState(StrEnum):
    """Every state of the GPU, with the values the control API and the event log use."""

    IDLE = "IDLE"
    LOADING_LLM = "LOADING_LLM"
    LLM_READY = "LLM_READY"
    GENERATING_TEXT = "GENERATING_TEXT"
    UNLOADING_LLM = "UNLOADING_LLM"
    LOADING_IMAGE = "LOADING_IMAGE"
    IMAGE_READY = "IMAGE_READY"
    GENERATING_IMAGE = "GENERATING_IMAGE"
    UNLOADING_IMAGE = "UNLOADING_IMAGE"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"


class Runtime(StrEnum):
    """The two runtime containers, which never hold the GPU at the same time."""

    LLM = "llm"
    IMAGE = "image"


@dataclass(frozen=True)
class Transition:
    """One legal move: `source` may be followed by `target` when `trigger` happens. The trigger is descriptive."""

    source: GpuState
    target: GpuState
    trigger: str


# The name comes from docs/gpu-orchestration.md, so the Error suffix that N818 asks for is not added.
class IllegalTransition(Exception):  # noqa: N818
    """Raised for a move that the table does not contain. The state of the caller must stay `source`."""

    def __init__(self, source: GpuState, target: GpuState) -> None:
        super().__init__(f"illegal GPU state transition {source} -> {target}")
        self.source = source
        self.target = target


# The state on process start: reality is reconciled before anything else happens.
INITIAL_STATE = GpuState.RECOVERING

# The diagram of docs/gpu-orchestration.md, edge by edge. A self-transition is never legal.
TRANSITIONS: tuple[Transition, ...] = (
    Transition(GpuState.RECOVERING, GpuState.IDLE, "nothing running"),
    Transition(GpuState.RECOVERING, GpuState.LLM_READY, "LLM healthy"),
    Transition(GpuState.RECOVERING, GpuState.IMAGE_READY, "image runtime healthy"),
    Transition(GpuState.RECOVERING, GpuState.ERROR, "not recoverable"),
    Transition(GpuState.IDLE, GpuState.LOADING_LLM, "mode llm and enough VRAM"),
    Transition(GpuState.IDLE, GpuState.LOADING_IMAGE, "mode image and enough VRAM"),
    Transition(GpuState.LOADING_LLM, GpuState.LLM_READY, "healthy, model verified"),
    Transition(GpuState.LOADING_LLM, GpuState.RECOVERING, "failed or timed out"),
    Transition(GpuState.LLM_READY, GpuState.GENERATING_TEXT, "lease granted"),
    Transition(GpuState.GENERATING_TEXT, GpuState.LLM_READY, "lease released"),
    Transition(GpuState.GENERATING_TEXT, GpuState.RECOVERING, "runtime failed, oom, lease expired"),
    Transition(GpuState.LLM_READY, GpuState.UNLOADING_LLM, "mode changed or drain"),
    Transition(GpuState.LLM_READY, GpuState.RECOVERING, "health lost"),
    Transition(GpuState.UNLOADING_LLM, GpuState.IDLE, "release verified"),
    Transition(GpuState.UNLOADING_LLM, GpuState.RECOVERING, "stop or release failed"),
    Transition(GpuState.LOADING_IMAGE, GpuState.IMAGE_READY, "healthy"),
    Transition(GpuState.LOADING_IMAGE, GpuState.RECOVERING, "failed or timed out"),
    Transition(GpuState.IMAGE_READY, GpuState.GENERATING_IMAGE, "lease granted"),
    Transition(GpuState.GENERATING_IMAGE, GpuState.IMAGE_READY, "lease released"),
    Transition(GpuState.GENERATING_IMAGE, GpuState.RECOVERING, "runtime failed, oom, lease expired"),
    Transition(GpuState.IMAGE_READY, GpuState.UNLOADING_IMAGE, "mode changed or drain"),
    Transition(GpuState.IMAGE_READY, GpuState.RECOVERING, "health lost"),
    Transition(GpuState.UNLOADING_IMAGE, GpuState.IDLE, "release verified"),
    Transition(GpuState.UNLOADING_IMAGE, GpuState.RECOVERING, "stop or release failed"),
    Transition(GpuState.ERROR, GpuState.RECOVERING, "user action or timed retry"),
)

# The runtime that a state allows to hold the GPU, from the Containers column of the state table. IDLE, RECOVERING
# and ERROR allow none: RECOVERING may find containers running, but it only stops them or confirms one healthy.
STATE_RUNTIME: Mapping[GpuState, Runtime | None] = MappingProxyType(
    {
        GpuState.IDLE: None,
        GpuState.LOADING_LLM: Runtime.LLM,
        GpuState.LLM_READY: Runtime.LLM,
        GpuState.GENERATING_TEXT: Runtime.LLM,
        GpuState.UNLOADING_LLM: Runtime.LLM,
        GpuState.LOADING_IMAGE: Runtime.IMAGE,
        GpuState.IMAGE_READY: Runtime.IMAGE,
        GpuState.GENERATING_IMAGE: Runtime.IMAGE,
        GpuState.UNLOADING_IMAGE: Runtime.IMAGE,
        GpuState.RECOVERING: None,
        GpuState.ERROR: None,
    }
)

_TARGETS: Mapping[GpuState, frozenset[GpuState]] = MappingProxyType(
    {state: frozenset(t.target for t in TRANSITIONS if t.source is state) for state in GpuState}
)


def allowed_targets(source: GpuState) -> frozenset[GpuState]:
    """Returns the states that may follow `source`. Every state has at least one, so no state is a dead end."""
    return _TARGETS[source]


def is_legal(source: GpuState, target: GpuState) -> bool:
    """Returns True when the table contains the move from `source` to `target`."""
    return target in _TARGETS[source]


def transition(source: GpuState, target: GpuState) -> GpuState:
    """Returns `target` when the move from `source` is legal.

    Raises IllegalTransition for any other move, including a move from a state to itself. Nothing is changed either
    way: the caller holds the current state and replaces it only with the returned value.
    """
    if not is_legal(source, target):
        raise IllegalTransition(source, target)
    return target
