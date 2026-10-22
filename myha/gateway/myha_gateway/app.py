"""The two ASGI apps: admin (Supervisor ingress only) and public (guests)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Lifespan, Receive, Scope, Send

from myha_gateway.api.admin import probe
from myha_gateway.api.public import health
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.deps import AppContext
from myha_gateway.errors import SAFETY_HEADERS, add_error_handlers, error_response
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


@asynccontextmanager
async def _admin_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Once per process, since the admin app always runs: database, S0 probe, the HA client."""
    ctx: AppContext = app.state.ctx
    try:
        ctx.settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        await run_migrations(ctx.engine)
        async with write_session(ctx.engine) as session:
            ctx.instance = await ensure_instance(session)
    except Exception:
        # Nothing works without the schema; uvicorn then exits with code 3 and s6 halts the app.
        logger.exception("startup aborted: database %s is not usable", ctx.settings.db_path)
        await ctx.close()
        raise RuntimeError("database not usable") from None  # uvicorn logs this short copy
    try:
        if ctx.settings.probe_mode:
            await probe.log_startup(ctx)
        await ctx.ha.start()  # an unreachable Core is logged and retried, never fatal
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
    # Plain ws: only while the public origin is plain http (dev and proxy setups).
    allow_ws = (settings.public_origin or "").startswith("http://")
    csp = content_security_policy(frame_ancestors="'none'", allow_ws=allow_ws)
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    return app


def create_admin_app(ctx: AppContext) -> FastAPI:
    """Admin app behind the Supervisor ingress proxy, reachable only from its peer address."""
    app = _app(ctx, validation_details=True, lifespan=_admin_lifespan)
    app.include_router(probe.router)
    serve_bundle(app, ctx.settings.web_dist / "admin", "admin.html")
    # Ingress frames the page under the HA origin, which may be plain http.
    csp = content_security_policy(frame_ancestors="'self'", allow_ws=True)
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    # Keep this the last add_middleware call: the last one added is the outermost.
    app.add_middleware(PeerGuardMiddleware, trusted_peer=ctx.settings.trusted_ingress_peer)
    return app
