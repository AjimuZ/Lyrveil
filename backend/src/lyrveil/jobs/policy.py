"""Job kinds with their GPU mode, priority, class, attempts and retry backoff.

The values come from docs/gpu-orchestration.md (Job kinds and priorities) and docs/backend.md (Retry policy). Backoffs
the retry table does not give are this module's choices, listed in docs/backend.md (Job queue).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class JobKind(StrEnum):
    CHAT_REPLY = "chat_reply"
    CHAT_REGENERATE = "chat_regenerate"
    CHARACTER_DRAFT = "character_draft"
    IMAGE_REQUEST = "image_request"
    VISUAL_PROFILE_TRANSLATE = "visual_profile_translate"
    PROACTIVE_MESSAGE = "proactive_message"
    IMAGE_PROPOSAL = "image_proposal"
    SUMMARY_CHECKPOINT = "summary_checkpoint"
    MEMORY_EXTRACTION = "memory_extraction"
    MEMORY_REEVALUATION = "memory_reevaluation"
    MAINTENANCE = "maintenance"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RequiredMode(StrEnum):
    """The GPU mode a job stage needs. NONE runs in any mode, and also when no runtime is loaded."""

    LLM = "llm"
    IMAGE = "image"
    NONE = "none"


class JobClass(StrEnum):
    """Foreground work answers the user. Soft and background work never causes a mode change (ADR 0010)."""

    FOREGROUND = "foreground"
    SOFT = "soft"
    BACKGROUND = "background"


# The single stage of kinds that have one. Kinds with several stages list them in `stages`.
DEFAULT_STAGE = "run"


@dataclass(frozen=True)
class KindPolicy:
    """What the queue needs to know about one kind.

    `stages` maps each stage, in run order, to the mode it needs; the first one is where a new job starts.
    `backoff_s[n - 1]` is the wait after the failed attempt n; past the end of the tuple, its last value repeats.
    Always holds: `max_attempts >= 1`, and `stages` is not empty.
    """

    priority: int
    job_class: JobClass
    max_attempts: int
    backoff_s: tuple[float, ...]
    stages: Mapping[str, RequiredMode] = field(
        default_factory=lambda: MappingProxyType({DEFAULT_STAGE: RequiredMode.LLM})
    )

    @property
    def first_stage(self) -> str:
        return next(iter(self.stages))

    def required_mode(self, stage: str) -> RequiredMode:
        """Returns the mode `stage` needs. Raises KeyError for a stage the kind does not have."""
        return self.stages[stage]

    def retry_delay_s(self, failed_attempt: int) -> float:
        """Returns the seconds to wait after failed attempt number `failed_attempt`, 1-based; 0 without backoff."""
        if not self.backoff_s:
            return 0.0
        return self.backoff_s[min(max(failed_attempt, 1), len(self.backoff_s)) - 1]


def _stages(*pairs: tuple[str, RequiredMode]) -> Mapping[str, RequiredMode]:
    return MappingProxyType(dict(pairs))


_TEXT_FOREGROUND_BACKOFF_S = (5.0,)
_LONG_BACKOFF_S = (60.0, 300.0, 1800.0)

POLICIES: Mapping[JobKind, KindPolicy] = MappingProxyType(
    {
        JobKind.CHAT_REPLY: KindPolicy(10, JobClass.FOREGROUND, 2, _TEXT_FOREGROUND_BACKOFF_S),
        JobKind.CHAT_REGENERATE: KindPolicy(10, JobClass.FOREGROUND, 2, _TEXT_FOREGROUND_BACKOFF_S),
        JobKind.CHARACTER_DRAFT: KindPolicy(20, JobClass.FOREGROUND, 2, _TEXT_FOREGROUND_BACKOFF_S),
        # docs/image-generation.md (Job stages): the stage decides the mode.
        JobKind.IMAGE_REQUEST: KindPolicy(
            30,
            JobClass.FOREGROUND,
            2,
            (10.0,),
            _stages(("prompt", RequiredMode.LLM), ("render", RequiredMode.IMAGE), ("persist", RequiredMode.NONE)),
        ),
        JobKind.VISUAL_PROFILE_TRANSLATE: KindPolicy(40, JobClass.FOREGROUND, 2, _TEXT_FOREGROUND_BACKOFF_S),
        # docs/scheduler.md (Generation flow): decide, then generate.
        JobKind.PROACTIVE_MESSAGE: KindPolicy(
            50,
            JobClass.SOFT,
            2,
            _TEXT_FOREGROUND_BACKOFF_S,
            _stages(("decide", RequiredMode.LLM), ("generate", RequiredMode.LLM)),
        ),
        JobKind.IMAGE_PROPOSAL: KindPolicy(60, JobClass.BACKGROUND, 1, ()),
        JobKind.SUMMARY_CHECKPOINT: KindPolicy(70, JobClass.BACKGROUND, 3, _LONG_BACKOFF_S),
        JobKind.MEMORY_EXTRACTION: KindPolicy(80, JobClass.BACKGROUND, 3, _LONG_BACKOFF_S),
        JobKind.MEMORY_REEVALUATION: KindPolicy(80, JobClass.BACKGROUND, 3, _LONG_BACKOFF_S),
        JobKind.MAINTENANCE: KindPolicy(90, JobClass.BACKGROUND, 1, (), _stages((DEFAULT_STAGE, RequiredMode.NONE))),
    }
)


def policy_for(kind: JobKind) -> KindPolicy:
    """Returns the policy of `kind`. Every JobKind has one."""
    return POLICIES[kind]
