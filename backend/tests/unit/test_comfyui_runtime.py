"""Tests of lyrveil/runtimes/comfyui.py against tests/fakes/fake_comfy.py over real HTTP and WebSocket, of its progress
mapping (T-IMG-05), and of its scope."""

import ast
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from fakes.fake_comfy import HISTORY, INTERRUPT, PINNED_VERSION, PROMPT, VIEW, WS, FakeComfy
from fakes.scripted_server import Step, refuses_connections, wait_for
from lyrveil.ports.image_runtime import (
    ImageConnectionLostError,
    ImageEvent,
    ImageOutOfMemoryError,
    ImageProgress,
    ImageRequest,
    ImageRuntime,
    ImageRuntimeUnavailableError,
    RenderFinished,
    RenderStalledError,
    RenderTimeoutError,
    WorkflowRejectedError,
)
from lyrveil.runtimes import comfyui
from lyrveil.runtimes.comfyui import ENDPOINTS, ComfyUiRuntime, ProgressMap

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CONTRACT_NAME = "runtime-contracts.md"
ENDPOINT_ROW = re.compile(r"^\| `(GET|POST|WS) ([^`\s]+)` \|")
PATH_LIKE = re.compile(r"/[a-z_]+(/[a-z_{}]+)*")


def node(class_type: str, **inputs: object) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs}


# The graph of `sdxl_hires.v1.json` in docs/image-generation.md, reduced to the links that decide the phases.
HIRES = {
    "1": node("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
    "5": node("EmptyLatentImage", width=832, height=1216, batch_size=1),
    "6": node("KSampler", model=["1", 0], latent_image=["5", 0], steps=28, denoise=1.0),
    "7": node("VAEDecode", samples=["6", 0], vae=["1", 2]),
    "9": node("ImageScaleBy", image=["7", 0], upscale_method="lanczos", scale_by=1.5),
    "10": node("VAEEncode", pixels=["9", 0], vae=["1", 2]),
    "11": node("KSampler", model=["1", 0], latent_image=["10", 0], steps=14, denoise=0.45),
    "12": node("VAEDecode", samples=["11", 0], vae=["1", 2]),
    "8": node("SaveImage", images=["12", 0], filename_prefix="lyrveil"),
}
# One pass, numbered like the fake, whose sampler messages name node "3".
BASE = {
    "1": node("CheckpointLoaderSimple", ckpt_name="model.safetensors"),
    "2": node("EmptyLatentImage", width=1024, height=1024, batch_size=1),
    "3": node("KSampler", model=["1", 0], latent_image=["2", 0], steps=3, denoise=1.0),
    "4": node("VAEDecode", samples=["3", 0], vae=["1", 2]),
    "9": node("SaveImage", images=["4", 0], filename_prefix="lyrveil"),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake() -> Iterator[FakeComfy]:
    with FakeComfy() as running:
        yield running


def request(prompt_id: str = "job-1", workflow: dict[str, Any] | None = None) -> ImageRequest:
    return ImageRequest(workflow=BASE if workflow is None else workflow, prompt_id=prompt_id)


async def collect(events: AsyncIterator[ImageEvent]) -> list[ImageEvent]:
    return [event async for event in events]


def contract_text() -> str:
    """Returns the contract page: in the repository's docs/, or in /app/docs in the test image."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "docs" / CONTRACT_NAME, here.parents[2] / "docs" / CONTRACT_NAME):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError(f"{CONTRACT_NAME} is in neither candidate folder")


def documented_endpoints() -> set[tuple[str, str]]:
    """Returns the method and path, without the query, of each row of the ComfyUI endpoint table."""
    found: set[tuple[str, str]] = set()
    inside = False
    for line in contract_text().splitlines():
        if line.startswith("## "):
            inside = line.startswith("## ComfyUI")
        if inside and (match := ENDPOINT_ROW.match(line)):
            found.add((match.group(1), match.group(2).split("?", 1)[0]))
    assert found, "no ComfyUI endpoint was read from the contract page: the table format changed"
    return found


def undocumented_paths(source: str, documented: set[tuple[str, str]]) -> list[str]:
    """Returns every string constant of `source` that looks like a runtime path and is not a documented endpoint."""
    paths = {path for _, path in documented}
    constants = [item.value for item in ast.walk(ast.parse(source)) if isinstance(item, ast.Constant)]
    return sorted(
        value
        for value in constants
        if isinstance(value, str) and PATH_LIKE.fullmatch(value) is not None and value not in paths
    )


def test_the_adapter_calls_only_documented_endpoints() -> None:
    documented = documented_endpoints()
    assert set(ENDPOINTS) <= documented
    assert undocumented_paths(Path(comfyui.__file__).read_text(encoding="utf-8"), documented) == []


def test_the_endpoint_check_reports_an_undocumented_path() -> None:
    source = 'STATS = "/system_stats"\nQUEUE = "/queue"\nUPLOAD = "/upload/image"\n'
    assert undocumented_paths(source, documented_endpoints()) == ["/queue", "/upload/image"]


def test_messages_map_to_progress_for_both_passes() -> None:
    """T-IMG-05: the checkpoint load, the first pass and the second pass, from the documented Alta graph."""
    phases = ProgressMap.of(HIRES)
    messages: list[tuple[str, dict[str, Any]]] = [
        ("execution_start", {}),
        ("executing", {"node": "1"}),
        ("executing", {"node": "6"}),
        ("progress", {"node": "6", "value": 12, "max": 28}),
        ("progress", {"node": "11", "value": 6, "max": 14}),
        ("progress", {"node": "11", "value": 15, "max": 14}),
        ("progress", {"node": "11", "value": True, "max": 14}),
        ("progress", {"node": "7", "value": 1, "max": 1}),
        ("progress", {"node": 6, "value": 1, "max": 28}),
        ("executing", {"node": None}),
    ]
    events = [phases.read(kind, data) for kind, data in messages]
    assert [event for event in events if event is not None] == [
        ImageProgress("loading"),
        ImageProgress("sampling", 12, 28),
        ImageProgress("refining", 6, 14),
    ]


def test_a_single_pass_workflow_reports_only_sampling() -> None:
    phases = ProgressMap.of(BASE)
    assert phases.samplers == {"3": "sampling"}
    assert phases.read("progress", {"node": "3", "value": 3, "max": 3}) == ImageProgress("sampling", 3, 3)


def test_frames_of_other_prompts_and_binary_frames_are_skipped() -> None:
    mine = '{"type": "progress", "data": {"prompt_id": "a", "node": "3", "value": 1, "max": 3}}'
    assert comfyui._message(mine, "a") == ("progress", {"prompt_id": "a", "node": "3", "value": 1, "max": 3})
    assert comfyui._message(mine, "b") is None
    assert comfyui._message(b"\x00\x01", "a") is None
    assert comfyui._message('{"type": "status", "data": {"status": {}}}', "a") is None
    assert comfyui._message('{"type": ', "a") is None


@pytest.mark.anyio
async def test_a_render_is_queued_followed_and_downloaded(fake: FakeComfy) -> None:
    async with ComfyUiRuntime(fake.url) as runtime:
        port: ImageRuntime = runtime
        events = await collect(port.render(request()))
        finished = events[-1]
        assert isinstance(finished, RenderFinished)
        image = await port.download(finished.images[0])
    assert events[:-1] == [ImageProgress("loading"), *(ImageProgress("sampling", step, 3) for step in (1, 2, 3))]
    assert finished.status == "success"
    assert [output.filename for output in finished.images] == ["fake_00001_.png"]
    assert image.startswith(PNG_SIGNATURE)
    websocket, submitted = fake.requests[0], fake.requests[1]
    assert (websocket.route, submitted.route) == (WS, PROMPT)
    assert submitted.body == {"prompt": BASE, "client_id": websocket.query["clientId"][0], "prompt_id": "job-1"}
    assert {r.route for r in fake.requests[2:]} == {HISTORY, VIEW}


@pytest.mark.anyio
async def test_an_unparsable_frame_does_not_end_the_render() -> None:
    with FakeComfy() as fake:
        fake.script(WS.key, Step(action="invalid_json"))
        async with ComfyUiRuntime(fake.url) as runtime:
            events = await collect(runtime.render(request()))
    finished = events[-1]
    assert isinstance(finished, RenderFinished)
    assert finished.status == "success"
    assert len([event for event in events if isinstance(event, ImageProgress) and event.phase == "sampling"]) == 3


@pytest.mark.anyio
async def test_an_interrupt_ends_a_running_render() -> None:
    with FakeComfy() as fake:
        fake.script(WS.key, Step(chunk_delay_s=0.1))
        async with ComfyUiRuntime(fake.url) as runtime:
            events = runtime.render(request())
            assert await anext(events) == ImageProgress("loading")
            assert await runtime.interrupt() is True
            rest = await collect(events)
    assert rest[-1] == RenderFinished("interrupted")
    assert INTERRUPT in [r.route for r in fake.requests]


@pytest.mark.anyio
async def test_an_out_of_memory_render_raises_image_oom() -> None:
    with FakeComfy() as fake:
        fake.script(WS.key, Step(action="oom"))
        async with ComfyUiRuntime(fake.url) as runtime:
            with pytest.raises(ImageOutOfMemoryError) as raised:
                await collect(runtime.render(request()))
    assert raised.value.code == "IMAGE_OOM"


@pytest.mark.anyio
async def test_an_out_of_memory_answer_to_the_submission_raises_image_oom() -> None:
    with FakeComfy() as fake:
        fake.script(PROMPT.key, Step(action="oom"))
        async with ComfyUiRuntime(fake.url) as runtime:
            with pytest.raises(ImageOutOfMemoryError):
                await collect(runtime.render(request()))


@pytest.mark.anyio
async def test_a_crash_during_a_render_is_a_lost_connection_and_the_runtime_is_then_unavailable() -> None:
    with FakeComfy() as fake:
        fake.script(WS.key, Step(action="crash", after_chunks=2))
        async with ComfyUiRuntime(fake.url) as runtime:
            received: list[ImageEvent] = []

            async def consume() -> None:
                async for event in runtime.render(request()):
                    received.append(event)

            with pytest.raises(ImageConnectionLostError) as raised:
                await consume()
            assert raised.value.code == "RUNTIME_UNAVAILABLE"
            assert received == [ImageProgress("loading")]
            assert wait_for(lambda: refuses_connections(fake.port))
            with pytest.raises(ImageRuntimeUnavailableError):
                await runtime.info()
            with pytest.raises(ImageRuntimeUnavailableError):
                await collect(runtime.render(request("job-2")))


@pytest.mark.anyio
async def test_a_workflow_refused_at_validation_raises_a_typed_error(fake: FakeComfy) -> None:
    async with ComfyUiRuntime(fake.url) as runtime:
        with pytest.raises(WorkflowRejectedError) as raised:
            await collect(runtime.render(request(workflow={})))
    assert raised.value.code == "INTERNAL"


@pytest.mark.anyio
async def test_a_silent_render_stalls_and_is_interrupted() -> None:
    with FakeComfy() as fake:
        fake.script(WS.key, Step(chunk_delay_s=1.0))
        async with ComfyUiRuntime(fake.url, stall_timeout_s=0.2) as runtime:
            with pytest.raises(RenderStalledError) as raised:
                await collect(runtime.render(request()))
        # The adapter waits for the interrupt's answer before it raises, so the request is already recorded.
        assert INTERRUPT in [r.route for r in fake.requests]
    assert raised.value.code == "GENERATION_STALLED"


@pytest.mark.anyio
async def test_a_render_past_the_total_timeout_fails() -> None:
    with FakeComfy(sampler_steps=20) as fake:
        fake.script(WS.key, Step(chunk_delay_s=0.1))
        async with ComfyUiRuntime(fake.url, total_timeout_s=0.35, stall_timeout_s=5.0) as runtime:
            with pytest.raises(RenderTimeoutError) as raised:
                await collect(runtime.render(request()))
    assert raised.value.code == "GENERATION_TIMEOUT"


@pytest.mark.anyio
async def test_info_reads_the_version_and_the_devices(fake: FakeComfy) -> None:
    async with ComfyUiRuntime(fake.url) as runtime:
        info = await runtime.info()
    assert info.version == PINNED_VERSION == comfyui.PINNED_VERSION
    assert [(device.type, device.vram_total) for device in info.devices] == [("cuda", 12 * 1024**3)]
