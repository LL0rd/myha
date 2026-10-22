"""Stays and their one-time invitations (design §4.3, §5; spec §8.2).

Every function runs inside the caller's write session and adds its audit events there; the
caller commits. Each mutation raises ``instance.policy_version``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db.models import Instance, Invite, RoleVersion, Stay, Unit
from myha_gateway.errors import ApiError
from myha_gateway.ids import hash_secret, new_id, new_secret
from myha_gateway.services.audit import AuditEvent, audit

MAX_REDEEM_MINUTES = 1440  # spec §8.2: extendable up to 24 hours, never past the stay
MAX_DEVICE_SLOTS = 50


@dataclass(frozen=True)
class IssuedInvite:
    """An invitation as issued: ``join_url`` carries the secret and is shown exactly once."""

    invite: Invite
    join_url: str


def stay_status(stay: Stay, now: datetime) -> str:
    """The stored status, with ``confirmed`` split into planned, ``active`` and ``ended``."""
    if stay.status != "confirmed" or now < stay.starts_at:
        return stay.status
    return "active" if now < stay.ends_at else "ended"


async def bump_policy_version(session: AsyncSession) -> int:
    """Raise ``instance.policy_version`` by one and return the new value."""
    query = update(Instance).values(policy_version=Instance.policy_version + 1)
    return (await session.execute(query.returning(Instance.policy_version))).scalar_one()


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(400, "INVALID_PARAMETERS", message, {"field": field})


async def create_stay(
    session: AsyncSession,
    *,
    unit_id: str,
    role_template_id: str,
    role_version: int | None,
    starts_at: datetime,
    ends_at: datetime,
    device_slots: int,
    actor: str,
) -> Stay:
    """A ``draft`` stay; ``role_version`` defaults to the newest published version."""
    if starts_at >= ends_at:
        raise _invalid("starts_at must lie before ends_at", "ends_at")
    if await session.get(Unit, unit_id) is None:
        raise _invalid("Unknown unit", "unit_id")
    query = select(func.max(RoleVersion.version_number)).where(
        RoleVersion.role_template_id == role_template_id
    )
    if role_version is not None:
        query = query.where(RoleVersion.version_number == role_version)
    published = (await session.execute(query)).scalar_one()
    if published is None:
        raise _invalid("No such published role version", "role_version")
    stay = Stay(
        id=new_id("stay"),
        unit_id=unit_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="draft",
        external_ref=None,
        role_template_id=role_template_id,
        role_version=published,
        device_slots=device_slots,
        checked_out_at=None,
    )
    session.add(stay)
    await session.flush()
    params = {"stay_id": stay.id, "unit_id": unit_id, "role": f"{role_template_id}@{published}"}
    await _audit(session, "stay.create", actor, params, await bump_policy_version(session))
    return stay


async def issue_invites(
    session: AsyncSession,
    stay: Stay,
    *,
    count: int,
    redeem_minutes: int,
    origin: str | None,
    instance: Instance,
    now: datetime,
    actor: str,
) -> list[IssuedInvite]:
    """``count`` invitations on the lowest free device slots; confirms a ``draft`` stay.

    ``redeem_minutes`` up to ``MAX_REDEEM_MINUTES``, which the API checks.
    A slot is free unless an unrevoked invitation holds it; a redeemed one keeps its device.
    """
    if origin is None:
        raise ApiError(409, "CONFLICT", "Public origin not configured", {"reason": "no_origin"})
    if stay.status not in ("draft", "confirmed") or now >= stay.ends_at:
        raise ApiError(409, "CONFLICT", "Stay is closed", {"reason": stay_status(stay, now)})
    taken = set(
        (
            await session.execute(
                select(Invite.device_slot).where(
                    Invite.stay_id == stay.id, Invite.revoked_at.is_(None)
                )
            )
        ).scalars()
    )
    free = [slot for slot in range(1, stay.device_slots + 1) if slot not in taken]
    if len(free) < count:
        raise ApiError(409, "CONFLICT", "No free device slot", {"free_slots": len(free)})
    stay.status = "confirmed"
    redeem_until = min(now + timedelta(minutes=redeem_minutes), stay.ends_at)
    pin = next((p["spki_sha256"] for p in instance.trust_pins if p.get("role") == "primary"), "")
    issued = []
    for slot in free[:count]:
        secret = new_secret()
        invite = Invite(
            id=new_id("inv"),
            secret_hash=hash_secret(secret),
            purpose="guest_join",
            stay_id=stay.id,
            device_slot=slot,
            redeem_until=redeem_until,
            consumed_at=None,
            consumed_session_id=None,
            revoked_at=None,
            created_at=now,
        )
        session.add(invite)
        url = f"{origin}/join#i={secret}&inst={instance.id}&fp={quote(pin, safe='')}"
        issued.append(IssuedInvite(invite, url))
    await session.flush()
    version = await bump_policy_version(session)
    for item in issued:  # never the secret: ids and slot only
        slot = item.invite.device_slot
        params = {"stay_id": stay.id, "invite_id": item.invite.id, "device_slot": slot}
        await _audit(session, "invite.issue", actor, params, version)
    return issued


async def revoke_invite(
    session: AsyncSession, invite_id: str, *, now: datetime, actor: str
) -> None:
    """Revoke an unredeemed invitation; revoking it again changes nothing."""
    invite = await session.get(Invite, invite_id)
    if invite is None:
        raise ApiError(404, "NOT_FOUND", "Unknown invitation")
    if invite.revoked_at is not None:
        return
    if invite.consumed_at is not None:
        message = "Invitation already redeemed; revoke its session instead"
        raise ApiError(409, "CONFLICT", message, {"reason": "redeemed"})
    invite.revoked_at = now
    params = {"stay_id": invite.stay_id, "invite_id": invite.id}
    await _audit(session, "invite.revoke", actor, params, await bump_policy_version(session))


async def _audit(
    session: AsyncSession, operation: str, actor: str, params: Mapping[str, object], version: int
) -> None:
    event = AuditEvent(
        operation=operation,
        result="ok",
        actor=actor,
        policy_version=version,
        normalized_params=params,
    )
    await audit(session, event)
