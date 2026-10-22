import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from starlette.types import Message

from myha_gateway.api.admin import probe
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.auth.passwords import Argon2Timing, measure_argon2
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
        "X-Ingress-Path-Foo": "/not/echoed",
    }
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe", headers=headers)
    assert r.json()["headers"] == {
        "x-remote-user-name": "dimitri",
        "x-forwarded-for": "192.168.1.20",
        "x-real-ip": "192.168.1.20",
    }


async def test_admin_probe_reports_supervisor_environment_and_paths(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-secret")
    (ctx.settings.data_dir / "options.json").write_text("{}", encoding="utf-8")
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe")
    body = r.json()
    assert body["supervisor_info"] == {
        "version": "2026.10.3",
        "channel": "stable",
        "arch": "amd64",
        "supported": True,
    }
    assert body["host_info"] is None  # 403: the app has no hassio_api
    assert body["self_info"]["ingress_entry"] == "/api/hassio_ingress/abc"
    assert "SUPERVISOR_TOKEN" in body["env_names"] and "sup-secret" not in r.text
    assert set(body["paths"]) == {"data_dir", "state_dir", "backup_dir", "web_dist"}
    assert body["paths"]["data_dir"] is True and body["paths"]["state_dir"] is False
    assert body["data_entries"] == ["options.json"]  # names only
    assert datetime.fromisoformat(body["time"]).utcoffset() == timedelta(0)


async def test_admin_probe_shows_supervisor_errors_instead_of_failing(ctx: AppContext) -> None:
    ctx.supervisor = fake_supervisor(reachable=False)
    r = await call(create_admin_app(ctx), "GET", "/api/v1/admin/probe")
    assert r.status_code == 200 and "sup-secret" not in r.text
    error = "SupervisorUnavailable: GET /info: ConnectError: Name or service not known"
    assert r.json()["supervisor_info"] == {"error": error}
    assert r.json()["host_info"]["error"].startswith("SupervisorUnavailable: GET /host/info")
    assert r.json()["self_info"]["error"].startswith("SupervisorUnavailable: GET /addons/self")


def fast_argon2() -> list[Argon2Timing]:
    return measure_argon2(memory_costs_kib=(8,), rounds=1)


@pytest.mark.parametrize("reachable", [True, False])
async def test_probe_mode_startup_logs_evidence_and_writes_marker(
    reachable: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "options.json").write_text('{"probe_mode": true}', encoding="utf-8")
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(probe, "measure_argon2", fast_argon2)
    caplog.set_level(logging.INFO)
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor(reachable=reachable)
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        pass
    marker = (tmp_path / "state" / "probe-live.marker").read_text(encoding="utf-8")
    assert datetime.fromisoformat(marker.strip()).utcoffset() == timedelta(0)
    if reachable:
        assert "probe: supervisor_info {'version': '2026.10.3', 'channel': 'stable'" in caplog.text
        assert "probe: host_info not permitted" in caplog.text
        assert "'ingress_entry': '/api/hassio_ingress/abc'" in caplog.text
    else:  # dev without Supervisor: logged, startup goes on
        assert "probe: supervisor_info {'error': 'SupervisorUnavailable: GET /info" in caplog.text
    assert "argon2id t=4 m=8 KiB: " in caplog.text


async def test_probe_mode_startup_survives_marker_and_argon2_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "options.json").write_text('{"probe_mode": true}', encoding="utf-8")
    (tmp_path / "state" / "probe-live.marker").mkdir(parents=True)  # where the file belongs
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))

    def broken_argon2() -> list[Argon2Timing]:
        raise MemoryError("argon2 needs more memory")

    monkeypatch.setattr(probe, "measure_argon2", broken_argon2)
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        pass  # reached: the app starts despite both failures
    assert "probe: marker not written" in caplog.text and "IsADirectoryError" in caplog.text
    assert "probe: argon2id timing failed" in caplog.text and "MemoryError" in caplog.text


async def test_admin_startup_without_probe_mode_leaves_no_marker(
    ctx: AppContext, tmp_path: Path
) -> None:
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        pass
    assert not (tmp_path / "state" / "probe-live.marker").exists()


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


@pytest.mark.parametrize(
    ("factory", "path"),
    [(create_public_app, "/healthz/"), (create_admin_app, "/api/v1/admin/probe/")],
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
