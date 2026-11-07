"""Composition root: reads the settings, builds the GPU monitor, the container gate, the process supervisor and the
lifecycle manager, and serves the control API of docs/api.md with Uvicorn on port 8100.

`python -m lyrveil_orchestrator.main` is the command of the `runtime` image. It always drives the real ContainerGate
and NvmlProbe: no setting or argument selects anything else. The fakes of tests/fakes reach `run` only through
`fakes.fake_orchestrator`, a module that exists only in the test-only `fake-runtime` stage of orchestrator/Dockerfile.
"""

import asyncio
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import uvicorn

from lyrveil_orchestrator.api import create_app
from lyrveil_orchestrator.container_gate import ContainerGate
from lyrveil_orchestrator.gpu_monitor import GpuProbe, GPUResourceMonitor, NvmlProbe
from lyrveil_orchestrator.lifecycle import ModelLifecycleManager
from lyrveil_orchestrator.supervisor import ProcessSupervisor

LISTEN_HOST = "0.0.0.0"  # Inside the container only: Compose publishes no orchestrator port.
LISTEN_PORT = 8100
# EX_CONFIG from sysexits.h, the status the backend and the runtime entrypoints use for a configuration error.
EXIT_INVALID_SETTINGS = 78
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LOG_LEVEL = "INFO"
# setup.cmd writes 32 random bytes as hex, the format the backend and create_app also require.
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]{64}")


class SettingsError(Exception):
    """A setting is missing or invalid. The message names the variable, never its value, so it may be printed."""


@dataclass(frozen=True)
class Settings:
    """The variables of docs/configuration.md that the orchestrator reads. None of them selects an adapter."""

    control_token: str
    log_level: str = DEFAULT_LOG_LEVEL


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Returns the settings read from `environ`, the process environment by default.

    Raises SettingsError unless LYRVEIL_CONTROL_TOKEN is 64 hexadecimal characters and LYRVEIL_LOG_LEVEL, when set,
    is one of LOG_LEVELS, matched without regard to case. Every other variable is ignored.
    """
    values = os.environ if environ is None else environ
    token = values.get("LYRVEIL_CONTROL_TOKEN", "")
    if not _HEX_TOKEN.fullmatch(token):
        raise SettingsError("LYRVEIL_CONTROL_TOKEN must be 64 hexadecimal characters, as written by setup.cmd")
    level = values.get("LYRVEIL_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if level not in LOG_LEVELS:
        raise SettingsError(f"LYRVEIL_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
    return Settings(control_token=token, log_level=level)


def configure_logging(level: str) -> None:
    """Sends every log record at `level` or above to stdout, one line each, where Docker's log driver collects it.

    The lifecycle events are JSON messages; Uvicorn's own records go through the same handler, with the access log off.
    """
    logging.basicConfig(
        level=level, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s", force=True
    )


async def serve(settings: Settings, gate: ContainerGate, probe: GpuProbe) -> None:
    """Serves the control API over `gate` and `probe` until Uvicorn stops, then closes both.

    The application's lifespan runs the lifecycle manager's loop and cancels it on shutdown. On SIGTERM, Uvicorn
    finishes its shutdown and raises the signal again, so the process may end before the close; the operating system
    then releases the socket connection and NVML with it.
    """
    monitor = GPUResourceMonitor(probe)
    try:
        lifecycle = ModelLifecycleManager(ProcessSupervisor(gate, monitor))
        app = create_app(settings.control_token, monitor, lifecycle)
        # One worker: the lifecycle manager and its single lease live in this process. The access log is off, like
        # the backend's, so every log line comes from code that decides what it writes.
        config = uvicorn.Config(
            app,
            host=LISTEN_HOST,
            port=LISTEN_PORT,
            log_config=None,
            access_log=False,
            server_header=False,
            lifespan="on",
        )
        await uvicorn.Server(config).serve()
    finally:
        await gate.aclose()
        monitor.close()


def run(gate: Callable[[], ContainerGate], probe: Callable[[], GpuProbe]) -> int:
    """Starts the orchestrator with the adapters the two factories build and returns the process exit status.

    Returns EXIT_INVALID_SETTINGS with a message on stderr, before logging or any adapter exists, when a setting is
    invalid; otherwise serves until the server stops and returns 0.
    """
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"lyrveil-orchestrator: {error}", file=sys.stderr)
        return EXIT_INVALID_SETTINGS
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info("orchestrator starting on port %d", LISTEN_PORT)
    asyncio.run(serve(settings, gate(), probe()))
    return 0


def main() -> int:
    """Starts the production orchestrator: the Docker socket through ContainerGate and the card through NVML."""
    return run(ContainerGate, NvmlProbe)


if __name__ == "__main__":
    sys.exit(main())
