"""The two ASGI apps: admin (Supervisor ingress only) and public (guests)."""

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from myha_gateway.api.admin import probe
from myha_gateway.api.public import health
from myha_gateway.deps import AppContext
from myha_gateway.errors import add_error_handlers, error_response


class PeerGuardMiddleware:
    """Pure ASGI guard: 403 for every HTTP or WebSocket request not sent by the trusted peer."""

    def __init__(self, app: ASGIApp, trusted_peer: str) -> None:
        self.app = app
        self.trusted_peer = trusted_peer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        if scope["type"] in ("http", "websocket") and (
            client is None or client[0] != self.trusted_peer
        ):
            response = error_response(
                403, "UNAUTHENTICATED", "Only reachable through Home Assistant ingress"
            )
            await response(scope, receive, send)  # a WebSocket gets an HTTP denial response
            return
        await self.app(scope, receive, send)


def create_public_app(ctx: AppContext) -> FastAPI:
    """Guest-facing app: no admin route, no docs, never trusts forwarding headers."""
    app = FastAPI(openapi_url=None)
    add_error_handlers(app, validation_details=False)
    app.include_router(health.router)
    return app


def create_admin_app(ctx: AppContext) -> FastAPI:
    """Admin app behind the Supervisor ingress proxy, reachable only from its peer address."""
    app = FastAPI(openapi_url=None)
    add_error_handlers(app, validation_details=True)
    app.include_router(probe.router)
    # Keep this the last add_middleware call: the last one added is the outermost.
    app.add_middleware(PeerGuardMiddleware, trusted_peer=ctx.settings.trusted_ingress_peer)
    return app
