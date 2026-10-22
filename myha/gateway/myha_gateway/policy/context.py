"""The effective context of a session or a preview (design §4.1, §4.5).

Pure functions over rows the caller loaded; loading comes with plan 2 (``policy/loader.py``).
Rows that do not belong together raise ``ValueError`` instead of producing some context.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from myha_gateway.db import models as db
from myha_gateway.policy.model import Fence

Purpose = Literal["access", "preview", "test"]
# Stay states that give a guest access; anything else, unknown states too, gives none.
_ACTIVE_STAYS: Mapping[str, frozenset[str]] = {
    "access": frozenset({"confirmed"}),
    "preview": frozenset({"confirmed", "draft"}),  # an admin may look at a stay before confirming
}


@dataclass(frozen=True)
class RoleBinding:
    role_template_id: str
    role_version: int
    unit_ids: frozenset[str]


@dataclass(frozen=True)
class EffectiveContext:
    # principal id | "hypothetical:guest:<stay_id>" | "hypothetical:group:<group_id>"
    # | "test:<session_id>" for a test session without principal
    principal_ref: str
    role_bindings: tuple[RoleBinding, ...]
    fences: tuple[Fence, ...]  # unit fences of every unit: the index filters by resource
    not_before: datetime | None
    not_after: datetime | None
    stay_id: str | None
    purpose: Purpose
    policy_version: int
    session_active: bool = True
    time_trusted: bool = True


def build_context_for_session(
    session: db.Session,
    principal: db.Principal | None,
    assignments: Iterable[db.Assignment],
    memberships: Iterable[db.Membership],
    stay: db.Stay | None,
    fences: Iterable[db.Fence],
    instance: db.Instance,
    now: datetime,
) -> EffectiveContext:
    """The context of an access or test session, following the source table of design §4.1."""
    if session.purpose == "test":  # proves reachability: no binding, no stay (design §4.3)
        return EffectiveContext(
            principal_ref=session.principal_id or f"test:{session.id}",
            role_bindings=(),
            fences=(),
            not_before=session.not_before,
            not_after=session.not_after,
            stay_id=None,
            purpose="test",
            policy_version=instance.policy_version,
            session_active=session.revoked_at is None,
            time_trusted=instance.time_trusted,
        )
    if session.purpose != "access":
        raise ValueError(f"no session context for purpose {session.purpose!r}")
    if principal is None or principal.id != session.principal_id:
        raise ValueError("the session belongs to another principal")
    return _principal_context(
        principal, session, assignments, memberships, stay, fences, instance, now, "access"
    )


def build_context_for_preview(
    target: Mapping[str, str],
    *,
    instance: db.Instance,
    at: datetime,
    principal: db.Principal | None = None,
    session: db.Session | None = None,
    stay: db.Stay | None = None,
    invite: db.Invite | None = None,
    assignments: Iterable[db.Assignment] = (),
    memberships: Iterable[db.Membership] = (),
    fences: Iterable[db.Fence] = (),
) -> EffectiveContext:
    """The context a preview shows (design §4.5); ``at`` is the possibly hypothetical now.

    ``target`` is one of ``{principal_id, session_id?}``, ``{stay_id}``, ``{invite_id}`` and
    ``{group_id}``; the rows are the ones it names.
    """
    keys = set(target)
    if keys == {"group_id"}:
        group_id = target["group_id"]
        ref = f"hypothetical:group:{group_id}"
        return _member_context(
            ref, None, {group_id}, None, assignments, fences, instance, "preview"
        )
    if keys == {"invite_id"}:  # an invite shows the guest of its stay
        if invite is None or invite.id != target["invite_id"] or invite.stay_id is None:
            raise ValueError("the invite does not match the preview target")
        keys, target = {"stay_id"}, {"stay_id": invite.stay_id}
    if keys == {"stay_id"}:
        if stay is None or stay.id != target["stay_id"]:
            raise ValueError("the stay does not match the preview target")
        ref = f"hypothetical:guest:{stay.id}"
        return _guest_context(ref, None, stay, None, fences, instance, "preview")
    if keys not in ({"principal_id"}, {"principal_id", "session_id"}):
        raise ValueError("unknown preview target")
    if principal is None or principal.id != target["principal_id"]:
        raise ValueError("the principal does not match the preview target")
    if session is not None and session.principal_id != principal.id:
        raise ValueError("the session belongs to another principal")
    if (None if session is None else session.id) != target.get("session_id"):
        raise ValueError("the session does not match the preview target")
    return _principal_context(
        principal, session, assignments, memberships, stay, fences, instance, at, "preview"
    )


def _principal_context(
    principal: db.Principal,
    session: db.Session | None,
    assignments: Iterable[db.Assignment],
    memberships: Iterable[db.Membership],
    stay: db.Stay | None,
    fences: Iterable[db.Fence],
    instance: db.Instance,
    now: datetime,
    purpose: Purpose,
) -> EffectiveContext:
    if principal.type == "guest":  # exactly the stay's binding; invites carry no role
        if stay is None or stay.id != principal.stay_id:
            raise ValueError("a guest acts only through the stay of its invitation")
        if session is not None and session.stay_id != stay.id:
            raise ValueError("the session belongs to another stay")
        return _guest_context(principal.id, principal.id, stay, session, fences, instance, purpose)
    if principal.type not in ("resident", "display"):
        raise ValueError(f"unknown principal type {principal.type!r}")
    groups = {
        membership.group_id
        for membership in memberships
        if membership.principal_id == principal.id
        and membership.valid_from <= now
        and (membership.valid_to is None or now < membership.valid_to)
    }
    return _member_context(
        principal.id, principal.id, groups, session, assignments, fences, instance, purpose
    )


def _member_context(
    ref: str,
    principal_id: str | None,
    group_ids: set[str],
    session: db.Session | None,
    assignments: Iterable[db.Assignment],
    fences: Iterable[db.Fence],
    instance: db.Instance,
    purpose: Purpose,
) -> EffectiveContext:
    """Residents, displays and the hypothetical member of a group: their active assignments."""
    subjects = {("principal", principal_id)} | {("group", group_id) for group_id in group_ids}
    bindings = (
        RoleBinding(row.role_template_id, row.role_version, frozenset(row.unit_ids))
        for row in assignments
        if row.status == "active" and (row.subject_type, row.subject_id) in subjects
    )
    session_id = None if session is None else session.id
    return EffectiveContext(
        principal_ref=ref,
        role_bindings=_capped(bindings, session),
        fences=_fences(fences, principal=principal_id, session=session_id),
        not_before=None if session is None else session.not_before,
        not_after=None if session is None else session.not_after,
        stay_id=None,
        purpose=purpose,
        policy_version=instance.policy_version,
        session_active=session is None or session.revoked_at is None,
        time_trusted=instance.time_trusted,
    )


def _guest_context(
    ref: str,
    principal_id: str | None,
    stay: db.Stay,
    session: db.Session | None,
    fences: Iterable[db.Fence],
    instance: db.Instance,
    purpose: Purpose,
) -> EffectiveContext:
    """A real or hypothetical guest: one binding and the window of the stay."""
    binding = RoleBinding(stay.role_template_id, stay.role_version, frozenset({stay.unit_id}))
    session_id = None if session is None else session.id
    return EffectiveContext(
        principal_ref=ref,
        role_bindings=_capped((binding,), session),
        fences=_fences(fences, principal=principal_id, stay=stay.id, session=session_id),
        not_before=stay.starts_at,
        not_after=stay.ends_at if session is None else min(stay.ends_at, session.not_after),
        stay_id=stay.id,
        purpose=purpose,
        policy_version=instance.policy_version,
        session_active=stay.status in _ACTIVE_STAYS.get(purpose, frozenset())
        and (session is None or session.revoked_at is None),
        time_trusted=instance.time_trusted,
    )


def _capped(bindings: Iterable[RoleBinding], session: db.Session | None) -> tuple[RoleBinding, ...]:
    """Distinct bindings, cut to the session's units when it names any (an empty list: no cap).

    A binding cut to no unit stays: it grants nothing, but its denies still apply.
    """
    cap = frozenset(session.unit_ids) if session is not None and session.unit_ids else None
    return tuple(
        dict.fromkeys(b if cap is None else replace(b, unit_ids=b.unit_ids & cap) for b in bindings)
    )


def _fences(rows: Iterable[db.Fence], **own: str | None) -> tuple[Fence, ...]:
    """Instance and unit fences plus those of the context's own principal, stay and session.

    Every row is parsed, so a broken fence fails the context instead of being skipped.
    """
    mine = {(scope, scope_id) for scope, scope_id in own.items() if scope_id is not None}
    parsed = (Fence.model_validate(row, from_attributes=True) for row in rows)
    return tuple(
        fence
        for fence in parsed
        if fence.scope_type in ("instance", "unit") or (fence.scope_type, fence.scope_id) in mine
    )
