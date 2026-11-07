"""The event WebSocket at /ws (docs/api.md, Event WebSocket): `connected`, `hello` with replay or resync, events."""

import json
from dataclasses import dataclass

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lyrveil.events.hub import PROTOCOL_VERSION, EventHub, Subscription

router = APIRouter()


@dataclass(frozen=True)
class _Hello:
    """A client `hello`. `valid` is False when a field has the wrong type: the client then gets a resync."""

    last_event_id: int | None
    boot_id: str | None
    valid: bool


def _parse_hello(text: object) -> _Hello | None:
    """Returns the `hello` in a text frame, or None for any other frame: `ping`, an unknown type or not JSON."""
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("type") != "hello":
        return None
    last, boot = value.get("last_event_id"), value.get("boot_id")
    last_ok = last is None or (isinstance(last, int) and not isinstance(last, bool))
    boot_ok = boot is None or isinstance(boot, str)
    if not (last_ok and boot_ok):
        return _Hello(None, None, valid=False)
    return _Hello(last, boot, valid=True)


async def _read_frames(websocket: WebSocket, hub: EventHub, subscription: Subscription) -> None:
    """Reads client frames until the client leaves. Only the first `hello` acts: it starts the replay or the resync.
    Other frames, a second `hello` included, are ignored, so a client frame never changes any stored state."""
    greeted = False
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        hello = None if greeted else _parse_hello(message.get("text"))
        if hello is None:
            continue
        greeted = True
        if hello.valid:
            hub.resume(subscription, hello.last_event_id, hello.boot_id)
        else:
            hub.resync(subscription)


async def _send_frames(websocket: WebSocket, subscription: Subscription, scope: anyio.CancelScope) -> None:
    """Sends the subscription's frames in order until the connection fails, then ends the connection's tasks."""
    try:
        while True:
            for frame in await subscription.take():
                await websocket.send_json(frame.frame())
    except (WebSocketDisconnect, RuntimeError, OSError):
        scope.cancel()


@router.websocket("/ws")
async def events(websocket: WebSocket) -> None:
    """Accepts the connection, sends the `connected` frame, then serves events until the client leaves.

    Host and Origin were already checked by `LocalOnlyMiddleware`. `app.state.boot_id` is fixed for the life of the
    application, so a client that sees another value knows the backend restarted. The connection subscribes before
    the `connected` frame, so nothing published afterwards is missed, but it sends events only once the client's
    `hello` was answered (EventHub.resume): an event sent earlier would hide the older ones the replay brings.
    """
    hub: EventHub = websocket.app.state.events
    await websocket.accept()
    subscription = hub.subscribe()
    try:
        await websocket.send_json({"v": PROTOCOL_VERSION, "type": "connected", "boot_id": websocket.app.state.boot_id})
        async with anyio.create_task_group() as group:
            group.start_soon(_send_frames, websocket, subscription, group.cancel_scope)
            await _read_frames(websocket, hub, subscription)
            group.cancel_scope.cancel()
    finally:
        hub.unsubscribe(subscription)
