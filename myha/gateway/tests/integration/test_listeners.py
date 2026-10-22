from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError

TRUSTED = "172.30.32.2"


@pytest.fixture
async def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppContext:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    return await AppContext.create()


async def call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    peer: str = TRUSTED,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(peer, 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


async def test_public_health_reports_client_ip(ctx: AppContext) -> None:
    r = await call(create_public_app(ctx), "GET", "/healthz", peer="203.0.113.9")
    assert r.status_code == 200 and r.json()["client_ip"] == "203.0.113.9"


async def test_public_listener_has_no_admin_routes(ctx: AppContext) -> None:  # ahead of A01
    app = create_public_app(ctx)
    r = await call(app, "GET", "/api/v1/admin/probe", headers={"X-Remote-User-Id": "owner1"})
    assert r.status_code == 404


async def test_admin_probe_requires_trusted_peer(ctx: AppContext) -> None:
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe", peer="10.0.0.5")
    assert r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_admin_probe_echoes_identity_headers_from_trusted_peer(ctx: AppContext) -> None:
    headers = {"X-Remote-User-Id": "u1", "X-Ingress-Path": "/api/hassio_ingress/abc"}
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe", headers=headers)
    assert r.status_code == 200 and r.json()["peer"] == "172.30.32.2"
    assert r.json()["headers"]["x-remote-user-id"] == "u1"


async def test_admin_probe_shows_only_identity_and_forwarding_headers(ctx: AppContext) -> None:
    headers = {
        "X-Remote-User-Name": "dimitri",
        "X-Forwarded-For": "192.168.1.20",
        "X-Real-IP": "192.168.1.20",
        "Authorization": "Bearer abc",
        "Cookie": "session=abc",
    }
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe", headers=headers)
    assert r.json()["headers"] == {
        "x-remote-user-name": "dimitri",
        "x-forwarded-for": "192.168.1.20",
        "x-real-ip": "192.168.1.20",
    }


async def test_healthz_echoes_forwarding_headers_without_trusting_them(ctx: AppContext) -> None:
    headers = {"X-Forwarded-For": "198.51.100.1", "X-Real-IP": "198.51.100.2"}
    r = await call(create_public_app(ctx), "GET", "/healthz", peer="203.0.113.9", headers=headers)
    assert r.json() == {
        "status": "ok",
        "client_ip": "203.0.113.9",
        "x_forwarded_for": "198.51.100.1",
        "x_real_ip": "198.51.100.2",
    }


@pytest.mark.parametrize("factory", [create_public_app, create_admin_app])
async def test_unknown_route_is_json_not_found(
    ctx: AppContext, factory: Callable[[AppContext], FastAPI]
) -> None:
    r = await call(factory(ctx), "GET", "/api/v1/nope")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


async def test_wrong_method_keeps_error_shape(ctx: AppContext) -> None:
    r = await call(create_public_app(ctx), "POST", "/healthz")
    assert r.status_code == 405 and r.headers["allow"] == "GET"
    assert r.json()["error"]["code"] == "ACTION_NOT_ALLOWED"


async def echo(n: int) -> int:
    return n


async def test_validation_errors_have_details_only_on_admin(ctx: AppContext) -> None:
    public, admin = create_public_app(ctx), create_admin_app(ctx)
    for app in (public, admin):
        app.get("/api/v1/echo")(echo)
    r_public = await call(public, "GET", "/api/v1/echo?n=x")
    r_admin = await call(admin, "GET", "/api/v1/echo?n=x")
    assert r_public.status_code == r_admin.status_code == 400
    assert r_public.json() == {
        "error": {"code": "INVALID_PARAMETERS", "message": "Invalid parameters"}
    }
    assert r_admin.json()["error"]["code"] == "INVALID_PARAMETERS"
    assert r_admin.json()["error"]["details"]["errors"][0]["loc"] == ["query", "n"]


async def test_api_error_renders_error_shape(ctx: AppContext) -> None:
    async def conflict() -> None:
        raise ApiError(409, "CONFLICT", "Version changed", {"expected_version": 3})

    app = create_public_app(ctx)
    app.get("/api/v1/conflict")(conflict)
    r = await call(app, "GET", "/api/v1/conflict")
    assert r.status_code == 409
    assert r.json() == {
        "error": {
            "code": "CONFLICT",
            "message": "Version changed",
            "details": {"expected_version": 3},
        }
    }


async def test_public_ready_follows_public_enabled(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ctx.public_ready
    monkeypatch.delenv("MYHA_PUBLIC_ENABLED")
    assert not (await AppContext.create()).public_ready


@pytest.mark.parametrize("path", ["/", "/assets/a.js", "/api/v1/admin/nope"])
async def test_peer_guard_blocks_every_path(ctx: AppContext, path: str) -> None:
    r = await call(create_admin_app(ctx), "GET", path, peer="127.0.0.1")
    assert r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_peer_guard_denies_websocket_handshake(ctx: AppContext) -> None:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "headers": [], "client": ("10.0.0.5", 1)}
    await create_admin_app(ctx)(scope, receive, send)
    assert sent[0]["type"] == "websocket.http.response.start" and sent[0]["status"] == 403
