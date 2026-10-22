"""Helpers shared by the integration tests."""

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from myha_gateway.ha.supervisor import SupervisorClient

TRUSTED = "172.30.32.2"
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
