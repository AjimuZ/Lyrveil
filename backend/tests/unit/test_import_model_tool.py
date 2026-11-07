"""Tests the model import tool of docs/operations.md (scripts\\lyrveil.cmd import-model) on temporary folders."""

import ast
import hashlib
import sys
from pathlib import Path

import pytest

from lyrveil.tools import import_model as import_tool
from lyrveil.tools.import_model import EXIT_FAILURE, EXIT_OK, ImportRefusedError, import_model, run, target_folder

CONTENT = bytes(range(256)) * 5000


def make_source(tmp_path: Path, name: str, content: bytes = CONTENT) -> Path:
    folder = tmp_path / "windows"
    folder.mkdir(exist_ok=True)
    source = folder / name
    source.write_bytes(content)
    return source


@pytest.mark.parametrize(
    ("name", "folder"),
    [("custom-12b.Q4_K_M.gguf", "llm"), ("my_checkpoint.v2.safetensors", "image/checkpoints")],
)
def test_each_extension_has_its_folder(name: str, folder: str) -> None:
    assert target_folder(name) == folder


@pytest.mark.parametrize(
    "name",
    [
        "model.bin",
        "model.GGUF",
        "model.gguf.txt",
        "model",
        "with space.gguf",
        "accenté.gguf",
        ".gguf",
        "a" * 129 + ".gguf",
    ],
)
def test_other_extensions_and_names_outside_the_pattern_are_refused(name: str) -> None:
    with pytest.raises(ImportRefusedError):
        target_folder(name)


def test_a_gguf_is_copied_to_llm_with_its_hash(tmp_path: Path) -> None:
    root = tmp_path / "models"
    source = make_source(tmp_path, "custom.gguf")
    imported = import_model(source, root)
    assert imported.relative_path == "llm/custom.gguf"
    assert imported.size == len(CONTENT)
    assert imported.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert (root / "llm" / "custom.gguf").read_bytes() == CONTENT
    assert sorted(path.name for path in (root / "llm").iterdir()) == ["custom.gguf"]


def test_a_safetensors_goes_to_the_checkpoints(tmp_path: Path) -> None:
    root = tmp_path / "models"
    imported = import_model(make_source(tmp_path, "custom.safetensors"), root)
    assert imported.relative_path == "image/checkpoints/custom.safetensors"
    assert (root / "image" / "checkpoints" / "custom.safetensors").read_bytes() == CONTENT


def test_an_existing_name_is_refused_and_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "models"
    (root / "llm").mkdir(parents=True)
    (root / "llm" / "custom.gguf").write_bytes(b"already here")
    source = make_source(tmp_path, "custom.gguf")
    assert run([str(source)], root) == EXIT_FAILURE
    assert "llm/custom.gguf exists already" in capsys.readouterr().err
    assert (root / "llm" / "custom.gguf").read_bytes() == b"already here"
    assert sorted(path.name for path in (root / "llm").iterdir()) == ["custom.gguf"]


def test_a_missing_file_is_refused_without_a_trace(tmp_path: Path) -> None:
    root = tmp_path / "models"
    assert run([str(tmp_path / "absent.gguf")], root) == EXIT_FAILURE
    assert not (root / "llm").exists() or not any((root / "llm").iterdir())


def test_the_command_prints_the_destination_and_hash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = make_source(tmp_path, "custom.gguf")
    assert run([str(source)], tmp_path / "models") == EXIT_OK
    expected = f"Imported llm/custom.gguf: {len(CONTENT)} bytes, SHA-256 {hashlib.sha256(CONTENT).hexdigest()}"
    assert capsys.readouterr().out.strip() == expected


def test_the_tool_imports_only_the_standard_library() -> None:
    # The tool reads a file nobody has checked, so no third-party package and no other lyrveil module runs with it.
    tree = ast.parse(Path(import_tool.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add((node.module or "").split(".")[0])
    assert imported - sys.stdlib_module_names == set()
