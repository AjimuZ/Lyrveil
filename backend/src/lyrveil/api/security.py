"""Local-only controls of docs/security.md: refuses a foreign Host or Origin, and sets the security headers."""

from collections.abc import Awaitable, Callable, Iterable, Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lyrveil.domain.errors import DomainError
from lyrveil.logs import get_logger

PROBLEM_MEDIA_TYPE = "application/problem+json"
# Methods that change nothing. A browser may send them cross-site, but the absence of CORS headers keeps the answer
# from the page, so they need no Origin check.
SAFE_METHODS = frozenset({"GET", "HEAD"})
# Close code "policy violation", used when the server cannot send an HTTP answer to a refused WebSocket handshake.
# Uvicorn then answers the handshake with 403.
WS_POLICY_VIOLATION = 1008
# The ASGI extension that lets an application answer a WebSocket handshake with an ordinary HTTP response.
WS_DENIAL_EXTENSION = "websocket.http.response"
# Messages that start a response. A header added here reaches the client on every answer, a refusal included.
RESPONSE_START_TYPES = frozenset({"http.response.start", "websocket.http.response.start", "websocket.accept"})

logger = get_logger(__name__)


def documented_headers(http_port: int) -> dict[str, str]:
    """Returns the headers of docs/security.md (HTTP headers), with the WebSocket origins on `http_port`.

    nginx.conf sets the same list with the default port; the backend sets it too, so a response is covered even when
    it does not pass through nginx.
    """
    connect = f"'self' ws://localhost:{http_port} ws://127.0.0.1:{http_port}"
    return {
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
            f"font-src 'self'; connect-src {connect}; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'; object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }


def problem_response(
    status: int, code: str, title: str, detail: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Returns an `application/problem+json` answer in the error format of docs/api.md (Errors)."""
    slug = code.lower().replace("_", "-")
    body = {
        "type": f"https://lyrveil.invalid/errors/{slug}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def forbidden_origin() -> JSONResponse:
    """Returns the 403 `FORBIDDEN_ORIGIN` answer. It never echoes the refused value."""
    return problem_response(
        403, "FORBIDDEN_ORIGIN", "Origine non consentita", "La richiesta non proviene dall'app locale."
    )


class LocalOnlyMiddleware:
    """Refuses HTTP requests and WebSocket handshakes that do not come from the local app.

    A request passes only when its Host is one of `hosts`, compared without case. A WebSocket handshake, and an HTTP
    request whose method is not GET or HEAD, must also carry an Origin in `origins` when it carries one at all: browsers
    always send it on those, while command-line clients usually do not. A refusal answers 403 `FORBIDDEN_ORIGIN` before
    the application sees the request, and logs which check failed without the refused value. A refused handshake gets
    the same HTTP answer through the WebSocket denial extension, or a close with code 1008 when the server lacks it.
    Lifespan and other scopes pass untouched.
    """

    def __init__(self, app: ASGIApp, hosts: Iterable[str], origins: Iterable[str]) -> None:
        self.app = app
        self.hosts = frozenset(host.lower() for host in hosts)
        self.origins = frozenset(origin.lower() for origin in origins)

    def refusal(self, scope: Scope) -> str | None:
        """Returns the log event of the failed check, "host_refused" or "origin_refused", or None to let it pass."""
        headers = Headers(scope=scope)
        if headers.get("host", "").lower() not in self.hosts:
            return "host_refused"
        origin = headers.get("origin")
        checked = scope["type"] == "websocket" or scope["method"] not in SAFE_METHODS
        if checked and origin is not None and origin.lower() not in self.origins:
            return "origin_refused"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        event = self.refusal(scope)
        if event is None:
            await self.app(scope, receive, send)
            return
        logger.warning(event, error_code="FORBIDDEN_ORIGIN")
        if scope["type"] == "websocket" and WS_DENIAL_EXTENSION not in scope.get("extensions", {}):
            await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION, "reason": ""})
            return
        # On a WebSocket scope Starlette sends the response as "websocket.http.response.*" messages.
        await forbidden_origin()(scope, receive, send)


class SecurityHeadersMiddleware:
    """Sets `headers` on every HTTP response and on every WebSocket handshake answer, accepted or refused.

    A value set by a route for one of these names is replaced, so the documented value always wins.
    """

    def __init__(self, app: ASGIApp, headers: Mapping[str, str]) -> None:
        self.app = app
        self.headers = dict(headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] in RESPONSE_START_TYPES:
                message.setdefault("headers", [])
                response_headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    response_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def domain_error_handler(_request: Request, error: Exception) -> JSONResponse:
    """Answers a DomainError a service raised with its own status, code and Italian detail as problem+json."""
    assert isinstance(error, DomainError)
    titles = {
        "NOT_FOUND": "Non trovato",
        "CONFLICT": "Operazione non consentita",
        "VALIDATION_ERROR": "Dati non validi",
        "PAYLOAD_TOO_LARGE": "Contenuto troppo grande",
    }
    return problem_response(error.status, error.code, titles.get(error.code, "Richiesta rifiutata"), error.detail)


def internal_error_handler(headers: Mapping[str, str]) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Returns the handler of unexpected exceptions: a generic 500 `INTERNAL` answer that carries `headers`.

    Starlette runs it outside the middleware stack, which is why it sets the headers itself. The exception is still
    raised afterwards, so the server logs it; nothing of it reaches the answer.
    """

    async def handle(_request: Request, _error: Exception) -> JSONResponse:
        return problem_response(
            500, "INTERNAL", "Errore interno", "Si è verificato un errore imprevisto.", headers=dict(headers)
        )

    return handle
