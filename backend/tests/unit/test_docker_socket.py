"""Fails when a backend module could open the Docker socket, which only the orchestrator's gate uses (ADR 0006)."""

import ast
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
# The Python code that runs in the backend container: the package and the Alembic migrations.
SCANNED_FOLDERS = ("src", "migrations")

# The patterns of orchestrator/tests/unit/test_socket_confinement.py, copied because the two packages share no code:
# change both together. Opening the Docker socket takes a socket or Docker client module, a Unix-socket function,
# the `uds` argument of the HTTPX and httpcore transports, the socket's path or URL, the DOCKER_HOST variable, or the
# docker command. The scan catches mistakes, not code written to evade it.
SOCKET_MODULES = {"_socket", "aiodocker", "docker", "requests_unixsocket", "socket"}
SOCKET_NAMES = {"AF_UNIX", "connect_unix", "create_unix_connection", "open_unix_connection"}
SOCKET_TEXT = ("docker.sock", "docker_host", "unix://")
# The docker command alone, as the first item of an argument list, or a command line that goes on with an option or a
# subcommand. A message such as "Docker did not answer" is neither, so modules may still name Docker in their errors.
DOCKER_COMMAND = re.compile(
    r"(?:\S*[/\\])?docker(?:\.exe)?(?:\s+(?:-|(?:attach|build|buildx|commit|compose|container|context|cp|create|events"
    r"|exec|image|images|info|inspect|kill|logs|network|pause|ps|pull|restart|rm|rmi|run|start|stats|stop|system|top"
    r"|unpause|update|version|volume|wait)\b).*)?",
    re.DOTALL,
)


def socket_uses(source: str) -> list[tuple[int, str]]:
    """Returns (line, finding) for each construct of `source` that could open the Docker socket, in line order."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(
                (node.lineno, f"imports {alias.name}")
                for alias in node.names
                if alias.name.split(".")[0] in SOCKET_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in SOCKET_MODULES:
                found.append((node.lineno, f"imports {node.module}"))
            found.extend((node.lineno, f"imports {alias.name}") for alias in node.names if alias.name in SOCKET_NAMES)
        elif isinstance(node, ast.Attribute) and node.attr in SOCKET_NAMES:
            found.append((node.lineno, f"uses {node.attr}"))
        elif isinstance(node, ast.Name) and node.id in SOCKET_NAMES:
            found.append((node.lineno, f"uses {node.id}"))
        elif isinstance(node, ast.keyword) and node.arg == "uds":
            found.append((node.lineno, "passes uds"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.lower()
            found.extend((node.lineno, f"names {marker}") for marker in SOCKET_TEXT if marker in text)
            if DOCKER_COMMAND.fullmatch(text.strip()):
                found.append((node.lineno, "runs docker"))
    return sorted(found)


def package_socket_uses(source_root: Path) -> dict[str, list[tuple[int, str]]]:
    """Maps each module under `source_root` that could open the Docker socket, by relative path, to its findings."""
    uses: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(source_root.rglob("*.py")):
        found = socket_uses(path.read_text(encoding="utf-8"))
        if found:
            uses[path.relative_to(source_root).as_posix()] = found
    return uses


@pytest.mark.parametrize("folder", SCANNED_FOLDERS)
def test_no_backend_module_can_open_the_docker_socket(folder: str) -> None:
    root = BACKEND_ROOT / folder
    # An empty or missing folder would pass the scan without showing anything.
    assert any(root.rglob("*.py")), f"{folder} holds no Python module"
    assert package_socket_uses(root) == {}


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ("import socket\n", "imports socket"),
        ("import socket as raw\n", "imports socket"),
        ("from socket import AF_UNIX\n", "imports socket"),
        ("from asyncio import open_unix_connection\n", "imports open_unix_connection"),
        ("import asyncio\nasyncio.open_unix_connection('/x')\n", "uses open_unix_connection"),
        ("import anyio\nanyio.connect_unix('/x')\n", "uses connect_unix"),
        ("import docker\n", "imports docker"),
        ("from docker.client import DockerClient\n", "imports docker.client"),
        ("import aiodocker\n", "imports aiodocker"),
        ("import httpx\nhttpx.AsyncHTTPTransport(uds='/x')\n", "passes uds"),
        ("SOCKET = '/var/run/docker.sock'\n", "names docker.sock"),
        ("URL = 'unix:///run/user/docker'\n", "names unix://"),
        ("import os\nos.environ['DOCKER_HOST']\n", "names docker_host"),
        ("import subprocess\nsubprocess.run(['docker', 'start', 'x'])\n", "runs docker"),
        ("import subprocess\nsubprocess.run(['/usr/bin/docker', 'ps'])\n", "runs docker"),
        ("import os\nos.system('docker stop x')\n", "runs docker"),
        ("import os\nos.system('docker compose up -d')\n", "runs docker"),
        ("import os\nos.system('docker --context remote ps')\n", "runs docker"),
    ],
)
def test_each_way_to_the_socket_is_found(source: str, finding: str) -> None:
    assert finding in {found for _, found in socket_uses(source)}


def test_code_that_does_not_reach_the_socket_is_not_reported() -> None:
    source = (
        '"""Sends the WebSocket events of a job and asks the orchestrator, never Docker, for the GPU."""\n'
        "import httpx\n"
        "client = httpx.AsyncClient(base_url='http://lyrveil-orchestrator:8100')\n"
        "ERROR_CODE = 'DOCKER_UNAVAILABLE'\n"
    )
    assert socket_uses(source) == []


@pytest.mark.parametrize(
    "text",
    ["Docker did not answer in time", "The Docker Engine does not answer.", "dockerd", "docker composer"],
)
def test_a_message_that_names_docker_is_not_taken_for_the_command(text: str) -> None:
    assert socket_uses(f"MESSAGE = {text!r}\n") == []


def test_a_module_that_opens_the_socket_fails_the_scan(tmp_path: Path) -> None:
    package = tmp_path / "lyrveil"
    (package / "runtimes").mkdir(parents=True)
    (package / "__init__.py").write_text('"""Lyrveil backend."""\n', encoding="utf-8")
    (package / "runtimes" / "restart.py").write_text("import docker\n", encoding="utf-8")
    assert package_socket_uses(tmp_path) == {"lyrveil/runtimes/restart.py": [(1, "imports docker")]}
