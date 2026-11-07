"""Message routes of docs/api.md (Messages): send, history, edit, delete, regenerate, variants and activation.

Like the conversation routes they stay out of the committed OpenAPI document until it is next regenerated with the
Docker `openapi` target, so `include_in_schema` is off. `remember` and `diagnostics` belong to later phases.
"""

from typing import Literal

import anyio.from_thread
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.domain.messages import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, JobRef, Message, Variant, message_document
from lyrveil.services.messages import MessageService

router = APIRouter(tags=["messages"], include_in_schema=False)


class MessageOut(BaseModel):
    """A message as the chat shows it. `content` is the active variant's text, empty while a reply is queued."""

    id: str
    conversation_id: str
    seq: int
    author: str
    kind: str
    status: str
    read_state: str | None
    active_variant_id: str | None
    content: str
    variant_count: int
    edited_at: str | None
    created_at: str


class MessagePage(BaseModel):
    """`items` ascend by `seq`; `next_before_seq` is the `before_seq` of the next older page, null at the start."""

    items: list[MessageOut]
    next_before_seq: int | None


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    queue_position: int


class SendResult(BaseModel):
    message: MessageOut
    reply: MessageOut
    job: JobOut


class RegenerateResult(BaseModel):
    message: MessageOut
    variant_id: str
    job: JobOut


class VariantOut(BaseModel):
    id: str
    message_id: str
    variant_no: int
    content: str
    source: str
    status: str
    created_at: str


class VariantList(BaseModel):
    items: list[VariantOut]


class SendMessage(BaseModel):
    """The text limit is checked by the service, which answers 413 over 8000 characters and 422 for blank text."""

    model_config = ConfigDict(extra="forbid")

    client_message_id: str | None = Field(default=None, min_length=1, max_length=100)
    content: str
    intent: Literal["auto", "text", "image"] = "auto"


class EditMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


def _message(message: Message) -> MessageOut:
    # The same document as the `message` of the message events, so REST and the WebSocket never disagree on shape.
    return MessageOut.model_validate(message_document(message))


def _job(job: JobRef) -> JobOut:
    return JobOut(id=job.id, kind=job.kind, status=job.status, queue_position=job.queue_position)


def _variant(variant: Variant) -> VariantOut:
    return VariantOut(
        id=variant.id,
        message_id=variant.message_id,
        variant_no=variant.variant_no,
        content=variant.content,
        source=variant.source.value,
        status=variant.status.value,
        created_at=variant.created_at,
    )


def _wake_worker(request: Request) -> None:
    """Lets the job worker pick the job just queued at once instead of at its next poll. Needs the route's worker
    thread, which FastAPI gives every plain `def` route. Does nothing while no worker runs."""
    scheduler = request.app.state.scheduler
    if scheduler is not None:
        anyio.from_thread.run_sync(scheduler.notify)


def get_service(request: Request) -> MessageService:
    """Returns the service on the application's engine; startup always opens one before it serves routes."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the message routes need a database")
    return MessageService(engine)


Service = Depends(get_service)


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def list_messages(
    conversation_id: str,
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    service: MessageService = Service,
) -> MessagePage:
    """Returns one page of history: the newest messages below `before_seq`, oldest first. 404 for a missing or deleted
    chat."""
    page = service.history(conversation_id, before_seq, limit)
    return MessagePage(items=[_message(item) for item in page.items], next_before_seq=page.next_before_seq)


@router.post("/conversations/{conversation_id}/messages", response_model=SendResult, status_code=202)
def send_message(
    conversation_id: str, body: SendMessage, request: Request, service: MessageService = Service
) -> SendResult:
    """Saves the user's message and queues the character's reply. Sending the same `client_message_id` again returns
    the first result. 404 for a missing or deleted chat, 409 for an archived chat or a full queue, 413 over 8000
    characters and 422 for blank text."""
    sent = service.send(conversation_id, body.content, client_message_id=body.client_message_id, intent=body.intent)
    _wake_worker(request)
    return SendResult(message=_message(sent.message), reply=_message(sent.reply), job=_job(sent.job))


@router.patch("/messages/{message_id}", response_model=MessageOut)
def edit_message(message_id: str, body: EditMessage, service: MessageService = Service) -> MessageOut:
    """Edits a message by adding an `edited` variant. 404 for a missing message, 409 while it is being produced."""
    return _message(service.edit(message_id, body.content))


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(message_id: str, service: MessageService = Service) -> Response:
    """Soft-deletes a message; 404 when it does not exist or is already deleted, 409 while it is being produced."""
    service.delete(message_id)
    return Response(status_code=204)


@router.post("/messages/{message_id}/regenerate", response_model=RegenerateResult, status_code=202)
def regenerate_message(message_id: str, request: Request, service: MessageService = Service) -> RegenerateResult:
    """Adds a variant to the last character message and queues its generation. 409 for any other message."""
    result = service.regenerate(message_id)
    _wake_worker(request)
    return RegenerateResult(message=_message(result.message), variant_id=result.variant_id, job=_job(result.job))


@router.get("/messages/{message_id}/variants", response_model=VariantList)
def list_variants(message_id: str, service: MessageService = Service) -> VariantList:
    """Returns every version of a message, in creation order."""
    return VariantList(items=[_variant(item) for item in service.variants(message_id)])


@router.post("/messages/{message_id}/variants/{variant_id}/activate", response_model=MessageOut)
def activate_variant(message_id: str, variant_id: str, service: MessageService = Service) -> MessageOut:
    """Shows another version of the last character message. 409 for any other message."""
    return _message(service.activate_variant(message_id, variant_id))
