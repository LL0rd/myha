"""The built web bundles: ``guest/`` on the public app, ``admin/`` on the admin app."""

import mimetypes
import os
import stat
from pathlib import Path

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from myha_gateway.errors import SAFETY_HEADERS, ApiError

# Python's own table has no font types and the Alpine image has no /etc/mime.types.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

_IMMUTABLE = "public, max-age=31536000, immutable"  # Vite puts a content hash in asset names


def content_security_policy(*, frame_ancestors: str, connect: str) -> str:
    """No external resources; ``connect`` lists where fetch and WebSockets may go."""
    return (
        f"default-src 'self'; connect-src {connect}; img-src 'self' data:; "
        f"style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors {frame_ancestors}; "
        "base-uri 'self'; form-action 'self'"
    )


class SecurityHeadersMiddleware:
    """Pure ASGI: the CSP and safety headers on every response produced inside the app.

    The 403 from the peer guard and the 500 from Starlette's outer middleware never pass
    through here; they set the two safety headers explicitly. JSON is never cached unless
    the response says otherwise.
    """

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self.app = app
        self.headers = {"Content-Security-Policy": csp, **SAFETY_HEADERS}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                headers.update(self.headers)
                if headers.get("content-type", "").startswith("application/json"):
                    headers.setdefault("Cache-Control", "no-store")
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
            response = _response(root, entry, scope["path"])
            # No partial responses: FileResponse would answer a bad Range in plain text.
            headers = [(key, value) for key, value in scope["headers"] if key != b"range"]
            await response({**scope, "headers": headers}, receive, send)
        else:
            await not_found(scope, receive, send)

    app.router.default = bundle


def _response(root: Path, entry: str, path: str) -> FileResponse:
    """Files by name, the entry page for client-side routes, JSON 404 for the rest."""
    segments = path.split("/")
    top, name = path.lstrip("/").partition("/")[0], segments[-1]
    if top == "api" or (not name and path != "/") or any(s.startswith(".") for s in segments):
        # The API namespace, trailing slashes, dotfiles and "."/".." segments, before any
        # lookup, so a cache class can never come from a path that names another file.
        raise HTTPException(404)
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
    elif top == "assets" and full.is_relative_to(root.resolve() / "assets"):
        cache = _IMMUTABLE  # by the file sent: a symlink out of assets/ is not a hashed asset
    else:
        cache = "no-cache"
    return FileResponse(full, stat_result=stat_result, headers={"Cache-Control": cache})


def _file(root: Path, path: str) -> tuple[Path, os.stat_result] | None:
    """The regular, readable file at ``path`` inside ``root``, else ``None``."""
    # resolve() and is_relative_to() are quadratic in the path depth: a 60 KB path blocks the
    # event loop of both listeners for seconds. Real bundle paths are far shorter.
    if len(path) > 255:
        return None
    try:
        base = root.resolve()
        full = (base / path.lstrip("/")).resolve()
        if not full.is_relative_to(base):  # symlinks out of the bundle, ".." from any caller
            return None
        stat_result = full.stat()
    except (OSError, ValueError):  # missing, no permission, NUL byte
        return None
    # FileResponse opens the file after sending the headers, too late for a 404: check now.
    # shortcut: a file deleted between this check and the open aborts the response; the
    # bundle is part of the read-only image, so only a dev rebuild while serving hits this.
    if not stat.S_ISREG(stat_result.st_mode) or not os.access(full, os.R_OK):
        return None
    return full, stat_result
