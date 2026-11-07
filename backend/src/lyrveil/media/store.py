"""MediaStore: the files below the media root. Writes pass through a staging folder and an atomic move; every read,
write and delete accepts only a path that really resolves below the root (docs/security.md, File safety)."""

import os
import uuid
from pathlib import Path

from lyrveil.logs import get_logger

# Inside the backend container the data volume is mounted at /data (docs/operations.md).
DEFAULT_MEDIA_ROOT = Path("/data/media")
# Unfinished writes live here; it sits on the same file system as their targets, so the final move is atomic.
STAGING = ".staging"
# A media file sits in a category folder at least: `assets/<character id>/<asset id>.png`, never at the root itself.
_MIN_DEPTH = 2

logger = get_logger(__name__)


class MediaPathError(Exception):
    """A media path does not resolve to a file location below the root: it leaves the root, names the root or a file
    directly in it, or points into the staging folder. The message never holds the path."""


class MediaStore:
    """The files below `root`. Paths given to it are relative to the root and built from generated identifiers only;
    a path from a request never reaches it (docs/security.md). Nothing is created until the first write."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve(self, relative: str) -> Path:
        """Returns the real path of `relative`.

        Symbolic links and `..` are resolved before the check, so neither can lead outside. Raises MediaPathError
        when the real path is not at least two levels below the real root, or lies in the staging folder.
        """
        root = self._root.resolve()
        candidate = (root / relative).resolve()
        try:
            parts = candidate.relative_to(root).parts
        except ValueError:
            raise MediaPathError("the path leaves the media root") from None
        if len(parts) < _MIN_DEPTH or parts[0] == STAGING:
            raise MediaPathError("the path is not a media file location")
        return candidate

    def save(self, relative: str, data: bytes) -> None:
        """Writes `data` at `relative`, replacing any file there.

        The bytes go to a new file in the staging folder, are flushed to disk, then move into place with os.replace,
        which is atomic within one file system: a reader sees the old file or the whole new one, never part of it, and
        a crash leaves at most a staging file for clear_staging. Raises MediaPathError as `resolve` does before writing
        anything, and OSError when the disk fails, after removing the staging file.
        """
        target = self.resolve(relative)
        staging = self._root.resolve() / STAGING
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"{uuid.uuid4().hex}.part"
        try:
            with staged.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

    def read(self, relative: str) -> bytes:
        """Returns the bytes of the file at `relative`. Raises MediaPathError as `resolve` does, and OSError when the
        file is missing or unreadable."""
        return self.resolve(relative).read_bytes()

    def delete(self, relative: str) -> None:
        """Removes the file at `relative`; a file that is already gone is no error.

        A path that fails the check of `resolve` is refused: the refusal is logged as `media_delete_refused`, nothing
        is removed and MediaPathError is raised, so the caller's operation stops.
        """
        try:
            path = self.resolve(relative)
        except MediaPathError:
            logger.warning("media_delete_refused")
            raise
        path.unlink(missing_ok=True)

    def clear_staging(self) -> int:
        """Removes what cut writes left in the staging folder and returns how many entries it removed. Startup calls it
        before any write can be in flight. A missing folder counts as empty; a link is removed, never followed."""
        staging = self._root.resolve() / STAGING
        if not staging.is_dir():
            return 0
        removed = 0
        for entry in staging.iterdir():
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
                removed += 1
        return removed
