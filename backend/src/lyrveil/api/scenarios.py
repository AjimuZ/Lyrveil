"""Scenario routes of docs/api.md (Scenarios): create, list, read, edit, archive, restore and delete.

Not in the committed OpenAPI document yet, like the conversation routes: `include_in_schema` is off until
`backend/openapi.json` is regenerated with Docker.
"""

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.domain.scenarios import (
    MAX_DESCRIPTION_CHARS,
    MAX_OPENING_CHARS,
    MAX_SETTING_CHARS,
    MAX_TITLE_CHARS,
    Scenario,
    ScenarioStatus,
)
from lyrveil.services.scenarios import ScenarioService

router = APIRouter(prefix="/scenarios", tags=["scenarios"], include_in_schema=False)


class ScenarioOut(BaseModel):
    """A scenario as the scenario screen and the new-chat picker show it. `character_id` null means any character."""

    id: str
    title: str
    description: str
    setting: str
    opening_message: str
    character_id: str | None
    language: str | None
    status: ScenarioStatus
    created_at: str
    updated_at: str


class ScenarioList(BaseModel):
    items: list[ScenarioOut]


class ScenarioIn(BaseModel):
    """The editable fields. A PUT replaces all of them; one left out takes its default."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=MAX_TITLE_CHARS)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_CHARS)
    setting: str = Field(default="", max_length=MAX_SETTING_CHARS)
    opening_message: str = Field(default="", max_length=MAX_OPENING_CHARS)
    character_id: str | None = None
    language: str | None = Field(default=None, max_length=16)


def _out(scenario: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=scenario.id,
        title=scenario.title,
        description=scenario.description,
        setting=scenario.setting,
        opening_message=scenario.opening_message,
        character_id=scenario.character_id,
        language=scenario.language,
        status=scenario.status,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def get_service(request: Request) -> ScenarioService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the scenario routes need a database")
    return ScenarioService(engine)


Service = Depends(get_service)


@router.get("", response_model=ScenarioList)
def list_scenarios(
    status: ScenarioStatus = ScenarioStatus.ACTIVE,
    character_id: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    service: ScenarioService = Service,
) -> ScenarioList:
    """Lists the scenarios in `status` (default active), newest change first. `character_id` keeps those usable with
    that character; `q` keeps those whose text contains every word of it, ignoring case and accents."""
    return ScenarioList(items=[_out(item) for item in service.list_scenarios(status, character_id, q)])


@router.post("", response_model=ScenarioOut, status_code=201)
def create_scenario(body: ScenarioIn, service: ScenarioService = Service) -> ScenarioOut:
    """Creates a scenario: 422 for a blank title or a text that describes a minor, 404 for an unknown character."""
    return _out(service.create(body.model_dump()))


@router.get("/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str, service: ScenarioService = Service) -> ScenarioOut:
    """Returns one scenario in any status; 404 when it does not exist."""
    return _out(service.get(scenario_id))


@router.put("/{scenario_id}", response_model=ScenarioOut)
def put_scenario(scenario_id: str, body: ScenarioIn, service: ScenarioService = Service) -> ScenarioOut:
    """Replaces the editable fields; running conversations keep their copy. 404 for an unknown scenario or character."""
    return _out(service.update(scenario_id, body.model_dump()))


@router.post("/{scenario_id}/archive", response_model=ScenarioOut)
def archive_scenario(scenario_id: str, service: ScenarioService = Service) -> ScenarioOut:
    """Archives a scenario, so it starts no new conversation; 404 when it does not exist."""
    return _out(service.set_archived(scenario_id, True))


@router.post("/{scenario_id}/unarchive", response_model=ScenarioOut)
def unarchive_scenario(scenario_id: str, service: ScenarioService = Service) -> ScenarioOut:
    """Makes an archived scenario active again; 404 when it does not exist."""
    return _out(service.set_archived(scenario_id, False))


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, service: ScenarioService = Service) -> Response:
    """Deletes a scenario for good; its conversations keep their copy. 404 when it does not exist."""
    service.delete(scenario_id)
    return Response(status_code=204)
