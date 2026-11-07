"""TextRuntime: the port through which services generate text with the loaded model, with its types and errors.

Callers must hold a valid llm lease, which guarantees one request at a time on the runtime (docs/gpu-orchestration.md).
No error message carries a prompt or generated text: they hold only endpoint paths, status codes and reasons.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

# "stop" and "length" come from the runtime. "aborted" means `abort` ended the generation before it finished.
FinishReason = Literal["stop", "length", "aborted"]


@dataclass(frozen=True)
class TextRequest:
    """One generation, with the GenerationInput fields of docs/runtime-contracts.md and nothing else.

    `genkey` is unique per job and is what `abort` names. A field left at None, and an empty `stop_sequence`, are not
    sent, so the runtime's default applies. `grammar` is a GBNF string for structured output.
    """

    prompt: str
    max_context_length: int
    max_length: int
    genkey: str
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    rep_pen: float | None = None
    rep_pen_range: int | None = None
    dry_multiplier: float | None = None
    dry_base: float | None = None
    dry_allowed_length: int | None = None
    dry_penalty_last_n: int | None = None
    dry_sequence_breakers: tuple[str, ...] | None = None
    stop_sequence: tuple[str, ...] = ()
    trim_stop: bool | None = None
    grammar: str | None = None
    sampler_seed: int | None = None


@dataclass(frozen=True)
class TextChunk:
    """One piece of a streamed reply.

    Every chunk but the last has a non-empty `token` and `finish_reason` None. The last one, and only it, has a
    `finish_reason`, an empty `token`, and `text`: the whole reply, the tokens joined in order.
    """

    token: str
    finish_reason: FinishReason | None = None
    text: str | None = None

    @property
    def final(self) -> bool:
        return self.finish_reason is not None


@dataclass(frozen=True)
class TextRuntimeInfo:
    """What the runtime reports about itself: the model name, the release version and the real context size."""

    model: str
    version: str
    max_context_length: int


class TextRuntimeError(Exception):
    """Base of every failure of a TextRuntime. `code` is the job error code of docs/api.md (Errors) to report."""

    code: ClassVar[str] = "RUNTIME_UNAVAILABLE"


class RuntimeUnavailableError(TextRuntimeError):
    """The runtime did not answer, refused the connection, dropped it, or answered with an undocumented status."""


class RuntimeStreamDroppedError(RuntimeUnavailableError):
    """A stream ended before its closing event: the connection closed or broke in the middle of the reply."""


class RuntimeResponseInvalidError(RuntimeUnavailableError):
    """An answer is not valid JSON, or lacks the fields that docs/runtime-contracts.md documents."""


class RuntimeOutOfMemoryError(TextRuntimeError):
    """The runtime failed to allocate CUDA memory. The backend moves to the next launch level and the job retries."""

    code: ClassVar[str] = "LLM_OOM"


class GenerationStalledError(TextRuntimeError):
    """A stream sent nothing for the stall timeout. The adapter has asked the runtime to abort the generation."""

    code: ClassVar[str] = "GENERATION_STALLED"


class GenerationTimeoutError(TextRuntimeError):
    """A generation ran past the total timeout. The adapter has asked the runtime to abort it."""

    code: ClassVar[str] = "GENERATION_TIMEOUT"


class TextRuntime(Protocol):
    """Generates text with the currently loaded model. Every method raises a TextRuntimeError subclass on failure."""

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        """Returns the runtime tokenizer's count for `text`. `special` adds the start token the runtime prepends."""
        ...

    def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        """Yields the reply's tokens in order, then one final chunk with the finish reason and the whole text."""
        ...

    async def generate(self, request: TextRequest) -> str:
        """Returns the whole reply at once, for structured tasks."""
        ...

    async def abort(self, genkey: str) -> bool:
        """Asks the runtime to end the generation of `genkey`. Returns whether the runtime reported success."""
        ...

    async def info(self) -> TextRuntimeInfo:
        """Returns what the runtime reports about the loaded model."""
        ...
