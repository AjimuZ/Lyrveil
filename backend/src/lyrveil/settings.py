"""Typed, immutable deployment settings read once from the LYRVEIL_* environment variables at startup."""

import re
from ipaddress import IPv4Address
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "LYRVEIL_"
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{64}")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class SettingsError(Exception):
    """A deployment setting is missing or invalid.

    The message names every offending variable with the reason, and never contains a value, because a value may be
    the control token.
    """


class Settings(BaseSettings):
    """The deployment settings of docs/configuration.md that the backend reads.

    Values come from the process environment only: Compose passes `.env` through `env_file`, and no `.env` is read
    inside the container. Variables without the prefix, and `LYRVEIL_BACKUP_DIR`, which only the Windows scripts read,
    are ignored. Instances are frozen.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    bind_address: IPv4Address = IPv4Address("127.0.0.1")
    http_port: int = Field(default=4200, ge=1, le=65535)
    control_token: SecretStr
    tz: str = "Europe/Rome"
    log_level: LogLevel = "INFO"
    allow_low_memory: bool = False
    model_licenses_accepted: bool = False

    @field_validator("control_token")
    @classmethod
    def _check_control_token(cls, value: SecretStr) -> SecretStr:
        # setup.cmd writes 32 random bytes as hex; anything else is a placeholder or a damaged file.
        if not _HEX_TOKEN.fullmatch(value.get_secret_value()):
            raise ValueError("must be 64 hexadecimal characters, as written by setup.cmd")
        return value

    @field_validator("tz")
    @classmethod
    def _check_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        # A folder name such as "Europe" raises OSError, a path or an empty name raises ValueError.
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("must be an IANA time zone name") from None
        return value

    @property
    def zone(self) -> ZoneInfo:
        """The time zone of time context and scheduling."""
        return ZoneInfo(self.tz)


def load_settings() -> Settings:
    """Returns the settings read from the environment.

    Raises SettingsError naming each missing or invalid variable, so startup stops before anything else runs.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = []
        for item in error.errors(include_url=False, include_input=False, include_context=False):
            field = str(item["loc"][0]) if item["loc"] else "*"
            problems.append(f"{ENV_PREFIX}{field.upper()}: {item['msg']}")
        raise SettingsError("Invalid deployment settings: " + "; ".join(problems)) from None
