"""Tests of lyrveil/events/hub.py: event ids, the replay rules of ADR 0016 and the bounded queue of a connection."""

from collections.abc import Sequence
from datetime import UTC, datetime

import anyio
import pytest

from lyrveil.events.hub import Event, EventHub, Resync

BOOT = "boot-1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def hub_with(count: int, capacity: int = 2000) -> EventHub:
    hub = EventHub(BOOT, capacity, clock=lambda: datetime(2026, 11, 7, 10, 0, tzinfo=UTC))
    for number in range(count):
        hub.publish("message.delta", {"n": number})
    return hub


def ids(frames: Sequence[Event | Resync] | None) -> list[int] | None:
    return None if frames is None else [frame.id for frame in frames]


def test_ids_grow_from_one_and_the_frame_has_the_documented_fields() -> None:
    hub = hub_with(3)
    assert hub.last_id == 3
    replay = hub.missed(0, BOOT)
    assert replay is not None
    assert replay[0].frame() == {
        "v": 1,
        "id": 1,
        "type": "message.delta",
        "ts": "2026-11-07T10:00:00.000Z",
        "data": {"n": 0},
    }
    assert ids(replay) == [1, 2, 3]


def test_a_known_position_gets_what_followed_and_anything_else_a_resync() -> None:
    hub = hub_with(5, capacity=3)
    assert ids(hub.missed(2, BOOT)) == [3, 4, 5]
    assert ids(hub.missed(5, BOOT)) == []
    # Event 2 left the buffer, so a client at 1 would miss it.
    assert hub.missed(1, BOOT) is None
    assert hub.missed(4, "another-boot") is None
    assert hub.missed(4, None) is None
    assert hub.missed(6, BOOT) is None
    assert hub.missed(-1, BOOT) is None
    assert ids(EventHub(BOOT).missed(0, BOOT)) == []


@pytest.mark.anyio
async def test_a_connection_gets_nothing_before_its_hello_then_the_replay_and_live_events() -> None:
    hub = hub_with(3)
    subscription = hub.subscribe()
    hub.publish("message.delta", {"n": 3})
    with anyio.move_on_after(0.05) as waited:
        await subscription.take()
    assert waited.cancelled_caught, "frames were released before the hello"
    hub.resume(subscription, 1, BOOT)
    hub.publish("message.delta", {"n": 4})
    with anyio.fail_after(1):
        assert ids(await subscription.take()) == [2, 3, 4, 5]
    hub.unsubscribe(subscription)
    hub.publish("message.delta", {"n": 5})
    with anyio.move_on_after(0.05) as waited:
        await subscription.take()
    assert waited.cancelled_caught


@pytest.mark.anyio
async def test_a_first_connection_keeps_its_live_events_and_a_bad_position_gets_one_resync() -> None:
    hub = hub_with(2)
    first, late = hub.subscribe(), hub.subscribe()
    hub.publish("message.delta", {"n": 2})
    hub.resume(first, None, None)
    hub.resume(late, 1, "another-boot")
    with anyio.fail_after(1):
        assert ids(await first.take()) == [3]
        frames = await late.take()
    assert frames == [Resync(3, "2026-11-07T10:00:00.000Z")]
    assert frames[0].frame()["type"] == "resync.required"


@pytest.mark.anyio
async def test_a_connection_that_falls_behind_gets_a_resync_instead_of_a_growing_queue() -> None:
    hub = hub_with(0, capacity=3)
    subscription = hub.subscribe()
    hub.resume(subscription, None, None)
    for number in range(4):
        hub.publish("message.delta", {"n": number})
    hub.publish("message.delta", {"n": 4})
    with anyio.fail_after(1):
        frames = await subscription.take()
    assert [type(frame).__name__ for frame in frames] == ["Resync", "Event"]
    assert ids(frames) == [4, 5]
