import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from myha_simulator.server import create_app, make_server
from tests.helpers import SimControl, SimHandle


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore MYHA_* and SUPERVISOR_TOKEN from the developer's shell."""
    for key in list(os.environ):
        if key.upper().startswith("MYHA_") or key.upper() == "SUPERVISOR_TOKEN":
            monkeypatch.delenv(key)


@pytest.fixture
async def sim() -> AsyncIterator[SimHandle]:
    """The HA simulator served on a free port of 127.0.0.1 for one test."""
    server = make_server(create_app(), "127.0.0.1", 0)
    server.capture_signals = contextlib.nullcontext  # Ctrl+C stays with pytest
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 (uvicorn has no startup event)
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        http_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=http_url) as client:
            yield SimHandle(http_url, f"ws://127.0.0.1:{port}/api/websocket", SimControl(client))
    finally:
        server.should_exit = True
        async with asyncio.timeout(5):  # cancels the server task if shutdown hangs
            await task
