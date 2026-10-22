"""Admin previews: another context's dashboard and simulated decisions (design §4.5, A28-A30).

A preview is a session with ``purpose=preview`` that only its admin can use, only on the admin
listener, for 15 minutes. Its context is rebuilt from the stored target on every call, so it
follows the live policy; ``at`` shifts the time the policy is evaluated at, never the device
states (A29). Nothing here can act: ``decide`` runs with ``execute=False`` and no path leads to
a service call (A28).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.ha.client import utcnow
from myha_gateway.ids import hash_secret, new_id, new_secret
from myha_gateway.policy.context import EffectiveContext, build_context_for_preview
from myha_gateway.policy.decide import Decision, decide
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.projection.dashboard import load_layout, project_dashboard
from myha_gateway.services.audit import AuditEvent, audit

PREVIEW_MINUTES = 15


@dataclass(frozen=True)
class Target:
    """A preview target's context and the rows that describe it to the admin."""

    target: Mapping[str, str]
    context: EffectiveContext
    principal: db.Principal | None
    stay: db.Stay | None
    invite: db.Invite | None
    group: db.Group | None
    unit_names: Mapping[str, str]
    role_names: Mapping[str, str]


@dataclass(frozen=True)
class Opened:
    """A usable preview of the requesting admin, at the time it is evaluated."""

    session: db.Session
    target: Target
    index: PolicyIndex
    at: datetime
    hypothetical: bool  # ``at`` was given, not the server time


def _not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"Unknown {what}")


async def _get[T](session: AsyncSession, model: type[T], key: str | None, what: str) -> T | None:
    """The row ``key`` names; 404 if it names one that does not exist."""
    if key is None:
        return None
    row = await session.get(model, key)
    if row is None:
        raise _not_found(what)
    return row


async def _all[T](session: AsyncSession, model: type[T]) -> list[T]:
    return list((await session.scalars(select(model))).all())


async def _load(session: AsyncSession, target: Mapping[str, str], at: datetime) -> Target:
    """The target's context from one read transaction; 404 for unknown rows, 400 for misfits."""
    version, trusted = (
        await session.execute(select(db.Instance.policy_version, db.Instance.time_trusted))
    ).one()
    principal = await _get(session, db.Principal, target.get("principal_id"), "principal")
    preview_of = await _get(session, db.Session, target.get("session_id"), "session")
    invite = await _get(session, db.Invite, target.get("invite_id"), "invitation")
    group = await _get(session, db.Group, target.get("group_id"), "group")
    stay_id = target.get("stay_id")
    for owner in (invite, principal):  # an invitation's or a guest's stay
        stay_id = stay_id or (None if owner is None else owner.stay_id)
    stay = await _get(session, db.Stay, stay_id, "stay")
    memberships = [] if principal is None else await _all(session, db.Membership)
    try:
        context = build_context_for_preview(
            target,
            instance=db.Instance(policy_version=version, time_trusted=trusted),
            at=at,
            principal=principal,
            session=preview_of,
            stay=stay,
            invite=invite,
            # shortcut: whole tables, the builder filters them; fine at home scale
            assignments=await _all(session, db.Assignment),
            memberships=memberships,
            fences=await _all(session, db.Fence),
        )
    except ValueError as exc:  # rows that do not belong together, or no single target form
        raise ApiError(400, "INVALID_PARAMETERS", str(exc)) from None
    units = (await session.execute(select(db.Unit.id, db.Unit.name))).all()
    roles = (await session.execute(select(db.RoleTemplate.id, db.RoleTemplate.name))).all()
    unit_names = {unit_id: name for unit_id, name in units}
    role_names = {role_id: name for role_id, name in roles}
    return Target(target, context, principal, stay, invite, group, unit_names, role_names)


async def create_preview(
    ctx: AppContext, target: Mapping[str, str], at: datetime | None, admin_id: str
) -> tuple[db.Session, str]:
    """A preview session of ``target`` for this admin, and its token, which the API never shows.

    ``at`` is the preview's default evaluation time.
    """
    now = utcnow()
    async with ctx.sessions() as session:
        loaded = await _load(session, target, at or now)
    context = loaded.context
    units = None  # a group's hypothetical member has no cap
    if loaded.group is None:
        units = sorted({unit for binding in context.role_bindings for unit in binding.unit_ids})
    token = new_secret()
    row = db.Session(
        id=new_id("ses"),
        token_hash=hash_secret(token),
        family_id=None,
        principal_id=None if loaded.principal is None else loaded.principal.id,
        audience="admin",
        purpose="preview",
        stay_id=None,
        unit_ids=units,
        not_before=None,
        not_after=now + timedelta(minutes=PREVIEW_MINUTES),
        last_seen_at=now,
        revoked_at=None,
        revoke_reason=None,
        csrf_secret=new_secret(),
        device_label=None,
        actor_admin_id=admin_id,
        preview_target={"target": dict(target), "at": None if at is None else at.isoformat()},
        created_at=now,
    )
    async with write_session(ctx.engine) as session:
        session.add(row)
        await session.flush()
        event = AuditEvent(
            operation="preview.create",
            result="ok",
            actor=f"admin:{admin_id}",
            effective_principal=row.principal_id,
            session_id=row.id,
            policy_version=context.policy_version,
            normalized_params={"purpose": "preview", "target": dict(target)},
        )
        await audit(session, event)
    return row, token


async def open_preview(
    ctx: AppContext, preview_id: str, admin_id: str, at: datetime | None
) -> Opened:
    """The admin's own live preview; any other id answers as unknown, so none is revealed."""
    now = utcnow()
    async with ctx.sessions() as session:
        row = await session.get(db.Session, preview_id)
        if (
            row is None
            or row.purpose != "preview"
            or row.actor_admin_id != admin_id
            or row.revoked_at is not None
            or now >= row.not_after
            or row.preview_target is None
        ):
            raise _not_found("preview")
        stored = row.preview_target.get("at")
        default = None if stored is None else datetime.fromisoformat(stored)
        when = at or default or now
        loaded = await _load(session, row.preview_target["target"], when)
    index = await ctx.policy.index(now)
    if index.policy_version != loaded.context.policy_version:
        details = {"reason": "policy_changed"}
        raise ApiError(409, "CONFLICT", "The policy changed meanwhile; try again", details)
    return Opened(row, loaded, index, when, hypothetical=when != now)


async def preview_dashboard(ctx: AppContext, opened: Opened) -> dict[str, Any]:
    """The target's dashboard with a banner, the facts it is built from and what it hides."""
    async with ctx.sessions() as session:
        layout = await load_layout(session)
    view = project_dashboard(
        opened.target.context, opened.index, ctx.policy.state_view, layout, opened.at
    )
    target = opened.target
    return {
        "banner": {
            "who": _who(target),
            "purpose": "preview",
            "hypothetical": target.principal is None,
            "expires_at": opened.session.not_after,
        },
        "context": _facts(opened),
        "hidden_note": _hidden_note(opened, view.status),
        "dashboard": view.model_dump(mode="json", exclude_unset=True),
    }


async def simulate(
    ctx: AppContext,
    opened: Opened,
    admin_id: str,
    resource_id: str,
    action: str,
    parameters: Mapping[str, Any],
) -> Decision:
    """What ``decide`` would say to the target, with the live device state; audited, never run."""
    view = ctx.policy.state_view(resource_id)  # after open_preview: the index is built
    decision = decide(
        opened.target.context,
        opened.index,
        resource_id,
        action,
        parameters,
        opened.at,
        view,
        execute=False,
    )
    event = AuditEvent(
        operation="preview.simulate",
        result="ok",
        actor=f"admin:{admin_id}",
        effective_principal=opened.session.principal_id,
        session_id=opened.session.id,
        reason_code=decision.code,
        resource_id=resource_id,
        policy_version=opened.index.policy_version,
        normalized_params={
            "purpose": "preview",
            "action": action,
            "parameters": dict(decision.normalized_params),
            "at": opened.at.isoformat() if opened.hypothetical else None,
        },
    )
    async with write_session(ctx.engine) as session:
        await audit(session, event)
    return decision


def _unit(target: Target, unit_id: str) -> str:
    return target.unit_names.get(unit_id, unit_id)


def _who(target: Target) -> str:
    """The target as the admin knows it: a name, never an entity id."""
    if target.principal is not None:
        return target.principal.display_name or f"{target.principal.type} {target.principal.id}"
    if target.group is not None:
        return f"Hypothetical member of {target.group.name}"
    assert target.stay is not None  # the remaining targets are a stay or its invitation
    return f"Hypothetical guest of {_unit(target, target.stay.unit_id)}"


def _facts(opened: Opened) -> list[str]:
    target = opened.target
    context = target.context
    facts = []
    if target.principal is not None:
        facts.append(f"Principal {target.principal.id} ({target.principal.type})")
        if "session_id" in target.target:
            facts.append(f"Limited to session {target.target['session_id']}")
    if target.group is not None:
        facts.append(f"Hypothetical person whose only bindings are those of {target.group.name}")
    if target.stay is not None:
        stay = target.stay
        facts.append(f"Stay {stay.id} ({stay.status}) in {_unit(target, stay.unit_id)}")
    if target.invite is not None:
        invite = target.invite
        state = "redeemed" if invite.consumed_at is not None else "not redeemed"
        facts.append(
            f"Invitation {invite.id}, slot {invite.device_slot}, {state}, "
            f"redeemable until {invite.redeem_until.isoformat()}"
        )
    for binding in context.role_bindings:
        name = target.role_names.get(binding.role_template_id, binding.role_template_id)
        units = ", ".join(sorted(_unit(target, unit) for unit in binding.unit_ids)) or "no unit"
        facts.append(f"Role {name} v{binding.role_version} in {units}")
    if not context.role_bindings:
        facts.append("No role binding")
    start = "open" if context.not_before is None else context.not_before.isoformat()
    end = "open" if context.not_after is None else context.not_after.isoformat()
    facts.append(f"Window from {start} until {end}")
    when = opened.at.isoformat()
    source = "a hypothetical time" if opened.hypothetical else "the server time"
    facts.append(f"Policy evaluated at {when}, {source}")
    facts.append("Device states as currently measured")
    return facts


def _hidden_note(opened: Opened, status: str) -> str:
    """What the dashboard leaves out: blocked devices, and controls of devices under review."""
    index, context = opened.index, opened.target.context
    statuses = [
        index.resources[rid].status for rid in index.resources if index.in_scope(context, rid)
    ]
    blocked, review = statuses.count("blocked"), statuses.count("needs_review")
    note = (
        f"{blocked} blocked device(s) in scope are hidden; "
        f"{review} device(s) awaiting review show no working controls."
    )
    if status != "online":
        note = f"The access is {status} at this time, so no device is shown. {note}"
    return note
