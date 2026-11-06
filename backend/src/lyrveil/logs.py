"""Structured JSON logging on stdout that keeps only allowlisted fields, so no content can reach a log record."""

import logging
import sys
import traceback
from types import TracebackType
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

SERVICE = "lyrveil-backend"

# Identifier and state fields of docs/backend.md (Logging). Their values must be short tokens, never free text.
IDENTIFIER_FIELDS = frozenset(
    {"service", "request_id", "job_id", "conversation_id", "character_id", "state", "error_code", "level", "ts"}
)
# Fields this module adds itself: the exception class, its frames, and the names of the fields it dropped.
DIAGNOSTIC_FIELDS = frozenset({"error_type", "traceback", "dropped_fields"})
# DEBUG adds sizes and timing: numeric fields whose name ends with one of these suffixes.
MEASURE_SUFFIXES = ("_ms", "_bytes", "_chars", "_tokens", "_count")
MAX_IDENTIFIER_LENGTH = 64
DROPPED = "[dropped]"

_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _resolve_exc_info(value: object) -> _ExcInfo | None:
    # structlog accepts exc_info=True, an exception instance or a sys.exc_info() tuple.
    if isinstance(value, BaseException):
        return type(value), value, value.__traceback__
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[1], BaseException):
        return value[0], value[1], value[2]
    if value is True:
        current = sys.exc_info()
        if current[1] is not None and current[0] is not None:
            return current[0], current[1], current[2]
    return None


def _describe_exception(exc_info: _ExcInfo) -> tuple[str, list[str]]:
    # The exception message is left out on purpose: messages such as a validation error can quote user text.
    kind, _, tb = exc_info
    name = kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
    frames = [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in traceback.extract_tb(tb)]
    return name, frames


def _is_measure(key: str, value: object) -> bool:
    return key.endswith(MEASURE_SUFFIXES) and isinstance(value, int | float) and not isinstance(value, bool)


def _is_identifier(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if not isinstance(value, str):
        return False
    return 0 < len(value) <= MAX_IDENTIFIER_LENGTH and value.isprintable() and " " not in value


def keep_allowed_fields(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Returns the record with only the allowlisted fields, and is the last processor before rendering.

    Always holds after it runs: `event` is the only free-text field, and it must be a constant event name chosen
    in code; identifier fields hold a short token or "[dropped]"; measures are numbers; an exception becomes its
    class name and its frames, without its message; every other field is removed and its name listed in
    `dropped_fields`. Field values that were removed are never rendered.
    """
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    exc_info = _resolve_exc_info(event_dict.get("exc_info"))
    for key, value in event_dict.items():
        if key == "event":
            kept[key] = value if isinstance(value, str) else DROPPED
        elif key in IDENTIFIER_FIELDS:
            kept[key] = value if _is_identifier(value) else DROPPED
        elif _is_measure(key, value):
            kept[key] = value
        elif key in {"exc_info", "exception", "stack_info"}:
            continue
        else:
            dropped.append(key)
    if exc_info is not None:
        kept["error_type"], kept["traceback"] = _describe_exception(exc_info)
    if dropped:
        kept["dropped_fields"] = sorted(dropped)
    return kept


def _add_service(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("service", SERVICE)
    return event_dict


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Sends every log record, from structlog and from the standard library, as one JSON object per line to stream.

    Requires level to be DEBUG, INFO, WARNING or ERROR. stream defaults to stdout. Calling it again replaces the
    previous configuration, so the root logger keeps exactly one handler.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _add_service,
    ]
    structlog.configure(
        processors=[structlog.stdlib.filter_by_level, *shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            keep_allowed_fields,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for previous in list(root.handlers):
        root.removeHandler(previous)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Returns a logger whose records go through the allowlist of configure_logging."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
