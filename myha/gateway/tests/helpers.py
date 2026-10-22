"""Helpers shared by the integration tests."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import websockets
from fastapi import FastAPI
from pydantic import SecretStr

from myha_gateway.ha.supervisor import SupervisorClient

TRUSTED = "172.30.32.2"
SIM_TOKEN = "sim-token"  # the simulator's admin and Supervisor token
SUPERVISOR_DATA = {
    "/info": {"supervisor": "2026.10.3", "channel": "stable", "arch": "amd64", "supported": True},
    "/addons/self/info": {
        "name": "myHA",
        "version": "0.1.0",
        "ingress_entry": "/api/hassio_ingress/abc",
        "ingress_url": "/api/hassio_ingress/abc/",
    },
}


def fake_supervisor(*, reachable: bool = True) -> SupervisorClient:
    """The Supervisor as an app without ``hassio_api`` sees it: ``/host/info`` answers 403."""

    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("Name or service not known", request=request)
        data = SUPERVISOR_DATA.get(request.url.path)
        if data is None:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json={"result": "ok", "data": data})

    transport = httpx.MockTransport(handler)
    return SupervisorClient("http://supervisor", SecretStr("sup-secret"), transport=transport)


async def call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    peer: str = TRUSTED,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    """Send one request to ``app`` from the TCP peer ``peer``."""
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=raise_app_exceptions, client=(peer, 1)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


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


async def ws_receive(ws: websockets.ClientConnection) -> Any:
    """The next message as JSON, at most 5 s away."""
    async with asyncio.timeout(5):
        return json.loads(await ws.recv())


async def ws_command(ws: websockets.ClientConnection, msg: dict[str, Any]) -> Any:
    """Send one command and return the next message (keep subscribers on other connections)."""
    await ws.send(json.dumps(msg))
    return await ws_receive(ws)


@asynccontextmanager
async def ws_connect(
    sim: SimHandle, token: str = SIM_TOKEN
) -> AsyncIterator[websockets.ClientConnection]:
    """A WebSocket connection to the simulator, authenticated with ``token``."""
    async with websockets.connect(sim.ws_url) as ws:
        assert (await ws_receive(ws))["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        assert (await ws_receive(ws))["type"] == "auth_ok"
        yield ws
