"""Diagnostic routes of docs/api.md: the prompt assembly of a reply, the conversation's replies that have one, and the
application setting that turns diagnostic mode on (`GET` and `PATCH /settings`, which holds only `app.diagnostic_mode`
until the settings screens arrive).

Like the other routes of this phase they stay out of the committed OpenAPI document until it is next regenerated with
the Docker `openapi` target, so `include_in_schema` is off. Both diagnostic reads answer 404 while the mode is off.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.services.diagnostics import DiagnosticsService

router = APIRouter(tags=["diagnostics"], include_in_schema=False)


class MemoryUsed(BaseModel):
    """A memory or episode that was in the prompt, with its score and the parts of the score (each 0 to 1, before
    weighting). `content` is its text now, null once it was deleted."""

    id: str
    type: str
    kind: str
    status: str
    label: str
    score: float
    token_cost: int
    parts: dict[str, float]
    content: str | None


class SummaryUsed(BaseModel):
    id: str
    level: int
    from_seq: int
    to_seq: int
    version: int
    status: str
    token_count: int


class Diagnostics(BaseModel):
    """The stored assembly of one reply. `blocks` maps each prompt block to its token count; `budgets` holds
    `context_limit`, `reply_reserve`, `safety_margin`, `available`, `memory_target` and `summary_target`. `notes`
    are the corrective notes of the anchor, `gaps` the message ranges no summary covers. `prompt_text` is null
    unless it was stored while diagnostic mode was on."""

    message_id: str
    conversation_id: str
    variant_id: str | None
    created_at: str
    token_total: int
    context_limit: int
    blocks: dict[str, int]
    budgets: dict[str, int]
    example_count: int
    notes: list[str]
    reductions: list[str]
    gaps: list[list[int]]
    history_count: int
    current_truncated: bool
    memories: list[MemoryUsed]
    summaries: list[SummaryUsed]
    prompt_text: str | None


class DiagnosticReply(BaseModel):
    """A reply with a stored assembly: `excerpt` is the start of its text."""

    message_id: str
    seq: int
    excerpt: str
    token_total: int
    context_limit: int
    created_at: str


class DiagnosticReplies(BaseModel):
    items: list[DiagnosticReply]


class Settings(BaseModel):
    """The application settings delivered so far."""

    model_config = ConfigDict(populate_by_name=True)

    diagnostic_mode: bool = Field(alias="app.diagnostic_mode")


class SettingsPatch(BaseModel):
    """Only the keys present change; an unknown key is refused with 422."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    diagnostic_mode: bool | None = Field(default=None, alias="app.diagnostic_mode")


def get_service(request: Request) -> DiagnosticsService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the diagnostic routes need a database")
    return DiagnosticsService(engine)


Service = Depends(get_service)


@router.get("/messages/{message_id}/diagnostics", response_model=Diagnostics)
def message_diagnostics(message_id: str, service: DiagnosticsService = Service) -> Diagnostics:
    """Returns the prompt assembly stored for the reply. 404 while diagnostic mode is off and for a reply with none."""
    return Diagnostics.model_validate(service.reply(message_id))


@router.get("/conversations/{conversation_id}/diagnostics", response_model=DiagnosticReplies)
def conversation_diagnostics(conversation_id: str, service: DiagnosticsService = Service) -> DiagnosticReplies:
    """Lists the replies of the conversation that have a stored assembly, newest first. 404 while diagnostic mode is off
    and for an unknown conversation."""
    return DiagnosticReplies(items=[DiagnosticReply(**item) for item in service.replies(conversation_id)])


@router.get("/settings", response_model=Settings, response_model_by_alias=True)
def get_settings(service: DiagnosticsService = Service) -> Settings:
    """Returns the application settings that exist: `app.diagnostic_mode`, false by default."""
    return Settings(diagnostic_mode=service.enabled())


@router.patch("/settings", response_model=Settings, response_model_by_alias=True)
def patch_settings(body: SettingsPatch, service: DiagnosticsService = Service) -> Settings:
    """Changes the settings present in the body and returns all of them. 422 for a key that does not exist."""
    if body.diagnostic_mode is not None:
        service.set_enabled(body.diagnostic_mode)
    return Settings(diagnostic_mode=service.enabled())
