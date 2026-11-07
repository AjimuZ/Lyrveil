"""The scheduling rules of docs/gpu-orchestration.md (Selection, Preemption) as pure functions: which GPU mode the
queue needs and which GPU job runs next, from a snapshot of the queue, the image batch and the user's idle time.

Nothing here reads a clock, the database or the orchestrator, so the rules are deterministic and tested with plain
values (T-ORC-12). JobScheduler takes the snapshot and applies the decision.
"""

from dataclasses import dataclass
from typing import Literal

from lyrveil.jobs.policy import JobClass
from lyrveil.jobs.queue import Job

# The two GPU modes the rules choose between. `none` is never chosen: a queue that needs no GPU stays in text mode.
GpuMode = Literal["llm", "image"]


@dataclass(frozen=True)
class SchedulingRules:
    """The settings of ADR 0010, with the defaults of docs/configuration.md. They are not read from
    `application_settings` yet.

    `image_max_wait_s` is `images.max_wait_seconds`, `images_max_in_a_row` is `images.max_in_a_row`, and
    `background_idle_s` is `jobs.background_idle_seconds`.
    """

    image_max_wait_s: float = 60.0
    images_max_in_a_row: int = 4
    background_idle_s: float = 20.0


@dataclass(frozen=True)
class QueueSnapshot:
    """The eligible queued jobs the rules choose among, each the first of its group in queue order (priority, then
    age, then id), or None when the group is empty.

    `foreground_text` is a foreground job whose stage needs `llm`, such as a reply or an `image_request` in stage
    `prompt`; `other_text` a soft or background job whose stage needs `llm`; `image` a job whose stage needs `image`.
    `image_waited_s` is how long the image that has waited longest has waited in text mode, 0 without an image.
    """

    foreground_text: Job | None = None
    other_text: Job | None = None
    image: Job | None = None
    image_waited_s: float = 0.0


@dataclass(frozen=True)
class ImageBatch:
    """The images run since the GPU last entered image mode, and whether the last batch ended at the cap.

    `capped` stays true from the moment a batch ends at `images_max_in_a_row` until text work runs or an image takes
    the GPU back by its wait, so the text mode that follows a capped batch gives text work its turn.
    """

    images_in_a_row: int = 0
    capped: bool = False


@dataclass(frozen=True)
class Decision:
    """What the queue needs now.

    `mode` is the GPU mode to be in. `run` is the GPU job to run once the GPU is ready in `mode`, or None when no GPU
    job may run yet; stages that need no GPU run either way. `deciding` is the job named on a mode request, None when
    nothing queued needs the GPU. `caps_batch` is true when this decision ends an image batch at the cap.
    """

    mode: GpuMode
    run: Job | None
    deciding: Job | None
    caps_batch: bool = False


def decide(
    mode: GpuMode,
    queue: QueueSnapshot,
    batch: ImageBatch,
    idle_s: float | None,
    rules: SchedulingRules,
) -> Decision:
    """Returns the decision of docs/gpu-orchestration.md (Selection) for a GPU whose desired mode is `mode`.

    `idle_s` is how long the user has been idle, None when the backend has seen no foreground work since it started.

    In text mode: an image that has waited `image_max_wait_s` takes the GPU; else foreground text runs; else a queued
    image takes the GPU, unless the last batch ended at the cap; else soft or background work runs once the user has
    been idle `background_idle_s`; else the GPU stays in text mode. In image mode: foreground text takes the GPU back;
    else the next image runs while the batch is under `images_max_in_a_row`, or past it when no other text work
    waits, since a switch would then serve nobody; else the GPU returns to text mode.

    Soft and background work never causes a switch to image mode, and never takes the GPU from a waiting image.
    """
    foreground, other, image = queue.foreground_text, queue.other_text, queue.image
    if mode == "image":
        if foreground is not None:
            return Decision("llm", foreground, foreground)
        if image is not None and (batch.images_in_a_row < rules.images_max_in_a_row or other is None):
            return Decision("image", image, image)
        return Decision("llm", None, other, caps_batch=image is not None)
    if image is not None and queue.image_waited_s >= rules.image_max_wait_s:
        return Decision("image", image, image)
    if foreground is not None:
        return Decision("llm", foreground, foreground)
    if image is not None and not batch.capped:
        return Decision("image", image, image)
    if other is not None and (idle_s is None or idle_s >= rules.background_idle_s):
        return Decision("llm", other, other)
    return Decision("llm", None, other)


def preempts(running: JobClass, arriving: JobClass) -> bool:
    """Tells whether a queued job of class `arriving` stops a running generation of class `running`
    (docs/gpu-orchestration.md, Preemption): only foreground work stops background work. A running foreground job is
    never preempted, and soft work is not background work."""
    return running is JobClass.BACKGROUND and arriving is JobClass.FOREGROUND
