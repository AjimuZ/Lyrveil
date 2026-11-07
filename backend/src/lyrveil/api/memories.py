"""Memory routes of docs/api.md (Memory) that the Memory Studio uses: the memory list, one memory with its sources and
conflict group, create, edit, delete, lock, unlock, confirm and history; the episode list; and a conversation's summary
checkpoints, relationship state and change log.

Not delivered: resolve, share, apply-to-profile, episode edits, relationship corrections and reprocess. Like the other
routes of this phase they stay out of the committed OpenAPI document until it is next regenerated with the Docker
`openapi` target, so `include_in_schema` is off.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.persistence.memory_studio import MemoryFilter
from lyrveil.services.memories import MemoryEdit, MemoryService
from lyrveil.services.memory_studio import DEFAULT_PAGE, MAX_PAGE, MemoryStudioService

router = APIRouter(tags=["memory"], include_in_schema=False)

Kind = Literal[
    "user_fact",
    "user_preference",
    "character_fact",
    "narrative_event",
    "promise",
    "decision",
    "open_thread",
    "boundary",
    "identity_candidate",
]
Provenance = Literal["stated", "inferred", "fictional", "user_confirmed"]
Status = Literal["active", "uncertain", "superseded", "contradicted", "expired", "invalidated"]


class SourceOut(BaseModel):
    """A message a memory, episode or state change rests on. `valid` is false once the message is deleted or shows
    another variant; `excerpt` is the start of the text that was read."""

    message_id: str
    conversation_id: str
    seq: int
    author: str
    excerpt: str
    valid: bool


class PeerOut(BaseModel):
    """Another memory of the same conflict group."""

    id: str
    content: str
    status: str
    provenance: str
    confidence: float


class MemoryOut(BaseModel):
    """A memory as the Studio lists it. `conflict_group_id` is set while the memory is part of a contradiction, and
    `status` is then `uncertain` until the user settles it."""

    id: str
    character_id: str
    scope: str
    conversation_id: str | None
    origin_conversation_id: str | None
    kind: str
    content: str
    subject_key: str | None
    value_text: str | None
    provenance: str
    durability: str
    status: str
    importance: int
    confidence: float
    locked: bool
    needs_review: bool
    valid_from: str | None
    valid_to: str | None
    expires_at: str | None
    superseded_by_id: str | None
    conflict_group_id: str | None
    version: int
    language: str
    last_used_at: str | None
    use_count: int
    created_at: str
    updated_at: str
    source_count: int = 0


class MemoryDetail(MemoryOut):
    """A memory with its source messages and the other memories of its conflict group."""

    sources: list[SourceOut]
    conflict: list[PeerOut]


class MemoryList(BaseModel):
    items: list[MemoryOut]
    next_cursor: str | None


class RevisionOut(BaseModel):
    """The memory as it was under `version`, and who changed it next (`user`, `extraction` or `consistency`) and why."""

    version: int
    actor: str
    reason: str
    created_at: str
    content: str | None
    subject: str | None
    value: str | None
    status: str | None
    provenance: str | None
    confidence: float | None
    importance: int | None


class RevisionList(BaseModel):
    items: list[RevisionOut]


class CreateMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=400)
    kind: Kind = "user_fact"
    importance: int = Field(default=3, ge=1, le=5)


class PatchMemory(BaseModel):
    """Only the fields present change. The text is 1 to 280 characters and the importance 1 to 5."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=400)
    subject: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=200)
    importance: int | None = None


class LinkOut(BaseModel):
    direction: Literal["from", "to"]
    relation: str
    episode_id: str
    title: str


class EpisodeOut(BaseModel):
    id: str
    character_id: str
    conversation_id: str
    title: str
    description: str
    occurred_at: str
    participants: list[str]
    importance: int
    status: str
    locked: bool
    created_at: str
    updated_at: str
    sources: list[SourceOut]
    links: list[LinkOut]


class EpisodeList(BaseModel):
    items: list[EpisodeOut]


class SummaryOut(BaseModel):
    """A checkpoint with its range, level, version and status. `content` holds `events`, `decisions`,
    `relationship_changes`, `unresolved`, `key_facts` and `source_seqs`, the messages the summary rests on."""

    id: str
    level: int
    from_seq: int
    to_seq: int
    version: int
    status: str
    text: str
    content: dict[str, Any]
    prompt_version: str
    token_count: int
    created_at: str


class SummaryList(BaseModel):
    items: list[SummaryOut]


class RecentEventOut(BaseModel):
    id: str
    title: str
    occurred_at: str


class RelationshipOut(BaseModel):
    """The relationship in words first (`description`, `bands`) and as numbers only for the advanced view (`values`)."""

    written: bool
    description: str
    narrative: str
    expectations: str
    boundaries: str
    emotional_state: str | None
    current_topic: str | None
    open_threads: list[str]
    goals: list[str]
    narrative_changes: list[str]
    recent_events: list[RecentEventOut]
    bands: dict[str, str]
    values: dict[str, int]
    updated_at: str | None


class HistoryOut(BaseModel):
    id: str
    event_kind: str
    dimension: str | None
    delta: int | None
    value_before: str | None
    value_after: str | None
    reason: str
    run_id: str | None
    status: str
    created_at: str
    sources: list[SourceOut]


class HistoryList(BaseModel):
    items: list[HistoryOut]


def _engine(request: Request) -> Engine:
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the memory routes need a database")
    return engine


def get_studio(request: Request) -> MemoryStudioService:
    """Returns the Studio service on the application's engine; startup always opens one before it serves routes."""
    return MemoryStudioService(_engine(request))


def get_memories(request: Request) -> MemoryService:
    """Returns the edit and delete service on the application's engine."""
    return MemoryService(_engine(request))


Studio = Depends(get_studio)
Memories = Depends(get_memories)


@router.get("/memories", response_model=MemoryList)
def list_memories(
    character_id: str = Query(min_length=1),
    conversation_id: str | None = None,
    scope: Literal["conversation", "shared"] | None = None,
    kind: Kind | None = None,
    provenance: Provenance | None = None,
    status: Annotated[list[Status] | None, Query()] = None,
    locked: bool | None = None,
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    studio: MemoryStudioService = Studio,
) -> MemoryList:
    """Lists the character's memories, the most recently changed first. `status` can be repeated; without it every
    status is listed. `q` is plain text matched by stem, never search syntax. `next_cursor` is the `cursor` of the next
    page, null at the end. 404 for an unknown character."""
    filters = MemoryFilter(
        character_id=character_id,
        conversation_id=conversation_id,
        scope=scope,
        kind=kind,
        provenance=provenance,
        statuses=tuple(status or ()),
        locked=locked,
    )
    page = studio.list_memories(filters, q, limit, cursor)
    return MemoryList(items=[MemoryOut(**item) for item in page.items], next_cursor=page.next_cursor)


@router.post("/memories", response_model=MemoryDetail, status_code=201)
def create_memory(body: CreateMemory, studio: MemoryStudioService = Studio) -> MemoryDetail:
    """Adds a memory the user wrote to a conversation, confirmed by the user. 404 for an unknown conversation, 409 when
    the conversation already has a memory in use with the same content."""
    return MemoryDetail(**studio.create(body.conversation_id, body.content, body.kind, body.importance))


@router.get("/memories/{memory_id}", response_model=MemoryDetail)
def get_memory(memory_id: str, studio: MemoryStudioService = Studio) -> MemoryDetail:
    """Returns one memory with its sources and conflict group; 404 when it does not exist."""
    return MemoryDetail(**studio.get_memory(memory_id))


@router.patch("/memories/{memory_id}", response_model=MemoryDetail)
def patch_memory(
    memory_id: str,
    body: PatchMemory,
    memories: MemoryService = Memories,
    studio: MemoryStudioService = Studio,
) -> MemoryDetail:
    """Corrects a memory and records a revision. 404 when it does not exist, 422 for text or an importance out of
    range and 409 when another memory in use has the same content."""
    fields = body.model_dump(exclude_unset=True)
    memories.edit(memory_id, MemoryEdit(**fields))
    return MemoryDetail(**studio.get_memory(memory_id))


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str, memories: MemoryService = Memories) -> Response:
    """Deletes the memory and leaves a tombstone; 404 when it does not exist."""
    memories.delete(memory_id)
    return Response(status_code=204)


@router.post("/memories/{memory_id}/lock", response_model=MemoryDetail)
def lock_memory(memory_id: str, studio: MemoryStudioService = Studio) -> MemoryDetail:
    """Protects the memory from automation; 404 when it does not exist."""
    return MemoryDetail(**studio.set_locked(memory_id, True))


@router.post("/memories/{memory_id}/unlock", response_model=MemoryDetail)
def unlock_memory(memory_id: str, studio: MemoryStudioService = Studio) -> MemoryDetail:
    """Lets automation change the memory again; 404 when it does not exist."""
    return MemoryDetail(**studio.set_locked(memory_id, False))


@router.post("/memories/{memory_id}/confirm", response_model=MemoryDetail)
def confirm_memory(memory_id: str, studio: MemoryStudioService = Studio) -> MemoryDetail:
    """Makes the memory `user_confirmed` with confidence 1.0, recording a revision. 404 when it does not exist, 409
    when it is not in use."""
    return MemoryDetail(**studio.confirm(memory_id))


@router.get("/memories/{memory_id}/revisions", response_model=RevisionList)
def memory_revisions(memory_id: str, studio: MemoryStudioService = Studio) -> RevisionList:
    """Returns the memory's history, newest first; 404 when it does not exist."""
    return RevisionList(items=[RevisionOut(**item) for item in studio.revisions(memory_id)])


@router.get("/episodes", response_model=EpisodeList)
def list_episodes(
    character_id: str = Query(min_length=1),
    conversation_id: str | None = None,
    status: Literal["active", "invalidated"] = "active",
    q: str | None = Query(default=None, max_length=200),
    studio: MemoryStudioService = Studio,
) -> EpisodeList:
    """Lists the character's episodes, the most recent first, up to 200, with their sources and links; 404 for an
    unknown character."""
    episodes = studio.list_episodes(character_id, conversation_id, q, status)
    return EpisodeList(items=[EpisodeOut(**item) for item in episodes])


@router.get("/conversations/{conversation_id}/summaries", response_model=SummaryList)
def conversation_summaries(conversation_id: str, studio: MemoryStudioService = Studio) -> SummaryList:
    """Returns every summary checkpoint of the conversation with its status; 404 for an unknown conversation."""
    return SummaryList(items=[SummaryOut(**item) for item in studio.summaries(conversation_id)])


@router.get("/conversations/{conversation_id}/relationship", response_model=RelationshipOut)
def conversation_relationship(conversation_id: str, studio: MemoryStudioService = Studio) -> RelationshipOut:
    """Returns the relationship state of the conversation; `written` is false before a run wrote one. 404 for an
    unknown conversation."""
    return RelationshipOut(**studio.relationship(conversation_id))


@router.get("/conversations/{conversation_id}/relationship/history", response_model=HistoryList)
def conversation_relationship_history(conversation_id: str, studio: MemoryStudioService = Studio) -> HistoryList:
    """Returns the change log of the relationship state, newest first, with reasons and sources; 404 for an unknown
    conversation."""
    return HistoryList(items=[HistoryOut(**item) for item in studio.relationship_history(conversation_id)])
