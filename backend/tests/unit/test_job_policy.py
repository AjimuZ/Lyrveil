"""Tests of the job policy table, the retry delays, and the identifier and timestamp helpers the queue stores."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.jobs.policy import POLICIES, JobClass, JobKind, RequiredMode, policy_for


def test_every_kind_has_the_documented_priority_class_and_attempts() -> None:
    documented = {
        JobKind.CHAT_REPLY: (10, JobClass.FOREGROUND, 2),
        JobKind.CHAT_REGENERATE: (10, JobClass.FOREGROUND, 2),
        JobKind.CHARACTER_DRAFT: (20, JobClass.FOREGROUND, 2),
        JobKind.IMAGE_REQUEST: (30, JobClass.FOREGROUND, 2),
        JobKind.VISUAL_PROFILE_TRANSLATE: (40, JobClass.FOREGROUND, 2),
        JobKind.PROACTIVE_MESSAGE: (50, JobClass.SOFT, 2),
        JobKind.IMAGE_PROPOSAL: (60, JobClass.BACKGROUND, 1),
        JobKind.SUMMARY_CHECKPOINT: (70, JobClass.BACKGROUND, 3),
        JobKind.MEMORY_EXTRACTION: (80, JobClass.BACKGROUND, 3),
        JobKind.MEMORY_REEVALUATION: (80, JobClass.BACKGROUND, 3),
        JobKind.MAINTENANCE: (90, JobClass.BACKGROUND, 1),
    }
    found = {kind: (policy.priority, policy.job_class, policy.max_attempts) for kind, policy in POLICIES.items()}
    assert found == documented


def test_the_image_request_stage_decides_its_mode() -> None:
    policy = policy_for(JobKind.IMAGE_REQUEST)
    assert [(stage, policy.required_mode(stage)) for stage in policy.stages] == [
        ("prompt", RequiredMode.LLM),
        ("render", RequiredMode.IMAGE),
        ("persist", RequiredMode.NONE),
    ]
    with pytest.raises(KeyError):
        policy.required_mode("upload")


def test_retry_delays_follow_the_retry_table() -> None:
    assert policy_for(JobKind.CHAT_REPLY).retry_delay_s(1) == 5.0
    assert policy_for(JobKind.IMAGE_REQUEST).retry_delay_s(1) == 10.0
    extraction = policy_for(JobKind.MEMORY_EXTRACTION)
    assert [extraction.retry_delay_s(attempt) for attempt in (1, 2, 3, 4)] == [60.0, 300.0, 1800.0, 1800.0]
    assert policy_for(JobKind.IMAGE_PROPOSAL).retry_delay_s(1) == 0.0


def test_new_ids_are_uuid_version_7_in_creation_order() -> None:
    first = new_id()
    later = [new_id() for _ in range(3)]
    parsed = uuid.UUID(first)
    assert (parsed.version, parsed.variant) == (7, uuid.RFC_4122)
    assert len({first, *later}) == 4
    # Within one millisecond the order is undefined, so only the timestamp prefix is compared.
    assert all(first[:13] <= identifier[:13] for identifier in later)


def test_timestamps_are_utc_text_with_milliseconds_and_reject_naive_values() -> None:
    moment = datetime(2026, 11, 7, 11, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    assert to_utc_text(moment) == "2026-11-07T10:00:00.123Z"
    assert from_utc_text("2026-11-07T10:00:00.123Z") == datetime(2026, 11, 7, 10, 0, 0, 123000, tzinfo=UTC)
    with pytest.raises(ValueError, match="aware"):
        to_utc_text(datetime(2026, 11, 7, 10, 0))
