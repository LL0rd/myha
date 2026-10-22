"""``WS /api/v1/events`` on the public listener (design §4.3): one guest's live events.

The client proves itself with a one-time ticket from ``POST /api/v1/ws-ticket`` in its first
frame (``auth.public.use_ws_ticket``). A handshake from a foreign ``Origin`` is refused before
it is accepted; a browser session's socket needs the public origin itself. uvicorn sends the
protocol pings and caps inbound frames (``__main__._server``).
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from myha_gateway.auth.public import use_ws_ticket
from myha_gateway.deps import AppContext
from myha_gateway.events.connection import ClientConnection

logger = logging.getLogger(__name__)
router = APIRouter()
CLOSE_POLICY = 1008


@router.websocket("/api/v1/events")
async def events(websocket: WebSocket) -> None:
    ctx: AppContext = websocket.app.state.ctx
    origin = websocket.headers.get("origin")
    if origin is not None and origin != ctx.public_origin:  # native clients send none
        await websocket.close(CLOSE_POLICY)  # before accept: the handshake gets a 403
        return
    await websocket.accept()

    async def authenticate(ticket: str) -> str | None:
        return await use_ws_ticket(ctx, ticket, origin)

    try:
        await ClientConnection(ctx, websocket, authenticate).run()
    except (WebSocketDisconnect, OSError):  # the client left, also mid-send (uvicorn: OSError)
        logger.debug("event connection lost")
