"""KoboldCppRuntime: the TextRuntime adapter for KoboldCpp v1.124.2, over the endpoints of docs/runtime-contracts.md.

Every request goes to a path of ENDPOINTS, and tests/unit/test_koboldcpp_runtime.py fails when the module names a path
that the contract page does not list. The client ignores proxy settings and follows no redirect, so a request only
reaches the runtime on the internal network.

Out of memory is recognized by an assumption shared with tests/fakes/fake_kobold.py, since the contract does not record
how KoboldCpp reports it: an error answer whose body says "out of memory", and a stream whose closing event has
`finish_reason` "error". The GPU suite records the real replies in P02.
"""

import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self

import anyio
import httpx

from lyrveil.ports.text_runtime import (
    FinishReason,
    GenerationStalledError,
    GenerationTimeoutError,
    RuntimeOutOfMemoryError,
    RuntimeResponseInvalidError,
    RuntimeStreamDroppedError,
    RuntimeUnavailableError,
    TextChunk,
    TextRequest,
    TextRuntimeError,
    TextRuntimeInfo,
)

# The runtime service on the internal network, on the upstream default port (docs/runtime-contracts.md, Flags).
KOBOLDCPP_URL = "http://lyrveil-llm:5001"
# The release that `GET /api/extra/version` must report (ADR 0028).
PINNED_VERSION = "1.124.2"

MODEL_PATH = "/api/v1/model"
VERSION_PATH = "/api/extra/version"
MAX_CONTEXT_PATH = "/api/extra/true_max_context_length"
TOKEN_COUNT_PATH = "/api/extra/tokencount"
STREAM_PATH = "/api/extra/generate/stream"
GENERATE_PATH = "/api/v1/generate"
ABORT_PATH = "/api/extra/abort"
# Every call the adapter makes. `GET /api/extra/perf` is documented for monitoring, which is not this adapter's job.
ENDPOINTS = frozenset(
    {
        ("GET", MODEL_PATH),
        ("GET", VERSION_PATH),
        ("GET", MAX_CONTEXT_PATH),
        ("POST", TOKEN_COUNT_PATH),
        ("POST", STREAM_PATH),
        ("POST", GENERATE_PATH),
        ("POST", ABORT_PATH),
    }
)

# docs/gpu-orchestration.md, Timeouts: jobs.text_total_timeout_s and jobs.text_stall_timeout_s.
TOTAL_TIMEOUT_S = 180.0
STALL_TIMEOUT_S = 60.0
# The other calls, and the connection of a generation. The timeout table has no value for them: an adapter choice.
REQUEST_TIMEOUT_S = 10.0

_OOM_MARKER = "out of memory"
_MODEL_PREFIX = "koboldcpp/"
# The finish reasons of the stream's closing event that end a reply. "error" is handled as out of memory.
_FINISHED: dict[str, FinishReason] = {"stop": "stop", "length": "length"}


class KoboldCppRuntime:
    """Generates text with the KoboldCpp at `base_url`. Use it as an async context manager, or call `aclose`.

    A generation may run for `total_timeout_s`, and a stream may stay silent for `stall_timeout_s`; past either, the
    adapter sends the abort of its `genkey` and raises. Every other call waits `request_timeout_s`. Errors are the
    TextRuntimeError subclasses of lyrveil.ports.text_runtime.
    """

    def __init__(
        self,
        base_url: str = KOBOLDCPP_URL,
        *,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
        stall_timeout_s: float = STALL_TIMEOUT_S,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, trust_env=False, follow_redirects=False)
        self._total_timeout_s = total_timeout_s
        self._stall_timeout_s = stall_timeout_s
        self._short_timeout = httpx.Timeout(request_timeout_s)
        # A generation's answer is bounded by AnyIO deadlines instead, which tell a stall from the total timeout.
        self._generation_timeout = httpx.Timeout(request_timeout_s, read=None)
        self._running: set[str] = set()
        self._aborted: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.aclose()

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        """Returns the `value` of `POST /api/extra/tokencount`, the runtime tokenizer's count. Never an estimate."""
        body = await self._call("POST", TOKEN_COUNT_PATH, {"prompt": text, "special": special})
        value = body.get("value")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeResponseInvalidError(f"KoboldCpp sent no token count to {TOKEN_COUNT_PATH}")
        return value

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        """Yields each non-empty token in order, then a final chunk with the finish reason and the whole text.

        The finish reason is "aborted" when `abort` named this generation's `genkey` while it ran. Raises
        RuntimeOutOfMemoryError for a closing event with "error", RuntimeStreamDroppedError when the stream ends or
        breaks before its closing event, RuntimeResponseInvalidError for an event that is not the documented JSON, and
        GenerationStalledError or GenerationTimeoutError past the timeouts.
        """
        deadline = anyio.current_time() + self._total_timeout_s
        pieces: list[str] = []
        opened = False
        self._running.add(request.genkey)
        try:
            async with self._client.stream(
                "POST", STREAM_PATH, json=_payload(request), timeout=self._generation_timeout
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise _status_error(STREAM_PATH, response)
                opened = True
                lines = response.aiter_lines()
                while True:
                    line = await self._next_line(lines, deadline, request.genkey)
                    if line is None:
                        raise RuntimeStreamDroppedError("the KoboldCpp stream ended before its closing event")
                    event = _event(line)
                    if event is None:
                        continue
                    token, reason = event
                    if token:
                        pieces.append(token)
                        yield TextChunk(token)
                    if reason is None:
                        continue
                    if reason == "error":
                        raise RuntimeOutOfMemoryError("the KoboldCpp stream ended with an error")
                    finish: FinishReason = "aborted" if request.genkey in self._aborted else _FINISHED[reason]
                    yield TextChunk("", finish, "".join(pieces))
                    return
        except httpx.RequestError as error:
            if opened:
                raise RuntimeStreamDroppedError("the KoboldCpp stream broke before its closing event") from error
            raise RuntimeUnavailableError(f"KoboldCpp did not answer POST {STREAM_PATH}") from error
        finally:
            self._finish(request.genkey)

    async def generate(self, request: TextRequest) -> str:
        """Returns the text of `POST /api/v1/generate`. Raises GenerationTimeoutError past the total timeout."""
        self._running.add(request.genkey)
        try:
            with anyio.fail_after(self._total_timeout_s):
                body = await self._call("POST", GENERATE_PATH, _payload(request), self._generation_timeout)
        except TimeoutError:
            await self._abort_quietly(request.genkey)
            raise GenerationTimeoutError(f"the generation ran past {self._total_timeout_s} s") from None
        finally:
            self._finish(request.genkey)
        results = body.get("results")
        first = results[0] if isinstance(results, list) and results else None
        text = first.get("text") if isinstance(first, dict) else None
        if not isinstance(text, str):
            raise RuntimeResponseInvalidError(f"KoboldCpp sent no text to {GENERATE_PATH}")
        return text

    async def abort(self, genkey: str) -> bool:
        """Sends `POST /api/extra/abort` for `genkey`. Returns whether the runtime answered `success` "true".

        A running stream of `genkey` then ends with the finish reason "aborted". Upstream, abort is unreliable with
        several queued requests; the llm lease keeps the runtime at one.
        """
        if genkey in self._running:
            self._aborted.add(genkey)
        try:
            body = await self._call("POST", ABORT_PATH, {"genkey": genkey})
        except TextRuntimeError:
            self._aborted.discard(genkey)
            raise
        return body.get("success") in ("true", True)

    async def info(self) -> TextRuntimeInfo:
        """Returns the model name without its `koboldcpp/` prefix, the reported version and the real context size.

        Comparing the version with PINNED_VERSION is the caller's decision.
        """
        model = (await self._call("GET", MODEL_PATH)).get("result")
        version = (await self._call("GET", VERSION_PATH)).get("version")
        context = (await self._call("GET", MAX_CONTEXT_PATH)).get("value")
        if not isinstance(model, str) or not model.startswith(_MODEL_PREFIX) or not isinstance(version, str):
            raise RuntimeResponseInvalidError("KoboldCpp sent an identity that the contract does not document")
        if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
            raise RuntimeResponseInvalidError(f"KoboldCpp sent no context size to {MAX_CONTEXT_PATH}")
        return TextRuntimeInfo(model=model.removeprefix(_MODEL_PREFIX), version=version, max_context_length=context)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: httpx.Timeout | None = None
    ) -> dict[str, Any]:
        """Returns the JSON object of a 200 answer. Raises RuntimeUnavailableError when no answer arrives, and the
        error of `_status_error` or RuntimeResponseInvalidError for another answer."""
        try:
            response = await self._client.request(method, path, json=body, timeout=timeout or self._short_timeout)
        except httpx.RequestError as error:
            raise RuntimeUnavailableError(f"KoboldCpp did not answer {method} {path}") from error
        if response.status_code != 200:
            raise _status_error(path, response)
        try:
            parsed = response.json()
        except ValueError:
            raise RuntimeResponseInvalidError(f"KoboldCpp sent invalid JSON to {method} {path}") from None
        if not isinstance(parsed, dict):
            raise RuntimeResponseInvalidError(f"KoboldCpp sent no JSON object to {method} {path}")
        return parsed

    async def _next_line(self, lines: AsyncIterator[str], deadline: float, genkey: str) -> str | None:
        """Returns the next line of a stream, or None at its end. Past the stall wait or the total deadline, sends the
        abort of `genkey` and raises GenerationStalledError or GenerationTimeoutError."""
        remaining = deadline - anyio.current_time()
        try:
            with anyio.fail_after(max(min(self._stall_timeout_s, remaining), 0.0)):
                return await anext(lines)
        except StopAsyncIteration:
            return None
        except TimeoutError:
            await self._abort_quietly(genkey)
            if remaining <= self._stall_timeout_s:
                raise GenerationTimeoutError(f"the generation ran past {self._total_timeout_s} s") from None
            raise GenerationStalledError(f"the stream sent nothing for {self._stall_timeout_s} s") from None

    async def _abort_quietly(self, genkey: str) -> None:
        """Aborts a generation the adapter gives up on. A failure is ignored: the caller reports the timeout."""
        with contextlib.suppress(TextRuntimeError):
            await self.abort(genkey)

    def _finish(self, genkey: str) -> None:
        self._running.discard(genkey)
        self._aborted.discard(genkey)


def _payload(request: TextRequest) -> dict[str, Any]:
    """Returns the GenerationInput body of `request`: every field that is set, with tuples as JSON arrays."""
    payload: dict[str, Any] = {}
    for field in dataclasses.fields(request):
        value = getattr(request, field.name)
        if value is None or value == ():
            continue
        payload[field.name] = list(value) if isinstance(value, tuple) else value
    return payload


def _event(line: str) -> tuple[str, str | None] | None:
    """Returns the `token` and `finish_reason` of a `data:` line of the stream, or None for any other line.

    Raises RuntimeResponseInvalidError when the data is not the documented JSON object.
    """
    if not line.startswith("data:"):
        return None
    try:
        data = json.loads(line.removeprefix("data:"))
    except ValueError:
        raise RuntimeResponseInvalidError("KoboldCpp sent a stream event that is not JSON") from None
    token = data.get("token") if isinstance(data, dict) else None
    reason = data.get("finish_reason") if isinstance(data, dict) else None
    if not isinstance(token, str) or not (reason is None or reason == "error" or reason in _FINISHED):
        raise RuntimeResponseInvalidError("KoboldCpp sent a stream event without the documented fields")
    return token, reason


def _status_error(path: str, response: httpx.Response) -> TextRuntimeError:
    """Returns the error for a non-200 answer whose body has been read: out of memory when the body says so."""
    if _OOM_MARKER in response.text.lower():
        return RuntimeOutOfMemoryError(f"KoboldCpp ran out of memory on {path}")
    return RuntimeUnavailableError(f"KoboldCpp answered {response.status_code} to {path}")
