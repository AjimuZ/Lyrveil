"""EventPublisher: the port through which services announce changes on the event WebSocket (docs/api.md, ADR 0016)."""

from collections.abc import Mapping
from typing import Any, Protocol

# Event types of docs/api.md (Event WebSocket) that the backend publishes so far.
TYPING_STARTED = "typing.started"
TYPING_STOPPED = "typing.stopped"
MESSAGE_DELTA = "message.delta"
MESSAGE_RESTARTED = "message.restarted"
MESSAGE_COMPLETED = "message.completed"
MESSAGE_FAILED = "message.failed"
MESSAGE_UPDATED = "message.updated"
IMAGE_PROGRESS = "image.progress"
IMAGE_COMPLETED = "image.completed"
IMAGE_PROPOSED = "image.proposed"


class EventPublisher(Protocol):
    """Publishes server events. Events are a hint for the interface: REST stays the source of truth, so a lost event
    loses no data, and a caller must commit its change before it publishes the event that announces it."""

    def publish(self, event_type: str, data: Mapping[str, Any]) -> int:
        """Publishes one event of a documented `event_type` with its `data`, and returns the event's id.

        Must be called on the event loop. `data` must be JSON-serializable and is never logged.
        """
        ...
