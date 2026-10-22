from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from starlette.types import Message

from myha_gateway.__main__ import main
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from tests.helpers import call, fake_supervisor


@pytest.fixture
async def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppContext:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()  # never the network
    return ctx


async def test_public_health_reports_client_ip(ctx: AppContext) -> None:
    r = await call(create_public_app(ctx), "GET", "/healthz", peer="203.0.113.9")
    assert r.status_code == 200 and r.json()["client_ip"] == "203.0.113.9"


async def test_public_listener_has_no_admin_routes(ctx: AppContext) -> None:  # ahead of A01
    app = create_public_app(ctx)
    r = await call(app, "GET", "/api/v1/admin/session", headers={"X-Remote-User-Id": "owner1"})
    assert r.status_code == 404


async def test_no_probe_route_and_no_probe_cli(ctx: AppContext) -> None:
    app = create_admin_app(ctx)
    r = await call(app, "GET", "/api/v1/admin/probe", headers={"X-Remote-User-Id": "owner1"})
    assert r.status_code == 404
    with pytest.raises(SystemExit) as exc:
        main(["probe"])
    assert exc.value.code == 2  # argparse: invalid choice


async def test_healthz_ignores_forwarding_headers(ctx: AppContext) -> None:
    headers = {"X-Forwarded-For": "198.51.100.1", "X-Real-IP": "198.51.100.2"}
    r = await call(create_public_app(ctx), "GET", "/healthz", peer="203.0.113.9", headers=headers)
    assert r.json() == {"status": "ok", "client_ip": "203.0.113.9"}


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


@pytest.mark.parametrize(
    ("factory", "path"),
    [(create_public_app, "/healthz/"), (create_admin_app, "/api/v1/admin/session/")],
)
async def test_trailing_slash_is_not_found_instead_of_redirect(
    ctx: AppContext, factory: Callable[[AppContext], FastAPI], path: str
) -> None:
    r = await call(factory(ctx), "GET", path)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def crash() -> None:
    raise RuntimeError("Bearer sup-secret")


@pytest.mark.parametrize("factory", [create_public_app, create_admin_app])
async def test_unhandled_error_is_json_500_without_internals(
    ctx: AppContext, factory: Callable[[AppContext], FastAPI]
) -> None:
    app = factory(ctx)
    app.get("/api/v1/crash")(crash)
    r = await call(app, "GET", "/api/v1/crash", raise_app_exceptions=False)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "Internal error"}}
    assert "sup-secret" not in r.text
    # Sent by Starlette's outermost middleware, past the header middleware.
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


async def test_server_side_http_error_maps_to_upstream_unavailable(ctx: AppContext) -> None:
    async def unavailable() -> None:
        raise HTTPException(503)

    app = create_public_app(ctx)
    app.get("/api/v1/unavailable")(unavailable)
    r = await call(app, "GET", "/api/v1/unavailable")
    assert r.status_code == 503
    assert r.json() == {"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "Service Unavailable"}}


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "UNAUTHENTICATED"),
        (409, "CONFLICT"),
        (429, "RATE_LIMITED"),
        (418, "INVALID_PARAMETERS"),
    ],
)
async def test_framework_http_errors_map_to_public_codes(
    ctx: AppContext, status: int, code: str
) -> None:
    async def refused() -> None:
        raise HTTPException(status, "Refused")

    app = create_public_app(ctx)
    app.get("/api/v1/refused")(refused)
    r = await call(app, "GET", "/api/v1/refused")
    assert r.status_code == status
    assert r.json() == {"error": {"code": code, "message": "Refused"}}


async def test_http_401_maps_to_unauthenticated(ctx: AppContext) -> None:
    async def unauthorized() -> None:  # what FastAPI's security helpers raise
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    app = create_public_app(ctx)
    app.get("/api/v1/unauthorized")(unauthorized)
    r = await call(app, "GET", "/api/v1/unauthorized")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json() == {"error": {"code": "UNAUTHENTICATED", "message": "Not authenticated"}}


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


async def test_peer_guard_denies_websocket_handshake(ctx: AppContext) -> None:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "headers": [], "client": ("10.0.0.5", 1)}
    await create_admin_app(ctx)(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]  # uvicorn answers HTTP 403
