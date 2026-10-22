"""The two ASGI apps: admin (Supervisor ingress only) and public (guests)."""

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from myha_gateway.api.admin import probe
from myha_gateway.api.public import health
from myha_gateway.deps import AppContext
from myha_gateway.errors import add_error_handlers, error_response
from myha_gateway.static import SecurityHeadersMiddleware, content_security_policy, serve_bundle


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
            await error_response(403, "UNAUTHENTICATED", message)(scope, receive, send)
        elif scope["type"] == "websocket" and not trusted:
            # Closing before accept makes the server reject the handshake with HTTP 403.
            await send({"type": "websocket.close", "code": 1008})
        else:
            await self.app(scope, receive, send)


def _app(*, validation_details: bool) -> FastAPI:
    """What both apps share: no docs, no slash redirects, one error shape."""
    app = FastAPI(openapi_url=None, redirect_slashes=False)
    add_error_handlers(app, validation_details=validation_details)
    return app


def create_public_app(ctx: AppContext) -> FastAPI:
    """Guest-facing app: no admin route, never trusts forwarding headers."""
    settings = ctx.settings
    app = _app(validation_details=False)
    app.include_router(health.router)
    serve_bundle(app, settings.web_dist / "guest", "index.html")
    # Plain ws: only while the public origin is plain http (dev and proxy setups).
    allow_ws = (settings.public_origin or "").startswith("http://")
    csp = content_security_policy(frame_ancestors="'none'", allow_ws=allow_ws)
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    return app


def create_admin_app(ctx: AppContext) -> FastAPI:
    """Admin app behind the Supervisor ingress proxy, reachable only from its peer address."""
    app = _app(validation_details=True)
    app.include_router(probe.router)
    serve_bundle(app, ctx.settings.web_dist / "admin", "admin.html")
    # Ingress frames the page under the HA origin, which may be plain http.
    csp = content_security_policy(frame_ancestors="'self'", allow_ws=True)
    app.add_middleware(SecurityHeadersMiddleware, csp=csp)
    # Keep this the last add_middleware call: the last one added is the outermost.
    app.add_middleware(PeerGuardMiddleware, trusted_peer=ctx.settings.trusted_ingress_peer)
    return app
