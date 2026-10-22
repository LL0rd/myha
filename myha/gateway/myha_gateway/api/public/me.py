"""The caller's own session, guest and stay (spec §12.1)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.public import (
    SessionContext,
    app_context,
    instance_of,
    require_session,
    session_view,
)
from myha_gateway.db.models import Unit
from myha_gateway.ha.client import utcnow

router = APIRouter()


@router.get("/api/v1/me")
async def me(
    request: Request,
    current: Annotated[SessionContext, Depends(require_session(purpose="access"))],
) -> dict[str, Any]:
    ctx = app_context(request)
    instance = instance_of(ctx)
    principal, session, stay = current.principal, current.session, current.stay
    unit_name = None
    if stay is not None:
        async with ctx.sessions() as db:
            unit = await db.get(Unit, stay.unit_id)
        unit_name = unit.name if unit is not None else None
    return {
        "principal": None
        if principal is None
        else {
            "id": principal.id,
            "type": principal.type,
            "display_name": principal.display_name,
            "locale": principal.locale,
            "theme": principal.theme,
        },
        "session": session_view(session, utcnow()),
        "csrf_token": session.csrf_secret,
        "stay": None if stay is None else {"unit_name": unit_name, "ends_at": stay.ends_at},
        "instance": {"name": instance.name, "operator_contact": instance.operator_contact},
    }
