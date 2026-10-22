"""Revoking sessions (design §4.3): rows in one transaction, after the commit the bus event.

Open connections close on the event; one that misses it finds the revocation at its next
check (``events.connection.RECHECK_SECONDS``). A redeemed invitation whose session is revoked
frees its device slot (``services.stays``), so nothing else changes here.
"""

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db import write_session
from myha_gateway.db.models import Invite, Principal, Stay
from myha_gateway.db.models import Session as SessionRow
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.events.bus import SessionRevoked
from myha_gateway.ha.client import utcnow
from myha_gateway.services.audit import AuditEvent, audit


async def revoke_session(
    app: AppContext,
    session_id: str,
    reason: str,
    *,
    actor: str,
    operation: str = "session.revoke",
) -> None:
    """Revoke the session and its refresh family; revoking it again changes nothing.

    ``operation`` names the audit event, such as ``auth.logout`` for the guest's own logout.
    """
    async with write_session(app.engine) as session:
        row = await session.get(SessionRow, session_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Unknown session")
        revoked = await _revoke(session, SessionRow.id == session_id, reason, utcnow())
        if revoked:
            params = {"session_ids": revoked}
            event = AuditEvent(
                operation=operation,
                result="ok",
                actor=actor,
                effective_principal=row.principal_id,
                session_id=session_id,
                reason_code=reason,
                normalized_params=params,
            )
            await audit(session, event)
    for revoked_id in revoked:
        app.bus.publish(SessionRevoked(reason, session_id=revoked_id))


async def revoke_stay_sessions(app: AppContext, stay_id: str, reason: str, *, actor: str) -> None:
    """Revoke every session of the stay with their refresh families, and its open invitations."""
    now = utcnow()
    async with write_session(app.engine) as session:
        if await session.get(Stay, stay_id) is None:
            raise ApiError(404, "NOT_FOUND", "Unknown stay")
        guests = select(Principal.id).where(Principal.stay_id == stay_id)
        of_stay = or_(SessionRow.stay_id == stay_id, SessionRow.principal_id.in_(guests))
        revoked = await _revoke(session, of_stay, reason, now)
        query = (
            update(Invite)
            .where(Invite.stay_id == stay_id, Invite.revoked_at.is_(None))
            .where(Invite.consumed_at.is_(None))
            .values(revoked_at=now)
            .returning(Invite.id)
        )
        invites = list((await session.scalars(query)).all())
        params: Mapping[str, object] = {
            "stay_id": stay_id,
            "session_ids": revoked,
            "invite_ids": invites,
        }
        event = AuditEvent(
            operation="stay.revoke",
            result="ok",
            actor=actor,
            reason_code=reason,
            normalized_params=params,
        )
        await audit(session, event)
    app.bus.publish(SessionRevoked(reason, stay_id=stay_id))  # its guests' families share it


async def _revoke(
    session: AsyncSession, match: ColumnElement[bool], reason: str, now: datetime
) -> list[str]:
    """Revoke the live sessions that ``match`` or share a refresh family with one; their ids."""
    families = select(SessionRow.family_id).where(match, SessionRow.family_id.is_not(None))
    query = (
        update(SessionRow)
        .where(or_(match, SessionRow.family_id.in_(families)), SessionRow.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
        .returning(SessionRow.id)
    )
    return sorted((await session.scalars(query)).all())
