"""Copies one model file from a read-only folder into the models volume, for a custom profile.

`scripts\\lyrveil.cmd import-model <file>` runs `python -m lyrveil.tools.import_model /import/<name>` in a one-off
container with no network, the file's Windows folder mounted read-only at /import and the models volume at /models
(docs/operations.md). A `.gguf` goes to `llm/`, a `.safetensors` to `image/checkpoints/`. The module imports the
standard library only, like the download tool, because it runs next to a file nobody has checked.

Exit statuses (docs/operations.md, Scripts): 0 copied, 1 refused or failed (another extension, a name outside
`^[A-Za-z0-9._-]{1,128}\\.(gguf|safetensors)$`, a name already present, a file that cannot be read or that changed
while it was copied), 2 not enough free space in the volume. A failure leaves no file behind, and never replaces one.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_SPACE = 2

DEFAULT_ROOT = Path("/models")
# The folders of docs/operations.md (Data layout), by extension; the download tool uses the same ones.
FOLDERS = {".gguf": "llm", ".safetensors": "image/checkpoints"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$")
# The copy is written under this name first, so the final name only ever appears complete. It differs from the
# download tool's `.part`, which a later download would resume from.
TEMPORARY_SUFFIX = ".import"
CHUNK_BYTES = 1 << 20


class ImportRefusedError(Exception):
    """A refusal or failure reported on standard error; `status` is the exit status it maps to."""

    status = EXIT_FAILURE


class NoSpaceError(ImportRefusedError):
    """The volume has less free space than the file needs."""

    status = EXIT_NO_SPACE


@dataclass(frozen=True)
class Imported:
    """What was copied: the path under the volume root, its size in bytes and its SHA-256."""

    relative_path: str
    size: int
    sha256: str


def target_folder(name: str) -> str:
    """Returns the folder under the volume root that receives a file called name.

    Raises ImportRefusedError for an extension other than `.gguf` and `.safetensors` (matched with case), and for a
    name outside NAME_PATTERN, such as one with a space or one longer than 128 characters before the extension.
    """
    folder = FOLDERS.get(os.path.splitext(name)[1])
    if folder is None:
        raise ImportRefusedError(f"{name}: only .gguf and .safetensors files can be imported.")
    if not NAME_PATTERN.match(name):
        message = f"{name}: the name must match {NAME_PATTERN.pattern}. Rename the file, then import it."
        raise ImportRefusedError(message)
    return folder


def import_model(source: Path, root: Path) -> Imported:
    """Copies source to its folder under root and returns what was copied.

    Requires that root is the models volume and that nothing else writes the same name meanwhile. The copy goes to a
    temporary name, is flushed to disk and then hard-linked to the final name, which fails when that name appeared
    in the meantime, so an existing file is never replaced. Raises ImportRefusedError when the name is refused,
    already present, or the file cannot be read or changes size while copied, and NoSpaceError when the volume lacks
    space.
    """
    name = source.name
    relative_folder = target_folder(name)
    relative_path = f"{relative_folder}/{name}"
    folder = root / relative_folder
    target = folder / name
    if target.exists() or target.is_symlink():
        raise ImportRefusedError(f"{relative_path} exists already in the models volume. Rename the file first.")
    if not source.is_file():
        raise ImportRefusedError(f"{name} is not a file.")
    folder.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    free = shutil.disk_usage(folder).free
    if size > free:
        raise NoSpaceError(f"{name} needs {size} bytes, and the models volume has {free} bytes free.")

    temporary = folder / f".{name}{TEMPORARY_SUFFIX}"
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            while chunk := reader.read(CHUNK_BYTES):
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if temporary.stat().st_size != size:
            raise ImportRefusedError(f"{name} changed while it was copied. Nothing was imported.")
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise ImportRefusedError(f"{relative_path} exists already in the models volume.") from None
    except OSError as error:
        reason = error.strerror or str(error)
        raise ImportRefusedError(f"{name} could not be copied: {reason}. Nothing was imported.") from error
    finally:
        temporary.unlink(missing_ok=True)
    return Imported(relative_path=relative_path, size=size, sha256=digest.hexdigest())


class _Parser(argparse.ArgumentParser):
    # argparse exits with 2 on a usage error, which docs/operations.md reserves for a failed preflight.
    def error(self, message: str) -> NoReturn:
        raise ImportRefusedError(f"{self.prog}: {message}")


def run(argv: Sequence[str], root: Path) -> int:
    """Imports the file named in argv into root and returns the exit status. A refusal is printed as a FAILURE line."""
    parser = _Parser(prog="lyrveil.tools.import_model", description="Copies a model file into the models volume.")
    parser.add_argument("file", type=Path)
    try:
        imported = import_model(parser.parse_args(list(argv)).file, root)
    except ImportRefusedError as error:
        print(f"FAILURE  {error}", file=sys.stderr, flush=True)
        return error.status
    print(f"Imported {imported.relative_path}: {imported.size} bytes, SHA-256 {imported.sha256}", flush=True)
    return EXIT_OK


def main() -> int:
    """Runs the import of the process arguments into /models."""
    return run(sys.argv[1:], DEFAULT_ROOT)


if __name__ == "__main__":
    sys.exit(main())
