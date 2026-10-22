"""Guest sessions on the public listener: redeem, session check, CSRF (design §4.3, E14, E25).

Session tokens, refresh tokens and WS tickets leave the server once and are stored only as
SHA-256 hashes. A browser session travels only as the cookie and a native one only as a bearer
token, so a stolen cookie value cannot skip the CSRF check by posing as a native client.
"""

import functools
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from sqlalchemy import select, update

from myha_gateway.auth.cookies import session_cookie_name
from myha_gateway.db import write_session
from myha_gateway.db.models import (
    Instance,
    Invite,
    Principal,
    RefreshToken,
    Session,
    Stay,
    WsTicket,
)
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.ha.client import utcnow
from myha_gateway.ids import constant_time_equal, hash_secret, new_id, new_secret
from myha_gateway.services.audit import AuditEvent, audit

LAST_SEEN_EVERY = timedelta(seconds=60)
TICKET_SECONDS = 30


@dataclass(frozen=True)
class SessionContext:
    """The checked session of a request; ``principal`` and ``stay`` only where it has them."""

    session: Session
    principal: Principal | None
    stay: Stay | None


@dataclass(frozen=True)
class Redeemed:
    session: Session
    token: str  # the cookie value, or the native access token
    refresh_token: str | None  # native only


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def app_context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


def instance_of(ctx: AppContext) -> Instance:
    """The instance row; missing only while a restore awaits review (design 4.6)."""
    if ctx.instance is None:
        details = {"reason": "restore_pending"}
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", "Restore pending", details)
    return ctx.instance


def cookie_name(ctx: AppContext) -> str:
    return session_cookie_name(instance_of(ctx).short_id)


def client_key(request: Request) -> str:
    """Rate-limit key of the TCP peer: an IPv4 address, or the /64 of an IPv6 address."""
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 4:
        return str(address)
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


@functools.cache  # one callable per limiter: FastAPI then runs it once per request
def rate_limit(name: str) -> Callable[[Request], Awaitable[None]]:
    """Dependency: 429 once the client is over the limiter ``ctx.limiters[name]``."""

    async def check(request: Request) -> None:  # async: the limiter is not thread-safe
        if not app_context(request).limiters[name].allow(client_key(request)):
            raise ApiError(429, "RATE_LIMITED", "Too many requests")

    return check


def redeem_refused() -> ApiError:
    """The one answer to every failed redeem, so that it tells nothing about why."""
    return ApiError(401, "UNAUTHENTICATED", "Invitation not valid")


def guest_not_after(instance: Instance, stay: Stay | None, now: datetime) -> datetime:
    """End of a guest browser session: ``guest_browser_days`` from now, never past the stay."""
    cap = now + timedelta(days=instance.session_limits["guest_browser_days"])
    return cap if stay is None else min(cap, stay.ends_at)


async def redeem(
    ctx: AppContext,
    *,
    secret: str,
    device_label: str | None,
    client: Literal["browser", "native"],
    client_ip: str,
) -> Redeemed:
    """Consume the invitation and create guest and session in one transaction (A22).

    Every refusal runs the same hash and UPDATE and ends in ``redeem_refused()``.
    """
    instance = instance_of(ctx)
    now, session_id = utcnow(), new_id("ses")
    consume = (
        update(Invite)
        .where(
            Invite.secret_hash == hash_secret(secret),
            Invite.purpose == "guest_join",  # test invitations come with the setup assistant
            Invite.consumed_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.redeem_until > now,
        )
        .values(consumed_at=now, consumed_session_id=session_id)
        .returning(Invite.id, Invite.stay_id)
        .execution_options(synchronize_session=False)
    )
    try:
        async with write_session(ctx.engine) as db:
            row = (await db.execute(consume)).one_or_none()
            if row is None:
                raise _Refused("invite_invalid")
            stay = None if row.stay_id is None else await db.get(Stay, row.stay_id)
            if stay is None or stay.status != "confirmed" or now >= stay.ends_at:
                raise _Refused("stay_closed")  # rolls the consumption back
            token, refresh, family_id = new_secret(), None, None
            if client == "native":
                # The session ends with its access token; the refresh family (Plan 3) renews it.
                minutes = instance.session_limits["native_access_minutes"]
                not_after = min(now + timedelta(minutes=minutes), stay.ends_at)
                refresh, family_id = new_secret(), new_id("fam")
            else:
                not_after = guest_not_after(instance, stay, now)
            principal = Principal(
                id=new_id("prn"),
                type="guest",
                status="active",
                display_name=device_label or "",
                locale=None,
                theme=None,
                stay_id=stay.id,
                invite_id=row.id,
                created_at=now,
            )
            session = Session(
                id=session_id,
                token_hash=hash_secret(token),
                family_id=family_id,
                principal_id=principal.id,
                audience=client,
                purpose="access",
                stay_id=stay.id,
                unit_ids=[stay.unit_id],  # a room change must follow it (WP3)
                not_before=stay.starts_at,
                not_after=not_after,
                last_seen_at=now,
                revoked_at=None,
                revoke_reason=None,
                csrf_secret=new_secret(),
                device_label=device_label,
                actor_admin_id=None,
                preview_target=None,
                created_at=now,
            )
            for parent in (principal, session):  # no ORM relationships: flush in FK order
                db.add(parent)
                await db.flush()
            if refresh is not None and family_id is not None:
                db.add(
                    RefreshToken(
                        id=new_id("rft"),
                        family_id=family_id,
                        token_hash=hash_secret(refresh),
                        session_id=session_id,
                        issued_at=now,
                        rotated_at=None,
                        used_at=None,
                    )
                )
            await db.flush()
            params = {"stay_id": stay.id, "invite_id": row.id, "audience": client}
            event = AuditEvent(
                operation="auth.redeem",
                result="ok",
                actor=principal.id,
                effective_principal=principal.id,
                session_id=session_id,
                normalized_params=params,
            )
            await audit(db, event)
    except _Refused as refused:
        key = f"auth.redeem|{refused.reason}|{now:%Y-%m-%dT%H:%M}|{client_ip}"
        event = AuditEvent(
            operation="auth.redeem", result="denied", reason_code=refused.reason, anon_key=key
        )
        async with write_session(ctx.engine) as db:
            await audit(db, event)
        raise redeem_refused() from None
    return Redeemed(session, token, refresh)


def _presented(request: Request, ctx: AppContext) -> tuple[str, bool] | None:
    """The bearer token, else the session cookie, and whether it came as the cookie."""
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        return (token, False) if scheme.lower() == "bearer" and token else None
    cookie = request.cookies.get(cookie_name(ctx))
    return (cookie, True) if cookie else None


def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Not signed in")


async def check_session(request: Request, purpose: str) -> SessionContext:
    """The request's session if it is active for ``purpose``; 401 otherwise.

    Unknown, revoked, of a disabled principal, of the wrong purpose or presented the wrong
    way: ``UNAUTHENTICATED``. Past ``not_after`` or of a stay no longer confirmed or over:
    ``ACCESS_EXPIRED``. A session before ``not_before`` passes; it is ``waiting``.
    """
    ctx = app_context(request)
    presented = _presented(request, ctx)
    if presented is None:
        raise _unauthenticated()
    token, as_cookie = presented
    query = (
        select(Session, Principal, Stay)
        .outerjoin(Principal, Principal.id == Session.principal_id)
        .outerjoin(Stay, Stay.id == Session.stay_id)
        .where(Session.token_hash == hash_secret(token))
    )
    async with ctx.sessions() as db:
        row = (await db.execute(query)).one_or_none()
    if row is None:
        raise _unauthenticated()
    session, principal, stay = row
    if (
        (session.audience == "browser") != as_cookie
        or session.purpose != purpose
        or session.revoked_at is not None
        or (principal is not None and principal.status != "active")
    ):
        raise _unauthenticated()
    now = utcnow()
    if now >= session.not_after or (
        stay is not None and (stay.status != "confirmed" or now >= stay.ends_at)
    ):
        raise ApiError(401, "ACCESS_EXPIRED", "Access expired")
    if now - session.last_seen_at >= LAST_SEEN_EVERY:
        async with write_session(ctx.engine) as db:
            touch = update(Session).where(Session.id == session.id).values(last_seen_at=now)
            await db.execute(touch)
        session.last_seen_at = now
    return SessionContext(session, principal, stay)


@functools.cache  # one callable per purpose: FastAPI then runs it once per request
def require_session(*, purpose: str) -> Callable[[Request], Awaitable[SessionContext]]:
    """Dependency: the request's active session of ``purpose``."""

    async def dependency(request: Request) -> SessionContext:
        return await check_session(request, purpose)

    return dependency


async def require_csrf_public(
    request: Request,
    current: Annotated[SessionContext, Depends(require_session(purpose="access"))],
) -> SessionContext:
    """Dependency for mutations: browser sessions need the CSRF token and the public origin.

    ``Sec-Fetch-Site`` must be ``same-origin`` if present, as for the admin listener. Native
    sessions carry a bearer token that no browser adds by itself, so they need neither.
    """
    if current.session.audience == "browser":
        headers = request.headers
        if not (
            headers.get("sec-fetch-site", "same-origin") == "same-origin"
            and headers.get("origin") == app_context(request).public_origin
            and constant_time_equal(headers.get("x-myha-csrf", ""), current.session.csrf_secret)
        ):
            raise ApiError(403, "UNAUTHENTICATED", "CSRF check failed")
    return current


def session_view(session: Session, now: datetime) -> dict[str, Any]:
    """What a client sees of its session window."""
    waiting = session.not_before is not None and now < session.not_before
    return {"not_before": session.not_before, "not_after": session.not_after, "waiting": waiting}


async def revoke(ctx: AppContext, current: SessionContext, reason: str) -> None:
    """Revoke the session now; its refresh family dies with it."""
    session, now = current.session, utcnow()
    async with write_session(ctx.engine) as db:
        query = update(Session).where(Session.id == session.id, Session.revoked_at.is_(None))
        await db.execute(query.values(revoked_at=now, revoke_reason=reason))
        event = AuditEvent(
            operation=f"auth.{reason}",
            result="ok",
            actor=session.principal_id or "anonymous",
            effective_principal=session.principal_id,
            session_id=session.id,
        )
        await audit(db, event)


async def renew(ctx: AppContext, current: SessionContext) -> Session:
    """Move a browser session's end to what stay and limits allow now; never further."""
    session, now = current.session, utcnow()
    if session.audience != "browser":
        raise ApiError(400, "INVALID_PARAMETERS", "Native sessions renew through refresh")
    not_after = guest_not_after(instance_of(ctx), current.stay, now)
    async with write_session(ctx.engine) as db:
        query = update(Session).where(Session.id == session.id).values(not_after=not_after)
        await db.execute(query)
    session.not_after = not_after
    return session


async def issue_ws_ticket(ctx: AppContext, current: SessionContext) -> str:
    """A one-time ticket for the WebSocket, valid ``TICKET_SECONDS``; only its hash is kept."""
    ticket, now = new_secret(), utcnow()
    async with write_session(ctx.engine) as db:
        db.add(
            WsTicket(
                id=new_id("wst"),
                ticket_hash=hash_secret(ticket),
                session_id=current.session.id,
                expires_at=now + timedelta(seconds=TICKET_SECONDS),
                used_at=None,
            )
        )
    return ticket
