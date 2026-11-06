"""Tests that log records are JSON lines with the documented fields, and that content never reaches them."""

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from lyrveil.logs import configure_logging, get_logger

MARKER = "MARKER-7f3c-secret-content"


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging("DEBUG", buffer)
    yield buffer
    configure_logging("INFO")


def records(buffer: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def test_a_record_is_one_json_line_with_the_documented_fields(stream: io.StringIO) -> None:
    get_logger("test").info("job_started", job_id="job-1", conversation_id="c-1", duration_ms=12)
    (record,) = records(stream)
    assert record["event"] == "job_started"
    assert record["level"] == "info"
    assert record["service"] == "lyrveil-backend"
    assert record["job_id"] == "job-1"
    assert record["conversation_id"] == "c-1"
    assert record["duration_ms"] == 12
    assert record["ts"].endswith("Z")


def test_content_fields_are_dropped_by_name(stream: io.StringIO) -> None:
    get_logger("test").info(
        "reply_done", content=MARKER, prompt=MARKER, memory_text=MARKER, prompt_chars=MARKER, size_chars=40
    )
    (record,) = records(stream)
    assert MARKER not in stream.getvalue()
    assert record["dropped_fields"] == ["content", "memory_text", "prompt", "prompt_chars"]
    assert record["size_chars"] == 40


def test_free_text_in_an_identifier_field_is_dropped(stream: io.StringIO) -> None:
    get_logger("test").info("state_changed", state=f"a sentence with {MARKER}", character_id="x" * 65)
    (record,) = records(stream)
    assert MARKER not in stream.getvalue()
    assert record["state"] == "[dropped]"
    assert record["character_id"] == "[dropped]"


def test_an_exception_keeps_its_type_and_frames_but_not_its_message(stream: io.StringIO) -> None:
    try:
        raise ValueError(MARKER)
    except ValueError:
        get_logger("test").exception("job_failed", error_code="RUNTIME_ERROR")
    (record,) = records(stream)
    assert MARKER not in stream.getvalue()
    assert record["error_type"] == "ValueError"
    assert record["error_code"] == "RUNTIME_ERROR"
    assert any("test_logs.py" in frame for frame in record["traceback"])


def test_standard_library_records_use_the_same_format_and_filter(stream: io.StringIO) -> None:
    logger = logging.getLogger("uvicorn.error")
    logger.info("server_started")
    try:
        raise RuntimeError(MARKER)
    except RuntimeError:
        logger.exception("request_failed")
    first, second = records(stream)
    assert first["event"] == "server_started"
    assert first["level"] == "info"
    assert second["error_type"] == "RuntimeError"
    assert MARKER not in stream.getvalue()


def test_records_below_the_level_are_not_written() -> None:
    buffer = io.StringIO()
    configure_logging("WARNING", buffer)
    try:
        get_logger("test").info("hidden")
        logging.getLogger("other").info("hidden")
        get_logger("test").warning("shown")
    finally:
        configure_logging("INFO")
    assert [record["event"] for record in records(buffer)] == ["shown"]
