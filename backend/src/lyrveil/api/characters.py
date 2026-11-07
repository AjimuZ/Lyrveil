"""Character routes of docs/api.md (Characters): list, create with the whole card, and read the card.

Save, versions, archive, purge and the exchange routes of that section arrive with the rest of the Creator (P03). Not in
the committed OpenAPI document yet, for the reason given in lyrveil.api.conversations.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import Engine

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.characters import CharacterSummary, StoredCharacter
from lyrveil.services.characters import CharacterService

router = APIRouter(prefix="/characters", tags=["characters"], include_in_schema=False)

# The parts of a card besides `identity`; in the flat body they sit beside the identity fields.
_CARD_SECTIONS = ("traits", "communication", "visual")


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


class CharacterDetail(BaseModel):
    id: str
    full_name: str
    status: str
    is_demo: bool
    current_version_no: int
    created_at: str
    updated_at: str
    card: CharacterCard


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


def _detail(character: StoredCharacter) -> CharacterDetail:
    identity = character.card.identity
    return CharacterDetail(
        id=character.id,
        full_name=identity.first_name if not identity.last_name else f"{identity.first_name} {identity.last_name}",
        status=character.status,
        is_demo=character.is_demo,
        current_version_no=character.current_version_no,
        created_at=character.created_at,
        updated_at=character.updated_at,
        card=character.card,
    )


def _as_card(body: dict[str, Any]) -> dict[str, Any]:
    """Returns the nested card of `body`. A body with an `identity` key already is one; otherwise it is the flat form of
    the minimal chat form, where the identity fields sit at the top level beside the card sections."""
    if "identity" in body:
        return body
    sections = {name: body[name] for name in _CARD_SECTIONS if name in body}
    return {"identity": {key: value for key, value in body.items() if key not in _CARD_SECTIONS}, **sections}


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
def create_character(body: Annotated[dict[str, Any], Body()], service: CharacterService = Service) -> CharacterOut:
    """Creates an active character from a card. The body is either `{"identity": {...}, "traits": [...],
    "communication": {...}, "visual": {...}}` or the flat form with the identity fields at the top level; only the first
    name and the age are required. Answers 422 `VALIDATION_ERROR` with an Italian detail for an age under 18 or over
    120, an empty name, a value outside the closed lists, an unknown field, a slot tag the vocabulary lacks or an outfit
    with a reserved key."""
    return _out(service.create(_as_card(body)))


@router.get("/{character_id}", response_model=CharacterDetail)
def read_character(character_id: str, service: CharacterService = Service) -> CharacterDetail:
    """Returns the character with its whole card. Answers 404 `NOT_FOUND` when no character has that id."""
    return _detail(service.get(character_id))
