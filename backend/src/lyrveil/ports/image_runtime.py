"""ImageRuntime: the port through which services render images with the image runtime, with its types and errors.

Callers must hold a valid image lease, which guarantees one render at a time on the runtime (docs/gpu-orchestration.md).
No error message carries a prompt: they hold only endpoint paths, status codes, node identifiers and reasons.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol

# The runtime phases of docs/image-generation.md (Progress). `prompt`, `waiting` and `saving` come from the job stage
# and the lifecycle state, so a runtime never reports them.
ImagePhase = Literal["loading", "sampling", "refining"]
# "interrupted" means `interrupt` ended the render, or another client's interrupt did, before it produced an image.
RenderStatus = Literal["success", "interrupted"]


@dataclass(frozen=True)
class ImageRequest:
    """One render: a workflow in ComfyUI API format, and the `prompt_id` that names it.

    `workflow` maps node identifiers to `{"class_type": ..., "inputs": {...}}`, filled by typed code from a file of
    `workflows/comfyui/`. `prompt_id` is unique per render; the runtime's messages and history are keyed by it.
    """

    workflow: dict[str, Any]
    prompt_id: str


@dataclass(frozen=True)
class ImageProgress:
    """One step of a render. `value` and `max` are the sampler's real step counts, and both are None for `loading`."""

    phase: ImagePhase
    value: int | None = None
    max: int | None = None


@dataclass(frozen=True)
class ImageOutput:
    """A file the render saved in the runtime, as the history lists it. `download` fetches its bytes."""

    filename: str
    subfolder: str
    type: str


@dataclass(frozen=True)
class RenderFinished:
    """The last event of a render. `images` holds at least one file for "success" and is empty for "interrupted"."""

    status: RenderStatus
    images: tuple[ImageOutput, ...] = ()


ImageEvent = ImageProgress | RenderFinished


@dataclass(frozen=True)
class ImageDevice:
    """One compute device the runtime reports, with its memory in bytes."""

    name: str
    type: str
    vram_total: int
    vram_free: int


@dataclass(frozen=True)
class ImageRuntimeInfo:
    """What the runtime reports about itself: its release version, stored with each image, and its devices."""

    version: str
    devices: tuple[ImageDevice, ...]


class ImageRuntimeError(Exception):
    """Base of every failure of an ImageRuntime. `code` is the job error code of docs/api.md (Errors) to report."""

    code: ClassVar[str] = "RUNTIME_UNAVAILABLE"


class ImageRuntimeUnavailableError(ImageRuntimeError):
    """The runtime did not answer, refused or dropped the connection, or answered with an undocumented status."""


class ImageConnectionLostError(ImageRuntimeUnavailableError):
    """The progress connection closed or broke before the render's closing message: the runtime crashed or stopped."""


class ImageResponseInvalidError(ImageRuntimeUnavailableError):
    """An answer is not valid JSON, lacks the fields that docs/runtime-contracts.md documents, or is too large."""


class ImageOutOfMemoryError(ImageRuntimeError):
    """The runtime failed to allocate CUDA memory. The job retries with a tiled workflow (docs/image-generation.md)."""

    code: ClassVar[str] = "IMAGE_OOM"


class WorkflowRejectedError(ImageRuntimeError):
    """The runtime refused the workflow at validation. Lyrveil submits only its own files, so this is a defect.

    `node_ids` lists the nodes the runtime named, which is enough to find the faulty input without logging the prompt.
    """

    code: ClassVar[str] = "INTERNAL"

    def __init__(self, message: str, node_ids: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.node_ids = node_ids


class RenderFailedError(ImageRuntimeError):
    """A node failed during the render for a reason other than memory. Retrying the same workflow would not help."""

    code: ClassVar[str] = "INTERNAL"


class RenderStalledError(ImageRuntimeError):
    """The render sent no message for the stall timeout. The adapter has asked the runtime to interrupt it."""

    code: ClassVar[str] = "GENERATION_STALLED"


class RenderTimeoutError(ImageRuntimeError):
    """The render ran past the total timeout. The adapter has asked the runtime to interrupt it."""

    code: ClassVar[str] = "GENERATION_TIMEOUT"


class ImageRuntime(Protocol):
    """Renders images with the running image runtime. Every method raises an ImageRuntimeError subclass on failure."""

    def render(self, request: ImageRequest) -> AsyncIterator[ImageEvent]:
        """Queues the workflow, yields its progress in order, then one RenderFinished, always the last event."""
        ...

    async def download(self, image: ImageOutput) -> bytes:
        """Returns the bytes of a file that a finished render listed."""
        ...

    async def interrupt(self) -> bool:
        """Asks the runtime to stop the running render. Returns whether the runtime accepted the request."""
        ...

    async def info(self) -> ImageRuntimeInfo:
        """Returns what the runtime reports about itself."""
        ...
