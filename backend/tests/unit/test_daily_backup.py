"""Tests DailyBackup on temporary data folders: one backup per local day, only while idle, pruning to the newest 7,
and a consistent copy of a database that another connection writes during the backup."""

import sqlite3
import tarfile
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lyrveil.cli import DATABASE_PATH, check_backup, create_backup, prune_backups
from lyrveil.persistence.migrations import head_revision
from lyrveil.services.daily_backup import RETRY_AFTER_FAILURE, DailyBackup, backup_day

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


def make_database(path: Path) -> None:
    # The two tables DailyBackup and create_backup read: the Alembic revision and the job states.
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (head_revision(),))
        connection.execute("CREATE TABLE generation_jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        connection.execute("CREATE TABLE pairs (value INTEGER NOT NULL)")
        connection.commit()


def execute(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(statement, parameters)
        connection.commit()


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    make_database(root / DATABASE_PATH)
    (root / "media").mkdir()
    (root / "media" / "a.webp").write_bytes(b"image a")
    return root


def put_backup(root: Path, stamp: str) -> None:
    folder = root / "backups"
    folder.mkdir(exist_ok=True)
    (folder / f"lyrveil-{stamp}.tar").write_bytes(b"an older backup")


def test_the_day_of_a_backup_is_taken_in_the_local_zone() -> None:
    # 23:30 UTC on 6 November is 00:30 on 7 November in Rome.
    assert backup_day("lyrveil-20261106T233000Z.tar", ROME) == datetime(2026, 11, 7).date()
    assert backup_day("lyrveil-20261106T223000Z.tar", ROME) == datetime(2026, 11, 6).date()
    assert backup_day(".lyrveil-20261106T233000Z.tar.part", ROME) is None


def test_a_backup_is_due_once_per_local_day(data_root: Path) -> None:
    daily = DailyBackup(data_root, ROME)
    assert daily.due(NOW)
    put_backup(data_root, "20261106T223000Z")
    assert daily.due(NOW), "a backup of yesterday leaves today's due"
    put_backup(data_root, "20261106T233000Z")
    assert not daily.due(NOW), "a backup written after midnight in Rome counts for today"


def test_idle_needs_a_quiet_interval_and_no_running_job(data_root: Path) -> None:
    database = data_root / DATABASE_PATH
    daily = DailyBackup(data_root, ROME)
    try:
        assert not daily.idle(), "the first look only records the baseline"
        assert daily.idle()
        execute(database, "INSERT INTO pairs VALUES (1)")
        assert not daily.idle(), "a commit since the previous look is activity"
        assert daily.idle()
        execute(database, "INSERT INTO generation_jobs VALUES ('j1', 'running')")
        assert not daily.idle()
        assert not daily.idle(), "a running job keeps the backend busy"
        execute(database, "UPDATE generation_jobs SET status = 'completed'")
        assert not daily.idle()
        assert daily.idle()
    finally:
        daily.close()


def test_idle_is_false_without_a_database(tmp_path: Path) -> None:
    daily = DailyBackup(tmp_path / "empty", ROME)
    assert not daily.idle()
    assert not daily.idle()
    assert not (tmp_path / "empty" / DATABASE_PATH).exists()


def test_run_once_writes_one_backup_a_day_while_idle_and_keeps_the_newest_7(data_root: Path) -> None:
    for day in range(1, 9):
        put_backup(data_root, f"202610{day:02d}T120000Z")
    daily = DailyBackup(data_root, ROME, clock=lambda: NOW)
    try:
        assert daily.run_once() is None, "not idle yet: no baseline"
        path = daily.run_once()
        assert path is not None
        assert path == data_root / "backups" / "lyrveil-20261107T120000Z.tar"
        check_backup(path)
        names = sorted(item.name for item in (data_root / "backups").iterdir())
        assert len(names) == 7
        assert names[0] == "lyrveil-20261003T120000Z.tar"
        assert names[-1] == path.name
        assert daily.run_once() is None, "today is done"
    finally:
        daily.close()


def test_a_failed_backup_waits_before_the_next_attempt(data_root: Path) -> None:
    moments = iter([NOW, NOW, NOW + RETRY_AFTER_FAILURE / 2, NOW + RETRY_AFTER_FAILURE])
    daily = DailyBackup(data_root, ROME, clock=lambda: next(moments))
    (data_root / "backups").write_bytes(b"a file where the folder belongs")
    try:
        assert daily.run_once() is None
        with pytest.raises(FileExistsError):
            daily.run_once()
        assert not daily.due(NOW + RETRY_AFTER_FAILURE / 2)
        (data_root / "backups").unlink()
        assert daily.run_once() is None, "still waiting after the failure"
        assert daily.run_once() is not None
    finally:
        daily.close()


def test_a_backup_taken_during_writes_is_consistent(data_root: Path, tmp_path: Path) -> None:
    # Every transaction of the writer adds two rows, so any consistent snapshot holds an even number of them.
    database = data_root / DATABASE_PATH
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany("INSERT INTO pairs VALUES (?)", [(n,) for n in range(20000)])
        connection.commit()
    stop = threading.Event()

    def write_pairs() -> None:
        with closing(sqlite3.connect(database, timeout=30)) as writer:
            while not stop.is_set():
                writer.execute("INSERT INTO pairs VALUES (1)")
                writer.execute("INSERT INTO pairs VALUES (2)")
                writer.commit()

    thread = threading.Thread(target=write_pairs)
    thread.start()
    try:
        # The archive DailyBackup writes, taken here while the writer commits, which the idle check would wait out.
        path, _manifest = create_backup(data_root, NOW)
    finally:
        stop.set()
        thread.join()
    check_backup(path)
    with tarfile.open(path) as archive:
        archive.extract(DATABASE_PATH, tmp_path / "out", filter="data")
    with closing(sqlite3.connect(tmp_path / "out" / DATABASE_PATH)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        count = copy.execute("SELECT count(*) FROM pairs").fetchone()[0]
    assert count >= 20000
    assert count % 2 == 0


def test_clear_unfinished_removes_only_what_a_cut_backup_left(data_root: Path) -> None:
    put_backup(data_root, "20261106T120000Z")
    folder = data_root / "backups"
    (folder / ".lyrveil-20261107T120000Z.tar.part").write_bytes(b"partial")
    (folder / ".lyrveil-20261107T120000Z.tar.staging" / "db").mkdir(parents=True)
    (folder / ".lyrveil-20261107T120000Z.tar.staging" / "db" / "lyrveil.sqlite3").write_bytes(b"copy")
    assert DailyBackup(data_root, ROME).clear_unfinished() == 2
    assert [item.name for item in folder.iterdir()] == ["lyrveil-20261106T120000Z.tar"]


def test_prune_keeps_the_newest_backups_and_ignores_other_files(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    for stamp in ("20261101T000000Z", "20261103T000000Z", "20261102T000000Z"):
        (folder / f"lyrveil-{stamp}.tar").write_bytes(b"x")
    (folder / "notes.txt").write_text("kept", encoding="utf-8")
    assert prune_backups(folder, 2) == ["lyrveil-20261101T000000Z.tar"]
    assert sorted(item.name for item in folder.iterdir()) == [
        "lyrveil-20261102T000000Z.tar",
        "lyrveil-20261103T000000Z.tar",
        "notes.txt",
    ]
