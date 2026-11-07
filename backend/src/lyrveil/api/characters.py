"""Character routes of docs/api.md (Characters): list, create with the whole card, read and save the card, its
versions with diff and restore, duplicate, archive, purge, the tag vocabulary the Creator offers, and the assisted
draft.

The exchange routes of that section arrive with the rest of the Creator (P03). Not in the committed OpenAPI document
yet, for the reason given in lyrveil.api.conversations.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from lyrveil.api.jobs import JobOut, wake_worker
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.characters import CharacterSummary, StoredCharacter, VersionSummary
from lyrveil.images.tags import load_vocabulary
from lyrveil.services.character_creator import CharacterCreatorService
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


class VersionOut(BaseModel):
    version_no: int
    change_summary: str
    actor: str
    created_at: str


class VersionList(BaseModel):
    items: list[VersionOut]


class VersionDetail(VersionOut):
    card: CharacterCard


class FieldChangeOut(BaseModel):
    field: str
    before: Any
    after: Any


class VersionDiff(BaseModel):
    from_version_no: int
    to_version_no: int
    changes: list[FieldChangeOut]


class VocabularyEntryOut(BaseModel):
    tag: str
    category: str
    aliases_it: list[str]


class VocabularyOut(BaseModel):
    items: list[VocabularyEntryOut]


class PurgePreview(BaseModel):
    conversations: int
    messages: int
    versions: int


class PurgeBody(BaseModel):
    confirm_name: str


class DraftBody(BaseModel):
    """The length limit is checked by the service, which answers 422 with an Italian detail."""

    model_config = ConfigDict(extra="forbid")

    description: str
    character_id: str | None = None


def _version_out(version: VersionSummary) -> VersionOut:
    return VersionOut(
        version_no=version.version_no,
        change_summary=version.change_summary,
        actor=version.actor,
        created_at=version.created_at,
    )


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


@router.post("/drafts", response_model=JobOut, status_code=202)
def start_draft(body: DraftBody, request: Request) -> JobOut:
    """Queues a `character_draft` job that drafts a card from `description` and answers it with `202`. The job's result
    (`GET /jobs/{id}`) holds the card to review; no character is written until the user saves it. With `character_id`
    the draft rewrites that character's card and keeps its locked traits and visual fields. Answers 422
    `VALIDATION_ERROR` for a blank description or one over 2000 characters, 404 `NOT_FOUND` for an unknown character
    and 409 `CONFLICT` when the queue is full."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the character routes need a database")
    job = CharacterCreatorService(engine).start_draft(body.description, body.character_id)
    wake_worker(request)
    return JobOut(id=job.id, kind=job.kind, status=job.status, queue_position=job.queue_position)


@router.get("/vocabulary", response_model=VocabularyOut)
def read_vocabulary() -> VocabularyOut:
    """Returns the tag vocabulary of the card categories, in file order: the tags the Creator offers for each identity
    slot, with the Italian words its search matches. Declared before `/{character_id}`, so "vocabulary" is no id."""
    return VocabularyOut(
        items=[
            VocabularyEntryOut(tag=entry.tag, category=entry.category, aliases_it=list(entry.aliases_it))
            for entry in load_vocabulary().entries
        ]
    )


@router.get("/{character_id}", response_model=CharacterDetail)
def read_character(character_id: str, service: CharacterService = Service) -> CharacterDetail:
    """Returns the character with its whole card. Answers 404 `NOT_FOUND` when no character has that id."""
    return _detail(service.get(character_id))


@router.put("/{character_id}", response_model=CharacterDetail)
def save_character(
    character_id: str, body: Annotated[dict[str, Any], Body()], service: CharacterService = Service
) -> CharacterDetail:
    """Saves the whole card, in the same two forms as the create route. It creates a version when anything changed and
    answers the character; a card equal to the stored one creates none. The locks in the body are the new locks. Answers
    404 `NOT_FOUND` for an unknown character and 422 `VALIDATION_ERROR` as the create route does."""
    return _detail(service.update(character_id, _as_card(body)))


@router.post("/{character_id}/duplicate", response_model=CharacterOut, status_code=201)
def duplicate_character(character_id: str, service: CharacterService = Service) -> CharacterOut:
    """Creates an active character with a copy of the card, named "<first name> (copia)", at version 1 with actor
    `duplicate`. No conversation, memory or older version is copied. Answers 404 `NOT_FOUND` for an unknown
    character."""
    return _out(service.duplicate(character_id))


@router.post("/{character_id}/archive", response_model=CharacterOut)
def archive_character(character_id: str, service: CharacterService = Service) -> CharacterOut:
    """Hides the character, and its conversations, from the active lists; nothing is deleted. Archiving an archived
    character answers it unchanged. Answers 404 `NOT_FOUND` for an unknown character."""
    return _out(service.archive(character_id))


@router.post("/{character_id}/unarchive", response_model=CharacterOut)
def unarchive_character(character_id: str, service: CharacterService = Service) -> CharacterOut:
    """Shows an archived character again. Answers 404 `NOT_FOUND` for an unknown character."""
    return _out(service.unarchive(character_id))


@router.get("/{character_id}/purge-preview", response_model=PurgePreview)
def purge_preview(character_id: str, service: CharacterService = Service) -> PurgePreview:
    """Counts what a purge deletes besides the character itself. Answers 404 `NOT_FOUND` for an unknown character."""
    counts = service.purge_preview(character_id)
    return PurgePreview(conversations=counts.conversations, messages=counts.messages, versions=counts.versions)


@router.post("/{character_id}/purge", status_code=204)
def purge_character(character_id: str, body: PurgeBody, service: CharacterService = Service) -> Response:
    """Deletes the character for good, with its conversations, messages and versions, once `confirm_name` equals its
    first name. Answers 204. Answers 404 `NOT_FOUND` for an unknown character, 422 `VALIDATION_ERROR` when the name does
    not match and 409 `CONFLICT` while a job of the character is running; nothing is deleted in those cases."""
    service.purge(character_id, body.confirm_name)
    return Response(status_code=204)


@router.get("/{character_id}/versions", response_model=VersionList)
def list_versions(character_id: str, service: CharacterService = Service) -> VersionList:
    """Lists the versions, newest first. Answers 404 `NOT_FOUND` for an unknown character."""
    return VersionList(items=[_version_out(item) for item in service.list_versions(character_id)])


@router.get("/{character_id}/versions/{version_no}", response_model=VersionDetail)
def read_version(character_id: str, version_no: int, service: CharacterService = Service) -> VersionDetail:
    """Returns one version with its card. Answers 404 `NOT_FOUND` for an unknown character or version."""
    found = service.get_version(character_id, version_no)
    return VersionDetail(**_version_out(found.summary).model_dump(), card=found.card)


@router.get("/{character_id}/versions/{from_no}/diff/{to_no}", response_model=VersionDiff)
def diff_versions(character_id: str, from_no: int, to_no: int, service: CharacterService = Service) -> VersionDiff:
    """Compares two versions field by field: `changes` lists each dotted field path with its value in `from_no` and in
    `to_no`. Answers 404 `NOT_FOUND` for an unknown character or version."""
    changes = service.diff(character_id, from_no, to_no)
    return VersionDiff(
        from_version_no=from_no,
        to_version_no=to_no,
        changes=[FieldChangeOut(field=item.field, before=item.before, after=item.after) for item in changes],
    )


@router.post("/{character_id}/versions/{version_no}/restore", response_model=CharacterDetail)
def restore_version(character_id: str, version_no: int, service: CharacterService = Service) -> CharacterDetail:
    """Appends a new version holding the card of `version_no` and answers the character. Answers 404 `NOT_FOUND` for an
    unknown character or version."""
    return _detail(service.restore(character_id, version_no))
