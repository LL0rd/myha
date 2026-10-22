"""``WS /api/v1/events`` on the public listener (design §4.3): one guest's live events.

The client proves itself with a one-time ticket from ``POST /api/v1/ws-ticket`` in its first
frame. Browser sessions also need the public origin: a foreign page's socket gets nothing even
with a stolen ticket. uvicorn sends the protocol pings (every 20 s) and caps inbound frames
(``ws_max_size``).
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import update

from myha_gateway.db import write_session
from myha_gateway.db.models import Session, WsTicket
from myha_gateway.deps import AppContext
from myha_gateway.events.connection import ClientConnection
from myha_gateway.ha.client import utcnow
from myha_gateway.ids import hash_secret

logger = logging.getLogger(__name__)
router = APIRouter()


async def use_ticket(ctx: AppContext, ticket: str, origin: str | None) -> str | None:
    """The ticket's session id if the ticket is unused and in time; it is used up either way."""
    now = utcnow()
    async with write_session(ctx.engine) as db:
        query = (
            update(WsTicket)
            .where(WsTicket.ticket_hash == hash_secret(ticket), WsTicket.used_at.is_(None))
            .where(WsTicket.expires_at > now)
            .values(used_at=now)
            .returning(WsTicket.session_id)
        )
        session_id = (await db.execute(query)).scalar_one_or_none()
        session = None if session_id is None else await db.get(Session, session_id)
    if session is None or (session.audience == "browser" and origin != ctx.public_origin):
        return None
    return session.id  # revoked or expired: the connection says so and closes


@router.websocket("/api/v1/events")
async def events(websocket: WebSocket) -> None:
    ctx: AppContext = websocket.app.state.ctx
    origin = websocket.headers.get("origin")
    await websocket.accept()

    async def authenticate(ticket: str) -> str | None:
        return await use_ticket(ctx, ticket, origin)

    try:
        await ClientConnection(ctx, websocket, authenticate).run()
    except (WebSocketDisconnect, OSError):  # the client left mid-send
        logger.debug("event connection lost")
