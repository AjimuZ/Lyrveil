"""Character routes of docs/api.md (Characters): the list and the creation the minimal form of P02.6a needs.

The other routes of that section (read, save, versions, archive, purge, exchange) arrive with the Creator (P03). Not in
the committed OpenAPI document yet, for the reason given in lyrveil.api.conversations.
"""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.domain.characters import CharacterSummary
from lyrveil.services.characters import CharacterService

router = APIRouter(prefix="/characters", tags=["characters"], include_in_schema=False)


class CharacterOut(BaseModel):
    id: str
    first_name: str
    last_name: str | None
    full_name: str
    age: int
    grammatical_gender: str
    primary_language: str
    status: str
    created_at: str
    updated_at: str


class CharacterList(BaseModel):
    items: list[CharacterOut]


class CreateCharacter(BaseModel):
    """The values are checked by the service, so a refusal carries an Italian detail in the problem document."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(max_length=400)
    last_name: str | None = Field(default=None, max_length=400)
    age: int
    grammatical_gender: str = "non_indicato"
    primary_language: str = "it"


def _out(character: CharacterSummary) -> CharacterOut:
    return CharacterOut(
        id=character.id,
        first_name=character.first_name,
        last_name=character.last_name,
        full_name=character.full_name,
        age=character.age,
        grammatical_gender=character.grammatical_gender,
        primary_language=character.primary_language,
        status=character.status,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


def get_service(request: Request) -> CharacterService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the character routes need a database")
    return CharacterService(engine)


Service = Depends(get_service)


@router.get("", response_model=CharacterList)
def list_characters(
    status: str = "active", q: str | None = Query(default=None, max_length=200), service: CharacterService = Service
) -> CharacterList:
    """Lists the characters in `status` (`active` by default, or `archived`), newest update first. `q` keeps those whose
    full name contains it, ignoring case. Answers 422 for another status."""
    return CharacterList(items=[_out(item) for item in service.list_characters(status, q)])


@router.post("", response_model=CharacterOut, status_code=201)
def create_character(body: CreateCharacter, service: CharacterService = Service) -> CharacterOut:
    """Creates an active character. Answers 422 `VALIDATION_ERROR` when the name is empty, the age is under 18, or the
    gender or language is not one of the documented values."""
    return _out(
        service.create(
            first_name=body.first_name,
            last_name=body.last_name,
            age=body.age,
            grammatical_gender=body.grammatical_gender,
            primary_language=body.primary_language,
        )
    )
