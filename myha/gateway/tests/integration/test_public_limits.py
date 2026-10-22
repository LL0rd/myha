"""The public listener's body cap and request deadline, against a real uvicorn server."""

import asyncio
import contextlib
import json
import os
import socket
import ssl
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

import myha_gateway.__main__ as gateway
from myha_gateway.deps import AppContext
from myha_gateway.limits import MAX_BODY, RequestDeadlineProtocol
from tests.helpers import fake_supervisor, self_signed

DEADLINE = 0.5
SLACK = 3  # upper bound on top of the deadline: a slow CI runner must not flake


@pytest.fixture(params=["http"])
async def port(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[int]:
    """The public server of ``_public_server`` on a free port, plus a JSON route ``/echo``.

    Parametrised indirectly with ``"https"`` it serves TLS with a throwaway certificate.
    """
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("MYHA_PUBLIC_BIND", "127.0.0.1")
    monkeypatch.setenv("MYHA_PUBLIC_PORT", "0")
    monkeypatch.setattr(RequestDeadlineProtocol, "request_timeout", DEADLINE)
    real = gateway.create_public_app

    def with_echo(ctx: AppContext) -> FastAPI:
        app = real(ctx)

        @app.post("/echo")
        async def echo(body: dict[str, Any]) -> dict[str, int]:
            return {"keys": len(body)}

        return app

    monkeypatch.setattr(gateway, "create_public_app", with_echo)
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    if request.param == "https":
        cert, key = self_signed(tmp_path)
        ctx.public_tls = (str(cert), str(key))
    server = gateway._public_server(ctx)
    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 (uvicorn has no startup event)
                await asyncio.sleep(0.01)
        yield int(server.servers[0].sockets[0].getsockname()[1])
    finally:
        server.should_exit = True
        async with asyncio.timeout(5):
            await task
        await ctx.close()


@contextlib.asynccontextmanager
async def connect(port: int) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield reader, writer
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


async def response(reader: asyncio.StreamReader) -> tuple[int, dict[str, str], bytes]:
    """Status, headers and body of the next response (with Content-Length)."""
    async with asyncio.timeout(5):
        head = await reader.readuntil(b"\r\n\r\n")
        status_line, *lines = head.decode().split("\r\n")[:-2]
        headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines)}
        body = await reader.readexactly(int(headers["content-length"]))
    return int(status_line.split()[1]), headers, body


async def closed_by_server(reader: asyncio.StreamReader) -> bool:
    """True if the server closes the connection within 5 s, discarding what it still sends."""
    async with asyncio.timeout(5):
        with contextlib.suppress(ConnectionResetError):
            while await reader.read(65536):
                pass
    return True


def post_head(length: int | None) -> bytes:
    framing = "Transfer-Encoding: chunked" if length is None else f"Content-Length: {length}"
    return (
        f"POST /echo HTTP/1.1\r\nHost: testserver\r\nContent-Type: application/json\r\n"
        f"{framing}\r\n\r\n"
    ).encode()


def chunk(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


def json_of(size: int) -> bytes:
    """A JSON object of exactly ``size`` bytes."""
    filler = size - len(b'{"a":""}')
    return b'{"a":"' + b"x" * filler + b'"}'


async def test_content_length_over_the_cap_is_refused_before_the_body(port: int) -> None:
    async with connect(port) as (reader, writer):
        writer.write(post_head(MAX_BODY + 1))  # announced, never sent
        status, headers, body = await response(reader)
        assert status == 413
        assert json.loads(body)["error"]["code"] == "INVALID_PARAMETERS"
        assert headers["connection"] == "close"
        assert headers["x-content-type-options"] == "nosniff"
        assert await closed_by_server(reader)


async def test_chunked_body_over_the_cap_is_refused_and_closed(port: int) -> None:
    async with connect(port) as (reader, writer):
        writer.write(post_head(None))
        data = json_of(MAX_BODY + 1)
        for start in range(0, len(data), 16 * 1024):
            writer.write(chunk(data[start : start + 16 * 1024]))
        writer.write(b"0\r\n\r\n")
        status, headers, body = await response(reader)
        assert status == 413
        assert json.loads(body)["error"]["code"] == "INVALID_PARAMETERS"
        assert headers["connection"] == "close"
        assert await closed_by_server(reader)


@pytest.mark.parametrize("chunked", [False, True])
async def test_body_at_the_cap_passes(port: int, chunked: bool) -> None:
    data = json_of(MAX_BODY)
    async with connect(port) as (reader, writer):
        if chunked:
            writer.write(post_head(None) + chunk(data) + b"0\r\n\r\n")
        else:
            writer.write(post_head(len(data)) + data)
        status, _, body = await response(reader)
    assert (status, json.loads(body)) == (200, {"keys": 1})


def within_deadline(started: float) -> bool:
    return DEADLINE <= time.monotonic() - started < DEADLINE + SLACK


async def test_slow_header_block_is_closed_at_the_deadline(port: int) -> None:
    started = time.monotonic()
    async with connect(port) as (reader, writer):
        writer.write(b"GET /healthz HTTP/1.1\r\n")

        async def trickle() -> None:  # data keeps arriving, the deadline holds anyway
            for _ in range(50):
                await asyncio.sleep(0.05)
                writer.write(b"X-A: b\r\n")

        trickler = asyncio.create_task(trickle())
        try:
            assert await closed_by_server(reader)
        finally:
            trickler.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await trickler
        assert within_deadline(started)


async def test_silent_connection_is_closed_at_the_deadline(port: int) -> None:
    started = time.monotonic()
    async with connect(port) as (reader, _):
        assert await closed_by_server(reader)
    assert within_deadline(started)


def answer_length(answer: bytes) -> int:
    """Length of the response whose head ``answer`` contains; the head only if its body runs
    until the close (uvicorn's 400), which the raw read below TLS then discards."""
    head, _, _ = answer.partition(b"\r\n\r\n")
    lines = head.decode().lower().split("\r\n")
    lengths = [int(line.split(":")[1]) for line in lines if line.startswith("content-length")]
    return len(head) + 4 + sum(lengths)


def tls_close_seconds(port: int, request: bytes = b"") -> float:
    """Seconds until the server drops a TLS connection after ``request`` and its answer.

    Measures the TCP close, not the TLS close_notify: after the handshake it reads the raw
    socket below TLS until EOF. A graceful TLS close would send close_notify at the deadline
    and then wait up to 30 s for the client's before closing TCP.
    """
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    started = time.monotonic()
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as tcp,
        context.wrap_socket(tcp) as tls,
        socket.socket(fileno=os.dup(tls.fileno())) as raw,
    ):
        if request:
            tls.sendall(request)
            answer = b""
            while b"\r\n\r\n" not in answer or len(answer) < answer_length(answer):
                data = tls.recv(65536)
                if not data:
                    raise AssertionError("closed before the answer")
                answer += data
        raw.settimeout(10)
        with contextlib.suppress(ConnectionResetError):
            while raw.recv(65536):
                pass
    return time.monotonic() - started


# A server-side close after an exception mid-response is an app bug and not covered here.
@pytest.mark.parametrize("port", ["https"], indirect=True)
@pytest.mark.parametrize(
    "request_bytes",
    [
        b"",  # silent: the request deadline
        b"BROKEN\r\n\r\n",  # uvicorn answers 400 and closes
        b"GET /healthz HTTP/1.1\r\nHost: testserver\r\nConnection: close\r\n\r\n",
    ],
    ids=["silent", "400", "connection-close"],
)
async def test_silent_tls_client_is_dropped_without_close_notify_wait(
    port: int, request_bytes: bytes
) -> None:
    seconds = await asyncio.to_thread(tls_close_seconds, port, request_bytes)
    assert DEADLINE <= seconds < DEADLINE + SLACK  # a server-side close waits one deadline


@pytest.mark.parametrize("port", ["https"], indirect=True)
async def test_idle_tls_keep_alive_is_dropped_without_close_notify_wait(
    port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RequestDeadlineProtocol, "request_timeout", 30)  # keep-alive fires first
    get = b"GET /healthz HTTP/1.1\r\nHost: testserver\r\n\r\n"
    seconds = await asyncio.to_thread(tls_close_seconds, port, get)
    assert 5 <= seconds < 5 + SLACK  # uvicorn's keep-alive timeout of _server


async def test_deadline_runs_again_from_the_end_of_the_previous_response(port: int) -> None:
    get = b"GET /healthz HTTP/1.1\r\nHost: testserver\r\n\r\n"
    started = time.monotonic()  # earlier than the deadline's start: the lower bound holds
    async with connect(port) as (reader, writer):
        writer.write(get)
        assert (await response(reader))[0] == 200
        writer.write(get[:10])  # the next head starts, but never ends
        assert await closed_by_server(reader)
    assert within_deadline(started)


async def test_body_beyond_the_deadline_is_cut(port: int) -> None:
    data = json_of(100)
    started = time.monotonic()
    async with connect(port) as (reader, writer):
        writer.write(post_head(len(data)) + data[:50])  # the rest never comes
        assert await closed_by_server(reader)
    assert within_deadline(started)


async def test_unread_body_trickling_after_the_answer_is_cut(port: int) -> None:
    started = time.monotonic()
    async with connect(port) as (reader, writer):
        head = b"POST /healthz HTTP/1.1\r\nHost: testserver\r\nTransfer-Encoding: chunked\r\n\r\n"
        writer.write(head)
        assert (await response(reader))[0] == 405  # answered without reading the body

        async def trickle() -> None:
            for _ in range(200):
                writer.write(chunk(b"x"))
                await asyncio.sleep(0.05)

        trickler = asyncio.create_task(trickle())
        try:
            assert await closed_by_server(reader)
        finally:
            trickler.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await trickler
    assert within_deadline(started)
