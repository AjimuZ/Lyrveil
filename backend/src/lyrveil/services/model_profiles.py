"""ModelProfiles: the active text profile and its launch file, the reload request, and custom profile registration.

docs/models.md (Selecting a profile, Custom profiles) and docs/security.md (Model files):

- A text profile change is checked first: the profile must exist, run on the machine's tier and have its file in the
  models volume with the expected size. A refusal (`MODEL_PROFILE_UNAVAILABLE`) changes nothing: no setting, no launch
  file, no mode. An accepted change stores `llm.profile_id` and writes the whole launch file at level A in the same
  transaction; the text runtime reads it at its next load.
- **Applica ora** queues a `maintenance` job, `model_reload`, ahead of replies (lyrveil.services.model_maintenance). It
  runs once the running job ends, so it waits for the running reply.
- A custom file is registered by name only (lyrveil.model_files). The `custom_model_hash` maintenance job hashes it
  and stores it in `models.custom` as `custom-<first 12 hex characters of its SHA-256>`.

Every method blocks on the database or the file system: call it from a worker thread.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, get_args

from sqlalchemy import Connection, Engine, text

from lyrveil.catalog import TIERS, Catalog, ImageProfile, TextProfile, TextTemplate, Tier
from lyrveil.domain.errors import ConflictError, DomainError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.model_files import (
    FOLDERS,
    ModelFileMissingError,
    ModelFileRefusedError,
    ModelFiles,
    ModelKind,
)
from lyrveil.orchestration.launch_file import LaunchValues, catalog_values, read_launch_settings, write_launch_file
from lyrveil.persistence.settings import LLM_PROFILE_ID, MODELS_CUSTOM, get_value, set_value

# ADR 0027 and scripts/lib/Models.psm1: the memory Docker sees from which the machine is on the target tier.
TARGET_TIER_BYTES = 20 * 1024**3
MEMINFO = Path("/proc/meminfo")
CUSTOM_PREFIX = "custom-"
# The payload `operation` of the two maintenance jobs this module queues.
RELOAD = "model_reload"
CUSTOM_HASH = "custom_model_hash"
# Ahead of replies (priority 10), so the work queued behind a reload runs on the new model.
RELOAD_PRIORITY = 5
# The values a custom text profile takes when the catalog has no reduced-tier profile of its family.
_CUSTOM_FALLBACK = LaunchValues(
    profile_id="custom-000000000000",
    model_file="custom.gguf",
    context_size=8192,
    batch_size=512,
    use_mmap=False,
    kv_quant="f16",
    swa_padding=0,
    smart_cache=0,
)
_TEMPLATES: tuple[str, ...] = get_args(TextTemplate)

FileState = Literal["verified", "size_mismatch", "missing"]

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def machine_tier(meminfo: Path = MEMINFO) -> Tier:
    """Returns the hardware tier of the memory the Docker VM has, read from `MemTotal` of `meminfo`: `target` from
    TARGET_TIER_BYTES, `reduced` below it and, as the scripts assume, when the file cannot be read."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                return "target" if int(line.split()[1]) * 1024 >= TARGET_TIER_BYTES else "reduced"
    except (OSError, ValueError, IndexError):
        pass
    return "reduced"


def download_command(profile_id: str) -> str:
    """Returns the command that downloads a catalog profile's file (docs/operations.md, Model downloads)."""
    return f".\\scripts\\lyrveil.cmd models -Model {profile_id}"


class ProfileUnavailableError(DomainError):
    """The profile does not exist, needs a larger hardware tier, or its file is missing or of another size (409
    `MODEL_PROFILE_UNAVAILABLE`). `detail` names the command that fixes it, when one does. Nothing was changed."""

    code = "MODEL_PROFILE_UNAVAILABLE"
    status = 409


@dataclass(frozen=True)
class CustomProfile:
    """One entry of `models.custom`. `template` is the family the user chose for a text file, None for an image.
    `bytes` is the size the file was hashed at: another size makes the profile unusable until it is registered
    again."""

    id: str
    kind: ModelKind
    file: str
    bytes: int
    sha256: str
    template: TextTemplate | None
    registered_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "template": self.template,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_json(cls, value: object) -> "CustomProfile | None":
        """Returns the entry stored as `value`, or None when it is not of the form `to_json` writes."""
        if not isinstance(value, dict):
            return None
        profile_id, kind, name = value.get("id"), value.get("kind"), value.get("file")
        size, digest, template, at = (value.get(key) for key in ("bytes", "sha256", "template", "registered_at"))
        if (
            not isinstance(profile_id, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or profile_id != CUSTOM_PREFIX + digest[:12]
            or kind not in FOLDERS
            or not isinstance(name, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not isinstance(at, str)
            or (kind == "text" and template not in _TEMPLATES)
            or (kind == "image" and template is not None)
        ):
            return None
        return cls(
            id=profile_id,
            kind=cast(ModelKind, kind),
            file=name,
            bytes=size,
            sha256=digest,
            template=cast(TextTemplate | None, template),
            registered_at=at,
        )


@dataclass(frozen=True)
class ReloadTarget:
    """What a reload loads: the profile and the model file the launch file names, present with the expected size."""

    profile_id: str
    model_file: str


class ModelProfiles:
    """The model settings of the application over `engine`, the validated `catalog`, the machine's `tier`, the models
    volume seen through `files`, and the text runtime's `launch_file` in the data volume."""

    def __init__(
        self,
        engine: Engine,
        catalog: Catalog,
        tier: Tier,
        files: ModelFiles,
        launch_file: Path,
        *,
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._catalog = catalog
        self._tier = tier
        self._files = files
        self._launch_file = launch_file
        self._queue = queue or JobQueue()
        self._clock = clock
        self._text = {profile.id: profile for profile in catalog.text}

    @property
    def tier(self) -> Tier:
        return self._tier

    # Reading.

    def customs(self, connection: Connection) -> list[CustomProfile]:
        """Returns the registered custom profiles, in registration order. An entry not of the stored form is left
        out."""
        stored = get_value(connection, MODELS_CUSTOM)
        if not isinstance(stored, list):
            return []
        found = (CustomProfile.from_json(item) for item in stored)
        return [profile for profile in found if profile is not None]

    def active_text_id(self, connection: Connection) -> str:
        """Returns the stored `llm.profile_id` when it names a catalog text profile or a registered custom text
        profile, and otherwise the default text profile of the tier."""
        stored = get_value(connection, LLM_PROFILE_ID)
        if isinstance(stored, str) and (
            stored in self._text or any(item.id == stored and item.kind == "text" for item in self.customs(connection))
        ):
            return stored
        return self._catalog.default_text(self._tier).id

    def overview(self) -> dict[str, Any]:
        """Returns what GET /settings/models answers: the tier, the active profiles, every catalog and custom profile
        with its local state, the unregistered files of the two model folders, and the launch file's values (None
        when it is missing)."""

        def work(connection: Connection) -> dict[str, Any]:
            customs = self.customs(connection)
            active = self.active_text_id(connection)
            registered = {(item.kind, item.file) for item in customs}
            catalog_files = {("text", profile.file) for profile in self._catalog.text} | {
                ("image", profile.file) for profile in self._catalog.image
            }
            local = [
                {"kind": item.kind, "file": item.name, "bytes": item.size}
                for kind in FOLDERS
                for item in self._files.listing(kind)
                if (kind, item.name) not in registered | catalog_files
            ]
            return {
                "tier": self._tier,
                "active": {"text": active, "image": self._catalog.default_image(self._tier).id},
                "text": [self._text_entry(profile) for profile in self._catalog.text]
                + [self._custom_entry(item) for item in customs if item.kind == "text"],
                "image": [self._image_entry(profile) for profile in self._catalog.image]
                + [self._custom_entry(item) for item in customs if item.kind == "image"],
                "local_files": local,
                "launch": read_launch_settings(self._launch_file),
            }

        return read(self._engine, work)

    def _state(self, kind: ModelKind, name: str, expected: int) -> FileState:
        size = self._files.size(kind, name)
        if size is None:
            return "missing"
        return "verified" if size == expected else "size_mismatch"

    def _text_entry(self, profile: TextProfile) -> dict[str, Any]:
        state = self._state("text", profile.file, profile.bytes)
        return {
            **self._catalog_entry(profile, state),
            "template": profile.template,
            "selectable": self._tier in profile.runs_on and state == "verified",
            "quality_evidence": profile.quality_evidence,
        }

    def _image_entry(self, profile: ImageProfile) -> dict[str, Any]:
        return self._catalog_entry(profile, self._state("image", profile.file, profile.bytes))

    def _catalog_entry(self, profile: TextProfile | ImageProfile, state: FileState) -> dict[str, Any]:
        return {
            "id": profile.id,
            "display_name": profile.display_name,
            "file": profile.file,
            "bytes": profile.bytes,
            "license": profile.license,
            "license_url": profile.license_url,
            "min_tier": profile.min_tier or TIERS[0],
            "custom": False,
            "state": state,
            "download_command": None if state == "verified" else download_command(profile.id),
        }

    def _custom_entry(self, item: CustomProfile) -> dict[str, Any]:
        state = self._state(item.kind, item.file, item.bytes)
        entry: dict[str, Any] = {
            "id": item.id,
            "display_name": item.file,
            "file": item.file,
            "bytes": item.bytes,
            "sha256": item.sha256,
            "license": None,
            "license_url": None,
            "min_tier": TIERS[0],
            "custom": True,
            "state": state,
            "download_command": None,
        }
        if item.kind == "text":
            entry |= {"template": item.template, "selectable": state == "verified", "quality_evidence": None}
        return entry

    # The text profile and its launch file.

    def _launch_values(self, connection: Connection, profile_id: str) -> LaunchValues:
        """Returns the level-A launch values of a catalog or custom text profile, without looking at its file.
        Raises ProfileUnavailableError for an identifier that names neither."""
        profile = self._text.get(profile_id)
        if profile is not None:
            return catalog_values(profile)
        custom = next((item for item in self.customs(connection) if item.id == profile_id), None)
        if custom is None or custom.kind != "text":
            raise ProfileUnavailableError(f"Il profilo {profile_id} non esiste.")
        base = next(
            (item for item in self._catalog.text if item.template == custom.template and TIERS[0] in item.runs_on),
            None,
        )
        if base is None:
            return replace(_CUSTOM_FALLBACK, profile_id=custom.id, model_file=custom.file)
        return catalog_values(base, profile_id=custom.id, model_file=custom.file)

    def _check_available(self, connection: Connection, profile_id: str) -> None:
        """Raises ProfileUnavailableError unless the text profile runs on the tier and its file is present with the
        expected size."""
        profile = self._text.get(profile_id)
        if profile is not None:
            if self._tier not in profile.runs_on:
                raise ProfileUnavailableError(
                    f"Il profilo {profile_id} richiede una macchina con almeno 20 GiB di memoria per Docker."
                )
            state = self._state("text", profile.file, profile.bytes)
            if state == "missing":
                raise ProfileUnavailableError(
                    f"Il file del profilo {profile_id} non c'è. Scaricalo con {download_command(profile_id)}"
                )
            if state == "size_mismatch":
                raise ProfileUnavailableError(
                    f"Il file del profilo {profile_id} ha una dimensione diversa da quella attesa. Scaricalo di nuovo "
                    f"con {download_command(profile_id)}"
                )
            return
        custom = next((item for item in self.customs(connection) if item.id == profile_id), None)
        if custom is None or custom.kind != "text":
            raise ProfileUnavailableError(f"Il profilo {profile_id} non esiste.")
        state = self._state("text", custom.file, custom.bytes)
        if state == "missing":
            raise ProfileUnavailableError(
                f"Il file {custom.file} non c'è. Importalo con .\\scripts\\lyrveil.cmd import-model <file>"
            )
        if state == "size_mismatch":
            raise ProfileUnavailableError(f"Il file {custom.file} è cambiato: registralo di nuovo.")

    def select_text(self, profile_id: str) -> None:
        """Makes `profile_id` the active text profile: stores `llm.profile_id` and writes the launch file at level A,
        in one transaction. Selecting the active profile again rewrites the file, which returns it to level A.

        Raises ProfileUnavailableError, changing nothing, when the profile does not exist, needs a larger tier, or its
        file is missing or of another size; OSError when the launch file cannot be written, and then the setting is
        not stored either.
        """

        def work(connection: Connection) -> None:
            self._check_available(connection, profile_id)
            values = self._launch_values(connection, profile_id)
            set_value(connection, LLM_PROFILE_ID, profile_id, to_utc_text(self._clock()))
            write_launch_file(self._launch_file, values)

        write(self._engine, work)
        logger.info("model_profile_selected", state=profile_id)

    def ensure_launch_file(self) -> bool:
        """Writes the launch file of the active text profile when none exists, and returns whether it wrote one. The
        file is written even when the profile's file is missing: the text runtime then reports the missing model."""
        if read_launch_settings(self._launch_file) is not None:
            return False
        values = read(self._engine, lambda connection: self._launch_values(connection, self.active_text_id(connection)))
        write_launch_file(self._launch_file, values)
        logger.info("launch_file_written", state=values.profile_id)
        return True

    def reload_target(self) -> ReloadTarget:
        """Returns what a reload loads, after checking, before any mode change, that the launch file names a known
        text profile whose file is present with the expected size. Writes the launch file first when it is missing.

        Raises ProfileUnavailableError when that check fails, and OSError when a missing launch file cannot be written.
        """
        self.ensure_launch_file()
        settings = read_launch_settings(self._launch_file) or {}
        profile_id, model_file = settings.get("MODEL_PROFILE", ""), settings.get("MODEL_FILE", "")

        def work(connection: Connection) -> int | None:
            profile = self._text.get(profile_id)
            if profile is not None:
                return profile.bytes
            custom = next((item for item in self.customs(connection) if item.id == profile_id), None)
            return None if custom is None or custom.kind != "text" else custom.bytes

        expected = read(self._engine, work)
        if expected is None:
            raise ProfileUnavailableError("Il file di avvio del modello di testo non nomina un profilo noto.")
        if self._files.size("text", model_file) != expected:
            command = (
                download_command(profile_id)
                if profile_id in self._text
                else ".\\scripts\\lyrveil.cmd import-model <file>"
            )
            raise ProfileUnavailableError(
                f"Il file {model_file} del profilo {profile_id} manca o ha una dimensione diversa da quella attesa. "
                f"Usa {command}"
            )
        return ReloadTarget(profile_id=profile_id, model_file=model_file)

    def request_reload(self) -> Job:
        """Queues the `model_reload` job of **Applica ora** and returns it, or returns the one already queued.

        Raises ProfileUnavailableError, queuing nothing, when `reload_target` refuses, and QueueFullError when the
        queue is full."""
        self.reload_target()
        return write(self._engine, lambda connection: self._enqueue_once(connection, {"operation": RELOAD}, True))

    def _enqueue_once(self, connection: Connection, payload: dict[str, Any], ahead: bool) -> Job:
        """Returns the queued maintenance job with this payload, or queues one, ahead of replies when `ahead`."""
        found = connection.execute(
            text(
                "SELECT id FROM generation_jobs WHERE kind = :kind AND status = 'queued' AND payload_json = :payload "
                "ORDER BY created_at LIMIT 1"
            ),
            {
                "kind": JobKind.MAINTENANCE.value,
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ).scalar()
        if found is not None:
            existing = self._queue.get(connection, found)
            if existing is not None:
                return existing
        return self._queue.enqueue(
            connection, JobKind.MAINTENANCE, payload, priority=RELOAD_PRIORITY if ahead else None
        )

    # Custom profiles.

    def register_custom(self, kind: ModelKind, name: str, template: TextTemplate | None) -> Job:
        """Queues the hash job that registers the file `name` of the folder of `kind`, and returns it, or the same job
        already queued.

        Raises ValidationFailedError for a text file without a template family or an image with one, and for a name,
        an extension, a link or a non-regular file lyrveil.model_files refuses; NotFoundError when the file is not in
        the folder; ConflictError for a catalog file, or one already registered at its current size or registered and
        active. Nothing is queued then.
        """
        if (kind == "text") != (template is not None):
            raise ValidationFailedError(
                "Per un file .gguf scegli la famiglia del modello; per un checkpoint non serve."
            )
        try:
            size = self._files.resolve(kind, name).stat().st_size
        except ModelFileRefusedError as error:
            raise ValidationFailedError(str(error)) from None
        except ModelFileMissingError as error:
            raise NotFoundError(str(error)) from None
        catalog = self._catalog.text if kind == "text" else self._catalog.image
        if any(profile.file == name for profile in catalog):
            raise ConflictError(f"{name} è il file di un profilo del catalogo.")

        def work(connection: Connection) -> Job:
            active = self.active_text_id(connection)
            for item in self.customs(connection):
                if item.kind == kind and item.file == name:
                    if item.id == active:
                        raise ConflictError(f"{name} è il file del profilo attivo: scegli prima un altro profilo.")
                    if item.bytes == size:
                        raise ConflictError(f"{name} è già registrato come {item.id}.")
            payload = {"operation": CUSTOM_HASH, "kind": kind, "file": name, "template": template}
            return self._enqueue_once(connection, payload, False)

        job = write(self._engine, work)
        logger.info("custom_model_queued", job_id=job.id)
        return job

    def store_hashed(self, kind: ModelKind, name: str, template: TextTemplate | None) -> CustomProfile:
        """Hashes the file `name` of `kind` and stores it in `models.custom`, then returns the stored profile.

        An entry of the same content (the same identifier) is kept as it is and returned. An older entry of the same
        file, registered at another size, is replaced unless it is the active profile. Raises ModelFileRefusedError and
        ModelFileMissingError as ModelFiles.sha256 does; then nothing is stored. Idempotent: running it again stores
        nothing new."""
        digest, size = self._files.sha256(kind, name)
        profile = CustomProfile(
            id=CUSTOM_PREFIX + digest[:12],
            kind=kind,
            file=name,
            bytes=size,
            sha256=digest,
            template=template,
            registered_at=to_utc_text(self._clock()),
        )

        def work(connection: Connection) -> CustomProfile:
            customs = self.customs(connection)
            same = next((item for item in customs if item.id == profile.id), None)
            if same is not None:
                return same
            active = self.active_text_id(connection)
            kept = [item for item in customs if item.id == active or not (item.kind == kind and item.file == name)]
            set_value(connection, MODELS_CUSTOM, _stored([*kept, profile]), profile.registered_at)
            return profile

        stored = write(self._engine, work)
        logger.info("custom_model_registered", state=stored.id)
        return stored

    def unregister_custom(self, profile_id: str) -> None:
        """Removes the registration `profile_id`, never its file. Raises NotFoundError for an unknown identifier and
        ConflictError while it is the active text profile."""

        def work(connection: Connection) -> None:
            customs = self.customs(connection)
            if not any(item.id == profile_id for item in customs):
                raise NotFoundError("Profilo non trovato.")
            if self.active_text_id(connection) == profile_id:
                raise ConflictError("Il profilo è in uso: scegli prima un altro profilo.")
            rest = [item for item in customs if item.id != profile_id]
            set_value(connection, MODELS_CUSTOM, _stored(rest), to_utc_text(self._clock()))

        write(self._engine, work)
        logger.info("custom_model_removed", state=profile_id)


def _stored(profiles: Sequence[CustomProfile]) -> list[dict[str, Any]]:
    return [profile.to_json() for profile in profiles]
