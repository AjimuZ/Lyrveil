"""The orchestrator of compose.test.yaml: the real control API, lifecycle manager and supervisor over FakeContainerGate
and FakeGpuProbe, so the test stack grants leases without a Docker socket or a GPU.

`python -m fakes.fake_orchestrator` runs it. It exists only in the `fake-runtime` stage of orchestrator/Dockerfile, the
image "lyrveil/orchestrator-fakes:test", which compose.yaml never uses. The fake gate starts each runtime as healthy at
once, so the default mode `llm` is ready a moment after start, while the fake KoboldCpp container answers the backend.
"""

import sys

from lyrveil_orchestrator.main import run

# Relative, unlike the other fakes: the backend's lint sees lyrveil_orchestrator as third-party and the
# orchestrator's as first-party, and only the local-folder section sorts the same way for both.
from .fake_docker import FakeContainerGate
from .fake_nvml import FakeGpuProbe


def main() -> int:
    """Starts the orchestrator over the fakes and returns its exit status, as lyrveil_orchestrator.main.run does."""
    return run(FakeContainerGate, FakeGpuProbe)


if __name__ == "__main__":
    sys.exit(main())
