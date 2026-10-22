"""Admin API: create and list stays (design §6)."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from myha_gateway.auth.admin import AdminIdentity, require_admin, require_csrf
from myha_gateway.db import write_session
from myha_gateway.db.models import Invite, Stay
from myha_gateway.deps import AppContext
from myha_gateway.ha.client import utcnow
from myha_gateway.services.stays import MAX_DEVICE_SLOTS, create_stay, stay_status

router = APIRouter()


class StayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    role_template_id: str
    role_version: int | None = Field(default=None, ge=1)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    device_slots: int = Field(ge=1, le=MAX_DEVICE_SLOTS)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(UTC)
        except OverflowError as exc:  # e.g. year 1 with a positive offset
            raise ValueError("not representable in UTC") from exc


def stay_json(stay: Stay, invites: list[Invite]) -> dict[str, Any]:
    """A stay with its derived status and its invitations, never their secrets."""
    return {
        "id": stay.id,
        "unit_id": stay.unit_id,
        "starts_at": stay.starts_at,
        "ends_at": stay.ends_at,
        "status": stay_status(stay, utcnow()),
        "role_template_id": stay.role_template_id,
        "role_version": stay.role_version,
        "device_slots": stay.device_slots,
        "invites": [
            {
                "invite_id": invite.id,
                "device_slot": invite.device_slot,
                "redeem_until": invite.redeem_until,
                "redeemed": invite.consumed_at is not None,
                "revoked": invite.revoked_at is not None,
            }
            for invite in invites
        ],
    }


@router.post("/api/v1/admin/stays", status_code=201)
async def create(
    request: Request, body: StayIn, admin: Annotated[AdminIdentity, Depends(require_csrf)]
) -> dict[str, Any]:
    ctx: AppContext = request.app.state.ctx
    async with write_session(ctx.engine) as session:
        stay = await create_stay(
            session,
            unit_id=body.unit_id,
            role_template_id=body.role_template_id,
            role_version=body.role_version,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            device_slots=body.device_slots,
            actor=f"admin:{admin.user_id}",
        )
    ctx.policy.invalidate()
    return stay_json(stay, [])


@router.get("/api/v1/admin/stays", dependencies=[Depends(require_admin(fresh=False))])
async def list_stays(request: Request) -> dict[str, Any]:
    """Every stay; status draft, planned, active, ended, checked_out or cancelled."""
    ctx: AppContext = request.app.state.ctx
    async with ctx.sessions() as session:
        stays = (await session.execute(select(Stay).order_by(Stay.starts_at))).scalars().all()
        query = select(Invite).where(Invite.stay_id.is_not(None)).order_by(Invite.device_slot)
        invites = (await session.execute(query)).scalars().all()
    by_stay: dict[str | None, list[Invite]] = {}
    for invite in invites:
        by_stay.setdefault(invite.stay_id, []).append(invite)
    return {"stays": [stay_json(stay, by_stay.get(stay.id, [])) for stay in stays]}
