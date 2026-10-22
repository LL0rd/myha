"""One-time tickets for the live WebSocket (design §4.3); their use comes with the event bus."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.public import (
    TICKET_SECONDS,
    SessionContext,
    app_context,
    issue_ws_ticket,
    require_csrf_public,
)

router = APIRouter()


@router.post("/api/v1/ws-ticket")
async def ws_ticket(
    request: Request, current: Annotated[SessionContext, Depends(require_csrf_public)]
) -> dict[str, Any]:
    ticket = await issue_ws_ticket(app_context(request), current)
    return {"ticket": ticket, "expires_in": TICKET_SECONDS}
