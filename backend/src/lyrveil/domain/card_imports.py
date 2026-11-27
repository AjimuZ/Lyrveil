"""Rows of `card_imports` and `character_sources` as the services use them (docs/data-model.md, P10)."""

from dataclasses import dataclass
from datetime import timedelta

# An import that is not saved expires this long after it was made (ADR 0047), with its staged avatar.
IMPORT_LIFETIME = timedelta(days=7)

READY = "ready"
SAVED = "saved"


@dataclass(frozen=True)
class StoredCardImport:
    """An import waiting for review. `source_json` is the parsed card unchanged, as JSON text, and `source_sha256` the
    hash of the file's bytes. `direct_draft_json` and `report_json` are the direct mapping as JSON text
    (lyrveil.domain.card_mapping); they are None only in a row stored before P10.2b.
    `avatar_staging_path` is relative to the media root and None without an avatar."""

    id: str
    status: str
    format: str
    container: str
    source_json: str
    source_sha256: str
    direct_draft_json: str | None
    report_json: str | None
    conversion_job_id: str | None
    avatar_staging_path: str | None
    character_id: str | None
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class StoredCharacterSource:
    """The original card of an imported character, as stored when the import was saved."""

    id: str
    character_id: str
    format: str
    container: str
    source_json: str
    source_sha256: str
    imported_at: str
