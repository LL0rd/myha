"""Limits of the public listener that uvicorn lacks: a request body cap and a request deadline.

Both keep a stranger from holding one of the listener's ``limit_concurrency`` slots, or memory,
for as long as they like; the admin listener is reachable only by the ingress peer and runs
without either.
"""

import asyncio

import h11
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

from myha_gateway.errors import error_response

MAX_BODY = 64 * 1024  # far above any JSON request of the public API
_TOO_LARGE = "Request body too large"
# h11 then marks the connection MUST_CLOSE: uvicorn closes it instead of draining the body.
_CLOSE = {"Connection": "close"}
# h11 states in which the peer's request is still due; any other means it sent the whole
# request or no longer speaks HTTP/1.1 (error, close, protocol switch).
_PENDING = (h11.IDLE, h11.SEND_BODY)


class BodyLimitMiddleware:
    """Answers ``413`` for a body over ``max_body`` bytes and closes the connection.

    A ``Content-Length`` over the cap is refused before any of the body is read. A chunked body
    is counted while the app reads it; crossing the cap raises ``HTTPException``, which FastAPI
    passes through its body parsing to the error handlers. A client still writing its body may
    see the close as ECONNRESET instead of the 413, which is acceptable for a JSON API.
    """

    def __init__(self, app: ASGIApp, max_body: int = MAX_BODY) -> None:
        self.app, self.max_body = app, max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length")
        if length is not None and int(length) > self.max_body:  # h11 accepts digits only
            response = error_response(413, "INVALID_PARAMETERS", _TOO_LARGE, headers=_CLOSE)
            await response(scope, receive, send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    raise HTTPException(413, _TOO_LARGE, headers=_CLOSE)
            return message

        await self.app(scope, limited, send)


class RequestDeadlineProtocol(H11Protocol):
    """uvicorn's h11 protocol, dropping a connection whose request is late (slowloris).

    The deadline starts when the connection opens and when a kept-alive one waits for its
    next request (uvicorn's shorter keep-alive timeout usually ends that wait first); it ends
    once h11 has the whole request, head and body (with the body cap, 10 s is ample), or once
    the connection became a WebSocket. Data arriving in between does
    not extend it. Expiry aborts the transport: a graceful TLS close would wait up to 30 s for
    the client's close_notify. uvicorn's keep-alive timeout aborts the same way.

    TLS connections stuck in the handshake never reach ``connection_made``: they count neither
    here nor against ``limit_concurrency``, but hold a descriptor until asyncio's 60 s
    handshake timeout.
    """

    request_timeout = 10.0  # seconds
    _deadline: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._deadline = self.loop.call_later(self.request_timeout, self._expired)

    def handle_events(self) -> None:
        # Runs for new data and after each response (pipelined requests), so it sees every
        # change of their_state.
        super().handle_events()
        if self.transport.get_protocol() is not self or self.conn.their_state not in _PENDING:
            self._cancel()  # after a WebSocket upgrade, connection_lost no longer reaches us
        elif self.conn.their_state is h11.IDLE and self._deadline is None:
            self._deadline = self.loop.call_later(self.request_timeout, self._expired)

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel()
        super().connection_lost(exc)

    def timeout_keep_alive_handler(self) -> None:
        if not self.transport.is_closing():
            self.conn.send(h11.ConnectionClosed())
            self.transport.abort()

    def _cancel(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _expired(self) -> None:
        self._deadline = None
        self.transport.abort()
