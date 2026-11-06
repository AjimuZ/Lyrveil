"""Fake KoboldCpp: an HTTP server for exactly the endpoints of docs/runtime-contracts.md, with scripted behavior.

The replies follow the contract's field names. The values are made up: the model is `koboldcpp/<model>`, a token is a
word, and a generation answers `reply_text`. The out-of-memory answer is an assumption, since the contract does not
record how KoboldCpp reports a failed allocation: a 500 answer whose message names CUDA, and a stream that ends with
`finish_reason` "error". The GPU suite replaces it with a recorded response at P02.

Run it in a container with `python -m fakes.fake_kobold`. It listens on port 5001 like the real runtime.
"""

import json
import time
from collections.abc import Callable, Iterator

from fakes.scripted_server import Reply, Request, Route, ScriptedServer, Step, json_reply, serve_forever

DEFAULT_PORT = 5001
# The version the pinned image reports: docs/runtime-contracts.md, KoboldCpp v1.124.2.
PINNED_VERSION = "1.124.2"
_OOM_MESSAGE = "CUDA error: out of memory"

MODEL = Route("GET", "/api/v1/model")
VERSION = Route("GET", "/api/extra/version")
MAX_CONTEXT = Route("GET", "/api/extra/true_max_context_length")
PERF = Route("GET", "/api/extra/perf")
TOKEN_COUNT = Route("POST", "/api/extra/tokencount")
STREAM = Route("POST", "/api/extra/generate/stream")
GENERATE = Route("POST", "/api/v1/generate")
ABORT = Route("POST", "/api/extra/abort")


def sse_event(token: str, finish_reason: str | None) -> bytes:
    """Returns one event of the generate/stream answer: `event: message` and a JSON `data` line."""
    data = json.dumps({"token": token, "finish_reason": finish_reason})
    return f"event: message\ndata: {data}\n\n".encode()


class FakeKobold(ScriptedServer):
    """Serves the eight KoboldCpp endpoints Lyrveil calls.

    Scripted steps apply per endpoint, one request each: `reply` with a `body` replaces the answer, `invalid_json` cuts
    the JSON (or sends an unparsable stream event), `oom` fails as described in the module docstring, and `crash`
    drops the connection and stops the fake, after `after_chunks` events on the stream. `delay_s` slows any endpoint and
    `chunk_delay_s` spaces the stream events. `POST /api/extra/abort` ends the running stream with `genkey` early, with
    `finish_reason` "stop".
    """

    ROUTES = (MODEL, VERSION, MAX_CONTEXT, PERF, TOKEN_COUNT, STREAM, GENERATE, ABORT)

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        on_crash: Callable[[], None] | None = None,
        *,
        model: str = "fake-model.gguf",
        context_size: int = 8192,
        reply_text: str = "Ciao, sono qui.",
    ) -> None:
        super().__init__(host, port, on_crash)
        self.model = model
        self.context_size = context_size
        self.reply_text = reply_text
        self.aborted: set[str] = set()
        self._started = time.monotonic()

    def streaming(self, route: Route) -> bool:
        return route == STREAM

    def build(self, request: Request, step: Step | None) -> Reply:
        route = request.route
        if step is not None and step.action == "reply" and step.body is not None:
            return json_reply(step.status, step.body)
        if step is not None and step.action == "invalid_json":
            return self._invalid(route)
        if step is not None and step.action == "oom":
            return self._out_of_memory(route)
        status = step.status if step is not None else 200
        chunk_delay = step.chunk_delay_s if step is not None else 0.0
        if route == MODEL:
            return json_reply(status, {"result": f"koboldcpp/{self.model}"})
        if route == VERSION:
            return json_reply(status, {"result": "KoboldCpp", "version": PINNED_VERSION})
        if route == MAX_CONTEXT:
            return json_reply(status, {"value": self.context_size})
        if route == PERF:
            return json_reply(status, self._perf())
        body = request.body if isinstance(request.body, dict) else None
        if body is None:
            return json_reply(400, {"detail": "The request body is not a JSON object"})
        if route == ABORT:
            return self._abort(body)
        prompt = body.get("prompt")
        if not isinstance(prompt, str):
            return json_reply(422, {"detail": "prompt must be a string"})
        if route == TOKEN_COUNT:
            return json_reply(status, self._count(prompt, body.get("special", True) is not False))
        if route == GENERATE:
            return json_reply(status, {"results": [{"text": self.reply_text}]})
        genkey = body.get("genkey")
        events = self._events(genkey if isinstance(genkey, str) else None)
        return Reply(status, events, "text/event-stream", stream=True, chunk_delay_s=chunk_delay)

    def _perf(self) -> dict[str, object]:
        return {
            "last_process": 0.1,
            "last_eval": 0.2,
            "last_token_count": len(self.reply_text.split()),
            "stop_reason": 0,
            "queue": 0,
            "idle": 1,
            "uptime": time.monotonic() - self._started,
        }

    @staticmethod
    def _count(prompt: str, special: bool) -> dict[str, object]:
        """Counts words as tokens, plus a BOS token when `special` is true, as the contract's request field does."""
        ids = list(range(len(prompt.split()) + (1 if special else 0)))
        return {"value": len(ids), "ids": ids, "prompt": prompt}

    def _abort(self, body: dict[str, object]) -> Reply:
        genkey = body.get("genkey")
        if not isinstance(genkey, str):
            return json_reply(422, {"detail": "genkey must be a string"})
        self.aborted.add(genkey)
        return json_reply(200, {"success": "true", "done": "true"})

    def _events(self, genkey: str | None) -> Iterator[bytes]:
        """Yields one event per word of the reply, then the closing event, and stops early when `genkey` is aborted."""
        for index, word in enumerate(self.reply_text.split(" ")):
            if genkey is not None and genkey in self.aborted:
                break
            yield sse_event(word if index == 0 else f" {word}", None)
        yield sse_event("", "stop")

    def _invalid(self, route: Route) -> Reply:
        if route == STREAM:
            return Reply(200, [b"event: message\ndata: {not json\n\n"], "text/event-stream", stream=True)
        return Reply(200, [b'{"results": [{"text": "Cia'])

    def _out_of_memory(self, route: Route) -> Reply:
        if route == STREAM:
            return Reply(200, [sse_event("", "error")], "text/event-stream", stream=True)
        return json_reply(500, {"detail": {"msg": _OOM_MESSAGE, "type": "service_unavailable"}})


if __name__ == "__main__":
    serve_forever(FakeKobold, DEFAULT_PORT)
