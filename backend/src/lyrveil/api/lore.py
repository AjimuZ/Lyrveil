"""Lore routes of docs/api.md (Lore): worlds, books, entries and links, and a character's own book.

The match route runs the keyword matcher with no model. The import and export routes read and write `lorebook_v3`
files (lyrveil.services.lorebook). Not in the committed OpenAPI document yet, like the persona routes:
`include_in_schema` is off until `backend/openapi.json` is regenerated with Docker.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from lyrveil.domain.exchange import MAX_IMPORT_BYTES
from lyrveil.domain.lore import BookKind, BookStatus, LoreBook, LoreEntry, LoreLink
from lyrveil.services.lore import LoreService
from lyrveil.services.lore_matcher import MatchReason

router = APIRouter(tags=["lore"], include_in_schema=False)

# A pasted test text is a few messages at most; the bound keeps one request from holding the worker on a huge text.
MAX_MATCH_TEXT_CHARS = 20_000
MAX_LANGUAGE_CHARS = 16


class BookOut(BaseModel):
    """A world or a character's own book. `character_id` is null for a world; `entry_count` is how many entries it
    holds."""

    id: str
    kind: BookKind
    character_id: str | None
    name: str
    description: str
    scan_depth: int
    token_budget: int | None
    status: BookStatus
    entry_count: int
    created_at: str
    updated_at: str


class BookList(BaseModel):
    items: list[BookOut]


class WorldImportOut(BaseModel):
    """The world a `lorebook_v3` file became, and the import report: `entries` imported, how many of them are
    `disabled`, and `items`, each with `index`, `title`, `reason`, `keys` and an Italian `detail`."""

    book: BookOut
    report: dict[str, Any]


class BookIn(BaseModel):
    """The editable fields of a book. A PUT replaces all of them; one left out takes its default. The values are not
    typed here, so the service refuses a wrong one with an Italian detail that names the field and its limit."""

    model_config = ConfigDict(extra="forbid")

    name: Any = None
    description: Any = None
    scan_depth: Any = None
    token_budget: Any = None


class EntryOut(BaseModel):
    """An entry as the editor shows it. `never_inserted` is true when its rendered line is over its block's maximum:
    the entry is stored but no prompt reads it."""

    id: str
    book_id: str
    title: str
    content: str
    keys: list[str]
    secondary_keys: list[str]
    exclude_keys: list[str]
    case_sensitive: bool
    constant: bool
    enabled: bool
    priority: int
    insertion_order: int
    note: str
    never_inserted: bool
    created_at: str
    updated_at: str


class EntryList(BaseModel):
    items: list[EntryOut]


class EntryIn(BaseModel):
    """The editable fields of an entry. A PUT replaces all of them; one left out takes its default. The values are
    not typed here, for the reason given at BookIn."""

    model_config = ConfigDict(extra="forbid")

    title: Any = None
    content: Any = None
    keys: Any = None
    secondary_keys: Any = None
    exclude_keys: Any = None
    case_sensitive: Any = None
    constant: Any = None
    enabled: Any = None
    priority: Any = None
    insertion_order: Any = None
    note: Any = None


class LinkOut(BaseModel):
    """A world linked to one target: exactly one of the three ids is set."""

    id: str
    book_id: str
    character_id: str | None
    scenario_id: str | None
    conversation_id: str | None
    created_at: str


class LinkList(BaseModel):
    items: list[LinkOut]


class LinkIn(BaseModel):
    """The target of a link: exactly one of the three ids. The service refuses any other count."""

    model_config = ConfigDict(extra="forbid")

    character_id: str | None = None
    scenario_id: str | None = None
    conversation_id: str | None = None


class MatchIn(BaseModel):
    """The text to try the keywords on, and the language that stems it (a code such as `it` or `en`; blank means the
    language of the book's keys)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_MATCH_TEXT_CHARS)
    language: str | None = Field(default=None, max_length=MAX_LANGUAGE_CHARS)


class EntryMatchOut(BaseModel):
    """One entry's outcome. `reason` is `matched` or `constant` when `active`, else why it stayed inactive. The three
    keys are the texts as typed: the primary key that matched, the secondary key that matched, and the exclude key that
    deactivated the entry."""

    entry_id: str
    title: str
    active: bool
    reason: MatchReason
    matched_key: str | None
    matched_secondary_key: str | None
    excluded_key: str | None


class MatchOut(BaseModel):
    items: list[EntryMatchOut]


def _book_out(book: LoreBook) -> BookOut:
    return BookOut(
        id=book.id,
        kind=book.kind,
        character_id=book.character_id,
        name=book.name,
        description=book.description,
        scan_depth=book.scan_depth,
        token_budget=book.token_budget,
        status=book.status,
        entry_count=book.entry_count,
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def _entry_out(entry: LoreEntry) -> EntryOut:
    return EntryOut(
        id=entry.id,
        book_id=entry.book_id,
        title=entry.title,
        content=entry.content,
        keys=list(entry.keys),
        secondary_keys=list(entry.secondary_keys),
        exclude_keys=list(entry.exclude_keys),
        case_sensitive=entry.case_sensitive,
        constant=entry.constant,
        enabled=entry.enabled,
        priority=entry.priority,
        insertion_order=entry.insertion_order,
        note=entry.note,
        never_inserted=entry.never_inserted,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _link_out(link: LoreLink) -> LinkOut:
    return LinkOut(
        id=link.id,
        book_id=link.book_id,
        character_id=link.character_id,
        scenario_id=link.scenario_id,
        conversation_id=link.conversation_id,
        created_at=link.created_at,
    )


def get_service(request: Request) -> LoreService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the lore routes need a database")
    return LoreService(engine)


Service = Depends(get_service)


@router.get("/lore/worlds", response_model=BookList)
def list_worlds(
    status: BookStatus = BookStatus.ACTIVE,
    q: str | None = Query(default=None, max_length=200),
    service: LoreService = Service,
) -> BookList:
    """Lists the worlds in `status` (default active) by name. `q` keeps those whose name or description contains every
    word of it, ignoring case and accents."""
    return BookList(items=[_book_out(book) for book in service.list_worlds(status, q)])


@router.post("/lore/worlds", response_model=BookOut, status_code=201)
def create_world(body: BookIn, service: LoreService = Service) -> BookOut:
    """Creates an active world: 422 `VALIDATION_ERROR` for a blank name or a value out of its limit, naming the field,
    and 422 `CONTENT_POLICY_BLOCKED` for a name that describes a minor."""
    return _book_out(service.create_world(body.model_dump(exclude_none=True)))


@router.post("/lore/worlds/import", response_model=WorldImportOut, status_code=201)
async def import_world(file: Annotated[UploadFile, File()], service: LoreService = Service) -> WorldImportOut:
    """Creates an active world from an uploaded `lorebook_v3` file and answers `201` with it and the import report.
    Answers 413 `PAYLOAD_TOO_LARGE` over 64 MB, 422 `IMPORT_INVALID` for a file that is not UTF-8 JSON, has another
    `spec` or a known field of the wrong type, and 422 `CONTENT_POLICY_BLOCKED` naming the path of a string that
    describes a minor. Nothing is written in those cases."""
    # One byte over the limit is enough to refuse, so a larger file is never read whole into memory.
    data = await file.read(MAX_IMPORT_BYTES + 1)
    book, report = await run_in_threadpool(service.import_world, data)
    return WorldImportOut(book=_book_out(book), report=report)


@router.get("/lore/books/{book_id}/export")
def export_book(book_id: str, service: LoreService = Service) -> Response:
    """Answers the book, a world or an own book, as a `lorebook_v3` JSON download named after it; 404 for an unknown
    book."""
    file_name, content = service.export_book(book_id)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/lore/books/{book_id}", response_model=BookOut)
def get_book(book_id: str, service: LoreService = Service) -> BookOut:
    """Returns a world or an own book in any status; 404 when it does not exist."""
    return _book_out(service.get_book(book_id))


@router.put("/lore/books/{book_id}", response_model=BookOut)
def put_book(book_id: str, body: BookIn, service: LoreService = Service) -> BookOut:
    """Replaces the name, description, scan depth and token budget: 404 for an unknown book and the refusals of the
    create route."""
    return _book_out(service.update_book(book_id, body.model_dump(exclude_none=True)))


@router.post("/lore/books/{book_id}/archive", response_model=BookOut)
def archive_world(book_id: str, service: LoreService = Service) -> BookOut:
    """Moves a world to the archived list; its entries and links stay and keep working. 404 for an unknown book and 409
    `CONFLICT` for an own book, which follows its character. Archiving an archived world changes nothing."""
    return _book_out(service.set_archived(book_id, True))


@router.post("/lore/books/{book_id}/unarchive", response_model=BookOut)
def unarchive_world(book_id: str, service: LoreService = Service) -> BookOut:
    """Brings an archived world back to the active list; the refusals of the archive route."""
    return _book_out(service.set_archived(book_id, False))


@router.delete("/lore/books/{book_id}", status_code=204)
def delete_book(book_id: str, service: LoreService = Service) -> Response:
    """Deletes a world with its entries and links: 404 when it does not exist and 409 `CONFLICT` for an own book, which
    goes only with its character."""
    service.delete_world(book_id)
    return Response(status_code=204)


@router.get("/characters/{character_id}/lore", response_model=BookOut)
def get_character_lore(character_id: str, service: LoreService = Service) -> BookOut:
    """Returns the character's own book, creating it empty on first use; 404 when the character does not exist."""
    return _book_out(service.own_book(character_id))


@router.get("/lore/books/{book_id}/entries", response_model=EntryList)
def list_entries(
    book_id: str,
    enabled: bool | None = None,
    q: str | None = Query(default=None, max_length=200),
    service: LoreService = Service,
) -> EntryList:
    """Lists the entries of a book by `insertion_order`. `enabled` keeps the enabled or the disabled ones; `q` keeps
    those whose title, content, keys or note contains every word of it, ignoring case and accents. 404 for an unknown
    book."""
    return EntryList(items=[_entry_out(entry) for entry in service.list_entries(book_id, enabled, q)])


@router.post("/lore/books/{book_id}/entries", response_model=EntryOut, status_code=201)
def create_entry(book_id: str, body: EntryIn, service: LoreService = Service) -> EntryOut:
    """Creates an entry: 404 for an unknown book, 422 `VALIDATION_ERROR` naming the field and its limit (an empty
    content, a text over its length, more than 16 keys, a key with no stem such as `lui`), 422
    `CONTENT_POLICY_BLOCKED` for a title or content that describes a minor, and 409 `CONFLICT` for a 501st entry."""
    return _entry_out(service.create_entry(book_id, body.model_dump(exclude_none=True)))


@router.get("/lore/entries/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: str, service: LoreService = Service) -> EntryOut:
    """Returns one entry; 404 when it does not exist."""
    return _entry_out(service.get_entry(entry_id))


@router.put("/lore/entries/{entry_id}", response_model=EntryOut)
def put_entry(entry_id: str, body: EntryIn, service: LoreService = Service) -> EntryOut:
    """Replaces every editable field and stems the keys again: 404 for an unknown entry and the refusals of the create
    route."""
    return _entry_out(service.update_entry(entry_id, body.model_dump(exclude_none=True)))


@router.delete("/lore/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: str, service: LoreService = Service) -> Response:
    """Deletes an entry; 404 when it does not exist."""
    service.delete_entry(entry_id)
    return Response(status_code=204)


@router.post("/lore/books/{book_id}/match", response_model=MatchOut)
def match_book(book_id: str, body: MatchIn, service: LoreService = Service) -> MatchOut:
    """Runs the keyword matcher on `text`, as one message, and answers every entry of the book by `insertion_order` with
    whether it is active, the key that matched or the reason it stayed inactive. It loads no model and changes nothing.
    404 for an unknown book, 422 `VALIDATION_ERROR` for a missing or over-long text."""
    return MatchOut(
        items=[
            EntryMatchOut(
                entry_id=entry.id,
                title=entry.title,
                active=result.active,
                reason=result.reason,
                matched_key=result.matched_key,
                matched_secondary_key=result.matched_secondary_key,
                excluded_key=result.excluded_key,
            )
            for entry, result in service.match(book_id, body.text, body.language)
        ]
    )


@router.get("/lore/books/{book_id}/links", response_model=LinkList)
def list_links(book_id: str, service: LoreService = Service) -> LinkList:
    """Lists what a book is linked to, oldest first; 404 when the book does not exist."""
    return LinkList(items=[_link_out(link) for link in service.list_links(book_id)])


@router.post("/lore/books/{book_id}/links", response_model=LinkOut, status_code=201)
def create_link(book_id: str, body: LinkIn, service: LoreService = Service) -> LinkOut:
    """Links a world to exactly one character, scenario or conversation: 422 `VALIDATION_ERROR` unless one target is
    given, 404 for an unknown book or target, and 409 `CONFLICT` for an own book or a target already linked."""
    return _link_out(
        service.add_link(
            book_id,
            character_id=body.character_id,
            scenario_id=body.scenario_id,
            conversation_id=body.conversation_id,
        )
    )


@router.delete("/lore/links/{link_id}", status_code=204)
def delete_link(link_id: str, service: LoreService = Service) -> Response:
    """Removes a link, never the world or its target; 404 when the link does not exist."""
    service.delete_link(link_id)
    return Response(status_code=204)
