"""Tests of tests/fakes/fake_comfy.py: each endpoint and each scripted behavior, over HTTP and WebSocket."""

import base64
import json
import os
import socket
import struct
import time
from collections.abc import Iterator
from typing import BinaryIO

import httpx
import pytest

from fakes.fake_comfy import HISTORY, INTERRUPT, PROMPT, SYSTEM_STATS, VIEW, WS, FakeComfy
from fakes.scripted_server import Step, refuses_connections, wait_for

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SUCCESS = [
    "execution_start",
    "executing",
    "progress",
    "progress",
    "progress",
    "executing",
    "execution_success",
]
TERMINAL = {"execution_success", "execution_error", "execution_interrupted"}


class Client:
    """A WebSocket client that reads the fake's text frames. It sends nothing after the handshake."""

    def __init__(self, port: int, client_id: str) -> None:
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /ws?clientId={client_id} HTTP/1.1\r\nHost: fake\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        self._stream: BinaryIO = self._socket.makefile("rb")
        status = self._stream.readline()
        assert status.startswith(b"HTTP/1.1 101"), status
        headers = []
        while (line := self._stream.readline()) not in {b"\r\n", b""}:
            headers.append(line.decode("ascii").lower())
        assert any(line.startswith("upgrade: websocket") for line in headers)

    def close(self) -> None:
        self._stream.close()
        self._socket.close()

    def frame(self) -> str | None:
        """Returns the next text frame, or None when the server closed the connection."""
        header = self._stream.read(2)
        if len(header) < 2:
            return None
        length = header[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._stream.read(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._stream.read(8))
        return self._stream.read(length).decode("utf-8")

    def frames_until_end(self) -> list[str]:
        """Returns the frames up to and including a terminal message, or up to the end of the connection."""
        frames: list[str] = []
        while (frame := self.frame()) is not None:
            frames.append(frame)
            try:
                if json.loads(frame)["type"] in TERMINAL:
                    break
            except ValueError:
                continue
        return frames


@pytest.fixture
def fake() -> Iterator[FakeComfy]:
    with FakeComfy() as running:
        yield running


def submit(fake: FakeComfy, prompt_id: str = "p1", client_id: str = "c1", **extra: object) -> httpx.Response:
    body = {"prompt": {"1": {"class_type": "CheckpointLoaderSimple"}}, "client_id": client_id, "prompt_id": prompt_id}
    return httpx.post(f"{fake.url}{PROMPT.pattern}", json=body | extra)


def types(frames: list[str]) -> list[str]:
    return [json.loads(frame)["type"] for frame in frames]


def test_system_stats_reports_the_version_and_the_card(fake: FakeComfy) -> None:
    stats = httpx.get(f"{fake.url}{SYSTEM_STATS.pattern}").json()
    assert stats["system"]["comfyui_version"] == "0.43.0"
    assert stats["devices"][0]["vram_total"] >= stats["devices"][0]["vram_free"] > 0


def test_a_submitted_prompt_plays_the_documented_messages_and_reaches_the_history(fake: FakeComfy) -> None:
    client = Client(fake.port, "c1")
    answer = submit(fake).json()
    assert (answer["prompt_id"], answer["number"]) == ("p1", 1)
    frames = client.frames_until_end()
    client.close()
    assert types(frames) == SUCCESS
    assert [json.loads(frame)["data"]["prompt_id"] for frame in frames] == ["p1"] * len(SUCCESS)
    assert json.loads(frames[1])["data"]["node"] == "1"
    assert json.loads(frames[-2])["data"]["node"] is None
    assert [json.loads(frame)["data"]["value"] for frame in frames if '"progress"' in frame] == [1, 2, 3]
    assert wait_for(lambda: "p1" in fake.history)
    history = httpx.get(f"{fake.url}/history/p1").json()
    image = history["p1"]["outputs"]["9"]["images"][0]
    assert set(image) == {"filename", "subfolder", "type"}
    view = httpx.get(f"{fake.url}{VIEW.pattern}", params=image)
    assert view.content.startswith(PNG_SIGNATURE)


def test_the_history_of_an_unknown_prompt_is_empty_and_an_unknown_file_is_not_found(fake: FakeComfy) -> None:
    assert httpx.get(f"{fake.url}/history/nope").json() == {}
    assert httpx.get(f"{fake.url}{VIEW.pattern}", params={"filename": "other.png"}).status_code == 404


def test_the_prompt_ids_and_numbers_follow_the_submissions(fake: FakeComfy) -> None:
    first = submit(fake, prompt_id="a").json()["number"]
    second = httpx.post(f"{fake.url}{PROMPT.pattern}", json={"prompt": {"1": {}}, "client_id": "c1"}).json()
    assert (first, second["number"]) == (1, 2)
    assert len(second["prompt_id"]) == 36


def test_a_prompt_without_nodes_fails_validation(fake: FakeComfy) -> None:
    response = httpx.post(f"{fake.url}{PROMPT.pattern}", json={"prompt": {}, "client_id": "c1"})
    assert response.status_code == 400
    assert {"error", "node_errors"} <= response.json().keys()


def test_an_out_of_memory_job_ends_with_an_execution_error_that_names_the_exception(fake: FakeComfy) -> None:
    fake.script(WS.key, Step(action="oom"))
    client = Client(fake.port, "c1")
    submit(fake)
    frames = client.frames_until_end()
    client.close()
    assert types(frames) == ["execution_start", "executing", "execution_error"]
    assert "OutOfMemoryError" in json.loads(frames[-1])["data"]["exception_type"]
    assert wait_for(lambda: "p1" in fake.history)
    assert httpx.get(f"{fake.url}/history/p1").json()["p1"]["status"]["status_str"] == "error"


def test_invalid_json_adds_one_unparsable_frame_to_a_job_that_still_succeeds(fake: FakeComfy) -> None:
    fake.script(WS.key, Step(action="invalid_json"))
    client = Client(fake.port, "c1")
    submit(fake)
    frames = client.frames_until_end()
    client.close()
    broken = []
    for frame in frames:
        try:
            json.loads(frame)
        except ValueError:
            broken.append(frame)
    assert len(broken) == 1
    assert types([frame for frame in frames if frame not in broken]) == SUCCESS


def test_a_crash_in_a_job_ends_the_connection_after_the_scripted_messages_and_stops_the_fake(fake: FakeComfy) -> None:
    fake.script(WS.key, Step(action="crash", after_chunks=2))
    client = Client(fake.port, "c1")
    submit(fake)
    assert types(client.frames_until_end()) == ["execution_start", "executing"]
    client.close()
    assert wait_for(lambda: fake.crashed)
    assert wait_for(lambda: refuses_connections(fake.port))


def test_delays_hold_back_the_first_message_and_space_the_others(fake: FakeComfy) -> None:
    fake.script(WS.key, Step(delay_s=0.2, chunk_delay_s=0.05))
    client = Client(fake.port, "c1")
    started = time.monotonic()
    submit(fake)
    client.frame()
    assert time.monotonic() - started >= 0.15
    rest = client.frames_until_end()
    client.close()
    assert time.monotonic() - started >= 0.2 + 0.05 * (len(SUCCESS) - 2)
    assert types(rest)[-1] == "execution_success"


def test_an_interrupt_ends_the_running_job_with_execution_interrupted(fake: FakeComfy) -> None:
    fake.script(WS.key, Step(chunk_delay_s=0.1))
    client = Client(fake.port, "c1")
    submit(fake)
    assert client.frame() is not None
    assert httpx.post(f"{fake.url}{INTERRUPT.pattern}").status_code == 200
    frames = client.frames_until_end()
    client.close()
    assert types(frames)[-1] == "execution_interrupted"
    assert len(frames) < len(SUCCESS)


def test_a_job_goes_to_the_client_that_submitted_it(fake: FakeComfy) -> None:
    one, two = Client(fake.port, "one"), Client(fake.port, "two")
    submit(fake, prompt_id="for-two", client_id="two")
    assert types(two.frames_until_end()) == SUCCESS
    submit(fake, prompt_id="for-one", client_id="one")
    frames = one.frames_until_end()
    one.close()
    two.close()
    assert {json.loads(frame)["data"]["prompt_id"] for frame in frames} == {"for-one"}


def test_scripted_http_steps_shape_the_answers(fake: FakeComfy) -> None:
    fake.script(PROMPT.key, Step(action="oom"), Step(action="invalid_json"), Step(status=503, body={"error": "busy"}))
    assert submit(fake).status_code == 500
    with pytest.raises(ValueError, match=r"Expecting|Unterminated"):
        submit(fake).json()
    busy = submit(fake)
    assert (busy.status_code, busy.json()) == (503, {"error": "busy"})
    fake.script(SYSTEM_STATS.key, Step(delay_s=0.3), Step(action="crash"))
    started = time.monotonic()
    httpx.get(f"{fake.url}{SYSTEM_STATS.pattern}")
    assert time.monotonic() - started >= 0.25
    with pytest.raises(httpx.TransportError):
        httpx.get(f"{fake.url}{SYSTEM_STATS.pattern}")
    assert wait_for(lambda: fake.crashed)


def test_an_unknown_path_is_not_found_and_a_known_path_with_another_method_is_refused(fake: FakeComfy) -> None:
    assert httpx.get(f"{fake.url}/queue").status_code == 404
    assert httpx.post(f"{fake.url}{SYSTEM_STATS.pattern}").status_code == 405
    assert httpx.get(f"{fake.url}{HISTORY.pattern.format(prompt_id='x')}").status_code == 200
    assert httpx.get(f"{fake.url}/ws").status_code == 405
