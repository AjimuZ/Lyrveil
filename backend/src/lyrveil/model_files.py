"""ModelFiles: the backend's read-only view of the models volume, which it lists, sizes and hashes for profiles.

docs/security.md (Model files): the backend accepts a file name, never a path. The name must match
`^[A-Za-z0-9._-]{1,128}\\.(gguf|safetensors)$` with the extension of its kind, and the file must be a regular file
directly in that kind's folder, `/models/llm/` for text and `/models/image/checkpoints/` for images. A symbolic link is
refused wherever it points, which also refuses every link out of the folder; the resolved path is checked again, so a
folder replaced by a link cannot lead elsewhere either. The volume is mounted read-only, and nothing here writes to it.
"""

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

ModelKind = Literal["text", "image"]
DEFAULT_MODELS_ROOT = Path("/models")
# docs/operations.md (Data layout), the folders of the download and import tools.
FOLDERS: Mapping[ModelKind, str] = MappingProxyType({"text": "llm", "image": "image/checkpoints"})
EXTENSIONS: Mapping[ModelKind, str] = MappingProxyType({"text": ".gguf", "image": ".safetensors"})
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$")
CHUNK_BYTES = 1 << 20


class ModelFileRefusedError(Exception):
    """The name is not one the backend accepts for that kind, or names a link, a folder or another non-regular file.
    The message is Italian and names no path outside the volume."""


class ModelFileMissingError(Exception):
    """No file of that name is in the kind's folder, or it changed size while it was read."""


@dataclass(frozen=True)
class ModelFile:
    """A regular file of a model folder: its kind, name and size in bytes."""

    kind: ModelKind
    name: str
    size: int


def check_name(kind: ModelKind, name: str) -> None:
    """Raises ModelFileRefusedError unless `name` matches NAME_PATTERN and ends with the extension of `kind`. A name
    with a slash, a backslash or a NUL, so every traversal, fails the pattern."""
    if not NAME_PATTERN.fullmatch(name):
        raise ModelFileRefusedError(
            "Il nome del file non è valido: usa solo lettere, cifre, punto, trattino e trattino basso, con "
            "estensione .gguf o .safetensors."
        )
    if not name.endswith(EXTENSIONS[kind]):
        raise ModelFileRefusedError(
            f"Per questo tipo di modello serve un file {EXTENSIONS[kind]}. Altri formati, come .ckpt e .pt, non sono "
            "accettati."
        )


class ModelFiles:
    """The model folders under `root`, the models volume. Every method blocks on the file system: call it from a
    worker thread."""

    def __init__(self, root: Path = DEFAULT_MODELS_ROOT) -> None:
        self._root = root

    def folder(self, kind: ModelKind) -> Path:
        return self._root / FOLDERS[kind]

    def resolve(self, kind: ModelKind, name: str) -> Path:
        """Returns the path of the regular file `name` in the folder of `kind`.

        Raises ModelFileRefusedError for a name `check_name` refuses, a symbolic link, a path that resolves outside
        the folder, or anything but a regular file, and ModelFileMissingError when the folder or the file is missing.
        """
        check_name(kind, name)
        folder = self.folder(kind)
        path = folder / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ModelFileMissingError(f"Il file {name} non è nella cartella dei modelli.") from None
        if stat.S_ISLNK(info.st_mode):
            raise ModelFileRefusedError(f"{name} è un collegamento: importa il file con import-model.")
        try:
            # Against the root's own path, so a model folder that is itself a link also leads outside.
            inside = path.resolve(strict=True).parent == self._root.resolve(strict=True) / FOLDERS[kind]
        except (FileNotFoundError, RuntimeError):
            raise ModelFileMissingError(f"Il file {name} non è nella cartella dei modelli.") from None
        if not inside:
            raise ModelFileRefusedError(f"{name} non è nella cartella dei modelli.")
        if not stat.S_ISREG(info.st_mode):
            raise ModelFileRefusedError(f"{name} non è un file.")
        return path

    def size(self, kind: ModelKind, name: str) -> int | None:
        """Returns the size of the regular file `name` of `kind`, or None when `resolve` refuses it or finds it
        missing. Reads no content."""
        try:
            return self.resolve(kind, name).stat().st_size
        except (ModelFileRefusedError, ModelFileMissingError, OSError):
            return None

    def listing(self, kind: ModelKind) -> list[ModelFile]:
        """Returns the regular files of the folder of `kind` that `resolve` accepts, sorted by name; an empty list when
        the folder is missing or unreadable. Temporary and hidden files are left out by the name rule, since a
        download's `.part` and an import's `.import` have another extension."""
        try:
            names = sorted(os.listdir(self.folder(kind)))
        except OSError:
            return []
        found: list[ModelFile] = []
        for name in names:
            if name.startswith("."):
                continue
            size = self.size(kind, name)
            if size is not None:
                found.append(ModelFile(kind=kind, name=name, size=size))
        return found

    def sha256(self, kind: ModelKind, name: str) -> tuple[str, int]:
        """Returns the SHA-256 of the file `name` of `kind`, in lowercase hex, and the size it was read at.

        Raises what `resolve` raises, and ModelFileMissingError when the file cannot be read or its size changes while
        it is read, as an import or a download in progress would make it."""
        path = self.resolve(kind, name)
        digest = hashlib.sha256()
        try:
            before = path.stat().st_size
            with path.open("rb") as reader:
                while chunk := reader.read(CHUNK_BYTES):
                    digest.update(chunk)
            after = path.stat().st_size
        except OSError:
            raise ModelFileMissingError(f"Il file {name} non si può leggere.") from None
        if before != after:
            raise ModelFileMissingError(f"Il file {name} è cambiato durante la lettura: registralo di nuovo.")
        return digest.hexdigest(), after
