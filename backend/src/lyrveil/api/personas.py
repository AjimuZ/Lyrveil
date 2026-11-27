"""Persona routes of docs/api.md (Personas): list, create, read, replace, delete, default, archive and unarchive.

Not in the committed OpenAPI document yet, like the conversation routes: `include_in_schema` is off until
`backend/openapi.json` is regenerated with Docker.
"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from lyrveil.domain.personas import Persona, PersonaStatus
from lyrveil.services.personas import PersonaService

router = APIRouter(prefix="/personas", tags=["personas"], include_in_schema=False)


class PersonaOut(BaseModel):
    """A persona as **Le tue persone** and the new-chat picker show it. `conversation_count` is how many conversations
    use it; above 0 the persona can only be archived."""

    id: str
    name: str
    grammatical_gender: str
    age: int | None
    appearance: str
    about: str
    is_default: bool
    status: PersonaStatus
    conversation_count: int
    created_at: str
    updated_at: str


class PersonaList(BaseModel):
    items: list[PersonaOut]


class PersonaIn(BaseModel):
    """The editable fields. A PUT replaces all of them; one left out takes its default. The ranges are checked by the
    service, so a refusal always has an Italian detail and the code `VALIDATION_ERROR`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    grammatical_gender: str = "non_indicato"
    age: int | None = None
    appearance: str = ""
    about: str = ""


def _out(persona: Persona) -> PersonaOut:
    return PersonaOut(
        id=persona.id,
        name=persona.name,
        grammatical_gender=persona.grammatical_gender,
        age=persona.age,
        appearance=persona.appearance,
        about=persona.about,
        is_default=persona.is_default,
        status=persona.status,
        conversation_count=persona.conversation_count,
        created_at=persona.created_at,
        updated_at=persona.updated_at,
    )


def get_service(request: Request) -> PersonaService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the persona routes need a database")
    return PersonaService(engine)


Service = Depends(get_service)


@router.get("", response_model=PersonaList)
def list_personas(status: PersonaStatus = PersonaStatus.ACTIVE, service: PersonaService = Service) -> PersonaList:
    """Lists the personas in `status` (default active): the default first, then by name."""
    return PersonaList(items=[_out(item) for item in service.list_personas(status)])


@router.post("", response_model=PersonaOut, status_code=201)
def create_persona(body: PersonaIn, service: PersonaService = Service) -> PersonaOut:
    """Creates a persona that is not the default: 422 `VALIDATION_ERROR` for a blank or over-long text, an unknown
    gender or an age outside 18 to 120, and 422 `CONTENT_POLICY_BLOCKED` naming the field for a text that describes a
    minor."""
    return _out(service.create(body.model_dump()))


@router.get("/{persona_id}", response_model=PersonaOut)
def get_persona(persona_id: str, service: PersonaService = Service) -> PersonaOut:
    """Returns one persona in any status; 404 when it does not exist."""
    return _out(service.get(persona_id))


@router.put("/{persona_id}", response_model=PersonaOut)
def put_persona(persona_id: str, body: PersonaIn, service: PersonaService = Service) -> PersonaOut:
    """Replaces the editable fields; the change reaches conversations from their next reply. 404 for an unknown persona
    and the refusals of the create route."""
    return _out(service.update(persona_id, body.model_dump()))


@router.delete("/{persona_id}", status_code=204)
def delete_persona(persona_id: str, service: PersonaService = Service) -> Response:
    """Deletes a persona no conversation uses: 404 when it does not exist, 409 `CONFLICT` while a conversation uses it
    or while it is the default."""
    service.delete(persona_id)
    return Response(status_code=204)


@router.post("/{persona_id}/default", response_model=PersonaOut)
def default_persona(persona_id: str, service: PersonaService = Service) -> PersonaOut:
    """Makes the persona the default: 404 when it does not exist, 409 when it is archived."""
    return _out(service.set_default(persona_id))


@router.post("/{persona_id}/archive", response_model=PersonaOut)
def archive_persona(persona_id: str, service: PersonaService = Service) -> PersonaOut:
    """Hides the persona from the new-chat picker; its conversations keep working. 404 when it does not exist, 409 for
    the default persona."""
    return _out(service.set_archived(persona_id, True))


@router.post("/{persona_id}/unarchive", response_model=PersonaOut)
def unarchive_persona(persona_id: str, service: PersonaService = Service) -> PersonaOut:
    """Shows an archived persona in the new-chat picker again; 404 when it does not exist."""
    return _out(service.set_archived(persona_id, False))
