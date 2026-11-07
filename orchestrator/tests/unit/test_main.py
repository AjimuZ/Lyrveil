"""The composition root: its settings, and that nothing lets the production orchestrator reach the fakes."""

import ast
from dataclasses import fields
from pathlib import Path
from typing import NoReturn

import pytest

import lyrveil_orchestrator
from lyrveil_orchestrator import main
from lyrveil_orchestrator.container_gate import ContainerGate
from lyrveil_orchestrator.gpu_monitor import NvmlProbe

TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def test_load_settings_reads_the_token_and_the_log_level() -> None:
    settings = main.load_settings({"LYRVEIL_CONTROL_TOKEN": TOKEN, "LYRVEIL_LOG_LEVEL": " debug "})
    assert settings == main.Settings(control_token=TOKEN, log_level="DEBUG")


def test_load_settings_defaults_the_log_level_to_info() -> None:
    assert main.load_settings({"LYRVEIL_CONTROL_TOKEN": TOKEN}).log_level == "INFO"


@pytest.mark.parametrize(
    "token",
    ["", "change-me", TOKEN[:-1], TOKEN + "0", "g" * 64],
)
def test_load_settings_refuses_a_token_that_is_not_64_hex_characters_without_echoing_it(token: str) -> None:
    with pytest.raises(main.SettingsError, match="LYRVEIL_CONTROL_TOKEN") as raised:
        main.load_settings({"LYRVEIL_CONTROL_TOKEN": token})
    assert not token or token not in str(raised.value)


def test_load_settings_refuses_an_unknown_log_level() -> None:
    with pytest.raises(main.SettingsError, match="LYRVEIL_LOG_LEVEL"):
        main.load_settings({"LYRVEIL_CONTROL_TOKEN": TOKEN, "LYRVEIL_LOG_LEVEL": "TRACE"})


def test_run_exits_78_on_invalid_settings_before_building_an_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LYRVEIL_CONTROL_TOKEN", raising=False)

    def refuse() -> NoReturn:
        raise AssertionError("no adapter may be built with invalid settings")

    assert main.run(refuse, refuse) == main.EXIT_INVALID_SETTINGS
    assert "LYRVEIL_CONTROL_TOKEN" in capsys.readouterr().err


def test_main_always_drives_the_docker_gate_and_nvml(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[object] = []

    def record(gate: object, probe: object) -> int:
        received.extend([gate, probe])
        return 0

    monkeypatch.setattr(main, "run", record)
    # Variables that might look like switches change nothing: main reads no setting before choosing the adapters.
    monkeypatch.setenv("LYRVEIL_FAKES", "1")
    monkeypatch.setenv("LYRVEIL_ORCHESTRATOR_GATE", "fake")
    assert main.main() == 0
    assert received == [ContainerGate, NvmlProbe]


def test_the_settings_hold_no_switch_for_an_adapter() -> None:
    assert [field.name for field in fields(main.Settings)] == ["control_token", "log_level"]


def test_no_module_of_the_orchestrator_imports_the_fakes() -> None:
    # The fakes are importable in this test stage, so only the source itself can show that production code never
    # names them. The Docker test checks that the runtime image cannot import them either.
    package = Path(lyrveil_orchestrator.__file__).parent
    offenders: list[str] = []
    for module in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module]
            offenders += [f"{module.name}: {name}" for name in names if name.split(".")[0] in {"fakes", "tests"}]
    assert offenders == []
