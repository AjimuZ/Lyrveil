"""Maintenance commands of the backend image: backups of the data volume, and the drain before a shutdown.

The Windows scripts run the backup commands in one-off containers with no network (docs/operations.md, Backup and
restore):

    backup                  writes a consistent backup of the data volume to /data/backups, keeping the newest 7
    export-backups FOLDER   copies every backup of /data/backups that FOLDER lacks, by name
    restore-check FILE      verifies a backup's manifest, sizes, SHA-256 hashes and schema revision; changes nothing
    restore FILE            checks FILE, then replaces db, media and runtime of /data with its content

`stop.cmd` runs the drain commands inside the running backend container (docs/gpu-orchestration.md, Safe shutdown):

    drain [--timeout S]     pauses the job queue and waits up to S seconds (60) for the running job to end
    drain-gpu [--timeout S] asks the orchestrator to drain, and waits up to S seconds (90) for IDLE
    resume                  resumes the queue and the orchestrator after a drain that no stop followed

A backup is `lyrveil-<UTC time>.tar`: `manifest.json` first, then `db/lyrveil.sqlite3`, copied with the SQLite backup
API, and every file of `media/` (except `media/.staging`) and `runtime/`. The manifest records the format, the
creation time, the app version, the database's Alembic revision and the size and SHA-256 of each file. The backend's
daily backup (lyrveil.services.daily_backup) writes the same archives with create_backup and prune_backups.

Exit status 0 on success and 1 on any failure or refusal, with a FAILURE line on standard error. `restore` requires
the backend to be stopped, because it moves the database files that a running backend keeps open. A drain that runs
out of time also exits 1: `stop.cmd` reports it as a warning and stops the services all the same.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, NoReturn

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from lyrveil import __version__
from lyrveil.jobs.drain import drain_queue, resume_queue
from lyrveil.jobs.queue import JobQueue
from lyrveil.orchestration.client import OrchestratorClient, OrchestratorUnavailableError
from lyrveil.orchestration.drain import drain_gpu
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import known_revisions
from lyrveil.settings import SettingsError, load_settings

EXIT_OK = 0
EXIT_FAILURE = 1

DATA_ROOT = Path("/data")
BACKUP_FOLDER = "backups"
DATABASE_PATH = "db/lyrveil.sqlite3"
# The top-level folders of the data volume that a backup holds and a restore replaces (docs/operations.md, Data
# layout). backups/ stays, so a restore never removes the backups kept in the volume.
CONTENT_FOLDERS = ("db", "media", "runtime")
COPIED_FOLDERS = ("media", "runtime")
# Unfinished files of the media store, which the backend deletes at startup (docs/backend.md, Startup sequence).
SKIPPED_FOLDER = "media/.staging"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = "lyrveil-backup"
MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 64 << 20
BACKUP_NAME = re.compile(r"^lyrveil-\d{8}T\d{6}Z\.tar$")
# docs/operations.md (Data layout): the data volume keeps the newest 7 backups; the Windows folder keeps every copy.
KEEP_BACKUPS = 7
# docs/gpu-orchestration.md (Safe shutdown): a running reply gets 60 s to finish. The GPU drain covers the stop grace
# of the image runtime (30 s) and the release verification (60 s at most).
QUEUE_DRAIN_TIMEOUT_S = 60
GPU_DRAIN_TIMEOUT_S = 90
CHUNK_BYTES = 1 << 20
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CliError(Exception):
    """A refusal or failure, printed as one FAILURE line. Messages name files of the backup, never their content."""


@dataclass(frozen=True)
class ManifestFile:
    """One file of a backup: its path inside the archive and the data volume, its size and its SHA-256."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """The content of `manifest.json`. `schema_revision` is the Alembic revision of the database in the backup."""

    created_at: str
    app_version: str
    schema_revision: str
    files: tuple[ManifestFile, ...]

    def to_json(self) -> bytes:
        """Returns the manifest as UTF-8 JSON, the form `parse_manifest` reads back."""
        document = {
            "format": MANIFEST_FORMAT,
            "format_version": MANIFEST_VERSION,
            "created_at": self.created_at,
            "app_version": self.app_version,
            "schema_revision": self.schema_revision,
            "files": [{"path": item.path, "bytes": item.size, "sha256": item.sha256} for item in self.files],
        }
        return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _out(message: str) -> None:
    print(message, flush=True)


def utc_stamp(moment: datetime) -> str:
    """Returns moment in UTC as `YYYYMMDDTHHMMSSZ`, the time in backup and pre-restore names."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def valid_member_path(path: str) -> bool:
    """True when path may name a file of a backup: relative, with `/` separators, no empty, `.` or `..` segment and
    no control character, under db, media or runtime, and exactly `db/lyrveil.sqlite3` under db."""
    parts = path.split("/")
    return (
        len(parts) >= 2
        and "\\" not in path
        and all(part not in ("", ".", "..") and part.isprintable() for part in parts)
        and parts[0] in CONTENT_FOLDERS
        and (parts[0] != "db" or path == DATABASE_PATH)
    )


def _hash_stream(reader: IO[bytes], writer: IO[bytes] | None = None) -> tuple[int, str]:
    # Returns the number of bytes read and their SHA-256, copying them to writer when one is given.
    digest = hashlib.sha256()
    size = 0
    while chunk := reader.read(CHUNK_BYTES):
        digest.update(chunk)
        size += len(chunk)
        if writer is not None:
            writer.write(chunk)
    return size, digest.hexdigest()


def _hash_file(path: Path) -> tuple[int, str]:
    with path.open("rb") as reader:
        return _hash_stream(reader)


def _text(document: dict[str, Any], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise CliError(f"The manifest has no valid {name}.")
    return value


def parse_manifest(data: bytes) -> Manifest:
    """Returns the manifest in data. Raises CliError when it is not the JSON of format `lyrveil-backup` version 1,
    lacks a field, lists an invalid or repeated path, a negative size or a malformed hash, or omits the database."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise CliError(f"The manifest is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise CliError("The manifest is not a JSON object.")
    if document.get("format") != MANIFEST_FORMAT or document.get("format_version") != MANIFEST_VERSION:
        raise CliError(f"The manifest is not of format {MANIFEST_FORMAT} version {MANIFEST_VERSION}.")
    entries = document.get("files")
    if not isinstance(entries, list):
        raise CliError("The manifest has no list of files.")
    files: list[ManifestFile] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CliError("The manifest lists a file that is not an object.")
        path, size, digest = entry.get("path"), entry.get("bytes"), entry.get("sha256")
        if not isinstance(path, str) or not valid_member_path(path) or path in seen:
            raise CliError("The manifest lists an invalid or repeated path.")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise CliError(f"The manifest gives {path} an invalid size.")
        if not isinstance(digest, str) or not _SHA256.match(digest):
            raise CliError(f"The manifest gives {path} an invalid SHA-256.")
        seen.add(path)
        files.append(ManifestFile(path=path, size=size, sha256=digest))
    if DATABASE_PATH not in seen:
        raise CliError(f"The manifest does not list {DATABASE_PATH}.")
    return Manifest(
        created_at=_text(document, "created_at"),
        app_version=_text(document, "app_version"),
        schema_revision=_text(document, "schema_revision"),
        files=tuple(files),
    )


def _open_archive(path: Path) -> tarfile.TarFile:
    if not path.is_file():
        raise CliError(f"{path.name} is not a file.")
    try:
        # "r:" accepts an uncompressed tar only, the one format backups are written in.
        return tarfile.open(path, "r:")
    except (tarfile.TarError, OSError) as error:
        raise CliError(f"{path.name} is not a readable tar file: {error}") from error


def _member_reader(archive: tarfile.TarFile, member: tarfile.TarInfo) -> IO[bytes]:
    reader = archive.extractfile(member)
    if reader is None:
        raise CliError(f"{member.name} is not a regular file.")
    return reader


def check_backup(path: Path, known: frozenset[str] | None = None) -> Manifest:
    """Returns the manifest of the backup at path when the backup can be restored, and changes nothing.

    Requires `manifest.json`, regular files only, exactly the files the manifest lists with its sizes and SHA-256
    hashes, and a schema revision among `known` (by default every revision of the installed migrations), since a
    database at another revision comes from a newer app. Raises CliError naming the first problem found.
    """
    known_set = known_revisions() if known is None else known
    with _open_archive(path) as archive:
        try:
            members = archive.getmembers()
        except (tarfile.TarError, OSError) as error:
            raise CliError(f"{path.name} is damaged: {error}") from error
        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            if member.name in by_name:
                raise CliError(f"{path.name} holds {member.name} twice.")
            by_name[member.name] = member
        manifest_member = by_name.pop(MANIFEST_NAME, None)
        if manifest_member is None or not manifest_member.isreg() or manifest_member.size > MAX_MANIFEST_BYTES:
            raise CliError(f"{path.name} has no valid {MANIFEST_NAME}.")
        try:
            manifest = parse_manifest(_member_reader(archive, manifest_member).read())
            listed = {item.path for item in manifest.files}
            extra = set(by_name) - listed
            if extra:
                raise CliError(f"{path.name} holds {len(extra)} entries that the manifest does not list.")
            for item in manifest.files:
                stored = by_name.get(item.path)
                if stored is None or not stored.isreg():
                    raise CliError(f"{path.name} lacks {item.path}, which the manifest lists.")
                if stored.size != item.size:
                    raise CliError(f"{item.path}: {stored.size} bytes in the archive, {item.size} in the manifest.")
                if _hash_stream(_member_reader(archive, stored))[1] != item.sha256:
                    raise CliError(f"{item.path}: its SHA-256 differs from the manifest.")
        except (tarfile.TarError, OSError) as error:
            raise CliError(f"{path.name} is damaged: {error}") from error
    if manifest.schema_revision not in known_set:
        raise CliError(
            f"The database schema of {path.name} is at revision {manifest.schema_revision}, which the installed app"
            " does not know, so the backup comes from a newer version. Update Lyrveil (git pull, then"
            " setup.cmd -Rebuild), then restore again."
        )
    return manifest


def _database_revision(connection: sqlite3.Connection) -> str | None:
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.DatabaseError:
        return None
    return str(rows[0][0]) if len(rows) == 1 else None


def snapshot_database(source: Path, target: Path) -> str:
    """Copies the database at source to target with the SQLite backup API and returns its Alembic revision.

    The copy is consistent while the backend writes, because the backup API copies a single snapshot, and it is a
    single file in rollback-journal mode. Raises CliError when source is not a database with exactly one revision.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # mode=rw never creates an empty database where the real one is missing.
        with (
            closing(sqlite3.connect(f"{source.as_uri()}?mode=rw", uri=True, timeout=30)) as reader,
            closing(sqlite3.connect(target)) as writer,
        ):
            reader.backup(writer)
            writer.execute("PRAGMA journal_mode = DELETE")
            revision = _database_revision(writer)
    except sqlite3.Error as error:
        raise CliError(f"The database could not be copied: {error}") from error
    if revision is None:
        raise CliError("The database has no schema revision. Start Lyrveil once, so it migrates, then back up again.")
    return revision


def _content_files(data_root: Path) -> list[str]:
    # The regular files of COPIED_FOLDERS as paths relative to data_root, sorted, without symbolic links or staging.
    found: list[str] = []
    for folder in COPIED_FOLDERS:
        for directory, subfolders, names in os.walk(data_root / folder, followlinks=False):
            current = Path(directory)
            subfolders[:] = sorted(
                name for name in subfolders if (current / name).relative_to(data_root).as_posix() != SKIPPED_FOLDER
            )
            for name in sorted(names):
                path = current / name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(data_root).as_posix()
                if not valid_member_path(relative):
                    raise CliError(f"A file under {folder}/ has a name that a restore cannot accept.")
                found.append(relative)
    return found


def _add_file(archive: tarfile.TarFile, name: str, source: Path) -> None:
    info = archive.gettarinfo(str(source), arcname=name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    with source.open("rb") as reader:
        archive.addfile(info, reader)


def create_backup(data_root: Path, now: datetime) -> tuple[Path, Manifest]:
    """Writes `backups/lyrveil-<UTC time of now>.tar` under data_root and returns its path and manifest.

    The archive is written under a temporary name and checked with check_backup before it takes its final name, so a
    backup that exists can be restored. Raises CliError when the database does not exist yet, when a backup of the
    same second exists, or when the copy or its check fails; nothing is left behind then.
    """
    database = data_root / DATABASE_PATH
    if not database.is_file():
        raise CliError(f"{DATABASE_PATH} does not exist yet. Start Lyrveil once, then back up.")
    folder = data_root / BACKUP_FOLDER
    folder.mkdir(exist_ok=True)
    name = f"lyrveil-{utc_stamp(now)}.tar"
    final = folder / name
    if final.exists():
        raise CliError(f"{BACKUP_FOLDER}/{name} exists already. Run the backup again in a second.")
    staging = folder / f".{name}.staging"
    part = folder / f".{name}.part"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        revision = snapshot_database(database, staging / DATABASE_PATH)
        sources = {DATABASE_PATH: staging / DATABASE_PATH}
        sources.update({relative: data_root / relative for relative in _content_files(data_root)})
        files: list[ManifestFile] = []
        for relative, source in sources.items():
            size, digest = _hash_file(source)
            files.append(ManifestFile(path=relative, size=size, sha256=digest))
        manifest = Manifest(
            created_at=now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            app_version=__version__,
            schema_revision=revision,
            files=tuple(files),
        )
        _write_archive(part, manifest, sources)
        # A file that changed between its hash and its copy makes this check fail, so no inconsistent backup remains.
        check_backup(part)
        os.replace(part, final)
    except OSError as error:
        raise CliError(f"The backup could not be written: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        part.unlink(missing_ok=True)
    return final, manifest


def _write_archive(part: Path, manifest: Manifest, sources: dict[str, Path]) -> None:
    data = manifest.to_json()
    with tarfile.open(part, "x", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = int(datetime.now(UTC).timestamp())
        archive.addfile(info, io.BytesIO(data))
        for item in manifest.files:
            _add_file(archive, item.path, sources[item.path])


def backup_names(folder: Path) -> list[str]:
    """Returns the names of the backups in folder, `lyrveil-<UTC time>.tar` files only, oldest first."""
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if BACKUP_NAME.match(path.name) and path.is_file())


def prune_backups(folder: Path, keep: int = KEEP_BACKUPS) -> list[str]:
    """Deletes the oldest backups of folder until at most `keep` remain, and returns the deleted names, oldest first.

    Only `lyrveil-<UTC time>.tar` files count and are deleted; the names sort by time, so the newest stay. Requires
    keep >= 1. Raises CliError when a file cannot be deleted; the deletions done before stay.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    names = backup_names(folder)
    surplus = names[: max(0, len(names) - keep)]
    for name in surplus:
        try:
            (folder / name).unlink()
        except OSError as error:
            raise CliError(f"{BACKUP_FOLDER}/{name} could not be deleted: {error}") from error
    return surplus


def export_backups(source: Path, target: Path) -> tuple[list[str], int]:
    """Copies every backup of source whose name target lacks, and returns the copied names and how many were present.

    Each copy is written as `<name>.part` and renamed once complete with the right size, so target only ever holds
    whole backups, and nothing in target is overwritten or deleted. Raises CliError when target is not a folder or a
    copy fails; the copies finished before the failure stay.
    """
    if not target.is_dir():
        raise CliError(f"The backup folder {target} is not a folder.")
    copied: list[str] = []
    present = 0
    for name in backup_names(source):
        if (target / name).exists():
            present += 1
            continue
        part = target / f"{name}.part"
        try:
            shutil.copyfile(source / name, part)
            if part.stat().st_size != (source / name).stat().st_size:
                raise CliError(f"{name} was not copied completely.")
            os.replace(part, target / name)
        except OSError as error:
            raise CliError(f"{name} could not be copied to the backup folder: {error}") from error
        finally:
            part.unlink(missing_ok=True)
        copied.append(name)
    return copied, present


def _extract(path: Path, manifest: Manifest, staging: Path) -> None:
    # Writes each file of the manifest under staging, checking its size and SHA-256 again while it is written, since
    # the file may have changed since check_backup read it.
    with _open_archive(path) as archive:
        for item in manifest.files:
            try:
                member = archive.getmember(item.path)
            except KeyError:
                raise CliError(f"{path.name} lacks {item.path}, which the manifest lists.") from None
            target = staging / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as writer:
                size, digest = _hash_stream(_member_reader(archive, member), writer)
            if size != item.size or digest != item.sha256:
                raise CliError(f"{item.path}: the extracted file differs from the manifest.")


def _swap(data_root: Path, staging: Path, previous: Path) -> None:
    # Moves the current content folders into previous, then the extracted ones into place. On a failure, every move
    # done so far is undone in reverse order, so the data volume holds the old content again.
    done: list[tuple[Path, Path]] = []
    try:
        for folder in CONTENT_FOLDERS:
            current = data_root / folder
            if current.exists() or current.is_symlink():
                os.rename(current, previous / folder)
                done.append((current, previous / folder))
        for folder in CONTENT_FOLDERS:
            if (staging / folder).exists():
                os.rename(staging / folder, data_root / folder)
                done.append((staging / folder, data_root / folder))
    except OSError as error:
        for origin, destination in reversed(done):
            os.rename(destination, origin)
        raise CliError(f"The data could not be replaced, so the previous data was put back: {error}") from error


def restore_backup(path: Path, data_root: Path, now: datetime) -> tuple[Path, Manifest]:
    """Replaces db, media and runtime under data_root with the content of the backup at path, and returns the folder
    `pre-restore-<UTC time>` that now holds the previous ones, and the manifest.

    Requires that the backend is stopped. The backup is checked first, then extracted to a staging folder in the data
    volume with its hashes checked again, and only then swapped in with renames, so a failure at any step leaves the
    previous data in place. Nothing is deleted: backups/ and other folders stay as they are. Raises CliError.
    """
    manifest = check_backup(path)
    stamp = utc_stamp(now)
    staging = data_root / f".restore-{stamp}"
    previous = data_root / f"pre-restore-{stamp}"
    if staging.exists() or previous.exists():
        raise CliError(f"{previous.name} exists already. Run the restore again in a second.")
    needed = sum(item.size for item in manifest.files)
    free = shutil.disk_usage(data_root).free
    if needed > free:
        raise CliError(f"The backup needs {needed} bytes, and the data volume has {free} bytes free.")
    try:
        staging.mkdir()
        _extract(path, manifest, staging)
        previous.mkdir()
        _swap(data_root, staging, previous)
    except (CliError, OSError) as error:
        if previous.is_dir() and not any(previous.iterdir()):
            previous.rmdir()
        if isinstance(error, CliError):
            raise
        raise CliError(f"The backup could not be extracted: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return previous, manifest


def _size_text(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def command_backup(_args: argparse.Namespace, data_root: Path, now: datetime) -> int:
    path, manifest = create_backup(data_root, now)
    total = sum(item.size for item in manifest.files)
    _out(
        f"Backup written: {BACKUP_FOLDER}/{path.name}, {len(manifest.files)} files, {_size_text(total)}, schema"
        f" revision {manifest.schema_revision}"
    )
    for name in prune_backups(data_root / BACKUP_FOLDER):
        _out(f"Removed {BACKUP_FOLDER}/{name}: the data volume keeps the newest {KEEP_BACKUPS} backups")
    return EXIT_OK


def command_export(args: argparse.Namespace, data_root: Path, _now: datetime) -> int:
    copied, present = export_backups(data_root / BACKUP_FOLDER, Path(args.folder))
    for name in copied:
        _out(f"Copied {name}")
    _out(f"Backups copied to the backup folder: {len(copied)}; already there: {present}")
    return EXIT_OK


def command_restore_check(args: argparse.Namespace, _data_root: Path, _now: datetime) -> int:
    path = Path(args.file)
    manifest = check_backup(path)
    _out(
        f"PASS     {path.name}: {len(manifest.files)} files match the manifest; created {manifest.created_at} by"
        f" version {manifest.app_version}, schema revision {manifest.schema_revision}"
    )
    return EXIT_OK


def command_restore(args: argparse.Namespace, data_root: Path, now: datetime) -> int:
    path = Path(args.file)
    previous, manifest = restore_backup(path, data_root, now)
    _out(f"Restored {len(manifest.files)} files from {path.name}. The previous data is in {previous.name}/.")
    return EXIT_OK


def _engine(data_root: Path) -> Engine:
    """Returns the engine of the data volume's database. Raises CliError when the database does not exist, so a drain
    never creates an empty one."""
    database = data_root / DATABASE_PATH
    if not database.is_file():
        raise CliError(f"{DATABASE_PATH} does not exist, so no job can run.")
    return create_database_engine(database)


def _orchestrator() -> OrchestratorClient:
    """Returns the control API client with the token of the backend's environment. Raises CliError, naming the
    variable and never its value, when the settings are invalid."""
    try:
        return OrchestratorClient(load_settings().control_token)
    except SettingsError as error:
        raise CliError(str(error)) from error


def _timeout(value: str) -> int:
    seconds = int(value)
    if seconds < 0:
        raise ValueError("a timeout cannot be negative")
    return seconds


def command_drain(args: argparse.Namespace, data_root: Path, _now: datetime) -> int:
    engine = _engine(data_root)
    try:
        result = drain_queue(engine, JobQueue(), args.timeout)
    except SQLAlchemyError as error:
        raise CliError(f"The job queue could not be paused: {type(error).__name__}") from error
    finally:
        engine.dispose()
    if not result.drained:
        raise CliError(
            f"Queue drain: {result.running} job(s) still running after {args.timeout} s. The queue stays paused, and"
            " the next start recovers them."
        )
    _out(f"PASS     Queue drain: the job queue is paused and no job is running (waited {result.waited_s:.0f} s)")
    return EXIT_OK


def command_drain_gpu(args: argparse.Namespace, _data_root: Path, _now: datetime) -> int:
    try:
        status = drain_gpu(_orchestrator(), args.timeout)
    except OrchestratorUnavailableError as error:
        raise CliError("Runtime release: the orchestrator did not answer, so it holds no runtime now.") from error
    if status.state == "ERROR":
        raise CliError("Runtime release: the orchestrator is in ERROR; docker compose stop stops the runtimes.")
    if status.state != "IDLE":
        raise CliError(f"Runtime release: the GPU is still {status.state} after {args.timeout} s.")
    _out("PASS     Runtime release: the runtime is stopped and the release of the GPU memory is verified")
    return EXIT_OK


def command_resume(_args: argparse.Namespace, data_root: Path, _now: datetime) -> int:
    engine = _engine(data_root)
    try:
        resume_queue(engine, JobQueue())
    except SQLAlchemyError as error:
        raise CliError(f"The job queue could not be resumed: {type(error).__name__}") from error
    finally:
        engine.dispose()
    _out("PASS     Queue: the job queue is resumed")
    try:
        _orchestrator().resume()
    except (CliError, OrchestratorUnavailableError):
        # The drain lives in the orchestrator's memory, so its next start leaves it in any case.
        _out("WARNING  GPU: the orchestrator did not answer; it leaves the drain when it starts again")
        return EXIT_OK
    _out("PASS     GPU: the orchestrator left the drain")
    return EXIT_OK


class _Parser(argparse.ArgumentParser):
    # argparse exits with 2 on a usage error, which docs/operations.md reserves for a failed preflight.
    def error(self, message: str) -> NoReturn:
        raise CliError(f"{self.prog}: {message}")


Handler = Callable[[argparse.Namespace, Path, datetime], int]


def _parser() -> _Parser:
    parser = _Parser(prog="lyrveil.cli", description="Maintenance commands of the Lyrveil backend.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup").set_defaults(handler=command_backup)
    export = commands.add_parser("export-backups")
    export.add_argument("folder")
    export.set_defaults(handler=command_export)
    for name, handler in (("restore-check", command_restore_check), ("restore", command_restore)):
        command = commands.add_parser(name)
        command.add_argument("file")
        command.set_defaults(handler=handler)
    drains: tuple[tuple[str, Handler, int], ...] = (
        ("drain", command_drain, QUEUE_DRAIN_TIMEOUT_S),
        ("drain-gpu", command_drain_gpu, GPU_DRAIN_TIMEOUT_S),
    )
    for name, drain_handler, default in drains:
        command = commands.add_parser(name)
        command.add_argument("--timeout", type=_timeout, default=default, metavar="SECONDS")
        command.set_defaults(handler=drain_handler)
    commands.add_parser("resume").set_defaults(handler=command_resume)
    return parser


def run(argv: Sequence[str], data_root: Path = DATA_ROOT, clock: Callable[[], datetime] | None = None) -> int:
    """Runs the command in argv on data_root and returns its exit status. A CliError is printed as a FAILURE line."""
    try:
        args = _parser().parse_args(list(argv))
        handler: Handler = args.handler
        return handler(args, data_root, (clock or _utc_now)())
    except CliError as error:
        print(f"FAILURE  {error}", file=sys.stderr, flush=True)
        return EXIT_FAILURE


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    """Runs the command of the process arguments on /data."""
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
