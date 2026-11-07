"""Tests the backup, export-backups, restore-check and restore commands of lyrveil.cli on temporary data folders."""

import io
import json
import os
import sqlite3
import tarfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lyrveil.cli import (
    DATABASE_PATH,
    MANIFEST_NAME,
    CliError,
    check_backup,
    create_backup,
    export_backups,
    restore_backup,
    run,
    valid_member_path,
)
from lyrveil.persistence.migrations import head_revision

NOW = datetime(2026, 11, 7, 12, 30, 5, tzinfo=UTC)
LATER = datetime(2026, 11, 7, 13, 0, 0, tzinfo=UTC)


def make_database(path: Path, revision: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (value,))
        connection.commit()


def read_marker(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("SELECT value FROM marker").fetchone()[0])


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    make_database(root / DATABASE_PATH, head_revision(), "backed up")
    (root / "media" / "images").mkdir(parents=True)
    (root / "media" / "images" / "a.webp").write_bytes(b"image a")
    (root / "media" / ".staging").mkdir()
    (root / "media" / ".staging" / "unfinished.webp").write_bytes(b"partial")
    (root / "runtime").mkdir()
    (root / "runtime" / "llm-launch.env").write_text("MODEL_FILE=x.gguf\n", encoding="utf-8")
    return root


def rewrite(source: Path, target: Path, change: dict[str, bytes | None]) -> None:
    # Copies the archive at source to target, replacing the content of the members named in change, or dropping them
    # when the value is None, and keeping each header's size in step with its content.
    with tarfile.open(source) as reader, tarfile.open(target, "w") as writer:
        for member in reader.getmembers():
            extracted = reader.extractfile(member)
            assert extracted is not None
            data = extracted.read()
            if member.name in change:
                replacement = change[member.name]
                if replacement is None:
                    continue
                data = replacement
            member.size = len(data)
            writer.addfile(member, io.BytesIO(data))


def test_a_backup_holds_the_database_media_and_launch_files_with_a_manifest(data_root: Path) -> None:
    path, manifest = create_backup(data_root, NOW)
    assert path == data_root / "backups" / "lyrveil-20261107T123005Z.tar"
    assert [item.path for item in manifest.files] == [DATABASE_PATH, "media/images/a.webp", "runtime/llm-launch.env"]
    assert manifest.schema_revision == head_revision()
    with tarfile.open(path) as archive:
        assert archive.getnames()[0] == MANIFEST_NAME
        document = json.loads(archive.extractfile(MANIFEST_NAME).read())  # type: ignore[union-attr]
    assert document["format"] == "lyrveil-backup"
    assert document["created_at"] == "2026-11-07T12:30:05Z"
    assert sorted(item.name for item in (data_root / "backups").iterdir()) == [path.name]


def test_the_database_copy_is_a_complete_single_file(data_root: Path, tmp_path: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    with tarfile.open(path) as archive:
        archive.extract(DATABASE_PATH, tmp_path / "out", filter="data")
    copy = tmp_path / "out" / DATABASE_PATH
    assert read_marker(copy) == "backed up"
    with closing(sqlite3.connect(copy)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_a_backup_needs_the_database(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="does not exist yet"):
        create_backup(tmp_path, NOW)


def test_a_backup_check_passes_and_changes_nothing(data_root: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    before = path.read_bytes()
    assert run(["restore-check", str(path)], data_root) == 0
    assert path.read_bytes() == before


def test_a_changed_file_fails_the_check(data_root: Path, tmp_path: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    tampered = tmp_path / "tampered.tar"
    rewrite(path, tampered, {"media/images/a.webp": b"image b"})
    with pytest.raises(CliError, match="SHA-256 differs"):
        check_backup(tampered)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({MANIFEST_NAME: None}, "no valid manifest.json"),
        ({"runtime/llm-launch.env": None}, "lacks runtime/llm-launch.env"),
        ({MANIFEST_NAME: b"not json"}, "not valid JSON"),
    ],
)
def test_a_damaged_backup_fails_the_check(
    data_root: Path, tmp_path: Path, change: dict[str, bytes | None], message: str
) -> None:
    path, _manifest = create_backup(data_root, NOW)
    damaged = tmp_path / "damaged.tar"
    rewrite(path, damaged, change)
    with pytest.raises(CliError, match=message):
        check_backup(damaged)


def test_an_entry_the_manifest_does_not_list_fails_the_check(data_root: Path, tmp_path: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    extended = tmp_path / "extended.tar"
    rewrite(path, extended, {})
    with tarfile.open(extended, "a") as archive:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(CliError, match="1 entries that the manifest does not list"):
        check_backup(extended)


def test_a_truncated_file_and_a_non_tar_fail_the_check(data_root: Path, tmp_path: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    with pytest.raises(CliError):
        check_backup(truncated)
    other = tmp_path / "other.tar"
    other.write_text("not a backup", encoding="utf-8")
    with pytest.raises(CliError, match="not a readable tar file"):
        check_backup(other)


def test_a_schema_the_app_does_not_know_is_refused(data_root: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    with pytest.raises(CliError, match="comes from a newer version"):
        check_backup(path, known=frozenset({"0000"}))


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        (DATABASE_PATH, True),
        ("media/images/a.webp", True),
        ("runtime/llm-launch.env", True),
        ("db/other.sqlite3", False),
        ("backups/lyrveil.tar", False),
        ("media/../db/lyrveil.sqlite3", False),
        ("/media/a.webp", False),
        ("media", False),
        ("media\\a.webp", False),
        ("media/a\nb", False),
    ],
)
def test_member_paths(path: str, valid: bool) -> None:
    assert valid_member_path(path) is valid


def test_export_copies_only_the_missing_backups(data_root: Path, tmp_path: Path) -> None:
    first, _manifest = create_backup(data_root, NOW)
    second, _manifest = create_backup(data_root, LATER)
    target = tmp_path / "windows"
    target.mkdir()
    (target / first.name).write_bytes(b"kept as it is")
    (target / "notes.txt").write_text("not a backup", encoding="utf-8")
    copied, present = export_backups(data_root / "backups", target)
    assert (copied, present) == ([second.name], 1)
    assert (target / second.name).read_bytes() == second.read_bytes()
    assert (target / first.name).read_bytes() == b"kept as it is"
    assert sorted(path.name for path in target.iterdir()) == sorted([first.name, second.name, "notes.txt"])


def test_export_needs_the_folder(data_root: Path, tmp_path: Path) -> None:
    assert run(["export-backups", str(tmp_path / "absent")], data_root) == 1


def test_restore_replaces_the_data_and_keeps_the_previous_data(data_root: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    database = data_root / DATABASE_PATH
    database.unlink()
    make_database(database, head_revision(), "after the backup")
    (data_root / "media" / "images" / "new.webp").write_bytes(b"new image")

    previous, manifest = restore_backup(path, data_root, LATER)

    assert previous == data_root / "pre-restore-20261107T130000Z"
    assert read_marker(database) == "backed up"
    assert sorted(p.name for p in (data_root / "media" / "images").iterdir()) == ["a.webp"]
    assert not (data_root / "media" / ".staging").exists()
    assert (data_root / "runtime" / "llm-launch.env").read_text(encoding="utf-8") == "MODEL_FILE=x.gguf\n"
    assert read_marker(previous / DATABASE_PATH) == "after the backup"
    assert (previous / "media" / "images" / "new.webp").read_bytes() == b"new image"
    assert (data_root / "backups" / path.name).is_file()
    assert sorted(p.name for p in data_root.iterdir()) == ["backups", "db", "media", previous.name, "runtime"]
    assert len(manifest.files) == 3


def test_a_refused_restore_changes_nothing(data_root: Path, tmp_path: Path) -> None:
    path, _manifest = create_backup(data_root, NOW)
    tampered = tmp_path / "tampered.tar"
    rewrite(path, tampered, {"runtime/llm-launch.env": b"MODEL_FILE=y.gguf\n"})
    before = sorted(p.relative_to(data_root).as_posix() for p in data_root.rglob("*"))
    assert run(["restore", str(tampered)], data_root, clock=lambda: LATER) == 1
    assert sorted(p.relative_to(data_root).as_posix() for p in data_root.rglob("*")) == before


def test_a_failed_swap_puts_the_previous_data_back(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _manifest = create_backup(data_root, NOW)
    (data_root / "media" / "images" / "new.webp").write_bytes(b"new image")
    real_rename = os.rename

    def failing_rename(source: Path, target: Path) -> None:
        # The extracted runtime folder cannot be moved into place, after db and media have been.
        if Path(source).name == "runtime" and Path(source).parent.name.startswith(".restore-"):
            raise OSError("simulated failure")
        real_rename(source, target)

    monkeypatch.setattr(os, "rename", failing_rename)
    with pytest.raises(CliError, match="previous data was put back"):
        restore_backup(path, data_root, LATER)
    assert (data_root / "media" / "images" / "new.webp").read_bytes() == b"new image"
    assert sorted(p.name for p in data_root.iterdir()) == ["backups", "db", "media", "runtime"]
