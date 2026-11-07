"""T-ORC-12, the rules part: priorities, batching, fairness and preemption of lyrveil.jobs.selection, with plain values
for the queue, the batch and the time (docs/gpu-orchestration.md, Selection and Preemption).

The scheduler side, a real preemption with the runtime's abort, is backend/tests/integration/test_job_cancellation.py.
"""

from datetime import UTC, datetime

import pytest

from lyrveil.jobs.policy import JobClass, JobKind, JobStatus, RequiredMode, policy_for
from lyrveil.jobs.queue import Job
from lyrveil.jobs.selection import GpuMode, ImageBatch, QueueSnapshot, SchedulingRules, decide, preempts

RULES = SchedulingRules()
CREATED = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


def job(kind: JobKind, stage: str | None = None, name: str = "") -> Job:
    policy = policy_for(kind)
    stage = stage or policy.first_stage
    return Job(
        id=name or f"{kind.value}-{stage}",
        kind=kind,
        status=JobStatus.QUEUED,
        stage=stage,
        required_mode=policy.required_mode(stage),
        priority=policy.priority,
        conversation_id=None,
        character_id=None,
        message_id=None,
        payload={},
        result=None,
        attempt=0,
        max_attempts=policy.max_attempts,
        not_before=None,
        lease_id=None,
        genkey=None,
        idempotency_key=None,
        error_code=None,
        error_detail=None,
        cancel_requested_at=None,
        created_at=CREATED,
        started_at=None,
        finished_at=None,
    )


REPLY = job(JobKind.CHAT_REPLY)
PROMPT = job(JobKind.IMAGE_REQUEST, "prompt")
IMAGE = job(JobKind.IMAGE_REQUEST, "render")
EXTRACTION = job(JobKind.MEMORY_EXTRACTION)
PROACTIVE = job(JobKind.PROACTIVE_MESSAGE)


def test_foreground_text_runs_first_in_text_mode_and_an_image_prompt_counts_as_one() -> None:
    queue = QueueSnapshot(foreground_text=REPLY, other_text=EXTRACTION, image=IMAGE, image_waited_s=59.9)
    decision = decide("llm", queue, ImageBatch(), idle_s=300.0, rules=RULES)
    assert (decision.mode, decision.run, decision.deciding) == ("llm", REPLY, REPLY)

    prompt = decide("llm", QueueSnapshot(foreground_text=PROMPT, image=IMAGE), ImageBatch(), None, RULES)
    assert (prompt.mode, prompt.run) == ("llm", PROMPT)


def test_an_image_that_waited_the_limit_takes_the_gpu_from_foreground_text() -> None:
    queue = QueueSnapshot(foreground_text=REPLY, image=IMAGE, image_waited_s=60.0)
    decision = decide("llm", queue, ImageBatch(), idle_s=0.0, rules=RULES)
    assert (decision.mode, decision.run, decision.deciding) == ("image", IMAGE, IMAGE)
    # The limit is a setting.
    shorter = SchedulingRules(image_max_wait_s=10.0)
    queue = QueueSnapshot(foreground_text=REPLY, image=IMAGE, image_waited_s=10.0)
    assert decide("llm", queue, ImageBatch(), 0.0, shorter).mode == "image"


def test_an_image_goes_before_soft_and_background_work_in_text_mode() -> None:
    queue = QueueSnapshot(other_text=EXTRACTION, image=IMAGE)
    decision = decide("llm", queue, ImageBatch(), idle_s=300.0, rules=RULES)
    assert (decision.mode, decision.run) == ("image", IMAGE)


@pytest.mark.parametrize("idle_s", [0.0, 19.9])
def test_background_work_waits_for_the_user_to_be_idle_and_never_switches_modes(idle_s: float) -> None:
    decision = decide("llm", QueueSnapshot(other_text=EXTRACTION), ImageBatch(), idle_s, RULES)
    assert (decision.mode, decision.run, decision.deciding) == ("llm", None, EXTRACTION)


@pytest.mark.parametrize("idle_s", [None, 20.0, 600.0])
def test_background_and_soft_work_run_once_the_user_is_idle(idle_s: float | None) -> None:
    for other in (EXTRACTION, PROACTIVE):
        decision = decide("llm", QueueSnapshot(other_text=other), ImageBatch(), idle_s, RULES)
        assert (decision.mode, decision.run) == ("llm", other)


def test_an_empty_queue_stays_in_text_mode_and_names_no_job() -> None:
    modes: tuple[GpuMode, ...] = ("llm", "image")
    for mode in modes:
        decision = decide(mode, QueueSnapshot(), ImageBatch(), None, RULES)
        assert (decision.mode, decision.run, decision.deciding, decision.caps_batch) == ("llm", None, None, False)


def test_images_run_in_a_batch_until_the_cap() -> None:
    queue = QueueSnapshot(other_text=EXTRACTION, image=IMAGE)
    for done in range(RULES.images_max_in_a_row):
        decision = decide("image", queue, ImageBatch(images_in_a_row=done), None, RULES)
        assert (decision.mode, decision.run, decision.caps_batch) == ("image", IMAGE, False)
    capped = decide("image", queue, ImageBatch(images_in_a_row=4), None, RULES)
    assert (capped.mode, capped.run, capped.deciding, capped.caps_batch) == ("llm", None, EXTRACTION, True)


def test_the_cap_does_not_end_a_batch_that_no_text_work_waits_for() -> None:
    decision = decide("image", QueueSnapshot(image=IMAGE), ImageBatch(images_in_a_row=9), None, RULES)
    assert (decision.mode, decision.run, decision.caps_batch) == ("image", IMAGE, False)


def test_after_a_capped_batch_text_work_gets_its_turn_before_the_next_image() -> None:
    queue = QueueSnapshot(other_text=EXTRACTION, image=IMAGE, image_waited_s=30.0)
    capped = ImageBatch(images_in_a_row=4, capped=True)
    decision = decide("llm", queue, capped, idle_s=25.0, rules=RULES)
    assert (decision.mode, decision.run) == ("llm", EXTRACTION)
    # While the user is still active the GPU stays in text mode, until the image has waited the limit.
    waiting = decide("llm", queue, capped, idle_s=5.0, rules=RULES)
    assert (waiting.mode, waiting.run) == ("llm", None)
    late = QueueSnapshot(other_text=EXTRACTION, image=IMAGE, image_waited_s=60.0)
    assert decide("llm", late, capped, 5.0, RULES).mode == "image"


def test_foreground_text_takes_the_gpu_back_from_an_image_batch() -> None:
    queue = QueueSnapshot(foreground_text=REPLY, image=IMAGE)
    decision = decide("image", queue, ImageBatch(images_in_a_row=1), None, RULES)
    assert (decision.mode, decision.run, decision.deciding) == ("llm", REPLY, REPLY)


def test_image_mode_returns_to_text_when_no_image_is_left() -> None:
    decision = decide("image", QueueSnapshot(other_text=EXTRACTION), ImageBatch(images_in_a_row=1), 0.0, RULES)
    assert (decision.mode, decision.run, decision.deciding, decision.caps_batch) == ("llm", None, EXTRACTION, False)


def test_only_foreground_work_preempts_and_only_background_work_is_preempted() -> None:
    assert preempts(JobClass.BACKGROUND, JobClass.FOREGROUND)
    for running in JobClass:
        for arriving in JobClass:
            if (running, arriving) != (JobClass.BACKGROUND, JobClass.FOREGROUND):
                assert not preempts(running, arriving)


def test_the_documented_priorities_order_the_queue() -> None:
    # docs/gpu-orchestration.md (Job kinds and priorities): lower runs first, replies before everything else.
    order = sorted(JobKind, key=lambda kind: policy_for(kind).priority)
    assert order[:2] == [JobKind.CHAT_REPLY, JobKind.CHAT_REGENERATE]
    assert order[-1] is JobKind.MAINTENANCE
    assert policy_for(JobKind.IMAGE_REQUEST).required_mode("render") is RequiredMode.IMAGE
