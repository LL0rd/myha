"""The two ASGI apps: admin (Supervisor ingress only) and public (guests)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select
from starlette.types import ASGIApp, Lifespan, Receive, Scope, Send

from myha_gateway.api.admin import session
from myha_gateway.api.public import health
from myha_gateway.auth.admin import AdminGuard
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db.models import Instance
from myha_gateway.deps import AppContext
from myha_gateway.errors import SAFETY_HEADERS, add_error_handlers, error_response
from myha_gateway.ha.supervisor import SupervisorUnavailable
from myha_gateway.static import SecurityHeadersMiddleware, content_security_policy, serve_bundle

logger = logging.getLogger(__name__)


class PeerGuardMiddleware:
    """Pure ASGI guard: rejects every HTTP request and WebSocket not sent by the trusted peer."""

    def __init__(self, app: ASGIApp, trusted_peer: str) -> None:
        self.app = app
        self.trusted_peer = trusted_peer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        trusted = client is not None and client[0] == self.trusted_peer
        if scope["type"] == "http" and not trusted:
            message = "Only reachable through Home Assistant ingress"
            response = error_response(403, "UNAUTHENTICATED", message, headers=SAFETY_HEADERS)
            await response(scope, receive, send)
        elif scope["type"] == "websocket" and not trusted:
            # Closing before accept makes the server reject the handshake with HTTP 403.
            await send({"type": "websocket.close", "code": 1008})
        else:
            await self.app(scope, receive, send)


def _app(
    ctx: AppContext, *, validation_details: bool, lifespan: Lifespan[FastAPI] | None = None
) -> FastAPI:
    """What both apps share: no docs, no slash redirects, one error shape, ``state.ctx``."""
    app = FastAPI(openapi_url=None, redirect_slashes=False, lifespan=lifespan)
    app.state.ctx = ctx
    add_error_handlers(app, validation_details=validation_details)
    return app


async def _log_supervisor(ctx: AppContext) -> None:
    """One startup line on the Supervisor version; ``require_admin`` enforces the minimum."""
    minimum = ctx.settings.min_supervisor_version
    try:
        info = await ctx.supervisor.info()
        current = info.is_at_least(minimum)
    except SupervisorUnavailable as exc:  # its message never contains the token
        logger.warning("Supervisor unreachable (minimum %s): %s", minimum, exc)
        return
    except Exception:  # only a log line: it must never stop the startup
        logger.exception("Supervisor version check failed")
        return
    if current:
        logger.info("Supervisor %s (minimum %s)", info.version, minimum)
    else:
        message = "Supervisor %s (minimum %s): admin access stays blocked until it is updated"
        logger.warning(message, info.version, minimum)


@asynccontextmanager
async def _admin_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown, once per process, since the admin app always runs.

    Order: restore check, database, instance row, public listener decision, then the HA
    client beside the Supervisor version line. ``_serve`` then opens the admin listener, and
    the public one if ``public_ready``.
    """
    ctx: AppContext = app.state.ctx
    # Before the database: a later restore must not find an instance row made by this start.
    ctx.restore_mode = (ctx.settings.backup_dir / "restore.sqlite").exists()
    try:
        ctx.settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        ctx.settings.state_dir.chmod(0o700)  # mkdir's mode spares an existing 0755 state/
        await run_migrations(ctx.engine)
        if ctx.restore_mode:  # load, never create: the restore brings its own row (Plan 3)
            async with ctx.sessions() as session:
                ctx.instance = (await session.execute(select(Instance))).scalar_one_or_none()
        else:
            async with write_session(ctx.engine) as session:
                ctx.instance = await ensure_instance(session)
    except Exception:
        # Nothing works without the schema; uvicorn then exits with code 3 and s6 halts the app.
        logger.exception("startup aborted: database %s is not usable", ctx.settings.db_path)
        await ctx.close()
        raise RuntimeError("database not usable") from None  # uvicorn logs this short copy
    try:
        if reason := ctx.refresh_public():
            logger.warning("public listener off: %s", reason)
        # Side by side, so that a slow Supervisor (5 s timeout) adds nothing to the HA start;
        # neither raises: an unreachable Core is logged and retried, never fatal.
        await asyncio.gather(ctx.ha.start(), _log_supervisor(ctx))
        yield
    finally:  # also after an aborted startup; stop() tolerates a client that never started
        await ctx.ha.stop()
        await ctx.close()


def create_public_app(ctx: AppContext) -> FastAPI:
    """Guest-facing app: no admin route, never trusts forwarding headers."""
    settings = ctx.settings
    app = _app(ctx, validation_details=False)
    app.include_router(health.router)
    serve_bundle(app, settings.web_dist / "guest", "index.html")
    # WebSockets only to the public origin itself: wss://host, ws://host for the http override.
    origin = ctx.public_origin
    connect = "'self'" if origin is None else f"'self' {origin.replace('http', 'ws', 1)}"
    csp = content_security_policy(frame_ancestors="'none'", connect=connect)
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    return app


def create_admin_app(ctx: AppContext) -> FastAPI:
    """Admin app behind the Supervisor ingress proxy, reachable only from its peer address."""
    app = _app(ctx, validation_details=True, lifespan=_admin_lifespan)
    # Caches of require_admin, which every admin API route depends on.
    app.state.admin_guard = AdminGuard(ctx)
    app.include_router(session.router)
    serve_bundle(app, ctx.settings.web_dist / "admin", "admin.html")
    # Ingress frames the page under the HA origin, which may be plain http.
    csp = content_security_policy(frame_ancestors="'self'", connect="'self' wss: ws:")
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    # Keep this the last add_middleware call: the last one added is the outermost.
    app.add_middleware(PeerGuardMiddleware, trusted_peer=ctx.settings.trusted_ingress_peer)
    return app
