"""DailyBackup: writes one backup of the data volume a day at the backend's first idle moment, and keeps the newest 7.

docs/operations.md (Backup and restore). The archive is the one of `lyrveil.cli backup` (create_backup), so a daily
backup passes `restore-check` like any other. The database is copied with the SQLite backup API, which takes a single
consistent snapshot while the backend writes; a media file that changes during the copy fails the archive's own
check, and the backup is tried again at the next idle moment.
"""

import shutil
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

import anyio
from anyio import to_thread

from lyrveil.cli import (
    BACKUP_FOLDER,
    BACKUP_NAME,
    DATABASE_PATH,
    KEEP_BACKUPS,
    CliError,
    backup_names,
    create_backup,
    prune_backups,
)
from lyrveil.jobs.policy import JobStatus
from lyrveil.logs import get_logger

# How often the backend looks for an idle moment. The database must also have had no committed write since the
# previous look, so idle means at least one interval without any write and without a running job.
CHECK_INTERVAL_S = 60.0
# After a failed backup, such as a full volume, the next attempt waits this long instead of one interval.
RETRY_AFTER_FAILURE = timedelta(hours=1)
# What a backup killed mid-write leaves in backups/: `.lyrveil-<time>.tar.part` and `.lyrveil-<time>.tar.staging`.
_UNFINISHED_PREFIX = ".lyrveil-"

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def backup_day(name: str, zone: tzinfo) -> date | None:
    """Returns the day, in zone, on which the backup called name was written, or None when name is not a backup."""
    if not BACKUP_NAME.match(name):
        return None
    stamp = datetime.strptime(name[len("lyrveil-") : -len(".tar")], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return stamp.astimezone(zone).date()


class DailyBackup:
    """Backs up `data_root` once per calendar day of `zone`, at the first idle moment, into `data_root/backups`.

    A day counts as done when backups/ holds a backup written on it, also one of `scripts\\lyrveil.cmd backup`.
    Idle means: the database exists, no generation job is `running`, and `PRAGMA data_version` of a read-only
    connection is unchanged since the previous look, so no connection committed a write in between. The first look
    only records the baseline. After each backup the volume keeps the newest `keep` backups. Never raises from
    run_forever; failures are logged and retried after RETRY_AFTER_FAILURE.
    """

    def __init__(
        self,
        data_root: Path,
        zone: tzinfo,
        *,
        clock: Callable[[], datetime] = _utc_now,
        check_interval_s: float = CHECK_INTERVAL_S,
        keep: int = KEEP_BACKUPS,
    ) -> None:
        self._data_root = data_root
        self._zone = zone
        self._clock = clock
        self._check_interval_s = check_interval_s
        self._keep = keep
        self._connection: sqlite3.Connection | None = None
        self._last_version: int | None = None
        self._retry_at: datetime | None = None

    @property
    def folder(self) -> Path:
        """The folder of the volume's backups, `backups/` under the data root."""
        return self._data_root / BACKUP_FOLDER

    def due(self, now: datetime) -> bool:
        """True when no backup in backups/ was written on the day of now in the zone, and no failure asks to wait."""
        if self._retry_at is not None and now < self._retry_at:
            return False
        today = now.astimezone(self._zone).date()
        return all(backup_day(name, self._zone) != today for name in backup_names(self.folder))

    def idle(self) -> bool:
        """True when the database had no committed write since the previous call and no job is running.

        The first call, and any call after the database could not be read, returns False and records the baseline.
        """
        version = self._probe()
        previous, self._last_version = self._last_version, version
        return version is not None and previous == version

    def _probe(self) -> int | None:
        # Returns the data_version, or None when the database is missing, unreadable or has a running job. The
        # connection is kept, because data_version only compares commits seen by the same connection.
        database = self._data_root / DATABASE_PATH
        if not database.is_file():
            self.close()
            return None
        try:
            if self._connection is None:
                # mode=ro never creates a database and never writes; check_same_thread=False because each look runs
                # in a worker thread, one at a time.
                self._connection = sqlite3.connect(
                    f"{database.as_uri()}?mode=ro", uri=True, timeout=5, check_same_thread=False
                )
            version = int(self._connection.execute("PRAGMA data_version").fetchone()[0])
            running = self._connection.execute(
                "SELECT 1 FROM generation_jobs WHERE status = ? LIMIT 1", (JobStatus.RUNNING.value,)
            ).fetchone()
        except sqlite3.Error:
            logger.warning("daily_backup_probe_failed", exc_info=True)
            self.close()
            return None
        return None if running is not None else version

    def run_once(self) -> Path | None:
        """Writes today's backup when one is due and the backend is idle, prunes the volume to `keep` backups, and
        returns the new backup's path; returns None when nothing was written. Blocks; run it in a worker thread.
        Raises CliError or OSError when the backup or the pruning fails; then no attempt is due before
        RETRY_AFTER_FAILURE has passed."""
        now = self._clock()
        if not self.due(now):
            return None
        if not self.idle():
            return None
        try:
            path, manifest = create_backup(self._data_root, now)
            pruned = prune_backups(self.folder, self._keep)
        except (CliError, OSError):
            self._retry_at = now + RETRY_AFTER_FAILURE
            raise
        self._retry_at = None
        logger.info(
            "daily_backup_written",
            file_count=len(manifest.files),
            backup_bytes=path.stat().st_size,
            pruned_count=len(pruned),
        )
        return path

    def clear_unfinished(self) -> int:
        """Deletes what a backup cut by a stop left in backups/ and returns how many entries it removed. Call it once
        at startup, before any backup of this process runs; an entry that cannot be deleted is skipped."""
        if not self.folder.is_dir():
            return 0
        removed = 0
        for entry in self.folder.iterdir():
            if not entry.name.startswith(_UNFINISHED_PREFIX):
                continue
            with suppress(OSError):
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
        return removed

    def close(self) -> None:
        """Closes the probe connection; the next look opens a new one and records a new baseline."""
        if self._connection is not None:
            with suppress(sqlite3.Error):
                self._connection.close()
        self._connection = None
        self._last_version = None

    async def run_forever(self) -> None:
        """Looks for an idle moment every check interval until cancelled. A backup that is being written when the
        application stops is finished first, because the worker thread is not abandoned."""
        try:
            try:
                await to_thread.run_sync(self.clear_unfinished)
            except OSError:
                logger.warning("daily_backup_cleanup_failed", exc_info=True)
            while True:
                await anyio.sleep(self._check_interval_s)
                try:
                    await to_thread.run_sync(self.run_once)
                except Exception:
                    logger.error("daily_backup_failed", exc_info=True)
        finally:
            self.close()
