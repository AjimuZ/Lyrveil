"""EventHub: numbers server events, keeps the newest 2000 for replay, and queues them for each WebSocket connection.

ADR 0016 and docs/api.md (Event WebSocket). Event ids grow from 1 for the life of the process, which `boot_id` names.
A reconnecting client names the last id it delivered; the hub replays what followed, or asks it to reload through
REST (`resync.required`) when it cannot: another process, a position it never handed out, or a gap older than the
buffer. Everything runs on the event loop and nothing here is thread-safe: publish from a worker thread with
`anyio.from_thread.run_sync(hub.publish, ...)`.
"""

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio

from lyrveil.domain.identifiers import to_utc_text

PROTOCOL_VERSION = 1
# ADR 0016: the newest events kept in memory for a reconnecting client.
REPLAY_CAPACITY = 2000
RESYNC_REQUIRED = "resync.required"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Event:
    """One published event. `data` is never logged: `message.delta` carries private text."""

    id: int
    type: str
    ts: str
    data: Mapping[str, Any]

    def frame(self) -> dict[str, Any]:
        """Returns the server frame of docs/api.md: `v`, `id`, `type`, `ts` and `data`."""
        return {"v": PROTOCOL_VERSION, "id": self.id, "type": self.type, "ts": self.ts, "data": dict(self.data)}


@dataclass(frozen=True)
class Resync:
    """Tells the client to reload through REST. `id` is the newest event id when the gap was found: the client resumes
    from it, because every event up to it was published after its change was committed, so REST already shows it."""

    id: int
    ts: str

    def frame(self) -> dict[str, Any]:
        return {"v": PROTOCOL_VERSION, "id": self.id, "type": RESYNC_REQUIRED, "ts": self.ts, "data": {}}


Frame = Event | Resync


class Subscription:
    """The frames waiting for one connection, in id order, released once the connection's `hello` was answered.

    Holds at most `limit` frames. Past that it drops them all and keeps a single Resync, so a client that reads too
    slowly reloads through REST instead of making the backend's memory grow.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._frames: deque[Frame] = deque()
        self._wake = anyio.Event()
        self._started = False

    def push(self, frame: Frame, now: str) -> None:
        """Queues `frame`, or replaces the whole queue with a Resync at `frame`'s id when the queue is full."""
        if len(self._frames) >= self._limit:
            self._frames.clear()
            self._frames.append(Resync(frame.id, now))
        else:
            self._frames.append(frame)
        self._wake.set()

    def start(self, frames: Iterable[Frame] | None) -> None:
        """Releases the queue to the sender. `frames`, when given, replace what was queued: the replay, or a Resync."""
        if frames is not None:
            self._frames = deque(frames)
        self._started = True
        self._wake.set()

    async def take(self) -> list[Frame]:
        """Waits until the queue is released and holds frames, then removes and returns them all, oldest first."""
        while not (self._started and self._frames):
            await self._wake.wait()
            self._wake = anyio.Event()
        frames = list(self._frames)
        self._frames.clear()
        return frames


class EventHub:
    """The process-wide event stream of the backend whose process is named `boot_id`.

    Keeps the newest `capacity` events for replay. `clock` returns the aware current time; tests pass a fake one.
    Always holds: ids are consecutive from 1, and every subscription receives every event published while it exists.
    """

    def __init__(self, boot_id: str, capacity: int = REPLAY_CAPACITY, clock: Callable[[], datetime] = _utc_now) -> None:
        self.boot_id = boot_id
        self._capacity = capacity
        self._clock = clock
        self._buffer: deque[Event] = deque(maxlen=capacity)
        self._last_id = 0
        self._subscriptions: set[Subscription] = set()

    @property
    def last_id(self) -> int:
        """The id of the newest event, 0 before the first one."""
        return self._last_id

    def publish(self, event_type: str, data: Mapping[str, Any]) -> int:
        """Numbers the event, keeps it for replay, queues it for every subscription and returns its id."""
        self._last_id += 1
        now = self._now()
        event = Event(id=self._last_id, type=event_type, ts=now, data=dict(data))
        self._buffer.append(event)
        for subscription in tuple(self._subscriptions):
            subscription.push(event, now)
        return event.id

    def subscribe(self) -> Subscription:
        """Returns a new subscription, which receives every event from now on. Call `unsubscribe` when it ends."""
        subscription = Subscription(self._capacity)
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        """Stops queuing events for `subscription`. Safe to call more than once."""
        self._subscriptions.discard(subscription)

    def missed(self, last_event_id: int, boot_id: str | None) -> list[Event] | None:
        """Returns the events after `last_event_id`, oldest first and possibly none, or None when they cannot be
        replayed: `boot_id` names another process (or none), the id was never handed out, or some events after it
        already left the buffer."""
        if boot_id != self.boot_id or not 0 <= last_event_id <= self._last_id:
            return None
        oldest = self._buffer[0].id if self._buffer else self._last_id + 1
        if last_event_id + 1 < oldest:
            return None
        return [event for event in self._buffer if event.id > last_event_id]

    def resume(self, subscription: Subscription, last_event_id: int | None, boot_id: str | None) -> None:
        """Answers a connection's `hello` and releases its queue.

        Without a position (`last_event_id` None, a first connection) the connection gets the events published since
        it subscribed. With one, it gets the replay of `missed`, or a single Resync when that is None.
        """
        if last_event_id is None:
            subscription.start(None)
            return
        replay = self.missed(last_event_id, boot_id)
        subscription.start(replay if replay is not None else [Resync(self._last_id, self._now())])

    def resync(self, subscription: Subscription) -> None:
        """Answers a `hello` the hub cannot read: the connection reloads through REST and resumes from now."""
        subscription.start([Resync(self._last_id, self._now())])

    def _now(self) -> str:
        return to_utc_text(self._clock())
