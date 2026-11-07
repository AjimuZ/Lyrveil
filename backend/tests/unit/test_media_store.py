"""T-SEC-08: MediaStore deletes only below the media root, and writes through staging with an atomic move."""

from pathlib import Path

import pytest

from lyrveil.media.store import STAGING, MediaPathError, MediaStore


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "media"


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A file next to the media root, which no media operation may touch."""
    path = tmp_path / "secret.txt"
    path.write_text("keep", encoding="utf-8")
    return path


def test_a_saved_file_is_moved_into_place_and_staging_is_left_empty(root: Path) -> None:
    store = MediaStore(root)
    store.save("assets/c/a.png", b"pixels")
    assert (root / "assets" / "c" / "a.png").read_bytes() == b"pixels"
    assert list((root / STAGING).iterdir()) == []
    assert store.read("assets/c/a.png") == b"pixels"


def test_delete_removes_a_media_file_and_ignores_a_missing_one(root: Path) -> None:
    store = MediaStore(root)
    store.save("assets/c/a.png", b"pixels")
    store.delete("assets/c/a.png")
    assert not (root / "assets" / "c" / "a.png").exists()
    store.delete("assets/c/a.png")


@pytest.mark.parametrize(
    "relative",
    ["../secret.txt", "assets/../../secret.txt", "", ".", "loose.png", f"{STAGING}/left.part", "assets/../loose.png"],
)
def test_delete_refuses_paths_outside_the_media_file_locations(root: Path, outside: Path, relative: str) -> None:
    store = MediaStore(root)
    store.save("assets/c/a.png", b"pixels")
    (root / "loose.png").write_bytes(b"x")
    (root / STAGING / "left.part").write_bytes(b"x")
    with pytest.raises(MediaPathError):
        store.delete(relative)
    assert outside.read_text(encoding="utf-8") == "keep"
    assert (root / "loose.png").exists()
    assert (root / STAGING / "left.part").exists()


def test_delete_refuses_an_absolute_path(root: Path, outside: Path) -> None:
    with pytest.raises(MediaPathError):
        MediaStore(root).delete(str(outside))
    assert outside.exists()


def test_delete_refuses_a_link_that_leads_outside(root: Path, outside: Path) -> None:
    folder = root / "assets" / "c"
    folder.mkdir(parents=True)
    # The unit tests run in the Linux backend container, where creating a symbolic link needs no privilege.
    (folder / "a.png").symlink_to(outside)
    with pytest.raises(MediaPathError):
        MediaStore(root).delete("assets/c/a.png")
    assert outside.read_text(encoding="utf-8") == "keep"


def test_save_refuses_a_path_outside_before_writing(root: Path, tmp_path: Path) -> None:
    with pytest.raises(MediaPathError):
        MediaStore(root).save("../written.png", b"x")
    assert not (tmp_path / "written.png").exists()


def test_clear_staging_removes_what_a_cut_write_left(root: Path) -> None:
    store = MediaStore(root)
    assert store.clear_staging() == 0
    (root / STAGING).mkdir(parents=True)
    (root / STAGING / "a.part").write_bytes(b"x")
    store.save("assets/c/a.png", b"pixels")
    assert store.clear_staging() == 1
    assert list((root / STAGING).iterdir()) == []
    assert (root / "assets" / "c" / "a.png").exists()


JOB = "0199a000-0000-7000-8000-000000000001"


def test_a_staged_job_file_survives_clear_staging_until_it_is_discarded(root: Path) -> None:
    store = MediaStore(root)
    assert store.read_staged(JOB) is None
    store.stage_job_file(JOB, b"first")
    store.stage_job_file(JOB, b"second")
    (root / STAGING / "loose.part").write_bytes(b"x")

    assert store.clear_staging() == 1
    assert store.read_staged(JOB) == b"second"
    assert [path.name for path in (root / STAGING / JOB).iterdir()] == ["render"]

    store.discard_staged(JOB)
    store.discard_staged(JOB)
    assert store.read_staged(JOB) is None
    assert not (root / STAGING / JOB).exists()


@pytest.mark.parametrize("job_id", ["../escape", "", "a/b", "not-a-uuid", JOB.upper()])
def test_only_a_plain_job_identifier_names_a_staged_file(root: Path, job_id: str) -> None:
    store = MediaStore(root)
    with pytest.raises(MediaPathError):
        store.stage_job_file(job_id, b"x")
    with pytest.raises(MediaPathError):
        store.read_staged(job_id)
