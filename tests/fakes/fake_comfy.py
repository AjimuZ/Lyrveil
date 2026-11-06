"""Fake ComfyUI: an HTTP and WebSocket server for exactly the endpoints of docs/runtime-contracts.md.

`POST /prompt` accepts a prompt and queues a job for its `client_id`. The job plays on the `WS /ws?clientId=<id>`
connection of that client, as the contract's message table lists them: `execution_start`, `executing` for node "1",
`progress` for the sampler node, `executing` with a null node, then `execution_success`. A job waits until its client
connects, and a finished job appears in `GET /history/{prompt_id}`. Nothing renders: `GET /view` serves a one-pixel PNG.

The steps scripted on `WS /ws` shape the next job, not the next connection: `oom` ends it with an `execution_error`
whose text holds `OutOfMemoryError`, `invalid_json` sends one unparsable frame among the progress frames, `crash` stops
the fake after `after_chunks` messages, `delay_s` waits before the first message and `chunk_delay_s` between messages.
`POST /interrupt` ends the running job with `execution_interrupted`. The steps on the HTTP routes shape their answers.

Run it in a container with `python -m fakes.fake_comfy`. It listens on port 8188 like the real runtime.
"""

import json
import queue
import struct
import threading
import time
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from fakes.scripted_server import Reply, Request, Route, ScriptedServer, Step, WebSocket, json_reply, serve_forever

DEFAULT_PORT = 8188
PINNED_VERSION = "0.43.0"

SYSTEM_STATS = Route("GET", "/system_stats")
PROMPT = Route("POST", "/prompt")
WS = Route("WS", "/ws")
HISTORY = Route("GET", "/history/{prompt_id}")
VIEW = Route("GET", "/view")
INTERRUPT = Route("POST", "/interrupt")

_CHECKPOINT_NODE = "1"
_SAMPLER_NODE = "3"
_OUTPUT_FILE = "fake_00001_.png"
_VRAM_BYTES = 12 * 1024**3


def _png() -> bytes:
    """Returns a valid 1x1 PNG, built here so the fake has no binary file."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")


@dataclass
class _Job:
    prompt_id: str
    step: Step | None


class FakeComfy(ScriptedServer):
    """Serves the six ComfyUI endpoints Lyrveil calls. See the module docstring for the job and step semantics."""

    ROUTES = (SYSTEM_STATS, PROMPT, WS, HISTORY, VIEW, INTERRUPT)

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        on_crash: Callable[[], None] | None = None,
        *,
        sampler_steps: int = 3,
    ) -> None:
        super().__init__(host, port, on_crash)
        self.sampler_steps = sampler_steps
        self.history: dict[str, dict[str, object]] = {}
        self._pending: dict[str, queue.Queue[_Job]] = {}
        self._pending_lock = threading.Lock()
        self._interrupt = threading.Event()
        self._number = 0

    def build(self, request: Request, step: Step | None) -> Reply:
        route = request.route
        if step is not None and step.action == "reply" and step.body is not None:
            return json_reply(step.status, step.body)
        if step is not None and step.action == "invalid_json":
            return Reply(200, [b'{"prompt_id": "'])
        if step is not None and step.action == "oom":
            return json_reply(500, {"error": {"type": "OutOfMemoryError", "message": "CUDA out of memory"}})
        status = step.status if step is not None else 200
        if route == SYSTEM_STATS:
            return json_reply(status, self._stats())
        if route == PROMPT:
            return self._submit(request, status)
        if route == HISTORY:
            prompt_id = request.path.rsplit("/", 1)[1]
            entry = self.history.get(prompt_id)
            return json_reply(status, {prompt_id: entry} if entry is not None else {})
        if route == VIEW:
            return self._view(request, status)
        self._interrupt.set()
        return Reply(status, [b""], "text/plain")

    @staticmethod
    def _stats() -> dict[str, object]:
        return {
            "system": {"os": "posix", "comfyui_version": PINNED_VERSION, "python_version": "3.12.16"},
            "devices": [
                {
                    "name": "cuda:0 Fake GPU",
                    "type": "cuda",
                    "index": 0,
                    "vram_total": _VRAM_BYTES,
                    "vram_free": _VRAM_BYTES,
                }
            ],
        }

    def _submit(self, request: Request, status: int) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        if not isinstance(body.get("prompt"), dict) or not body["prompt"]:
            error = {"type": "prompt_no_outputs", "message": "Prompt has no outputs", "details": ""}
            return json_reply(400, {"error": error, "node_errors": {}})
        prompt_id = body.get("prompt_id")
        prompt_id = prompt_id if isinstance(prompt_id, str) else str(uuid.uuid4())
        client_id = body.get("client_id")
        self._number += 1
        job = _Job(prompt_id, self.next_step(WS))
        with self._pending_lock:
            self._pending.setdefault(client_id if isinstance(client_id, str) else "", queue.Queue()).put(job)
        return json_reply(status, {"prompt_id": prompt_id, "number": self._number, "node_errors": {}})

    @staticmethod
    def _view(request: Request, status: int) -> Reply:
        filename = request.query.get("filename", [""])[0]
        if not filename.startswith("fake_"):
            return json_reply(404, {"detail": "file not found"})
        return Reply(status, [_png()], "image/png")

    def websocket(self, request: Request, connection: WebSocket) -> None:
        client_id = request.query.get("clientId", [""])[0]
        with self._pending_lock:
            jobs = self._pending.setdefault(client_id, queue.Queue())
        while not self.stopping and not connection.peer_closed():
            try:
                job = jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            if not self._play(job, connection):
                return

    def _play(self, job: _Job, connection: WebSocket) -> bool:
        """Plays one job on `connection` and records it in the history. Returns False when the fake crashed."""
        step = job.step
        self._interrupt.clear()
        self.sleep(step.delay_s if step is not None else 0.0)
        # `sent` is the number of messages already delivered when the loop reaches `message`.
        for sent, message in enumerate(self._messages(job)):
            if step is not None and step.action == "crash" and sent >= step.after_chunks:
                self.crash()
                return False
            if self._interrupt.is_set():
                connection.send_text(self._frame("execution_interrupted", job.prompt_id, node_id=_SAMPLER_NODE))
                self.history[job.prompt_id] = {"outputs": {}, "status": {"status_str": "error", "completed": False}}
                return True
            connection.send_text(message)
            self.sleep(step.chunk_delay_s if step is not None else 0.0)
        failed = step is not None and step.action == "oom"
        self.history[job.prompt_id] = self._outcome(failed)
        return True

    def _messages(self, job: _Job) -> list[str]:
        """Returns the frames of one job, following its step."""
        step = job.step
        pid = job.prompt_id
        frames = [self._frame("execution_start", pid), self._frame("executing", pid, node=_CHECKPOINT_NODE)]
        if step is not None and step.action == "oom":
            error = {
                "node_id": _SAMPLER_NODE,
                "node_type": "KSampler",
                "exception_type": "torch.OutOfMemoryError",
                "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB",
            }
            return [*frames, self._frame("execution_error", pid, **error)]
        for value in range(1, self.sampler_steps + 1):
            frames.append(self._frame("progress", pid, node=_SAMPLER_NODE, value=value, max=self.sampler_steps))
            if step is not None and step.action == "invalid_json" and value == 1:
                frames.append('{"type": "progress", "data": ')
        finished = self._frame("executing", pid, node=None)
        success = self._frame("execution_success", pid, timestamp=int(time.time() * 1000))
        return [*frames, finished, success]

    @staticmethod
    def _frame(kind: str, prompt_id: str, **fields: object) -> str:
        return json.dumps({"type": kind, "data": {"prompt_id": prompt_id, **fields}})

    @staticmethod
    def _outcome(failed: bool) -> dict[str, object]:
        if failed:
            return {"outputs": {}, "status": {"status_str": "error", "completed": False}}
        image = {"filename": _OUTPUT_FILE, "subfolder": "", "type": "output"}
        return {"outputs": {"9": {"images": [image]}}, "status": {"status_str": "success", "completed": True}}


if __name__ == "__main__":
    serve_forever(FakeComfy, DEFAULT_PORT)
