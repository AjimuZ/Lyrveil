"""Probes the control API's unauthenticated health route, the only control request that needs no token."""

import http.client
import urllib.error
import urllib.request

# The orchestrator on the internal control network, port 8100 (docs/api.md, Control API).
CONTROL_API_URL = "http://lyrveil-orchestrator:8100"
PROBE_TIMEOUT_S = 1.0


def probe_control_api(base_url: str = CONTROL_API_URL, timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """Returns True when `GET /v1/health` of the control API answers 200 within `timeout_s`.

    Never raises: an unreachable host, a timeout and any other answer all return False. Blocking, so call it from a
    plain `def` route.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/v1/health", timeout=timeout_s) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return False
