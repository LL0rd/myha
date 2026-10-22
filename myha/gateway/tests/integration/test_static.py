import os
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from tests.integration.test_listeners import TRUSTED, call

Factory = Callable[[AppContext], FastAPI]
IMMUTABLE = "public, max-age=31536000, immutable"
CSP = (
    "default-src 'self'; connect-src 'self' wss:{ws}; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors {frame}; "
    "base-uri 'self'; form-action 'self'"
)
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Not Found"}}
FILES = [
    "guest/index.html",
    "guest/assets/g.js",
    "guest/manifest.webmanifest",
    "guest/sw.js",
    "admin/admin.html",
    "admin/assets/a.js",
]


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake ``pnpm build`` output; each file contains its own path to show which one was sent."""
    dist = tmp_path / "web"
    for name in FILES:
        (dist / name).parent.mkdir(parents=True, exist_ok=True)
        (dist / name).write_text(name)
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_WEB_DIST", str(dist))
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    return dist


async def make(factory: Factory) -> FastAPI:
    return factory(await AppContext.create())


async def get_verbatim(app: FastAPI, path: str) -> httpx.Response:
    """GET ``path`` as a raw client sends it: httpx itself would drop ``..`` segments."""

    async def verbatim(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, "path": path}, receive, send)

    transport = httpx.ASGITransport(app=verbatim, client=(TRUSTED, 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/")


@pytest.mark.parametrize(
    ("factory", "path", "file", "cache"),
    [
        (create_public_app, "/", "guest/index.html", "no-store"),
        (create_public_app, "/join", "guest/index.html", "no-store"),
        (create_public_app, "/stays/42", "guest/index.html", "no-store"),
        (create_public_app, "/assets/g.js", "guest/assets/g.js", IMMUTABLE),
        (create_public_app, "/sw.js", "guest/sw.js", "no-cache"),
        (create_public_app, "/manifest.webmanifest", "guest/manifest.webmanifest", "no-cache"),
        (create_admin_app, "/", "admin/admin.html", "no-store"),
        (create_admin_app, "/rooms", "admin/admin.html", "no-store"),
        (create_admin_app, "/admin.html", "admin/admin.html", "no-store"),
        (create_admin_app, "/assets/a.js", "admin/assets/a.js", IMMUTABLE),
    ],
)
async def test_bundle_files_are_served_with_cache_policy(
    dist: Path, factory: Factory, path: str, file: str, cache: str
) -> None:
    r = await call(await make(factory), "GET", path)
    assert r.status_code == 200 and r.text == file
    assert r.headers["cache-control"] == cache


async def test_content_types_and_head(dist: Path) -> None:
    app = await make(create_public_app)
    assert (await call(app, "GET", "/")).headers["content-type"].startswith("text/html")
    assert (await call(app, "GET", "/sw.js")).headers["content-type"].startswith("text/javascript")
    head = await call(app, "HEAD", "/assets/g.js")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize(
    ("factory", "path"),
    [
        (create_public_app, "/admin.html"),  # the admin entry never reaches guests
        (create_public_app, "/assets/a.js"),  # nor do admin chunks
        (create_admin_app, "/assets/g.js"),
        (create_admin_app, "/sw.js"),
        (create_public_app, "/foo.txt"),
        (create_public_app, "/assets/missing.js"),
        (create_public_app, "/assets"),
        (create_public_app, "/assets/"),
        (create_public_app, "/join/"),  # trailing slash: neither a page nor a redirect
        (create_admin_app, "/rooms/"),
        (create_public_app, "/api"),  # the API namespace never falls back to the page
        (create_public_app, "/api/v1/nope"),
        (create_admin_app, "/api/v1/nope"),
        (create_admin_app, "/api/v1/admin/nope"),
        (create_public_app, "/assets/%2e%2e/%2e%2e/admin/admin.html"),
        (create_public_app, "/assets/" + "%2e%2e/" * 20 + "etc/passwd"),
        (create_public_app, "/%2e%2e/admin/admin.html"),
        (create_admin_app, "/assets/%2e%2e/%2e%2e/guest/index.html"),
        (create_public_app, "/assets/%00.js"),
        (create_public_app, "/assets/" + "a" * 300 + ".js"),
    ],
)
async def test_everything_else_is_json_not_found(dist: Path, factory: Factory, path: str) -> None:
    r = await call(await make(factory), "GET", path)
    assert r.status_code == 404 and r.json() == NOT_FOUND
    assert "cache-control" not in r.headers


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../../admin/admin.html",
        "/assets/" + "../" * 20 + "etc/passwd",
        "/../admin/admin.html",
    ],
)
async def test_literal_dot_segments_cannot_leave_the_bundle(dist: Path, path: str) -> None:
    r = await get_verbatim(await make(create_public_app), path)
    assert r.status_code == 404 and r.json() == NOT_FOUND


@pytest.mark.parametrize("path", ["/api/v1/nope", "/join"])
@pytest.mark.parametrize("factory", [create_public_app, create_admin_app])
async def test_other_methods_get_no_page_and_no_405(
    dist: Path, factory: Factory, path: str
) -> None:
    r = await call(await make(factory), "POST", path)
    assert r.status_code == 404 and r.json() == NOT_FOUND


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files regardless of their mode")
async def test_unreadable_files_are_json_not_found(dist: Path) -> None:
    locked = dist / "guest/locked"
    locked.mkdir()
    (locked / "x.js").write_text("x")
    (dist / "guest/assets/g.js").chmod(0)
    locked.chmod(0)
    try:
        app = await make(create_public_app)
        for path in ("/assets/g.js", "/locked/x.js"):
            r = await call(app, "GET", path)
            assert r.status_code == 404 and r.json() == NOT_FOUND
    finally:
        locked.chmod(0o700)  # lets pytest remove tmp_path


@pytest.mark.parametrize(
    ("factory", "missing"),
    [
        (create_public_app, "."),
        (create_public_app, "guest"),
        (create_admin_app, "."),
        (create_admin_app, "admin"),
    ],
)
async def test_missing_build_answers_json_not_found(
    dist: Path, factory: Factory, missing: str
) -> None:
    shutil.rmtree(dist / missing)
    app = await make(factory)
    r = await call(app, "GET", "/")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "NOT_FOUND", "message": "web bundle not built"}}
    assert (await call(app, "GET", "/assets/a.js")).json() == NOT_FOUND


@pytest.mark.parametrize("path", ["/", "/healthz", "/assets/g.js", "/assets/a.js", "/nope.txt"])
@pytest.mark.parametrize(
    ("factory", "frame"), [(create_public_app, "'none'"), (create_admin_app, "'self'")]
)
async def test_every_response_carries_csp_and_hardening_headers(
    dist: Path, factory: Factory, frame: str, path: str
) -> None:
    r = await call(await make(factory), "GET", path)
    assert r.headers["content-security-policy"] == CSP.format(ws=" ws:", frame=frame)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


async def test_public_csp_drops_plain_ws_for_https_origin(
    dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "https://guest.example")
    public, admin = await make(create_public_app), await make(create_admin_app)
    csp = (await call(public, "GET", "/")).headers["content-security-policy"]
    assert csp == CSP.format(ws="", frame="'none'") and " ws:" not in csp
    # Ingress may serve the admin page over plain http whatever the public origin is.
    admin_csp = (await call(admin, "GET", "/")).headers["content-security-policy"]
    assert admin_csp == CSP.format(ws=" ws:", frame="'self'")


@pytest.mark.parametrize("path", ["/", "/admin.html", "/assets/a.js"])
async def test_admin_bundle_needs_trusted_peer(dist: Path, path: str) -> None:
    r = await call(await make(create_admin_app), "GET", path, peer="10.0.0.5")
    assert r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"
