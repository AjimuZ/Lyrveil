"""Tests of lyrveil/runtimes/koboldcpp.py against tests/fakes/fake_kobold.py over real HTTP, and of its scope."""

import ast
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from fakes.fake_kobold import ABORT, GENERATE, PINNED_VERSION, STREAM, TOKEN_COUNT, FakeKobold
from fakes.scripted_server import Action, Route, Step, refuses_connections, wait_for
from lyrveil.ports.text_runtime import (
    GenerationStalledError,
    GenerationTimeoutError,
    RuntimeOutOfMemoryError,
    RuntimeResponseInvalidError,
    RuntimeStreamDroppedError,
    RuntimeUnavailableError,
    TextChunk,
    TextRequest,
    TextRuntime,
    TextRuntimeError,
)
from lyrveil.runtimes import koboldcpp
from lyrveil.runtimes.koboldcpp import ENDPOINTS, KoboldCppRuntime

REPLY = "Ciao, sono qui."
LONG_REPLY = "uno due tre quattro cinque sei sette otto nove dieci"
CONTRACT_NAME = "runtime-contracts.md"
ENDPOINT_ROW = re.compile(r"^\| `(GET|POST|WS) ([^`\s]+)` \|")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake() -> Iterator[FakeKobold]:
    with FakeKobold(reply_text=REPLY) as running:
        yield running


def request(genkey: str = "job-1") -> TextRequest:
    return TextRequest(prompt="Ciao", max_context_length=8192, max_length=64, genkey=genkey)


async def collect(chunks: AsyncIterator[TextChunk]) -> list[TextChunk]:
    return [chunk async for chunk in chunks]


async def call(runtime: KoboldCppRuntime, route: Route) -> object:
    """Runs the adapter method that requests `route`: a whole stream, a token count or a generation."""
    if route == STREAM:
        return await collect(runtime.stream(request()))
    if route == TOKEN_COUNT:
        return await runtime.count_tokens("uno")
    return await runtime.generate(request())


def contract_text() -> str:
    """Returns the contract page: in the repository's docs/, or in /app/docs in the test image."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "docs" / CONTRACT_NAME, here.parents[2] / "docs" / CONTRACT_NAME):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError(f"{CONTRACT_NAME} is in neither candidate folder")


def kobold_section() -> list[str]:
    """Returns the lines of the `## KoboldCpp` part of the contract page."""
    lines: list[str] = []
    inside = False
    for line in contract_text().splitlines():
        if line.startswith("## "):
            inside = line.startswith("## KoboldCpp")
        if inside:
            lines.append(line)
    return lines


def documented_endpoints() -> set[tuple[str, str]]:
    found = {(match.group(1), match.group(2)) for line in kobold_section() if (match := ENDPOINT_ROW.match(line))}
    assert found, "no KoboldCpp endpoint was read from the contract page: the table format changed"
    return found


def documented_generation_fields() -> set[str]:
    """Returns the field names of the `GenerationInput fields used` table."""
    fields: set[str] = set()
    inside = False
    for line in kobold_section():
        if line.startswith("### "):
            inside = line.startswith("### GenerationInput")
        if inside and line.startswith("| `"):
            fields.update(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    assert fields, "no GenerationInput field was read from the contract page: the table format changed"
    return fields


def undocumented_paths(source: str, documented: set[tuple[str, str]]) -> list[str]:
    """Returns every string constant of `source` that looks like a runtime path and is not a documented endpoint."""
    paths = {path for _, path in documented}
    constants = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)]
    return sorted(
        value for value in constants if isinstance(value, str) and value.startswith("/api/") and value not in paths
    )


def test_the_adapter_calls_only_documented_endpoints() -> None:
    documented = documented_endpoints()
    assert set(ENDPOINTS) <= documented
    assert undocumented_paths(Path(koboldcpp.__file__).read_text(encoding="utf-8"), documented) == []


def test_the_endpoint_check_reports_an_undocumented_path() -> None:
    source = 'MODEL = "/api/v1/model"\nSEARCH = "/api/extra/websearch"\n'
    assert undocumented_paths(source, documented_endpoints()) == ["/api/extra/websearch"]


def test_a_full_request_sends_only_documented_generation_fields() -> None:
    full = TextRequest(
        prompt="p",
        max_context_length=8192,
        max_length=64,
        genkey="g",
        temperature=1.0,
        top_p=0.95,
        top_k=64,
        min_p=0.05,
        rep_pen=1.05,
        rep_pen_range=320,
        dry_multiplier=0.8,
        dry_base=1.75,
        dry_allowed_length=2,
        dry_penalty_last_n=320,
        dry_sequence_breakers=("\n",),
        stop_sequence=("<turn|>",),
        trim_stop=True,
        grammar="root ::= x",
        sampler_seed=7,
    )
    payload = koboldcpp._payload(full)
    assert set(payload) <= documented_generation_fields()
    assert payload["stop_sequence"] == ["<turn|>"]
    minimal = {"prompt": "Ciao", "max_context_length": 8192, "max_length": 64, "genkey": "job-1"}
    assert koboldcpp._payload(request()) == minimal


@pytest.mark.anyio
async def test_a_stream_yields_the_tokens_in_order_then_the_final_text(fake: FakeKobold) -> None:
    async with KoboldCppRuntime(fake.url) as runtime:
        port: TextRuntime = runtime
        chunks = await collect(port.stream(request()))
    assert [chunk.token for chunk in chunks] == ["Ciao,", " sono", " qui.", ""]
    assert [chunk.final for chunk in chunks] == [False, False, False, True]
    assert (chunks[-1].finish_reason, chunks[-1].text) == ("stop", REPLY)
    assert fake.requests[0].route == STREAM
    assert fake.requests[0].body == koboldcpp._payload(request())


@pytest.mark.anyio
async def test_an_abort_stops_a_running_stream() -> None:
    with FakeKobold(reply_text=LONG_REPLY) as fake:
        fake.script(STREAM.key, Step(chunk_delay_s=0.1))
        async with KoboldCppRuntime(fake.url) as runtime:
            chunks = runtime.stream(request("job-2"))
            first = await anext(chunks)
            assert first.token == "uno"
            assert await runtime.abort("job-2") is True
            rest = await collect(chunks)
    final = rest[-1]
    assert final.finish_reason == "aborted"
    assert final.text is not None
    assert len(final.text.split()) < len(LONG_REPLY.split())
    assert fake.requests[-1].route == ABORT
    assert fake.requests[-1].body == {"genkey": "job-2"}


@pytest.mark.anyio
async def test_a_dropped_stream_raises_a_typed_error() -> None:
    with FakeKobold(reply_text=REPLY) as fake:
        fake.script(STREAM.key, Step(action="crash", after_chunks=2))
        async with KoboldCppRuntime(fake.url) as runtime:
            received: list[str] = []

            async def consume() -> None:
                async for chunk in runtime.stream(request()):
                    received.append(chunk.token)

            with pytest.raises(RuntimeStreamDroppedError):
                await consume()
    assert received == ["Ciao,", " sono"]


@pytest.mark.anyio
async def test_a_silent_stream_stalls_and_is_aborted() -> None:
    with FakeKobold(reply_text=LONG_REPLY) as fake:
        fake.script(STREAM.key, Step(chunk_delay_s=1.0))
        async with KoboldCppRuntime(fake.url, stall_timeout_s=0.2) as runtime:
            with pytest.raises(GenerationStalledError) as raised:
                await collect(runtime.stream(request("job-3")))
        # The adapter waits for the abort's answer before it raises, so the request is already recorded.
        assert [r.body for r in fake.requests if r.route == ABORT] == [{"genkey": "job-3"}]
    assert raised.value.code == "GENERATION_STALLED"


@pytest.mark.anyio
async def test_a_stream_past_the_total_timeout_fails() -> None:
    with FakeKobold(reply_text=LONG_REPLY) as fake:
        fake.script(STREAM.key, Step(chunk_delay_s=0.1))
        async with KoboldCppRuntime(fake.url, total_timeout_s=0.35, stall_timeout_s=5.0) as runtime:
            with pytest.raises(GenerationTimeoutError) as raised:
                await collect(runtime.stream(request()))
    assert raised.value.code == "GENERATION_TIMEOUT"


@pytest.mark.anyio
async def test_token_counts_come_from_the_runtime(fake: FakeKobold) -> None:
    async with KoboldCppRuntime(fake.url) as runtime:
        # The fake counts words, plus one start token when `special` is true: no estimate gives these numbers.
        assert await runtime.count_tokens("uno due tre") == 3
        assert await runtime.count_tokens("uno due tre", special=True) == 4
    assert [r.route for r in fake.requests] == [TOKEN_COUNT, TOKEN_COUNT]
    assert fake.requests[0].body == {"prompt": "uno due tre", "special": False}


@pytest.mark.anyio
async def test_generate_and_info_read_the_documented_fields(fake: FakeKobold) -> None:
    async with KoboldCppRuntime(fake.url) as runtime:
        assert await runtime.generate(request()) == REPLY
        info = await runtime.info()
    assert (info.model, info.version, info.max_context_length) == ("fake-model.gguf", PINNED_VERSION, 8192)
    assert fake.requests[0].route == GENERATE


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route", "action", "error"),
    [
        (GENERATE, "oom", RuntimeOutOfMemoryError),
        (STREAM, "oom", RuntimeOutOfMemoryError),
        (TOKEN_COUNT, "oom", RuntimeOutOfMemoryError),
        (GENERATE, "invalid_json", RuntimeResponseInvalidError),
        (STREAM, "invalid_json", RuntimeResponseInvalidError),
        (GENERATE, "crash", RuntimeUnavailableError),
        (STREAM, "crash", RuntimeUnavailableError),
        (TOKEN_COUNT, "crash", RuntimeUnavailableError),
    ],
)
async def test_failed_replies_map_to_the_documented_errors(
    route: Route, action: Action, error: type[TextRuntimeError]
) -> None:
    with FakeKobold(reply_text=REPLY) as fake:
        fake.script(route.key, Step(action=action))
        async with KoboldCppRuntime(fake.url) as runtime:
            with pytest.raises(error) as raised:
                await call(runtime, route)
    assert raised.value.code == ("LLM_OOM" if action == "oom" else "RUNTIME_UNAVAILABLE")


@pytest.mark.anyio
async def test_a_runtime_that_crashed_is_unavailable() -> None:
    with FakeKobold() as fake:
        fake.script(GENERATE.key, Step(action="crash"))
        async with KoboldCppRuntime(fake.url) as runtime:
            with pytest.raises(RuntimeUnavailableError):
                await runtime.generate(request())
            assert wait_for(lambda: refuses_connections(fake.port))
            with pytest.raises(RuntimeUnavailableError):
                await runtime.count_tokens("uno")
