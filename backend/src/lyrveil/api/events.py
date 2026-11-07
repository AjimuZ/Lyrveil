"""The event WebSocket at /ws (docs/api.md, Event WebSocket): handshake and `connected` frame, events in P02."""

from fastapi import APIRouter, WebSocket

router = APIRouter()

PROTOCOL_VERSION = 1


@router.websocket("/ws")
async def events(websocket: WebSocket) -> None:
    """Accepts the connection, sends the `connected` frame with the process's `boot_id`, then waits for the close.

    Host and Origin were already checked by `LocalOnlyMiddleware`. `app.state.boot_id` is fixed for the life of the
    application, so a client that sees another value knows the backend restarted. Client frames, `hello` and
    `ping`, are read and ignored until P02 adds replay and events; a frame never changes any state.
    """
    await websocket.accept()
    await websocket.send_json({"v": PROTOCOL_VERSION, "type": "connected", "boot_id": websocket.app.state.boot_id})
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass
