"""SQL of the `image_proposals` table (docs/data-model.md, `image_proposals`).

Every function works on a connection the caller owns and never commits, so the service decides what one transaction
holds. Times are the UTC text of lyrveil.domain.identifiers; text comparison orders them.
"""

import json
from typing import Any

from sqlalchemy import Connection, RowMapping, text

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
EXPIRED = "expired"


def insert(
    connection: Connection,
    *,
    proposal_id: str,
    conversation_id: str,
    message_id: str,
    scene_json: str,
    reason: str,
    now: str,
) -> None:
    """Stores a `pending` proposal. `scene_json` is the validated result as JSON."""
    connection.execute(
        text(
            "INSERT INTO image_proposals (id, conversation_id, message_id, scene_json, reason, status, created_at) "
            "VALUES (:id, :conversation_id, :message_id, :scene_json, :reason, 'pending', :now)"
        ),
        {
            "id": proposal_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "scene_json": scene_json,
            "reason": reason,
            "now": now,
        },
    )


def get(connection: Connection, proposal_id: str) -> RowMapping | None:
    """Returns the proposal row, or None when no row has that id."""
    return (
        connection.execute(text("SELECT * FROM image_proposals WHERE id = :id"), {"id": proposal_id}).mappings().first()
    )


def decide(connection: Connection, proposal_id: str, status: str, now: str, job_id: str | None = None) -> bool:
    """Moves a `pending` proposal to `status` (accepted, rejected or expired) and returns whether it changed one.
    A proposal already decided is left as it is, so two answers cannot both win."""
    result = connection.execute(
        text(
            "UPDATE image_proposals SET status = :status, decided_at = :now, job_id = :job_id "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"id": proposal_id, "status": status, "now": now, "job_id": job_id},
    )
    return result.rowcount == 1


def expire_before(connection: Connection, cutoff: str, now: str) -> int:
    """Expires every `pending` proposal created before `cutoff` and returns how many."""
    result = connection.execute(
        text(
            "UPDATE image_proposals SET status = 'expired', decided_at = :now "
            "WHERE status = 'pending' AND created_at < :cutoff"
        ),
        {"cutoff": cutoff, "now": now},
    )
    return result.rowcount


def has_pending(connection: Connection, conversation_id: str) -> bool:
    """Returns whether the conversation has a proposal waiting for an answer."""
    return (
        connection.execute(
            text("SELECT 1 FROM image_proposals WHERE conversation_id = :id AND status = 'pending' LIMIT 1"),
            {"id": conversation_id},
        ).first()
        is not None
    )


def job_active(connection: Connection, conversation_id: str) -> bool:
    """Returns whether an `image_proposal` job of the conversation is queued or running."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM generation_jobs WHERE kind = 'image_proposal' AND conversation_id = :id "
                "AND status IN ('queued', 'running') LIMIT 1"
            ),
            {"id": conversation_id},
        ).first()
        is not None
    )


def latest(connection: Connection, conversation_id: str) -> tuple[str, int] | None:
    """Returns the creation time and the message `seq` of the conversation's newest proposal, or None."""
    row = connection.execute(
        text(
            "SELECT p.created_at, m.seq FROM image_proposals AS p JOIN messages AS m ON m.id = p.message_id "
            "WHERE p.conversation_id = :id ORDER BY p.created_at DESC, p.id DESC LIMIT 1"
        ),
        {"id": conversation_id},
    ).first()
    return None if row is None else (row[0], row[1])


def messages_after(connection: Connection, conversation_id: str, seq: int) -> int:
    """Returns how many messages that are not deleted follow `seq` in the conversation."""
    counted = connection.execute(
        text("SELECT count(*) FROM messages WHERE conversation_id = :id AND seq > :seq AND deleted_at IS NULL"),
        {"id": conversation_id, "seq": seq},
    ).scalar_one()
    return int(counted)


def document(row: RowMapping) -> dict[str, Any]:
    """Returns the proposal as the API and the `image.proposed` event show it: `scene` is the text of the decision's
    scene."""
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "message_id": row["message_id"],
        "scene": json.loads(row["scene_json"])["scene"],
        "reason": row["reason"],
        "status": row["status"],
        "job_id": row["job_id"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }
