"""Limits of the public listener that uvicorn lacks: a request body cap and a header deadline.

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


class BodyLimitMiddleware:
    """Answers ``413`` for a body over ``max_body`` bytes and closes the connection.

    A ``Content-Length`` over the cap is refused before any of the body is read. A chunked body
    is counted while the app reads it; crossing the cap raises ``HTTPException``, which FastAPI
    passes through its body parsing to the error handlers.
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


class HeaderDeadlineProtocol(H11Protocol):
    """uvicorn's h11 protocol, closing a connection whose request head is late (slowloris).

    The deadline starts when the connection opens and when a further request begins on a
    kept-alive one; it ends once h11 has parsed the head. Data arriving in between does not
    extend it. uvicorn itself only times out the idle gap between requests.
    """

    header_timeout = 10.0  # seconds
    _deadline: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._deadline = self.loop.call_later(self.header_timeout, self._expired)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        if self.conn.their_state is not h11.IDLE:  # the head is complete (or invalid)
            self._cancel()
        elif self._deadline is None:  # the first bytes of the next request
            self._deadline = self.loop.call_later(self.header_timeout, self._expired)

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel()
        super().connection_lost(exc)

    def _cancel(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None

    def _expired(self) -> None:
        self._deadline = None
        self.transport.close()
