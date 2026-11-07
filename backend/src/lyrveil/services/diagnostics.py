"""DiagnosticsService: the diagnostic mode of docs/frontend.md (Memory Studio, Diagnostica) over the stored prompt
assemblies of docs/data-model.md.

The mode is the application setting `app.diagnostic_mode`, off by default. While it is off nothing is shown: the reads
answer 404 as if the records did not exist. `assembly_record` is the one place that turns a built prompt into the
stored row; it keeps token counts, ids, scores and the corrective notes, and the prompt text only while the mode is on.
Reads open one read transaction and the switch one write transaction. No text of a message, memory or prompt is ever
logged here.
"""

from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.context.builder import Assembly, ContextSettings
from lyrveil.domain.errors import NotFoundError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import assemblies as repository
from lyrveil.persistence import memory_studio
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.assemblies import NewAssembly
from lyrveil.persistence.settings import DIAGNOSTIC_MODE

_MODE_OFF = "La modalità diagnostica non è attiva."
_NO_RECORD = "Per questa risposta non c'è nessuna traccia diagnostica."
_NO_CONVERSATION = "La conversazione non esiste."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def assembly_record(
    assembly: Assembly,
    settings: ContextSettings,
    *,
    conversation_id: str,
    message_id: str,
    variant_id: str,
    notes: Sequence[str],
    keep_prompt: bool,
) -> NewAssembly:
    """Returns the row that records `assembly`, built with `settings`, for the reply `message_id` / `variant_id`.

    `blocks` holds the token count of each block, the budgets (the window, the reply reserve, the safety margin, the
    room left for the prompt and the targets of memories and summaries), the corrective `notes` of the anchor, the
    reductions applied, the history size and the gaps no summary covers. `memory_refs` holds each memory or episode with
    its score and the parts of the score, but not its text, which is read from the memory when shown. The prompt text is
    kept only when `keep_prompt` is true (diagnostic mode).
    """
    blocks = {
        "blocks": dict(assembly.blocks),
        "budgets": {
            "context_limit": assembly.context_limit,
            "reply_reserve": assembly.max_length,
            "safety_margin": settings.safety_margin_tokens,
            "available": assembly.available,
            "memory_target": settings.memory_target_tokens,
            "summary_target": settings.summary_target_tokens,
        },
        "example_count": assembly.example_count,
        "notes": list(notes),
        "reductions": list(assembly.reductions),
        "gaps": [list(gap) for gap in assembly.gaps],
        "history_count": len(assembly.history_seqs),
        "current_truncated": assembly.current_truncated,
    }
    refs = [
        {
            "id": item.id,
            "type": item.item_type,
            "kind": item.kind,
            "status": item.status,
            "label": item.label,
            "score": item.score,
            "token_cost": item.token_cost,
            "parts": asdict(item.parts),
        }
        for item in assembly.memories
    ]
    return NewAssembly(
        id=new_id(),
        conversation_id=conversation_id,
        message_id=message_id,
        variant_id=variant_id,
        blocks=blocks,
        memory_refs=refs,
        summary_ids=list(assembly.summary_ids),
        token_total=assembly.token_total,
        context_limit=assembly.context_limit,
        prompt_text=assembly.prompt.text if keep_prompt else None,
    )


class DiagnosticsService:
    """The diagnostic switch and the reads of stored assemblies on `engine`. `clock` returns the aware current time."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def enabled(self) -> bool:
        """Tells whether diagnostic mode is on."""
        return read(self._engine, lambda connection: settings_repository.get_flag(connection, DIAGNOSTIC_MODE, False))

    def set_enabled(self, enabled: bool) -> bool:
        """Turns diagnostic mode on or off and returns the new state. Turning it off keeps the stored rows but they are
        no longer shown; the prompt text of replies built from now on is not stored."""
        now = to_utc_text(self._clock())
        write(self._engine, lambda connection: settings_repository.set_flag(connection, DIAGNOSTIC_MODE, enabled, now))
        return enabled

    def replies(self, conversation_id: str) -> list[dict[str, Any]]:
        """Returns the conversation's replies that have a stored assembly, newest first: `message_id`, `seq`, the
        start of the reply (`excerpt`), `token_total`, `context_limit` and `created_at`. Raises NotFoundError while the
        mode is off and for an unknown conversation."""

        def work(connection: Connection) -> list[dict[str, Any]]:
            _require_enabled(connection)
            if not memory_studio.conversation_exists(connection, conversation_id):
                raise NotFoundError(_NO_CONVERSATION)
            return repository.for_conversation(connection, conversation_id)

        return read(self._engine, work)

    def reply(self, message_id: str) -> dict[str, Any]:
        """Returns the stored assembly of a reply: its ids, `token_total`, `context_limit`, the `blocks` token counts,
        the `budgets`, the `notes`, `reductions`, `gaps`, `example_count`, `history_count`, `current_truncated`, the
        `memories` (id, type, kind, status, label, score, parts, token cost and the memory's current `content`, null
        once it is deleted), the `summaries` it used and the `prompt_text`, null unless it was stored. Raises
        NotFoundError while the mode is off and for a reply with no stored assembly."""

        def work(connection: Connection) -> dict[str, Any]:
            _require_enabled(connection)
            stored = repository.for_message(connection, message_id)
            if stored is None:
                raise NotFoundError(_NO_RECORD)
            refs: list[dict[str, Any]] = stored["memory_refs"]
            lines = repository.memory_lines(connection, [ref["id"] for ref in refs])
            memories = [{**ref, "content": lines.get(ref["id"])} for ref in refs]
            detail = dict(stored["blocks"])
            return {
                "message_id": stored["message_id"],
                "conversation_id": stored["conversation_id"],
                "variant_id": stored["variant_id"],
                "created_at": stored["created_at"],
                "token_total": stored["token_total"],
                "context_limit": stored["context_limit"],
                **detail,
                "memories": memories,
                "summaries": repository.summary_rows(connection, stored["summary_ids"]),
                "prompt_text": stored["prompt_text"],
            }

        return read(self._engine, work)

    def record(
        self,
        assembly: Assembly,
        settings: ContextSettings,
        *,
        conversation_id: str,
        message_id: str,
        variant_id: str,
        notes: Sequence[str],
    ) -> bool:
        """Stores the assembly of a delivered reply in one write transaction, with the prompt text only if diagnostic
        mode is on at that moment, and returns whether the text was stored. Always records the counts, so turning the
        mode on later still has the earlier replies' blocks and memories. Blocks: run it in a thread."""

        def work(connection: Connection) -> bool:
            keep = settings_repository.get_flag(connection, DIAGNOSTIC_MODE, False)
            new = assembly_record(
                assembly,
                settings,
                conversation_id=conversation_id,
                message_id=message_id,
                variant_id=variant_id,
                notes=notes,
                keep_prompt=keep,
            )
            repository.insert(connection, new, to_utc_text(self._clock()))
            return keep

        return write(self._engine, work)


def _require_enabled(connection: Connection) -> None:
    if not settings_repository.get_flag(connection, DIAGNOSTIC_MODE, False):
        raise NotFoundError(_MODE_OFF)
