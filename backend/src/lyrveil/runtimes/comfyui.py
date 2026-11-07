"""ComfyUiRuntime: the ImageRuntime adapter for ComfyUI v0.43.0, over the endpoints of docs/runtime-contracts.md.

Every request goes to a path of ENDPOINTS, and tests/unit/test_comfyui_runtime.py fails when the module names a path
that the contract page does not list. The HTTP client ignores proxy settings and follows no redirect, and the WebSocket
client ignores proxy settings, so a request only reaches the runtime on the internal network.

A render opens `WS /ws` with its own client identifier before `POST /prompt`, because ComfyUI sends a client's
messages only to a connection that is already open. Progress comes from the messages of docs/image-generation.md
(Progress), mapped by ProgressMap; the outcome comes from the closing message and, on success, from the history.
"""

import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, TypeGuard
from urllib.parse import quote

import anyio
import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from lyrveil.ports.image_runtime import (
    ImageConnectionLostError,
    ImageDevice,
    ImageEvent,
    ImageOutOfMemoryError,
    ImageOutput,
    ImagePhase,
    ImageProgress,
    ImageRequest,
    ImageResponseInvalidError,
    ImageRuntimeError,
    ImageRuntimeInfo,
    ImageRuntimeUnavailableError,
    RenderFailedError,
    RenderFinished,
    RenderStalledError,
    RenderTimeoutError,
    WorkflowRejectedError,
)

# The runtime service on the internal network, on the upstream default port (docs/runtime-contracts.md, Flags).
COMFYUI_URL = "http://lyrveil-image:8188"
# The release that `GET /system_stats` reports in `system.comfyui_version` (docs/technology.md).
PINNED_VERSION = "0.43.0"

SYSTEM_STATS_PATH = "/system_stats"
PROMPT_PATH = "/prompt"
WS_PATH = "/ws"
HISTORY_PATH = "/history/{prompt_id}"
VIEW_PATH = "/view"
INTERRUPT_PATH = "/interrupt"
# Every call the adapter makes: all six endpoints of the ComfyUI table.
ENDPOINTS = frozenset(
    {
        ("GET", SYSTEM_STATS_PATH),
        ("POST", PROMPT_PATH),
        ("WS", WS_PATH),
        ("GET", HISTORY_PATH),
        ("GET", VIEW_PATH),
        ("POST", INTERRUPT_PATH),
    }
)

# docs/gpu-orchestration.md, Timeouts: jobs.image_total_timeout_s and jobs.image_stall_timeout_s.
TOTAL_TIMEOUT_S = 300.0
STALL_TIMEOUT_S = 120.0
# The other calls, and opening the progress connection. The timeout table has no value for them: an adapter choice.
REQUEST_TIMEOUT_S = 10.0
# The history entry may appear after `execution_success`: fake_comfy records it after the message, and ComfyUI's prompt
# worker records it once execution returns (not yet read at v0.43.0). The adapter asks again for this long.
HISTORY_WAIT_S = 5.0
_HISTORY_POLL_S = 0.05
# A final Alta PNG is about 4 to 6 MB (docs/image-generation.md, Persistence). Anything far larger is not our image.
MAX_IMAGE_BYTES = 64 * 1024 * 1024

# docs/runtime-contracts.md: text matching `OutOfMemoryError` in `execution_error` maps to IMAGE_OOM.
_OOM_MARKER = "OutOfMemoryError"
_LOADER_CLASS = "CheckpointLoaderSimple"
_SAMPLER_CLASS = "KSampler"
_EMPTY_LATENT_CLASS = "EmptyLatentImage"
_OUTPUT_TYPE = "output"


@dataclass(frozen=True)
class ProgressMap:
    """Maps the ComfyUI messages of one workflow to ImageProgress, following docs/image-generation.md (Progress).

    `executing` of a checkpoint loader is `loading`. `progress` of a KSampler is `sampling` when the sampler starts
    from an EmptyLatentImage, the first pass, and `refining` otherwise, the second pass of **Alta**. Deriving the
    phases from the graph keeps them right for every workflow file, whatever its node numbers.
    """

    loaders: frozenset[str]
    samplers: dict[str, ImagePhase]

    @classmethod
    def of(cls, workflow: Mapping[str, Any]) -> Self:
        loaders: set[str] = set()
        samplers: dict[str, ImagePhase] = {}
        for node_id, node in workflow.items():
            kind = node.get("class_type") if isinstance(node, dict) else None
            if kind == _LOADER_CLASS:
                loaders.add(node_id)
            elif kind == _SAMPLER_CLASS:
                first_pass = _input_class(workflow, node, "latent_image") == _EMPTY_LATENT_CLASS
                samplers[node_id] = "sampling" if first_pass else "refining"
        return cls(frozenset(loaders), samplers)

    def read(self, kind: str, data: Mapping[str, Any]) -> ImageProgress | None:
        """Returns the progress that a message of type `kind` reports, or None when it reports none.

        A `progress` message whose `value` and `max` are not counts with 0 <= value <= max and max > 0 reports none:
        percentages come only from real step counts.
        """
        node = data.get("node")
        if not isinstance(node, str):
            return None
        if kind == "executing" and node in self.loaders:
            return ImageProgress("loading")
        if kind != "progress" or node not in self.samplers:
            return None
        value, maximum = data.get("value"), data.get("max")
        if _count(value) and _count(maximum) and 0 <= value <= maximum and maximum > 0:
            return ImageProgress(self.samplers[node], value, maximum)
        return None


class ComfyUiRuntime:
    """Renders images with the ComfyUI at `base_url`. Use it as an async context manager, or call `aclose`.

    A render may run for `total_timeout_s`, and may send no message of its own for `stall_timeout_s`; past either, the
    adapter sends `POST /interrupt` and raises. Every other call waits `request_timeout_s`. Errors are the
    ImageRuntimeError subclasses of lyrveil.ports.image_runtime.
    """

    def __init__(
        self,
        base_url: str = COMFYUI_URL,
        *,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
        stall_timeout_s: float = STALL_TIMEOUT_S,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        history_wait_s: float = HISTORY_WAIT_S,
        max_image_bytes: int = MAX_IMAGE_BYTES,
    ) -> None:
        self._base_url = httpx.URL(base_url)
        self._client = httpx.AsyncClient(base_url=base_url, trust_env=False, follow_redirects=False)
        self._total_timeout_s = total_timeout_s
        self._stall_timeout_s = stall_timeout_s
        self._request_timeout_s = request_timeout_s
        self._short_timeout = httpx.Timeout(request_timeout_s)
        self._history_wait_s = history_wait_s
        self._max_image_bytes = max_image_bytes

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.aclose()

    async def render(self, request: ImageRequest) -> AsyncIterator[ImageEvent]:
        """Queues `request.workflow`, yields its progress, then one RenderFinished.

        Messages of other prompts, binary preview frames and frames that are not the documented JSON are skipped:
        progress is informative, and the outcome rests on the closing message. Raises WorkflowRejectedError when
        validation refuses the workflow, ImageOutOfMemoryError or RenderFailedError for an `execution_error`,
        ImageConnectionLostError when the connection ends before the closing message, ImageResponseInvalidError when a
        success lists no output image, and RenderStalledError or RenderTimeoutError past the timeouts.
        """
        progress = ProgressMap.of(request.workflow)
        client_id = uuid.uuid4().hex
        deadline = anyio.current_time() + self._total_timeout_s
        connection = await self._connect(client_id)
        try:
            await self._submit(request, client_id)
            while True:
                kind, data = await self._next_message(connection, request.prompt_id, deadline)
                if kind == "execution_success":
                    yield RenderFinished("success", await self._outputs(request.prompt_id))
                    return
                if kind == "execution_interrupted":
                    yield RenderFinished("interrupted")
                    return
                if kind == "execution_error":
                    raise _execution_error(data)
                event = progress.read(kind, data)
                if event is not None:
                    yield event
        finally:
            with contextlib.suppress(OSError, WebSocketException):
                await connection.close()

    async def download(self, image: ImageOutput) -> bytes:
        """Returns the bytes of `GET /view` for `image`. Raises ImageResponseInvalidError for an empty file or one over
        `max_image_bytes`, which is not read further."""
        params = {"filename": image.filename, "subfolder": image.subfolder, "type": image.type}
        chunks: list[bytes] = []
        size = 0
        try:
            async with self._client.stream("GET", VIEW_PATH, params=params, timeout=self._short_timeout) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise _status_error(VIEW_PATH, response)
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_image_bytes:
                        raise ImageResponseInvalidError(f"ComfyUI sent more than {self._max_image_bytes} bytes")
                    chunks.append(chunk)
        except httpx.RequestError as error:
            raise ImageRuntimeUnavailableError(f"ComfyUI did not answer GET {VIEW_PATH}") from error
        if size == 0:
            raise ImageResponseInvalidError(f"ComfyUI sent an empty file to GET {VIEW_PATH}")
        return b"".join(chunks)

    async def interrupt(self) -> bool:
        """Sends `POST /interrupt`. Returns whether ComfyUI answered 200.

        A running render then ends with RenderFinished "interrupted". ComfyUI stops whatever executes, which the image
        lease keeps to this adapter's render.
        """
        try:
            response = await self._client.post(INTERRUPT_PATH, timeout=self._short_timeout)
        except httpx.RequestError as error:
            raise ImageRuntimeUnavailableError(f"ComfyUI did not answer POST {INTERRUPT_PATH}") from error
        return response.status_code == 200

    async def info(self) -> ImageRuntimeInfo:
        """Returns `system.comfyui_version` and the devices of `GET /system_stats`.

        Comparing the version with PINNED_VERSION is the caller's decision.
        """
        body = await self._call("GET", SYSTEM_STATS_PATH)
        system = body.get("system")
        version = system.get("comfyui_version") if isinstance(system, dict) else None
        devices = body.get("devices")
        if not isinstance(version, str) or not isinstance(devices, list):
            raise ImageResponseInvalidError(f"ComfyUI sent no version or devices to {SYSTEM_STATS_PATH}")
        return ImageRuntimeInfo(version=version, devices=tuple(_device(entry) for entry in devices))

    async def _connect(self, client_id: str) -> ClientConnection:
        """Opens `WS /ws` for `client_id`. Raises ImageRuntimeUnavailableError when the handshake does not complete."""
        scheme = "wss" if self._base_url.scheme == "https" else "ws"
        url = self._base_url.copy_with(scheme=scheme, path=WS_PATH, params={"clientId": client_id})
        try:
            # No keepalive pings: the stall timeout already detects a silent runtime.
            return await connect(
                str(url),
                open_timeout=self._request_timeout_s,
                close_timeout=self._request_timeout_s,
                ping_interval=None,
                proxy=None,
            )
        except (OSError, WebSocketException) as error:
            raise ImageRuntimeUnavailableError(f"ComfyUI did not open WS {WS_PATH}") from error

    async def _submit(self, request: ImageRequest, client_id: str) -> None:
        """Sends `POST /prompt`. Raises WorkflowRejectedError for a validation failure, and the error of
        `_status_error` or ImageResponseInvalidError for another unexpected answer."""
        body = {"prompt": request.workflow, "client_id": client_id, "prompt_id": request.prompt_id}
        try:
            response = await self._client.post(PROMPT_PATH, json=body, timeout=self._short_timeout)
        except httpx.RequestError as error:
            raise ImageRuntimeUnavailableError(f"ComfyUI did not answer POST {PROMPT_PATH}") from error
        if response.status_code == 400:
            answer = _json_object(response, PROMPT_PATH)
            errors = answer.get("node_errors")
            if "error" not in answer or not isinstance(errors, dict):
                raise ImageResponseInvalidError(f"ComfyUI refused POST {PROMPT_PATH} without the documented fields")
            nodes = tuple(sorted(str(node) for node in errors))
            raise WorkflowRejectedError(f"ComfyUI refused the workflow at validation, nodes {list(nodes)}", nodes)
        if response.status_code != 200:
            raise _status_error(PROMPT_PATH, response)
        if _json_object(response, PROMPT_PATH).get("prompt_id") != request.prompt_id:
            raise ImageResponseInvalidError(f"ComfyUI queued another prompt_id than POST {PROMPT_PATH} named")

    async def _next_message(
        self, connection: ClientConnection, prompt_id: str, deadline: float
    ) -> tuple[str, dict[str, Any]]:
        """Returns the type and data of the next message of `prompt_id`. Past the stall wait, which only a message of
        `prompt_id` restarts, or the total deadline, sends `POST /interrupt` and raises RenderStalledError or
        RenderTimeoutError."""
        stall_deadline = anyio.current_time() + self._stall_timeout_s
        while True:
            try:
                with anyio.fail_after(max(min(stall_deadline, deadline) - anyio.current_time(), 0.0)):
                    frame = await connection.recv()
            except TimeoutError:
                await self._interrupt_quietly()
                if deadline <= stall_deadline:
                    raise RenderTimeoutError(f"the render ran past {self._total_timeout_s} s") from None
                raise RenderStalledError(f"the render sent nothing for {self._stall_timeout_s} s") from None
            except ConnectionClosed as error:
                raise ImageConnectionLostError("the ComfyUI connection ended before the render finished") from error
            message = _message(frame, prompt_id)
            if message is not None:
                return message

    async def _outputs(self, prompt_id: str) -> tuple[ImageOutput, ...]:
        """Returns the output images of `GET /history/{prompt_id}`, asking again for `history_wait_s` while the entry
        is missing. Raises ImageResponseInvalidError when it stays missing or lists no output image."""
        path = HISTORY_PATH.format(prompt_id=quote(prompt_id, safe=""))
        give_up = anyio.current_time() + self._history_wait_s
        while True:
            entry = (await self._call("GET", path)).get(prompt_id)
            if entry is not None:
                break
            if anyio.current_time() >= give_up:
                raise ImageResponseInvalidError(f"ComfyUI has no history entry for the render after {path}")
            await anyio.sleep(_HISTORY_POLL_S)
        outputs = entry.get("outputs") if isinstance(entry, dict) else None
        images = [
            image
            for node in (outputs.values() if isinstance(outputs, dict) else ())
            for image in _node_images(node)
            if image.type == _OUTPUT_TYPE
        ]
        if not images:
            raise ImageResponseInvalidError("ComfyUI's history lists no output image for the render")
        return tuple(images)

    async def _call(self, method: str, path: str) -> dict[str, Any]:
        """Returns the JSON object of a 200 answer. Raises ImageRuntimeUnavailableError when no answer arrives, and the
        error of `_status_error` or ImageResponseInvalidError for another answer."""
        try:
            response = await self._client.request(method, path, timeout=self._short_timeout)
        except httpx.RequestError as error:
            raise ImageRuntimeUnavailableError(f"ComfyUI did not answer {method} {path}") from error
        if response.status_code != 200:
            raise _status_error(path, response)
        return _json_object(response, path)

    async def _interrupt_quietly(self) -> None:
        """Interrupts a render the adapter gives up on. A failure is ignored: the caller reports the timeout."""
        with contextlib.suppress(ImageRuntimeError):
            await self.interrupt()


def _count(value: object) -> TypeGuard[int]:
    """Returns True for an int that is not a bool. JSON `true` parses to a bool, which Python counts as an int."""
    return isinstance(value, int) and not isinstance(value, bool)


def _input_class(workflow: Mapping[str, Any], node: Mapping[str, Any], name: str) -> object:
    """Returns the `class_type` of the node that input `name` of `node` links to, or None for a value or a bad link."""
    inputs = node.get("inputs")
    link = inputs.get(name) if isinstance(inputs, dict) else None
    if not isinstance(link, list | tuple) or len(link) != 2 or not isinstance(link[0], str):
        return None
    source = workflow.get(link[0])
    return source.get("class_type") if isinstance(source, dict) else None


def _message(frame: str | bytes, prompt_id: str) -> tuple[str, dict[str, Any]] | None:
    """Returns the type and data of a text frame about `prompt_id`, or None for any other frame."""
    if not isinstance(frame, str):
        return None
    try:
        message = json.loads(frame)
    except ValueError:
        return None
    kind = message.get("type") if isinstance(message, dict) else None
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(kind, str) or not isinstance(data, dict) or data.get("prompt_id") != prompt_id:
        return None
    return kind, data


def _execution_error(data: Mapping[str, Any]) -> ImageRuntimeError:
    """Returns the error for an `execution_error`: out of memory when its exception names OutOfMemoryError."""
    exception = f"{data.get('exception_type', '')} {data.get('exception_message', '')}"
    node = data.get("node_id")
    if _OOM_MARKER in exception:
        return ImageOutOfMemoryError(f"ComfyUI ran out of memory in node {node}")
    return RenderFailedError(f"ComfyUI failed in node {node} with {data.get('exception_type')}")


def _node_images(node: object) -> list[ImageOutput]:
    """Returns the `images` of one output node of the history. Raises ImageResponseInvalidError for a malformed one."""
    entries = node.get("images", []) if isinstance(node, dict) else []
    if not isinstance(entries, list):
        raise ImageResponseInvalidError("ComfyUI's history lists images that are not a list")
    images: list[ImageOutput] = []
    for entry in entries:
        fields = entry if isinstance(entry, dict) else {}
        filename, subfolder, kind = fields.get("filename"), fields.get("subfolder"), fields.get("type")
        if not (isinstance(filename, str) and isinstance(subfolder, str) and isinstance(kind, str)):
            raise ImageResponseInvalidError("ComfyUI's history lists an image without the documented fields")
        images.append(ImageOutput(filename=filename, subfolder=subfolder, type=kind))
    return images


def _device(entry: object) -> ImageDevice:
    """Returns one device of `/system_stats`. Raises ImageResponseInvalidError when a field is missing."""
    fields = entry if isinstance(entry, dict) else {}
    name, kind = fields.get("name"), fields.get("type")
    total, free = fields.get("vram_total"), fields.get("vram_free")
    if isinstance(name, str) and isinstance(kind, str) and _count(total) and _count(free):
        return ImageDevice(name=name, type=kind, vram_total=total, vram_free=free)
    raise ImageResponseInvalidError(f"ComfyUI sent a device without the documented fields to {SYSTEM_STATS_PATH}")


def _json_object(response: httpx.Response, path: str) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        raise ImageResponseInvalidError(f"ComfyUI sent invalid JSON to {path}") from None
    if not isinstance(parsed, dict):
        raise ImageResponseInvalidError(f"ComfyUI sent no JSON object to {path}")
    return parsed


def _status_error(path: str, response: httpx.Response) -> ImageRuntimeError:
    """Returns the error for a non-200 answer whose body has been read: out of memory when the body names it."""
    if _OOM_MARKER in response.text:
        return ImageOutOfMemoryError(f"ComfyUI ran out of memory on {path}")
    return ImageRuntimeUnavailableError(f"ComfyUI answered {response.status_code} to {path}")
