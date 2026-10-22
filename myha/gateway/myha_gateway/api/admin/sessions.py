"""Admin API: list client sessions and revoke one (design §6)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from myha_gateway.auth.admin import AdminIdentity, require_admin, require_csrf
from myha_gateway.db.models import Principal
from myha_gateway.db.models import Session as SessionRow
from myha_gateway.deps import AppContext
from myha_gateway.services.sessions import revoke_session

router = APIRouter()


@router.get("/api/v1/admin/sessions", dependencies=[Depends(require_admin(fresh=True))])
async def list_sessions(request: Request, stay_id: str | None = None) -> dict[str, Any]:
    """Every session, or those of one stay, oldest first; never a secret."""
    ctx: AppContext = request.app.state.ctx
    query = (
        select(SessionRow, Principal)
        .outerjoin(Principal, Principal.id == SessionRow.principal_id)
        .order_by(SessionRow.created_at, SessionRow.id)
    )
    if stay_id is not None:
        query = query.where(SessionRow.stay_id == stay_id)
    async with ctx.sessions() as session:
        rows = (await session.execute(query)).all()
    return {"sessions": [_session_json(row, principal) for row, principal in rows]}


@router.post("/api/v1/admin/sessions/{session_id}/revoke", status_code=204)
async def revoke(
    request: Request, session_id: str, admin: Annotated[AdminIdentity, Depends(require_csrf)]
) -> None:
    ctx: AppContext = request.app.state.ctx
    await revoke_session(ctx, session_id, "revoked", actor=f"admin:{admin.user_id}")


def _session_json(row: SessionRow, principal: Principal | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "principal": None
        if principal is None
        else {"id": principal.id, "type": principal.type, "display_name": principal.display_name},
        "audience": row.audience,
        "purpose": row.purpose,
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "not_after": row.not_after,
        "revoked_at": row.revoked_at,
        "reason": row.revoke_reason,
    }
