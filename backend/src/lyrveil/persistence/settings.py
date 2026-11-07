"""SQL of `application_settings`: the settings the backend reads (docs/configuration.md, Application settings).

Every function works on a connection the caller owns and never commits. A key with no row holds its documented default,
so a fresh database needs no seeding.
"""

import json
from typing import Any

from sqlalchemy import Connection, text

# docs/configuration.md: shows a reply's prompt assembly in the Memory Studio and stores `prompt_text`. Off by default.
DIAGNOSTIC_MODE = "app.diagnostic_mode"
# docs/configuration.md: a chat message that asks for an image replaces the text reply with an image (on by default).
IMAGES_AUTO_DETECT_REQUESTS = "images.auto_detect_requests"
# docs/configuration.md: the character may propose an image on its own, which the user must accept (off by default).
IMAGES_CONTEXTUAL_ENABLED = "images.contextual_enabled"
# Not a user setting: true while `python -m lyrveil.cli drain` holds the job queue for a shutdown (JobQueue.paused,
# lyrveil.jobs.drain). The backend sets it back to false when it starts.
JOBS_PAUSED = "jobs.paused"
# docs/configuration.md (Models): the active text profile, a catalog identifier or `custom-<12 hex>`. No row means the
# default text profile of the machine's tier.
LLM_PROFILE_ID = "llm.profile_id"
# docs/configuration.md (Models): the registered custom profiles, a JSON list (lyrveil.services.model_profiles).
MODELS_CUSTOM = "models.custom"


def get_value(connection: Connection, key: str) -> Any | None:
    """Returns the decoded JSON stored under `key`, or None when there is no row or the row is not valid JSON."""
    row = connection.execute(text("SELECT value_json FROM application_settings WHERE key = :key"), {"key": key})
    value = row.scalar()
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def get_flag(connection: Connection, key: str, default: bool) -> bool:
    """Returns the boolean stored under `key`, or `default` when there is no row or the row is not a JSON boolean."""
    stored = get_value(connection, key)
    return stored if isinstance(stored, bool) else default


def set_flag(connection: Connection, key: str, value: bool, now: str) -> None:
    """Stores `value` under `key`, replacing any earlier value. `now` is the UTC text of the change."""
    set_value(connection, key, value, now)


def set_value(connection: Connection, key: str, value: Any, now: str) -> None:
    """Stores the JSON of `value` under `key`, replacing any earlier value. `now` is the UTC text of the change.
    Raises TypeError for a value JSON cannot encode."""
    connection.execute(
        text(
            "INSERT INTO application_settings (key, value_json, updated_at) VALUES (:key, :value, :now) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at"
        ),
        {"key": key, "value": json.dumps(value), "now": now},
    )
