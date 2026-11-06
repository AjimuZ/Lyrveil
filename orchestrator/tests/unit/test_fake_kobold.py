"""Tests of tests/fakes/fake_kobold.py: each endpoint of the contract and each scripted behavior, over real HTTP."""

import json
import time
from collections.abc import Iterator

import httpx
import pytest

from fakes.fake_kobold import (
    ABORT,
    GENERATE,
    MAX_CONTEXT,
    MODEL,
    PERF,
    PINNED_VERSION,
    STREAM,
    TOKEN_COUNT,
    VERSION,
    FakeKobold,
)
from fakes.scripted_server import Step, refuses_connections, wait_for

REPLY = "Ciao, sono qui."
FULL_STREAM = ["Ciao,", " sono", " qui."]


@pytest.fixture
def fake() -> Iterator[FakeKobold]:
    with FakeKobold(reply_text=REPLY) as running:
        yield running


def events(response: httpx.Response) -> list[dict[str, object]]:
    """Decodes the `data` lines of an SSE answer, failing on an event that is not `event: message`."""
    lines = [line for line in response.text.split("\n") if line]
    assert all(line in {"event: message"} or line.startswith("data: ") for line in lines)
    return [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")]


def post(fake: FakeKobold, route: str, **body: object) -> httpx.Response:
    return httpx.post(f"{fake.url}{route}", json=body)


def test_the_get_endpoints_answer_with_the_contract_fields(fake: FakeKobold) -> None:
    assert httpx.get(f"{fake.url}{MODEL.pattern}").json() == {"result": "koboldcpp/fake-model.gguf"}
    version = httpx.get(f"{fake.url}{VERSION.pattern}").json()
    assert version["version"] == PINNED_VERSION
    assert isinstance(version["result"], str)
    assert httpx.get(f"{fake.url}{MAX_CONTEXT.pattern}").json() == {"value": 8192}
    perf = httpx.get(f"{fake.url}{PERF.pattern}").json()
    expected = {"last_process", "last_eval", "last_token_count", "stop_reason", "queue", "idle", "uptime"}
    assert expected <= perf.keys()


def test_the_token_count_adds_a_start_token_unless_special_is_false(fake: FakeKobold) -> None:
    with_start = post(fake, TOKEN_COUNT.pattern, prompt="uno due tre").json()
    without = post(fake, TOKEN_COUNT.pattern, prompt="uno due tre", special=False).json()
    assert (with_start["value"], without["value"]) == (4, 3)
    assert len(without["ids"]) == 3
    assert without["prompt"] == "uno due tre"


def test_a_structured_generation_returns_the_reply_text(fake: FakeKobold) -> None:
    assert post(fake, GENERATE.pattern, prompt="Ciao").json() == {"results": [{"text": REPLY}]}


def test_a_stream_sends_one_event_per_word_then_a_closing_event(fake: FakeKobold) -> None:
    response = post(fake, STREAM.pattern, prompt="Ciao", genkey="g1")
    assert response.headers["content-type"] == "text/event-stream"
    parsed = events(response)
    assert [event["token"] for event in parsed] == [*FULL_STREAM, ""]
    assert [event["finish_reason"] for event in parsed] == [None, None, None, "stop"]


def test_an_abort_ends_the_running_stream_with_a_stop_event() -> None:
    with FakeKobold(reply_text="uno due tre quattro cinque") as fake:
        fake.script(STREAM.key, Step(chunk_delay_s=0.1))
        with httpx.stream("POST", f"{fake.url}{STREAM.pattern}", json={"prompt": "x", "genkey": "g2"}) as response:
            lines = response.iter_lines()
            first = next(line for line in lines if line.startswith("data: "))
            assert json.loads(first.removeprefix("data: ")) == {"token": "uno", "finish_reason": None}
            answer = post(fake, ABORT.pattern, genkey="g2").json()
            assert answer == {"success": "true", "done": "true"}
            rest = [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")]
    assert rest[-1] == {"token": "", "finish_reason": "stop"}
    assert len(rest) < 5


@pytest.mark.parametrize("path", [TOKEN_COUNT.pattern, GENERATE.pattern, STREAM.pattern, ABORT.pattern])
def test_a_body_that_is_not_a_json_object_is_refused(fake: FakeKobold, path: str) -> None:
    assert httpx.post(f"{fake.url}{path}", content=b"not json").status_code == 400
    assert httpx.post(f"{fake.url}{path}", json={"prompt": 3, "genkey": 3}).status_code == 422


def test_a_scripted_body_and_status_replace_the_answer(fake: FakeKobold) -> None:
    fake.script(MODEL.key, Step(status=503, body={"detail": "loading"}))
    assert httpx.get(f"{fake.url}{MODEL.pattern}").status_code == 503
    assert httpx.get(f"{fake.url}{MODEL.pattern}").json() == {"result": "koboldcpp/fake-model.gguf"}


def test_a_step_is_used_once_and_in_order(fake: FakeKobold) -> None:
    fake.script(PERF.key, Step(status=500), Step(status=429))
    statuses = [httpx.get(f"{fake.url}{PERF.pattern}").status_code for _ in range(3)]
    assert statuses == [500, 429, 200]


def test_a_delay_holds_the_answer_back(fake: FakeKobold) -> None:
    fake.script(GENERATE.key, Step(delay_s=0.3))
    started = time.monotonic()
    post(fake, GENERATE.pattern, prompt="x")
    assert time.monotonic() - started >= 0.25


def test_invalid_json_cuts_the_answer_short(fake: FakeKobold) -> None:
    fake.script(GENERATE.key, Step(action="invalid_json"))
    fake.script(STREAM.key, Step(action="invalid_json"))
    response = post(fake, GENERATE.pattern, prompt="x")
    assert response.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        response.json()
    stream = post(fake, STREAM.pattern, prompt="x")
    with pytest.raises(json.JSONDecodeError):
        json.loads(stream.text.split("data: ")[1])


def test_out_of_memory_fails_a_generation_and_ends_a_stream_with_an_error(fake: FakeKobold) -> None:
    fake.script(GENERATE.key, Step(action="oom"))
    fake.script(STREAM.key, Step(action="oom"))
    failed = post(fake, GENERATE.pattern, prompt="x")
    assert failed.status_code == 500
    assert "out of memory" in failed.text
    assert [event["finish_reason"] for event in events(post(fake, STREAM.pattern, prompt="x"))] == ["error"]


def test_a_crash_drops_the_connection_and_stops_the_fake() -> None:
    with FakeKobold() as fake:
        fake.script(GENERATE.key, Step(action="crash"))
        with pytest.raises(httpx.TransportError):
            post(fake, GENERATE.pattern, prompt="x")
        assert wait_for(lambda: fake.crashed)
        assert wait_for(lambda: refuses_connections(fake.port))


def test_a_crash_in_a_stream_lets_the_scripted_events_through_without_a_closing_event() -> None:
    with FakeKobold(reply_text=REPLY) as fake:
        fake.script(STREAM.key, Step(action="crash", after_chunks=2))
        parsed = events(post(fake, STREAM.pattern, prompt="x"))
        assert [event["token"] for event in parsed] == FULL_STREAM[:2]
        assert wait_for(lambda: fake.crashed)


def test_a_script_can_come_from_json_as_the_container_does() -> None:
    with FakeKobold() as fake:
        fake.load_script('{"POST /api/v1/generate": [{"action": "oom"}, {"status": 418, "body": {"x": 1}}]}')
        assert post(fake, GENERATE.pattern, prompt="x").status_code == 500
        assert post(fake, GENERATE.pattern, prompt="x").status_code == 418


@pytest.mark.parametrize("text", ["[]", '{"GET /nope": []}', '{"GET /api/v1/model": 3}'])
def test_a_script_for_a_missing_route_or_of_the_wrong_shape_is_refused(text: str) -> None:
    with FakeKobold() as fake, pytest.raises(ValueError, match=r"script|route|list"):
        fake.load_script(text)


def test_requests_are_recorded_in_order_with_their_bodies(fake: FakeKobold) -> None:
    httpx.get(f"{fake.url}{MODEL.pattern}")
    post(fake, GENERATE.pattern, prompt="Ciao", max_length=8)
    assert [request.route for request in fake.requests] == [MODEL, GENERATE]
    assert fake.requests[1].body == {"prompt": "Ciao", "max_length": 8}
