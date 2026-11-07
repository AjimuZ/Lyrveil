"""SQL of the `generated_images` table, and the link from a message variant to its image.

Every function works on a connection the caller owns and never commits, so the service decides what one transaction
holds (docs/image-generation.md, Persistence).
"""

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, text


@dataclass(frozen=True)
class NewImage:
    """One row of `generated_images` to insert; the fields are the columns of docs/data-model.md.

    `scene` and `workflow` are the decoded JSON documents. `file_path` and `thumb_path` are relative to the media
    root and built from `id` only.
    """

    id: str
    job_id: str
    message_id: str | None
    conversation_id: str | None
    character_id: str
    purpose: str
    file_path: str
    thumb_path: str
    media_type: str
    width: int
    height: int
    byte_size: int
    sha256: str
    user_request: str
    scene: dict[str, Any]
    aspect: str
    outfit_key: str
    prompt_positive: str
    prompt_negative: str
    seed: int
    steps: int
    cfg: float
    sampler: str
    scheduler: str
    clip_skip: int
    checkpoint_file: str
    checkpoint_sha256: str | None
    model_version: str | None
    profile_id: str
    quality_preset: str
    hires_denoise: float | None
    hires_scale: float | None
    hires_steps: int | None
    workflow_id: str
    workflow: dict[str, Any]
    comfyui_version: str | None
    created_at: str


_JSON_COLUMNS = {"scene": "scene_json", "workflow": "workflow_json"}
_SELECT = "SELECT * FROM generated_images"
# The columns that metadata shows. `workflow_json` is the exact submitted graph and stays out of the API.
_PUBLIC = (
    "id",
    "message_id",
    "conversation_id",
    "character_id",
    "purpose",
    "media_type",
    "width",
    "height",
    "byte_size",
    "sha256",
    "user_request",
    "aspect",
    "outfit_key",
    "prompt_positive",
    "prompt_negative",
    "seed",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "clip_skip",
    "checkpoint_file",
    "model_version",
    "profile_id",
    "quality_preset",
    "hires_denoise",
    "hires_scale",
    "hires_steps",
    "workflow_id",
    "comfyui_version",
    "created_at",
)


def insert(connection: Connection, image: NewImage) -> bool:
    """Stores `image` unless an image of the same job exists, and returns whether it wrote a row.

    `generated_images.job_id` is unique, so repeating the call for a job that was already persisted writes nothing:
    the second persist of a restarted job is a no-op.
    """
    values = asdict(image)
    for key, column in _JSON_COLUMNS.items():
        values[column] = json.dumps(values.pop(key), ensure_ascii=False, separators=(",", ":"))
    columns = ", ".join(values)
    marks = ", ".join(f":{name}" for name in values)
    result = connection.execute(text(f"INSERT OR IGNORE INTO generated_images ({columns}) VALUES ({marks})"), values)
    return result.rowcount == 1


def find_by_job(connection: Connection, job_id: str) -> RowMapping | None:
    """Returns the image row the job persisted, or None."""
    return connection.execute(text(f"{_SELECT} WHERE job_id = :id"), {"id": job_id}).mappings().first()


def get(connection: Connection, image_id: str) -> RowMapping | None:
    """Returns the image row, deleted ones included, or None when no row has that id."""
    return connection.execute(text(f"{_SELECT} WHERE id = :id"), {"id": image_id}).mappings().first()


def document(row: RowMapping) -> dict[str, Any]:
    """Returns the metadata of an image row as the API shows it: the public columns and the decoded `scene`."""
    shown: dict[str, Any] = {name: row[name] for name in _PUBLIC}
    shown["scene"] = json.loads(row["scene_json"])
    return shown


def link_variant(connection: Connection, variant_id: str, image_id: str) -> None:
    """Points the message variant at its image. `message_variants.image_id` is a plain column, so the code keeps it
    valid."""
    connection.execute(
        text("UPDATE message_variants SET image_id = :image WHERE id = :id"), {"image": image_id, "id": variant_id}
    )


def image_of_message(connection: Connection, message_id: str) -> RowMapping | None:
    """Returns the image of the message's active variant, or None when the message shows no image."""
    return (
        connection.execute(
            text(
                "SELECT g.* FROM generated_images AS g JOIN message_variants AS v ON v.image_id = g.id "
                "JOIN messages AS m ON m.active_variant_id = v.id WHERE m.id = :id"
            ),
            {"id": message_id},
        )
        .mappings()
        .first()
    )


def file_paths(connection: Connection, character_id: str) -> list[str]:
    """Returns the file and thumbnail paths of every image of the character, deleted ones included, for a purge."""
    rows = connection.execute(
        text("SELECT file_path, thumb_path FROM generated_images WHERE character_id = :id ORDER BY id"),
        {"id": character_id},
    )
    return [path for row in rows for path in row]
