"""System route of docs/api.md (System): `GET /system/resources`, the snapshot of the AI resources screen.

Not in the committed OpenAPI document yet, for the reason given in lyrveil.api.conversations. `GET /system/status` is
planned.
"""

from typing import Any

from fastapi import APIRouter, Request

from lyrveil.services.ai_resources import AiResources

router = APIRouter(prefix="/system", tags=["system"], include_in_schema=False)


@router.get("/resources")
def read_resources(request: Request) -> dict[str, Any]:
    """Returns the GPU state, the card's memory, the queue by required mode and the active text model with its launch
    level (see AiResources.snapshot). A part that cannot be read is null; the route still answers 200. A plain `def`,
    so FastAPI runs the blocking reads in its thread pool."""
    resources: AiResources | None = request.app.state.ai_resources
    if resources is None:
        raise RuntimeError("the resources route needs AiResources")
    return resources.snapshot()
