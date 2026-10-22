"""Helpers shared by the integration tests."""

import httpx
from fastapi import FastAPI

TRUSTED = "172.30.32.2"


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
