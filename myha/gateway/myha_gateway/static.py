"""The built web bundles: ``guest/`` on the public app, ``admin/`` on the admin app."""

import os
import stat
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from myha_gateway.errors import ApiError

_IMMUTABLE = "public, max-age=31536000, immutable"  # Vite puts a content hash in asset names


def content_security_policy(*, frame_ancestors: str, allow_ws: bool) -> str:
    """No external resources; plain ``ws:`` only where the page itself may come over http."""
    connect = "'self' wss: ws:" if allow_ws else "'self' wss:"
    return (
        f"default-src 'self'; connect-src {connect}; img-src 'self' data:; "
        f"style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors {frame_ancestors}; "
        "base-uri 'self'; form-action 'self'"
    )


class SecurityHeadersMiddleware:
    """Pure ASGI: adds the CSP and the hardening headers to every HTTP response."""

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self.app = app
        self.headers = [
            (b"content-security-policy", csp.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *self.headers]
            await send(message)

        await self.app(scope, receive, send_with_headers if scope["type"] == "http" else send)


def serve_bundle(app: FastAPI, root: Path, entry: str) -> None:
    """Answer GET/HEAD requests no route matched from the bundle at ``root``.

    Installed as the router default rather than a catch-all route, so every route wins,
    including routes added later, and a wrong method on an API route stays 405.
    """
    not_found = app.router.default

    async def bundle(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("GET", "HEAD"):
            # Sync stat calls on the local image take microseconds; no thread hop needed.
            await _response(root, entry, scope["path"])(scope, receive, send)
        else:
            await not_found(scope, receive, send)

    app.router.default = bundle


def _response(root: Path, entry: str, path: str) -> FileResponse:
    """Files by name, the entry page for client-side routes, JSON 404 for the rest."""
    top, name = path.lstrip("/").partition("/")[0], path.rpartition("/")[2]
    if top == "api" or (not name and path != "/"):
        raise HTTPException(404)  # the API namespace and trailing slashes never get the page
    if top == "assets" or "." in name:
        found = _file(root, path)
        if found is None:
            raise HTTPException(404)
    else:
        found = _file(root, entry)
        if found is None:
            raise ApiError(404, "NOT_FOUND", "web bundle not built")
    full, stat_result = found
    if full.suffix == ".html":
        cache = "no-store"
    elif top == "assets":
        cache = _IMMUTABLE
    else:
        cache = "no-cache"
    return FileResponse(full, stat_result=stat_result, headers={"Cache-Control": cache})


def _file(root: Path, path: str) -> tuple[Path, os.stat_result] | None:
    """The regular, readable file at ``path`` inside ``root``, else ``None``."""
    try:
        base = root.resolve()
        full = (base / path.lstrip("/")).resolve()
        if not full.is_relative_to(base):  # "..", absolute paths, symlinks out of the bundle
            return None
        stat_result = full.stat()
    except (OSError, ValueError):  # missing, no permission, name too long, NUL byte
        return None
    # FileResponse opens the file after sending the headers, too late for a 404: check now.
    # shortcut: a file deleted between this check and the open aborts the response; the
    # bundle is part of the read-only image, so only a dev rebuild while serving hits this.
    if not stat.S_ISREG(stat_result.st_mode) or not os.access(full, os.R_OK):
        return None
    return full, stat_result
