"""Admin API: issue one-time invitations for a stay and revoke them (design §4.3, §6)."""

from typing import Annotated, Any

import segno
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from myha_gateway.auth.admin import AdminIdentity, require_csrf
from myha_gateway.db import write_session
from myha_gateway.db.models import Instance, Stay
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.ha.client import utcnow
from myha_gateway.services.stays import (
    MAX_DEVICE_SLOTS,
    MAX_REDEEM_MINUTES,
    issue_invites,
    revoke_invite,
)

router = APIRouter()


class InvitesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=MAX_DEVICE_SLOTS)
    redeem_minutes: int = Field(default=15, ge=1, le=MAX_REDEEM_MINUTES)


@router.post("/api/v1/admin/stays/{stay_id}/invites", status_code=201)
async def issue(
    request: Request,
    stay_id: str,
    body: InvitesIn,
    admin: Annotated[AdminIdentity, Depends(require_csrf)],  # a fresh role check (E5)
) -> dict[str, Any]:
    """The invitations with secret link and QR code: this answer is the only place they appear."""
    ctx: AppContext = request.app.state.ctx
    async with write_session(ctx.engine) as session:
        stay = await session.get(Stay, stay_id)
        if stay is None:
            raise ApiError(404, "NOT_FOUND", "Unknown stay")
        instance = (await session.execute(select(Instance))).scalar_one()
        issued = await issue_invites(
            session,
            stay,
            count=body.count,
            redeem_minutes=body.redeem_minutes,
            origin=ctx.public_origin,
            instance=instance,
            now=utcnow(),
            actor=f"admin:{admin.user_id}",
        )
    ctx.policy.invalidate()

    def qr_codes() -> list[str]:  # CPU-bound: off the event loop
        return [segno.make(item.join_url, error="m").svg_inline(scale=4) for item in issued]

    svgs = await run_in_threadpool(qr_codes)
    return {
        "invites": [
            {
                "invite_id": item.invite.id,
                "device_slot": item.invite.device_slot,
                "join_url": item.join_url,
                "qr_svg": svg,
                "redeem_until": item.invite.redeem_until,
                "usable_until": stay.ends_at,
            }
            for item, svg in zip(issued, svgs, strict=True)
        ]
    }


@router.post("/api/v1/admin/invites/{invite_id}/revoke", status_code=204)
async def revoke(
    request: Request, invite_id: str, admin: Annotated[AdminIdentity, Depends(require_csrf)]
) -> None:
    ctx: AppContext = request.app.state.ctx
    async with write_session(ctx.engine) as session:
        await revoke_invite(session, invite_id, now=utcnow(), actor=f"admin:{admin.user_id}")
    ctx.policy.invalidate()
