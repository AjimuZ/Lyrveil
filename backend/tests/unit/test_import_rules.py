"""Enforces the import rules of docs/backend.md (Quality gates) on the lyrveil package."""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"


@dataclass(frozen=True)
class ImportRule:
    """Modules under `layer` may not import a module under any `forbidden` prefix, unless it is under `allowed`."""

    layer: str
    forbidden: tuple[str, ...]
    allowed: tuple[str, ...] = ()


RULES = (
    ImportRule("lyrveil.domain", forbidden=("lyrveil",), allowed=("lyrveil.domain",)),
    ImportRule("lyrveil.services", forbidden=("lyrveil.persistence.models", "lyrveil.runtimes")),
    ImportRule("lyrveil.api", forbidden=("lyrveil.runtimes",)),
)


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _imported_modules(tree: ast.Module, package: str) -> list[tuple[int, str]]:
    # `from a import b` yields a.b, because b may be a submodule; relative imports are resolved against package.
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                anchor = ".".join(parts[: len(parts) - node.level + 1])
                base = f"{anchor}.{base}" if base else anchor
            found.extend((node.lineno, base if alias.name == "*" else f"{base}.{alias.name}") for alias in node.names)
    return found


def find_violations(source_root: Path, rules: tuple[ImportRule, ...] = RULES) -> list[str]:
    """Returns one "path:line: module imports target" entry per import that breaks a rule, in path order.

    source_root is the folder that holds the lyrveil package. An empty list means every rule holds.
    """
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        module = ".".join(parts)
        package = module if is_package else ".".join(parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for rule in rules:
            if not _under(module, rule.layer):
                continue
            for line, target in _imported_modules(tree, package):
                if any(_under(target, prefix) for prefix in rule.forbidden) and not any(
                    _under(target, prefix) for prefix in rule.allowed
                ):
                    violations.append(f"{path.relative_to(source_root).as_posix()}:{line}: {module} imports {target}")
    return violations


def test_the_package_follows_the_import_rules() -> None:
    assert (SOURCE_ROOT / "lyrveil" / "__init__.py").is_file()
    assert find_violations(SOURCE_ROOT) == []


def write_module(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.relative_to(root).parents:
        if parent != Path():
            (root / parent / "__init__.py").touch()
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "text", "target"),
    [
        ("lyrveil/domain/entities.py", "import lyrveil.settings\n", "lyrveil.settings"),
        ("lyrveil/domain/entities.py", "from lyrveil import logs\n", "lyrveil.logs"),
        ("lyrveil/domain/rules/state.py", "from ...services import chat\n", "lyrveil.services.chat"),
        ("lyrveil/domain/__init__.py", "from ..persistence.models import Row\n", "lyrveil.persistence.models.Row"),
        ("lyrveil/services/chat.py", "from lyrveil.persistence.models import Row\n", "lyrveil.persistence.models.Row"),
        ("lyrveil/services/chat.py", "from ..runtimes import kobold\n", "lyrveil.runtimes.kobold"),
        ("lyrveil/api/routes.py", "import lyrveil.runtimes.kobold\n", "lyrveil.runtimes.kobold"),
    ],
)
def test_a_forbidden_import_is_reported(tmp_path: Path, relative: str, text: str, target: str) -> None:
    write_module(tmp_path, relative, text)
    (violation,) = find_violations(tmp_path)
    assert violation.endswith(f"imports {target}")
    assert violation.startswith(f"{relative}:1:")


def test_allowed_imports_are_not_reported(tmp_path: Path) -> None:
    write_module(tmp_path, "lyrveil/domain/entities.py", "import dataclasses\nfrom . import enums\n")
    write_module(tmp_path, "lyrveil/domain/enums.py", "from lyrveil.domain.entities import Character\n")
    write_module(tmp_path, "lyrveil/services/chat.py", "from lyrveil.persistence import repositories\n")
    write_module(tmp_path, "lyrveil/api/routes.py", "from lyrveil.services import chat\n")
    assert find_violations(tmp_path) == []
