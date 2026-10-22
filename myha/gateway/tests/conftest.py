import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from myha_simulator.server import create_app, make_server


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore MYHA_* and SUPERVISOR_TOKEN from the developer's shell."""
    for key in list(os.environ):
        if key.upper().startswith("MYHA_") or key.upper() == "SUPERVISOR_TOKEN":
            monkeypatch.delenv(key)


@dataclass(frozen=True)
class SimControl:
    """Client for the simulator's ``/_sim`` control API; error answers raise."""

    client: httpx.AsyncClient  # base URL is the simulator, usable for the Supervisor API too

    async def get(self, path: str) -> Any:
        response = await self.client.get(f"/_sim/{path}")
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, body: Any = None) -> Any:
        response = await self.client.post(f"/_sim/{path}", json=body)
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True)
class SimHandle:
    http_url: str  # Supervisor base URL (MYHA_SUPERVISOR_URL)
    ws_url: str  # Core WebSocket URL (MYHA_HA_WS_URL)
    control: SimControl


@pytest.fixture
async def sim() -> AsyncIterator[SimHandle]:
    """The HA simulator served on a free port of 127.0.0.1 for one test."""
    server = make_server(create_app(), "127.0.0.1", 0)
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
