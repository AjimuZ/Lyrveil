"""Tests that the deployment settings parse valid values and stop startup with a named, value-free message."""

import pytest

from lyrveil.main import EXIT_INVALID_SETTINGS, main
from lyrveil.settings import SettingsError, load_settings

TOKEN = "0123456789abcdef" * 4


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each test starts from a known environment with only a valid token set.
    for name in (
        "LYRVEIL_BIND_ADDRESS",
        "LYRVEIL_HTTP_PORT",
        "LYRVEIL_TZ",
        "LYRVEIL_LOG_LEVEL",
        "LYRVEIL_ALLOW_LOW_MEMORY",
        "LYRVEIL_MODEL_LICENSES_ACCEPTED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LYRVEIL_CONTROL_TOKEN", TOKEN)


def test_defaults_match_the_configuration_document() -> None:
    settings = load_settings()
    assert str(settings.bind_address) == "127.0.0.1"
    assert settings.http_port == 4200
    assert settings.tz == "Europe/Rome"
    assert settings.zone.key == "Europe/Rome"
    assert settings.log_level == "INFO"
    assert settings.allow_low_memory is False
    assert settings.model_licenses_accepted is False
    assert settings.control_token.get_secret_value() == TOKEN


def test_the_allowed_hosts_and_origins_are_the_loopback_names_on_the_http_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings()
    assert settings.allowed_hosts == ("localhost:4200", "127.0.0.1:4200")
    assert settings.allowed_origins == ("http://localhost:4200", "http://127.0.0.1:4200")
    monkeypatch.setenv("LYRVEIL_HTTP_PORT", "80")
    # Browsers leave the default port out of Host and Origin.
    assert load_settings().allowed_hosts == ("localhost:80", "127.0.0.1:80", "localhost", "127.0.0.1")
    monkeypatch.setenv("LYRVEIL_BIND_ADDRESS", "192.168.1.10")
    assert "192.168.1.10:80" not in load_settings().allowed_hosts


def test_reads_the_example_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LYRVEIL_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LYRVEIL_ALLOW_LOW_MEMORY", "true")
    monkeypatch.setenv("LYRVEIL_HTTP_PORT", "4300")
    monkeypatch.setenv("LYRVEIL_BACKUP_DIR", "C:\\Users\\someone\\backups")
    settings = load_settings()
    assert settings.log_level == "DEBUG"
    assert settings.allow_low_memory is True
    assert settings.http_port == 4300


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LYRVEIL_LOG_LEVEL", "LOUD"),
        ("LYRVEIL_TZ", "Mars/Olympus"),
        ("LYRVEIL_TZ", "Europe"),
        ("LYRVEIL_HTTP_PORT", "70000"),
        ("LYRVEIL_BIND_ADDRESS", "localhost"),
        ("LYRVEIL_ALLOW_LOW_MEMORY", "perhaps"),
        ("LYRVEIL_CONTROL_TOKEN", "generated-by-setup"),
    ],
)
def test_an_invalid_value_is_named_without_its_value(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError) as raised:
        load_settings()
    assert f"{name}:" in str(raised.value)
    assert value not in str(raised.value)


def test_a_missing_token_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LYRVEIL_CONTROL_TOKEN")
    with pytest.raises(SettingsError, match="LYRVEIL_CONTROL_TOKEN: Field required"):
        load_settings()


def test_settings_are_immutable() -> None:
    settings = load_settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.http_port = 1  # type: ignore[misc]


def test_startup_stops_with_status_78_and_the_setting_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LYRVEIL_LOG_LEVEL", "LOUD")
    assert main() == EXIT_INVALID_SETTINGS
    assert "LYRVEIL_LOG_LEVEL" in capsys.readouterr().err
