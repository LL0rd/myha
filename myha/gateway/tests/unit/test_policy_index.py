"""Effective context (design §4.1, §4.5) and the immutable policy index, built from ORM rows."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from myha_gateway.adapters.base import Action, DeviceCapabilities
from myha_gateway.db import models as db
from myha_gateway.policy.codes import DecisionCode
from myha_gateway.policy.context import (
    EffectiveContext,
    RoleBinding,
    build_context_for_preview,
    build_context_for_session,
)
from myha_gateway.policy.index import EffectiveCapability, PolicyIndex, ResourceRef
from myha_gateway.policy.model import Bound, Fence, Interval, RoleRules

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
INSTANCE = db.Instance(policy_version=7, time_trusted=True)
STAY_BINDING = RoleBinding("room_guest", 1, frozenset({"unit_203"}))

# --- rows ---


def principal(principal_id: str = "prn_res", kind: str = "resident") -> db.Principal:
    return db.Principal(id=principal_id, type=kind, status="active")


def guest() -> db.Principal:
    return db.Principal(id="prn_guest", type="guest", status="active", stay_id="stay_203")


def session(
    session_id: str = "ses_1", principal_id: str | None = "prn_res", **fields: Any
) -> db.Session:
    base = {
        "id": session_id,
        "principal_id": principal_id,
        "purpose": "access",
        "stay_id": None,
        "not_before": None,
        "not_after": NOW + 30 * DAY,
        "revoked_at": None,
        "unit_ids": None,  # no cap
    }
    return db.Session(**(base | fields))


def guest_session(session_id: str = "ses_guest", **fields: Any) -> db.Session:
    window = {
        "stay_id": "stay_203",
        "unit_ids": ["unit_203"],
        "not_before": NOW - 2 * DAY,  # the stay starts later; its start opens the window
        "not_after": NOW + DAY,
    }
    return session(session_id, "prn_guest", **(window | fields))


def stay(**fields: Any) -> db.Stay:
    base = {
        "id": "stay_203",
        "unit_id": "unit_203",
        "starts_at": NOW - DAY,
        "ends_at": NOW + 2 * DAY,
        "status": "confirmed",
        "role_template_id": "room_guest",
        "role_version": 1,
    }
    return db.Stay(**(base | fields))


def assignment(
    subject: str,
    template: str,
    units: list[str],
    *,
    group: bool = False,
    version: int = 1,
    status: str = "active",
) -> db.Assignment:
    return db.Assignment(
        id=f"asg_{template}_{subject}",
        subject_type="group" if group else "principal",
        subject_id=subject,
        role_template_id=template,
        role_version=version,
        unit_ids=units,
        status=status,
    )


def membership(
    group_id: str,
    *,
    valid_from: datetime = NOW - DAY,
    valid_to: datetime | None = None,
    principal_id: str = "prn_res",
) -> db.Membership:
    return db.Membership(
        id=f"mem_{group_id}_{principal_id}",
        principal_id=principal_id,
        group_id=group_id,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def fence_row(scope_type: str, scope_id: str | None, max_c: str = "22", **fields: Any) -> db.Fence:
    base = {
        "id": f"fen_{scope_type}_{scope_id}",
        "scope_type": scope_type,
        "scope_id": scope_id,
        "resource_ids": None,
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "0", "max": max_c}},
    }
    return db.Fence(**(base | fields))


FENCE_ROWS = [
    fence_row("instance", None),
    fence_row("unit", "unit_203"),
    fence_row("unit", "unit_lr"),
    fence_row("principal", "prn_res"),
    fence_row("principal", "prn_guest"),
    fence_row("principal", "prn_other"),
    fence_row("stay", "stay_203"),
    fence_row("stay", "stay_other"),
    fence_row("session", "ses_1"),
    fence_row("session", "ses_2"),
    fence_row("session", "ses_guest"),
]
SHARED_SCOPES = {("instance", None), ("unit", "unit_203"), ("unit", "unit_lr")}


def scopes(ctx: EffectiveContext) -> set[tuple[str, str | None]]:
    return {(fence.scope_type, fence.scope_id) for fence in ctx.fences}


def for_session(
    ses: db.Session,
    who: db.Principal | None,
    *,
    assignments: list[db.Assignment] | None = None,
    memberships: list[db.Membership] | None = None,
    the_stay: db.Stay | None = None,
    fences: list[db.Fence] | None = None,
    instance: db.Instance = INSTANCE,
    now: datetime = NOW,
) -> EffectiveContext:
    return build_context_for_session(
        ses, who, assignments or [], memberships or [], the_stay, fences or [], instance, now
    )


# --- context of a session ---


@pytest.mark.parametrize("kind", ["resident", "display"])
def test_resident_binds_own_and_group_assignments_of_memberships_valid_now(kind: str) -> None:
    memberships = [
        membership("grp_family"),
        membership("grp_from_now", valid_from=NOW),  # valid_from is inclusive
        membership("grp_expired", valid_to=NOW),  # valid_to is exclusive
        membership("grp_future", valid_from=NOW + DAY),
        membership("grp_foreign", principal_id="prn_other"),
    ]
    assignments = [
        assignment("prn_res", "resident", ["unit_lr"], version=2),
        assignment("grp_family", "family", ["unit_203", "unit_lr"], group=True),
        assignment("grp_from_now", "early", ["unit_203"], group=True),
        assignment("grp_expired", "old", ["unit_x"], group=True),
        assignment("grp_future", "later", ["unit_x"], group=True),
        assignment("grp_foreign", "foreign", ["unit_x"], group=True),
        assignment("prn_other", "other", ["unit_x"]),
        assignment("prn_res", "paused", ["unit_x"], status="disabled_pending_review"),
    ]
    ctx = for_session(
        session(), principal(kind=kind), assignments=assignments, memberships=memberships
    )
    assert set(ctx.role_bindings) == {
        RoleBinding("resident", 2, frozenset({"unit_lr"})),
        RoleBinding("family", 1, frozenset({"unit_203", "unit_lr"})),
        RoleBinding("early", 1, frozenset({"unit_203"})),
    }
    assert (ctx.principal_ref, ctx.stay_id, ctx.purpose) == ("prn_res", None, "access")
    assert (ctx.not_before, ctx.not_after) == (None, NOW + 30 * DAY)
    assert ctx.session_active and ctx.time_trusted and ctx.policy_version == 7


def test_identical_assignments_give_one_binding() -> None:
    assignments = [
        assignment("prn_res", "family", ["unit_203"]),
        assignment("grp_family", "family", ["unit_203"], group=True),
    ]
    ctx = for_session(
        session(), principal(), assignments=assignments, memberships=[membership("grp_family")]
    )
    assert ctx.role_bindings == (RoleBinding("family", 1, frozenset({"unit_203"})),)


def test_session_units_cap_every_binding_and_none_sets_no_cap() -> None:
    assignments = [
        assignment("prn_res", "family", ["unit_203", "unit_lr"]),
        assignment("grp_family", "family", ["unit_203"], group=True),
        assignment("prn_res", "living", ["unit_lr"], version=3),
    ]
    rows: dict[str, Any] = {"assignments": assignments, "memberships": [membership("grp_family")]}
    capped = for_session(session(unit_ids=["unit_203"]), principal(), **rows)
    assert capped.role_bindings == (
        RoleBinding("family", 1, frozenset({"unit_203"})),
        RoleBinding("living", 3, frozenset()),  # kept for its denies
    )
    assert INDEX.in_scope(capped, "res_203_trv")
    assert not INDEX.in_scope(capped, "res_lr_ac")
    uncapped = for_session(session(), principal(), **rows)
    assert INDEX.in_scope(uncapped, "res_lr_ac")
    nothing = for_session(session(unit_ids=[]), principal(), **rows)  # an empty cap: no unit
    assert all(not binding.unit_ids for binding in nothing.role_bindings)
    assert not INDEX.in_scope(nothing, "res_203_trv")
    preview = build_context_for_preview(
        {"principal_id": "prn_res", "session_id": "ses_1"},
        principal=principal(),
        session=session(unit_ids=["unit_203"]),
        instance=INSTANCE,
        at=NOW,
        **rows,
    )
    assert preview == replace(capped, purpose="preview")
    elsewhere = for_session(guest_session(unit_ids=["unit_lr"]), guest(), the_stay=stay())
    assert elsewhere.role_bindings == (RoleBinding("room_guest", 1, frozenset()),)
    assert not INDEX.in_scope(elsewhere, "res_203_trv")


def test_a_session_cap_keeps_the_denies_of_the_bindings_it_empties() -> None:
    """A cap only narrows: LIVING, bound to unit_lr, denies set_level on the room-203 light."""
    grant = assignment("prn_res", "room_guest", ["unit_203", "unit_lr"])
    deny = assignment("prn_res", "living", ["unit_lr"], version=3)
    granted = for_session(session(unit_ids=["unit_203"]), principal(), assignments=[grant])
    assert INDEX.effective_capability(granted, "res_203_light", Action.LIGHT_SET_LEVEL)
    for unit_ids in (None, ["unit_203"]):
        both = for_session(session(unit_ids=unit_ids), principal(), assignments=[grant, deny])
        assert INDEX.effective_capability(both, "res_203_light", Action.LIGHT_SET_LEVEL) is None
    no_units = assignment("prn_res", "living", [], version=3)
    unbound = for_session(session(), principal(), assignments=[grant, no_units])
    assert INDEX.effective_capability(unbound, "res_203_light", Action.LIGHT_SET_LEVEL) is None


@pytest.mark.parametrize(
    ("session_end", "window_end"),
    [(NOW + DAY, NOW + DAY), (NOW + 5 * DAY, NOW + 2 * DAY)],
)
def test_guest_has_exactly_the_stay_binding_and_window(
    session_end: datetime, window_end: datetime
) -> None:
    ctx = for_session(
        guest_session(not_after=session_end),
        guest(),
        assignments=[
            assignment("prn_guest", "resident", ["unit_lr"]),
            assignment("grp_family", "family", ["unit_lr"], group=True),
        ],
        memberships=[membership("grp_family", principal_id="prn_guest")],
        the_stay=stay(),
    )
    assert ctx.role_bindings == (STAY_BINDING,)
    assert (ctx.not_before, ctx.not_after) == (NOW - DAY, window_end)
    assert (ctx.principal_ref, ctx.stay_id, ctx.purpose) == ("prn_guest", "stay_203", "access")
    assert ctx.session_active


@pytest.mark.parametrize(
    ("status", "revoked_at", "active"),
    [
        ("confirmed", None, True),
        ("cancelled", None, False),
        ("checked_out", None, False),
        ("confirmed", NOW, False),
    ],
)
def test_guest_session_is_inactive_after_cancel_checkout_or_revocation(
    status: str, revoked_at: datetime | None, active: bool
) -> None:
    ses = guest_session(revoked_at=revoked_at)
    assert for_session(ses, guest(), the_stay=stay(status=status)).session_active is active


@pytest.mark.parametrize(
    ("status", "for_access", "for_preview"),
    [
        ("confirmed", True, True),
        ("draft", False, True),
        ("cancelled", False, False),
        ("checked_out", False, False),
        ("archived", False, False),  # a state this code does not know
    ],
)
def test_only_a_confirmed_stay_is_active_and_a_preview_also_shows_a_draft(
    status: str, for_access: bool, for_preview: bool
) -> None:
    the_stay = stay(status=status)
    assert for_session(guest_session(), guest(), the_stay=the_stay).session_active is for_access
    preview = build_context_for_preview(
        {"stay_id": "stay_203"}, stay=the_stay, instance=INSTANCE, at=NOW
    )
    assert preview.session_active is for_preview


@pytest.mark.parametrize(
    ("status", "active"), [("active", True), ("disabled", False), ("x", False)]
)
def test_only_an_active_principal_has_an_active_session(status: str, active: bool) -> None:
    resident = db.Principal(id="prn_res", type="resident", status=status)
    assert for_session(session(), resident).session_active is active
    visitor = db.Principal(id="prn_guest", type="guest", status=status, stay_id="stay_203")
    assert for_session(guest_session(), visitor, the_stay=stay()).session_active is active
    preview = build_context_for_preview(
        {"principal_id": "prn_res"}, principal=resident, instance=INSTANCE, at=NOW
    )
    assert preview.session_active is active


def test_revoked_resident_session_is_inactive() -> None:
    assert not for_session(session(revoked_at=NOW), principal()).session_active


def test_time_trust_and_policy_version_come_from_the_instance() -> None:
    untrusted = db.Instance(policy_version=12, time_trusted=False)
    ctx = for_session(session(), principal(), instance=untrusted)
    assert (ctx.time_trusted, ctx.policy_version) == (False, 12)


def test_resident_fences_are_instance_unit_own_principal_and_own_session() -> None:
    first = for_session(session(), principal(), fences=FENCE_ROWS)
    assert scopes(first) == SHARED_SCOPES | {("principal", "prn_res"), ("session", "ses_1")}
    second = for_session(session("ses_2"), principal(), fences=FENCE_ROWS)
    assert scopes(second) == SHARED_SCOPES | {("principal", "prn_res"), ("session", "ses_2")}
    assert all(isinstance(fence, Fence) for fence in first.fences)


def test_guest_fences_are_instance_unit_stay_own_principal_and_own_session() -> None:
    ctx = for_session(guest_session(), guest(), the_stay=stay(), fences=FENCE_ROWS)
    assert scopes(ctx) == SHARED_SCOPES | {
        ("principal", "prn_guest"),
        ("stay", "stay_203"),
        ("session", "ses_guest"),
    }


def test_fence_rows_keep_their_rules_as_policy_models() -> None:
    row = fence_row("principal", "prn_res", max_c="21", resource_ids=["res_203_trv"])
    (fence,) = for_session(session(), principal(), fences=[row]).fences
    assert fence.resource_ids == ("res_203_trv",)
    assert fence.parameters.target_c == Bound(min=Decimal("0"), max=Decimal("21"))


def test_a_broken_fence_row_fails_instead_of_being_skipped() -> None:
    broken = fence_row("stay", "stay_other", parameters={"target_c": {"min": "23", "max": "22"}})
    with pytest.raises(ValidationError):
        for_session(session(), principal(), fences=[broken])


def test_test_session_has_no_binding_no_stay_and_no_fences() -> None:
    ses = session("ses_test", None, purpose="test", not_after=NOW + timedelta(minutes=10))
    ctx = for_session(
        ses, None, assignments=[assignment("prn_res", "family", ["unit_203"])], fences=FENCE_ROWS
    )
    revoked = session("ses_test", None, purpose="test", revoked_at=NOW)
    assert not for_session(revoked, None).session_active
    assert ctx == EffectiveContext(
        principal_ref="test:ses_test",
        role_bindings=(),
        fences=(),
        not_before=None,
        not_after=NOW + timedelta(minutes=10),
        stay_id=None,
        purpose="test",
        policy_version=7,
    )


@pytest.mark.parametrize("purpose", ["preview", "pairing"])
def test_preview_and_pairing_sessions_have_no_session_context(purpose: str) -> None:
    with pytest.raises(ValueError, match="purpose"):
        for_session(session(purpose=purpose), principal())


def test_rows_that_do_not_belong_together_are_refused() -> None:
    with pytest.raises(ValueError):
        for_session(session(principal_id="prn_other"), principal())
    with pytest.raises(ValueError):
        for_session(session(), None)
    with pytest.raises(ValueError):
        for_session(guest_session(), guest())  # a guest without its stay
    with pytest.raises(ValueError):
        for_session(guest_session(), guest(), the_stay=stay(id="stay_other"))
    with pytest.raises(ValueError):
        for_session(guest_session(stay_id="stay_other"), guest(), the_stay=stay())
    with pytest.raises(ValueError):
        for_session(session(), principal(kind="robot"))


# --- context of a preview ---


def test_stay_preview_is_a_hypothetical_guest_of_the_stay() -> None:
    ctx = build_context_for_preview(
        {"stay_id": "stay_203"},
        stay=stay(status="draft"),
        fences=FENCE_ROWS,
        instance=INSTANCE,
        at=NOW,
    )
    assert ctx.principal_ref == "hypothetical:guest:stay_203"
    assert ctx.role_bindings == (STAY_BINDING,)
    assert (ctx.not_before, ctx.not_after) == (NOW - DAY, NOW + 2 * DAY)
    assert (ctx.stay_id, ctx.purpose, ctx.session_active) == ("stay_203", "preview", True)
    assert scopes(ctx) == SHARED_SCOPES | {("stay", "stay_203")}


def test_invite_preview_is_the_preview_of_its_stay() -> None:
    by_invite = build_context_for_preview(
        {"invite_id": "inv_1"},
        invite=db.Invite(id="inv_1", stay_id="stay_203"),
        stay=stay(),
        fences=FENCE_ROWS,
        instance=INSTANCE,
        at=NOW,
    )
    by_stay = build_context_for_preview(
        {"stay_id": "stay_203"}, stay=stay(), fences=FENCE_ROWS, instance=INSTANCE, at=NOW
    )
    assert by_invite == by_stay


def test_group_preview_is_a_hypothetical_member_bound_only_by_the_group() -> None:
    assignments = [
        assignment("grp_family", "family", ["unit_203"], group=True),
        assignment(
            "grp_family", "paused", ["unit_x"], group=True, status="disabled_pending_review"
        ),
        assignment("grp_other", "other", ["unit_x"], group=True),
        assignment("prn_res", "resident", ["unit_lr"]),
        db.Assignment(
            id="asg_none",
            subject_type="principal",
            subject_id=None,
            role_template_id="nobody",
            role_version=1,
            unit_ids=["unit_x"],
            status="active",
        ),
    ]
    ctx = build_context_for_preview(
        {"group_id": "grp_family"},
        assignments=assignments,
        memberships=[membership("grp_other")],
        fences=FENCE_ROWS,
        instance=INSTANCE,
        at=NOW,
    )
    assert ctx.principal_ref == "hypothetical:group:grp_family"
    assert ctx.role_bindings == (RoleBinding("family", 1, frozenset({"unit_203"})),)
    assert (ctx.not_before, ctx.not_after, ctx.stay_id) == (None, None, None)
    assert scopes(ctx) == SHARED_SCOPES


def test_principal_preview_reads_memberships_at_the_hypothetical_time() -> None:
    rows: dict[str, Any] = {
        "principal": principal(),
        "assignments": [assignment("grp_family", "family", ["unit_203"], group=True)],
        "memberships": [membership("grp_family", valid_from=NOW + DAY)],
        "fences": FENCE_ROWS,
        "instance": INSTANCE,
    }
    target = {"principal_id": "prn_res"}
    assert build_context_for_preview(target, at=NOW, **rows).role_bindings == ()
    later = build_context_for_preview(target, at=NOW + 2 * DAY, **rows)
    assert later.role_bindings == (RoleBinding("family", 1, frozenset({"unit_203"})),)
    assert (later.principal_ref, later.purpose) == ("prn_res", "preview")
    assert (later.not_before, later.not_after) == (None, None)
    assert scopes(later) == SHARED_SCOPES | {("principal", "prn_res")}


def test_principal_preview_with_a_session_is_that_session_seen_as_preview() -> None:
    preview = build_context_for_preview(
        {"principal_id": "prn_res", "session_id": "ses_1"},
        principal=principal(),
        session=session(),
        fences=FENCE_ROWS,
        instance=INSTANCE,
        at=NOW,
    )
    access = for_session(session(), principal(), fences=FENCE_ROWS)
    assert preview == replace(access, purpose="preview")


def test_guest_principal_preview_uses_its_stay() -> None:
    ctx = build_context_for_preview(
        {"principal_id": "prn_guest"},
        principal=guest(),
        stay=stay(),
        fences=FENCE_ROWS,
        instance=INSTANCE,
        at=NOW,
    )
    assert ctx.role_bindings == (STAY_BINDING,)
    assert (ctx.principal_ref, ctx.stay_id) == ("prn_guest", "stay_203")
    assert (ctx.not_before, ctx.not_after) == (NOW - DAY, NOW + 2 * DAY)
    assert scopes(ctx) == SHARED_SCOPES | {("principal", "prn_guest"), ("stay", "stay_203")}


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"role_version": "room_guest@1"},
        {"session_id": "ses_1"},
        {"stay_id": "stay_203", "group_id": "grp_family"},
        {"principal_id": "prn_res", "stay_id": "stay_203"},
    ],
)
def test_unknown_preview_targets_are_refused(target: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="preview target"):
        build_context_for_preview(
            target, principal=principal(), stay=stay(), instance=INSTANCE, at=NOW
        )


@pytest.mark.parametrize(
    ("target", "rows"),
    [
        ({"stay_id": "stay_other"}, {"stay": stay()}),
        ({"stay_id": "stay_203"}, {}),
        ({"invite_id": "inv_1"}, {"stay": stay()}),
        (
            {"invite_id": "inv_1"},
            {"invite": db.Invite(id="inv_2", stay_id="stay_203"), "stay": stay()},
        ),
        ({"principal_id": "prn_guest"}, {"principal": guest(), "stay": stay(id="stay_other")}),
        (
            {"principal_id": "prn_res", "session_id": "ses_1"},
            {"principal": principal(), "session": session(purpose="preview")},
        ),
        (
            {"invite_id": "inv_1"},
            {"invite": db.Invite(id="inv_1", stay_id="stay_other"), "stay": stay()},
        ),
        ({"principal_id": "prn_other"}, {"principal": principal()}),
        ({"principal_id": "prn_res", "session_id": "ses_2"}, {"principal": principal()}),
        (
            {"principal_id": "prn_res", "session_id": "ses_2"},
            {"principal": principal(), "session": session()},
        ),
        (
            {"principal_id": "prn_res", "session_id": "ses_2"},
            {"principal": principal(), "session": session("ses_2", "prn_other")},
        ),
    ],
)
def test_preview_rows_must_match_the_target(target: dict[str, str], rows: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        build_context_for_preview(target, instance=INSTANCE, at=NOW, **rows)


# --- policy index ---


def index_of(
    role_versions: dict[tuple[str, int], RoleRules],
    resources: dict[str, ResourceRef],
    units: dict[str, Any],
) -> PolicyIndex:
    return PolicyIndex(role_versions, resources, units, policy_version=7)


def climate(
    confirmed_modes: frozenset[str] = frozenset({"off", "heat"}),
    low: str | None = "8",
    high: str | None = "30",
    step: str | None = "0.5",
) -> DeviceCapabilities:
    return DeviceCapabilities(
        "climate",
        frozenset({Action.CLIMATE_SET_TEMPERATURE, Action.CLIMATE_SET_MODE}),
        target_c_min=None if low is None else Decimal(low),
        target_c_max=None if high is None else Decimal(high),
        target_c_step=None if step is None else Decimal(step),
        hvac_modes=frozenset({"off", "cool", "heat", "auto"}),
        confirmed_modes=confirmed_modes,
    )


LIGHT = DeviceCapabilities(
    "light", frozenset({Action.LIGHT_ON, Action.LIGHT_OFF, Action.LIGHT_SET_LEVEL})
)
BLOCKED = DeviceCapabilities("sensor", frozenset(), blocked_reason="group_entity")
MODES = frozenset({"off", "cool", "heat"})
RESOURCES = {
    ref.id: ref
    for ref in [
        ResourceRef("res_203_trv", "unit_203", "climate", "confirmed", climate()),
        ResourceRef("res_203_light", "unit_203", "light", "confirmed", LIGHT),
        ResourceRef("res_203_hum", "unit_203", "sensor", "blocked", BLOCKED),
        ResourceRef("res_lr_ac", "unit_lr", "climate", "confirmed", climate(frozenset())),
        ResourceRef("res_lr_ac2", "unit_lr", "climate", "confirmed", climate(MODES)),
    ]
}
UNITS = {
    "unit_203": frozenset({"res_203_trv", "res_203_light", "res_203_hum"}),
    "unit_lr": frozenset({"res_lr_ac", "res_lr_ac2"}),
}


def target_c(*ranges: tuple[str, str, str]) -> dict[str, Any]:
    return {"target_c": {"intervals": [{"min": a, "max": b, "step": s} for a, b, s in ranges]}}


ROOM_GUEST = RoleRules.model_validate(
    {
        "grants": [
            {"resources": ["res_203_trv", "res_203_light"], "actions": ["metadata.read"]},
            {
                "resources": ["res_203_trv"],
                "actions": ["climate.set_temperature"],
                "parameters": target_c(("18", "20", "0.5")),
            },
            {
                "resources": ["res_203_trv"],
                "actions": ["climate.set_temperature"],
                "parameters": target_c(("22", "24", "0.5")),
            },
            {"resources": ["res_203_light"], "actions": ["light.on", "light.set_level"]},
            {"resources": ["res_203_trv", "res_lr_ac"], "actions": ["climate.set_mode"]},
        ],
        "denies": [{"resources": ["res_203_trv", "res_203_light"], "actions": ["history.read"]}],
    }
)
LIVING = RoleRules.model_validate(
    {
        "grants": [
            {"resources": ["res_lr_ac", "res_lr_ac2"], "actions": ["climate.set_temperature"]},
            {
                "resources": ["res_lr_ac"],
                "actions": ["climate.set_temperature"],
                "parameters": target_c(("5", "40", "1")),
            },
            {
                "resources": ["res_lr_ac", "res_lr_ac2"],
                "actions": ["climate.set_mode"],
                "parameters": {"mode": {"allowed": ["off", "cool", "heat"]}},
            },
            {"resources": ["res_203_light"], "actions": ["light.off"]},
        ],
        "denies": [{"resources": ["res_203_light"], "actions": ["light.set_level"]}],
    }
)
INDEX = index_of({("room_guest", 1): ROOM_GUEST, ("living", 3): LIVING}, RESOURCES, UNITS)
LIVING_BINDING = RoleBinding("living", 3, frozenset({"unit_lr"}))


def ctx(*bindings: RoleBinding, fences: tuple[dict[str, Any], ...] = ()) -> EffectiveContext:
    parsed = tuple(Fence.model_validate(fence) for fence in fences)
    return EffectiveContext("prn_res", bindings, parsed, None, None, None, "access", 7)


def fence(
    scope_type: str = "instance", scope_id: str | None = None, **fields: Any
) -> dict[str, Any]:
    base = {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "0", "max": "22"}},
    }
    return base | fields


def interval(low: str, high: str, step: str = "0.5") -> Interval:
    return Interval.model_validate({"min": low, "max": high, "step": step})


def temperatures(
    context: EffectiveContext, resource_id: str = "res_203_trv"
) -> tuple[Interval, ...]:
    capability = INDEX.effective_capability(context, resource_id, Action.CLIMATE_SET_TEMPERATURE)
    assert capability is not None
    return capability.intervals


def test_two_grants_keep_their_intervals_apart() -> None:
    capability = INDEX.effective_capability(
        ctx(STAY_BINDING), "res_203_trv", Action.CLIMATE_SET_TEMPERATURE
    )
    assert capability == EffectiveCapability(
        intervals=(interval("18", "20"), interval("22", "24")),
        matched=("room_guest@1:1", "room_guest@1:2"),
    )


def test_a_fence_clips_every_interval_and_drops_what_is_empty() -> None:
    assert temperatures(ctx(STAY_BINDING, fences=(fence(),))) == (
        interval("18", "20"),
        interval("22", "22"),
    )
    at_most_21 = fence(parameters={"target_c": {"min": "19", "max": "21"}})
    assert temperatures(ctx(STAY_BINDING, fences=(at_most_21,))) == (interval("19", "20"),)
    capability = INDEX.effective_capability(
        ctx(STAY_BINDING, fences=(fence(parameters={"target_c": {"min": "20.5", "max": "21"}}),)),
        "res_203_trv",
        Action.CLIMATE_SET_TEMPERATURE,
    )
    assert capability == EffectiveCapability(matched=("room_guest@1:1", "room_guest@1:2"))


def test_every_applicable_fence_clips() -> None:
    fences = (
        fence(),
        fence("principal", "prn_res", parameters={"target_c": {"min": "19", "max": "30"}}),
    )
    assert temperatures(ctx(STAY_BINDING, fences=fences)) == (
        interval("19", "20"),
        interval("22", "22"),
    )


def test_fences_of_other_actions_units_or_resources_do_not_clip() -> None:
    others = (
        fence(action="climate.set_mode", parameters={"mode": {"allowed": ["off"]}}),
        fence("unit", "unit_lr"),
        fence(resource_ids=["res_lr_ac"]),
    )
    assert temperatures(ctx(STAY_BINDING, fences=others)) == (
        interval("18", "20"),
        interval("22", "24"),
    )
    own = (fence("unit", "unit_203"), fence(resource_ids=["res_203_trv"]))
    for one in own:
        assert temperatures(ctx(STAY_BINDING, fences=(one,)))[-1] == interval("22", "22")


def test_device_range_clips_and_a_grant_without_rule_spans_the_device_range() -> None:
    assert temperatures(ctx(LIVING_BINDING), "res_lr_ac") == (
        interval("8", "30", "0.5"),
        interval("8", "30", "1"),
    )
    assert temperatures(ctx(LIVING_BINDING, fences=(fence(),)), "res_lr_ac") == (
        interval("8", "22", "0.5"),
        interval("8", "22", "1"),
    )


def living_temperatures(
    capabilities: DeviceCapabilities, *fences: dict[str, Any]
) -> tuple[Interval, ...]:
    """LIVING's set_temperature grants on res_lr_ac: one without rule, one 5-40 in steps of 1."""
    ref = replace(RESOURCES["res_lr_ac"], capabilities=capabilities)
    index = index_of({("living", 3): LIVING}, {ref.id: ref}, {"unit_lr": frozenset({ref.id})})
    capability = index.effective_capability(
        ctx(LIVING_BINDING, fences=fences), ref.id, Action.CLIMATE_SET_TEMPERATURE
    )
    assert capability is not None and capability.matched == ("living@3:0", "living@3:1")
    return capability.intervals


def test_a_grant_without_rule_moves_in_device_steps() -> None:
    assert living_temperatures(climate(step="0.1")) == (
        interval("8", "30", "0.1"),
        interval("8", "30", "1"),
    )


def test_a_device_without_limits_is_clipped_by_the_schema_grants_and_fences() -> None:
    # without a rule a grant spans what the target_c schema accepts, on the 0.5 grid
    assert living_temperatures(climate(low=None, high=None, step=None)) == (
        interval("-99.5", "99.5"),
        interval("5", "40", "1"),
    )
    assert living_temperatures(climate(low=None, high=None), fence()) == (
        interval("0", "22"),
        interval("5", "22", "1"),
    )


def test_raw_device_limits_snap_inwards_onto_the_grid() -> None:
    assert living_temperatures(climate(low="7.123456789", high="29.987")) == (
        interval("7.5", "29.5"),
        interval("8", "29", "1"),
    )
    assert living_temperatures(climate(low="-7.129", high="-0.001")) == (interval("-7", "-0.5"),)


@pytest.mark.parametrize(
    ("low", "high"),
    [
        ("-1E+40", "1E+40"),
        ("8", "1E+40"),
        ("-1E+40", "30"),
        ("1E+40", None),
        ("8", "1000000"),
        ("-1000000", "30"),
        ("NaN", "30"),
        ("8", "Infinity"),
        ("-Infinity", None),
        ("sNaN", "30"),
    ],
)
def test_a_device_limit_the_rule_format_cannot_hold_offers_nothing(
    low: str | None, high: str | None
) -> None:
    assert living_temperatures(climate(low=low, high=high)) == ()


def test_a_device_limit_just_inside_the_rule_format_still_clips() -> None:
    assert living_temperatures(climate(low="-999999.99", high="999999.99")) == (
        interval("-99.5", "99.5"),
        interval("5", "40", "1"),
    )


def test_a_device_range_that_rounding_empties_offers_nothing() -> None:
    assert living_temperatures(climate(low="20.001", high="20.009")) == ()


@pytest.mark.parametrize(("low", "high"), [("8", "30"), ("8.000000000", "30.0000000")])
def test_device_limits_are_written_without_trailing_zeros(low: str, high: str) -> None:
    (full, _) = living_temperatures(climate(low=low, high=high))
    assert full.model_dump(mode="json") == {"min": "8", "max": "30", "step": "0.5", "origin": "0"}


def test_grant_intervals_cut_each_grant_to_the_device_but_not_to_fences() -> None:
    fenced = ctx(LIVING_BINDING, fences=(fence(),))  # at most 22, which only the capability cuts
    action = Action.CLIMATE_SET_TEMPERATURE
    assert INDEX.grant_intervals(fenced, "res_lr_ac", action) == {
        "living@3:0": (interval("8", "30", "0.5"),),
        "living@3:1": (interval("8", "30", "1"),),
    }
    beyond = replace(RESOURCES["res_lr_ac"], capabilities=climate(high="1E+40"))
    index = index_of(
        {("living", 3): LIVING}, {beyond.id: beyond}, {"unit_lr": frozenset({beyond.id})}
    )
    # granted, but nothing admissible: every grant stays, without an interval
    assert index.grant_intervals(fenced, beyond.id, action) == {"living@3:0": (), "living@3:1": ()}
    assert INDEX.grant_intervals(ctx(STAY_BINDING), "res_unknown", action) == {}
    with pytest.raises(ValueError, match="takes no number"):
        INDEX.grant_intervals(ctx(STAY_BINDING), "res_203_trv", Action.CLIMATE_SET_MODE)


def temperature_rule(*intervals: dict[str, str]) -> RoleRules:
    grant = {
        "resources": ["res_203_trv"],
        "actions": ["climate.set_temperature"],
        "parameters": {"target_c": {"intervals": list(intervals)}},
    }
    return RoleRules.model_validate({"grants": [grant]})


def trv_index(rules: RoleRules, capabilities: DeviceCapabilities) -> PolicyIndex:
    """``rules`` as room_guest@1 with res_203_trv reporting ``capabilities``."""
    ref = replace(RESOURCES["res_203_trv"], capabilities=capabilities)
    return index_of({("room_guest", 1): rules}, {ref.id: ref}, {"unit_203": frozenset({ref.id})})


def trv_temperatures(
    rules: RoleRules, capabilities: DeviceCapabilities, *fences: dict[str, Any]
) -> tuple[Interval, ...]:
    capability = trv_index(rules, capabilities).effective_capability(
        ctx(STAY_BINDING, fences=fences), "res_203_trv", Action.CLIMATE_SET_TEMPERATURE
    )
    assert capability is not None
    return capability.intervals


def test_intervals_promise_only_values_on_their_grid() -> None:
    pinned = fence(parameters={"target_c": {"min": "21.3", "max": "21.3"}})
    assert living_temperatures(climate(), pinned) == ()
    assert living_temperatures(climate(low="7.13", high="7.4")) == ()
    on_grid = fence(parameters={"target_c": {"min": "21.5", "max": "21.5"}})
    assert living_temperatures(climate(), on_grid) == (interval("21.5", "21.5"),)


def test_the_device_step_joins_the_grant_step() -> None:
    halves = temperature_rule({"min": "18", "max": "20", "step": "0.5"})
    assert trv_temperatures(halves, climate(step="1")) == (interval("18", "20", "1"),)
    assert trv_temperatures(halves, climate(step="0.25")) == (interval("18", "20", "0.5"),)
    tenths = temperature_rule({"min": "19.2", "max": "23.7", "step": "0.1"})
    assert trv_temperatures(tenths, climate(step="1")) == (interval("20", "23", "1"),)
    # the request schema takes one decimal, so quarters settle on halves
    quarters = temperature_rule({"min": "18", "max": "20", "step": "0.25"})
    assert trv_temperatures(quarters, climate(step="0.25")) == (interval("18", "20", "0.5"),)


def test_a_grant_without_rule_steps_in_tenths_or_coarser() -> None:
    assert living_temperatures(climate(step="0.25"))[0] == interval("8", "30", "0.5")
    assert living_temperatures(climate(step="0.01"))[0] == interval("8", "30", "0.1")


def test_grids_with_an_origin_meet_where_both_have_values() -> None:
    # 0.5 steps from 0.5 are the 0.5 grid, so a whole-degree device leaves whole degrees
    halves = temperature_rule({"min": "18", "max": "21", "step": "0.5", "origin": "0.5"})
    assert trv_temperatures(halves, climate(step="1")) == (interval("18", "21", "1"),)
    # whole degrees from 0.5 never meet a whole-degree device
    offset = temperature_rule({"min": "18", "max": "21", "step": "1", "origin": "0.5"})
    assert trv_temperatures(offset, climate(step="1")) == ()
    # 0.3 and 0.5 meet every 1.5 degrees, a quotient no finite decimal holds
    thirds = temperature_rule({"min": "18", "max": "22", "step": "0.3"})
    assert trv_temperatures(thirds, climate(step="0.5")) == (interval("18", "21", "1.5"),)
    # 0.3 steps from 0.1 meet the 0.5 grid every 1.5 degrees from 1: 19, 20.5, 22
    shifted = temperature_rule({"min": "18", "max": "22", "step": "0.3", "origin": "0.1"})
    met = Interval.model_validate({"min": "19", "max": "22", "step": "1.5", "origin": "1"})
    assert trv_temperatures(shifted, climate(step="0.5")) == (met,)
    # a joint step beyond the rule format offers nothing, not even 0 (fail closed)
    huge = temperature_rule({"min": "0", "max": "99", "step": "999999.7"})
    assert trv_temperatures(huge, climate(low="-10", high="10")) == ()


def test_rule_grants_end_where_the_request_schema_ends() -> None:
    wide = temperature_rule({"min": "-200", "max": "200", "step": "1"})
    assert trv_temperatures(wide, climate(low=None, high=None)) == (interval("-99", "99", "1"),)


def test_a_device_limit_on_a_grant_bound_keeps_that_single_value() -> None:
    halves = temperature_rule({"min": "18", "max": "20", "step": "0.5"})
    assert trv_temperatures(halves, climate(low="20", high="30")) == (interval("20", "20"),)


def test_grant_intervals_are_snapped_but_not_fenced() -> None:
    tenths = temperature_rule({"min": "19.2", "max": "23.7", "step": "0.1"})
    fenced = ctx(STAY_BINDING, fences=(fence(),))
    index = trv_index(tenths, climate(step="1"))
    assert index.grant_intervals(fenced, "res_203_trv", Action.CLIMATE_SET_TEMPERATURE) == {
        "room_guest@1:0": (interval("20", "23", "1"),)
    }


def test_check_ranges_cut_each_temperature_grant_to_the_fences() -> None:
    fenced = ctx(STAY_BINDING, fences=(fence(),))
    assert INDEX.check_ranges(fenced, "res_203_trv") == (
        (Decimal(18), Decimal(20)),
        (Decimal(22), Decimal(22)),
    )


def test_check_ranges_without_a_temperature_grant_are_its_fences_cut_to_the_device() -> None:
    modes_only = RoleBinding("room_guest", 1, frozenset({"unit_lr"}))  # set_mode on res_lr_ac
    assert INDEX.check_ranges(ctx(modes_only), "res_lr_ac") is None  # neither grant nor fence
    fenced = ctx(modes_only, fences=(fence(),))
    assert INDEX.check_ranges(fenced, "res_lr_ac") == ((Decimal(8), Decimal(22)),)
    raw = replace(RESOURCES["res_lr_ac"], capabilities=climate(low="7.123456789", high="21.987"))
    index = index_of({("room_guest", 1): ROOM_GUEST}, {raw.id: raw}, {"unit_lr": {raw.id}})
    assert index.check_ranges(fenced, raw.id) == ((Decimal("7.13"), Decimal("21.98")),)
    beyond = replace(raw, capabilities=climate(high="1E+40"))
    index = index_of({("room_guest", 1): ROOM_GUEST}, {raw.id: beyond}, {"unit_lr": {raw.id}})
    assert index.check_ranges(fenced, raw.id) == ()  # a limit beyond the format: fail closed
    assert INDEX.check_ranges(fenced, "res_unknown") is None


def test_fences_reach_only_their_own_action() -> None:
    mode_fence = fence(action="climate.set_mode", parameters={"mode": {"allowed": ["off"]}})
    both = ctx(STAY_BINDING, fences=(mode_fence, fence()))
    for action in (Action.CLIMATE_SET_TEMPERATURE, Action.CLIMATE_SET_MODE):
        assert [f.action for f in INDEX.fences(both, "res_203_trv", action)] == [action]


def test_percent_without_rule_is_zero_to_hundred_in_whole_steps() -> None:
    capability = INDEX.effective_capability(
        ctx(STAY_BINDING), "res_203_light", Action.LIGHT_SET_LEVEL
    )
    assert capability == EffectiveCapability(
        intervals=(interval("0", "100", "1"),), matched=("room_guest@1:3",)
    )


def test_percent_rules_are_clipped_to_zero_to_hundred() -> None:
    rules = RoleRules.model_validate(
        {
            "grants": [
                {
                    "resources": ["res_203_light"],
                    "actions": ["light.set_level"],
                    "parameters": {
                        "level_pct": {"intervals": [{"min": "-10", "max": "150", "step": "5"}]}
                    },
                }
            ]
        }
    )
    index = index_of({("room_guest", 1): rules}, RESOURCES, UNITS)
    capability = index.effective_capability(
        ctx(STAY_BINDING), "res_203_light", Action.LIGHT_SET_LEVEL
    )
    assert capability is not None
    assert capability.intervals == (interval("0", "100", "5"),)


def test_a_deny_wins_even_from_a_binding_of_another_unit() -> None:
    both = ctx(STAY_BINDING, LIVING_BINDING)
    assert INDEX.effective_capability(both, "res_203_light", Action.LIGHT_SET_LEVEL) is None
    assert INDEX.effective_capability(ctx(STAY_BINDING), "res_203_light", Action.LIGHT_SET_LEVEL)
    assert INDEX.effective_capability(both, "res_203_trv", Action.HISTORY_READ) is None


def test_no_grant_is_none() -> None:
    assert INDEX.effective_capability(ctx(STAY_BINDING), "res_203_light", Action.LIGHT_OFF) is None
    assert INDEX.effective_capability(ctx(STAY_BINDING), "res_203_trv", Action.STATE_READ) is None
    assert INDEX.effective_capability(ctx(), "res_203_trv", Action.METADATA_READ) is None
    assert INDEX.effective_capability(ctx(STAY_BINDING), "res_unknown", Action.LIGHT_ON) is None


def test_a_grant_counts_only_inside_the_units_of_its_binding() -> None:
    assert (
        INDEX.effective_capability(ctx(LIVING_BINDING), "res_203_light", Action.LIGHT_OFF) is None
    )
    assert (
        INDEX.effective_capability(ctx(STAY_BINDING), "res_lr_ac", Action.CLIMATE_SET_MODE) is None
    )
    elsewhere = RoleBinding("living", 3, frozenset({"unit_203"}))
    capability = INDEX.effective_capability(ctx(elsewhere), "res_203_light", Action.LIGHT_OFF)
    assert capability == EffectiveCapability(matched=("living@3:3",))


def test_parameterless_actions_carry_only_the_matched_grants() -> None:
    assert INDEX.effective_capability(
        ctx(STAY_BINDING), "res_203_trv", Action.METADATA_READ
    ) == EffectiveCapability(matched=("room_guest@1:0",))


def test_modes_are_grant_union_cut_by_fences_and_confirmed_modes() -> None:
    living = ctx(LIVING_BINDING)
    assert INDEX.effective_capability(living, "res_lr_ac2", Action.CLIMATE_SET_MODE) == (
        EffectiveCapability(allowed=MODES, matched=("living@3:2",))
    )
    off_cool = fence(action="climate.set_mode", parameters={"mode": {"allowed": ["off", "cool"]}})
    fenced = ctx(LIVING_BINDING, fences=(off_cool,))
    capability = INDEX.effective_capability(fenced, "res_lr_ac2", Action.CLIMATE_SET_MODE)
    assert capability is not None and capability.allowed == {"off", "cool"}
    # a grant without a mode rule allows every mode the admin confirmed for the device
    guest_modes = INDEX.effective_capability(
        ctx(STAY_BINDING), "res_203_trv", Action.CLIMATE_SET_MODE
    )
    assert guest_modes == EffectiveCapability(
        allowed=frozenset({"off", "heat"}), matched=("room_guest@1:4",)
    )
    # nothing confirmed, nothing allowed
    none_confirmed = INDEX.effective_capability(living, "res_lr_ac", Action.CLIMATE_SET_MODE)
    assert none_confirmed == EffectiveCapability(matched=("living@3:2",))


def test_in_scope_follows_the_unit_of_the_resource() -> None:
    guest_ctx = ctx(STAY_BINDING)
    assert INDEX.in_scope(guest_ctx, "res_203_trv")
    assert not INDEX.in_scope(guest_ctx, "res_lr_ac")
    assert not INDEX.in_scope(guest_ctx, "res_unknown")
    assert not INDEX.in_scope(ctx(), "res_203_trv")
    assert INDEX.in_scope(ctx(STAY_BINDING, LIVING_BINDING), "res_lr_ac")


def test_a_blocked_resource_keeps_its_ref_for_the_decision() -> None:
    ref = INDEX.resource("res_203_hum")
    assert ref is not None and ref.status == "blocked"
    assert ref.capabilities.blocked_reason == "group_entity"
    assert INDEX.in_scope(ctx(STAY_BINDING), "res_203_hum")
    assert INDEX.resource("res_unknown") is None


def test_an_unknown_role_version_fails_loudly() -> None:
    """An index that misses a bound role version is a loader bug, never a smaller role."""
    assert INDEX.rules_for(ctx(STAY_BINDING, LIVING_BINDING)) == [
        (STAY_BINDING, ROOM_GUEST),
        (LIVING_BINDING, LIVING),
    ]
    unknown = RoleBinding("room_guest", 2, frozenset({"unit_203"}))
    with pytest.raises(ValueError, match="^unknown role version room_guest@2$"):
        INDEX.rules_for(ctx(STAY_BINDING, unknown))
    with pytest.raises(ValueError, match="unknown role version"):
        INDEX.effective_capability(ctx(unknown), "res_203_trv", Action.METADATA_READ)


def test_session_fences_narrow_only_their_own_session() -> None:
    rows = [fence_row("session", "ses_guest", max_c="21")]
    fenced = for_session(guest_session(), guest(), the_stay=stay(), fences=rows)
    other = for_session(guest_session("ses_guest_2"), guest(), the_stay=stay(), fences=rows)
    assert temperatures(fenced) == (interval("18", "20"),)
    assert temperatures(other) == (interval("18", "20"), interval("22", "24"))


def test_the_index_is_read_only_and_answers_repeatably() -> None:
    with pytest.raises(TypeError):
        INDEX.units["unit_x"] = frozenset()  # type: ignore[index]
    with pytest.raises(AttributeError):
        INDEX.units = {}  # type: ignore[misc]
    with pytest.raises(AttributeError):
        INDEX.resources = {}  # type: ignore[misc]
    fenced = ctx(STAY_BINDING, fences=(fence(),))
    first = INDEX.effective_capability(fenced, "res_203_trv", Action.CLIMATE_SET_TEMPERATURE)
    again = INDEX.effective_capability(fenced, "res_203_trv", Action.CLIMATE_SET_TEMPERATURE)
    assert first == again
    assert temperatures(ctx(STAY_BINDING)) == (interval("18", "20"), interval("22", "24"))


@pytest.mark.parametrize(
    ("resources", "units"),
    [
        ({"res_x": RESOURCES["res_203_trv"]}, {"unit_203": frozenset({"res_203_trv"})}),
        ({"res_203_trv": RESOURCES["res_203_trv"]}, {}),
        ({"res_203_trv": RESOURCES["res_203_trv"]}, {"unit_lr": frozenset({"res_203_trv"})}),
        (
            {"res_203_trv": RESOURCES["res_203_trv"]},
            {"unit_203": frozenset({"res_203_trv", "res_203_light"})},
        ),
        (
            {"res_203_trv": RESOURCES["res_203_trv"]},
            {"unit_203": frozenset({"res_203_trv"}), "unit_lr": frozenset({"res_203_trv"})},
        ),
    ],
)
def test_resources_and_units_must_agree(
    resources: dict[str, ResourceRef], units: dict[str, frozenset[str]]
) -> None:
    with pytest.raises(ValueError):
        index_of({}, resources, units)


def test_a_unit_without_resources_is_fine() -> None:
    index = index_of({}, {}, {"unit_empty": frozenset()})
    assert index.units == {"unit_empty": frozenset()}


def light_capability(
    action: Action, status: str = "confirmed", capabilities: DeviceCapabilities = LIGHT
) -> EffectiveCapability | None:
    grant = {
        "resources": ["res_203_light"],
        "actions": ["state.read", "history.read", "light.on", "light.set_level"],
    }
    ref = ResourceRef("res_203_light", "unit_203", "light", status, capabilities)  # type: ignore[arg-type]
    index = index_of(
        {("room_guest", 1): RoleRules.model_validate({"grants": [grant]})},
        {ref.id: ref},
        {"unit_203": frozenset({ref.id})},
    )
    return index.effective_capability(ctx(STAY_BINDING), ref.id, action)


def test_the_capability_offers_nothing_the_device_or_its_review_rules_out() -> None:
    assert light_capability(Action.STATE_READ) == EffectiveCapability(matched=("room_guest@1:0",))
    on_off = DeviceCapabilities("light", frozenset({Action.LIGHT_ON, Action.LIGHT_OFF}))
    assert light_capability(Action.LIGHT_SET_LEVEL, capabilities=on_off) is None
    assert light_capability(Action.LIGHT_ON, capabilities=on_off) is not None
    assert light_capability(Action.STATE_READ, "blocked") is None
    assert light_capability(Action.STATE_READ, "needs_review") is not None
    assert light_capability(Action.LIGHT_ON, "needs_review") is None
    assert light_capability(Action.HISTORY_READ) is None
    assert (
        light_capability(Action.STATE_READ, "pending") is None
    )  # a status this code does not know
    assert light_capability(Action.LIGHT_ON, "pending") is None


def test_check_ranges_are_the_unsnapped_grant_parts_inside_device_and_fences() -> None:
    # a device that restores 21.2 under a fence of 21.3 conforms: the range ends at 21.3
    at_most = fence(parameters={"target_c": {"min": "0", "max": "21.3"}})
    living = ctx(LIVING_BINDING, fences=(at_most,))
    assert INDEX.check_ranges(living, "res_lr_ac") == ((Decimal(8), Decimal("21.3")),)
    assert INDEX.check_ranges(ctx(STAY_BINDING), "res_203_trv") == (
        (Decimal(18), Decimal(20)),
        (Decimal(22), Decimal(24)),
    )
    gap = ctx(STAY_BINDING, fences=(fence(parameters={"target_c": {"min": "20.5", "max": "21"}}),))
    assert INDEX.check_ranges(gap, "res_203_trv") == ()
    broken = trv_index(ROOM_GUEST, climate(high="NaN"))
    assert broken.check_ranges(ctx(STAY_BINDING), "res_203_trv") == ()


def test_check_ranges_count_a_grant_the_device_cannot_offer_now() -> None:
    review = replace(RESOURCES["res_203_trv"], status="needs_review")
    rules = temperature_rule({"min": "19", "max": "24", "step": "0.5"})
    index = index_of({("room_guest", 1): rules}, {review.id: review}, {"unit_203": {review.id}})
    fenced = ctx(STAY_BINDING, fences=(fence(),))  # at most 22
    assert index.effective_capability(fenced, review.id, Action.CLIMATE_SET_TEMPERATURE) is None
    assert index.check_ranges(fenced, review.id) == ((Decimal(19), Decimal(22)),)  # not 8..22


def test_a_deny_on_set_temperature_never_widens_the_check() -> None:
    """A deny takes the action away, not the limits of its grants (design §4.1)."""
    grant = {
        "resources": ["res_203_trv"],
        "actions": ["climate.set_temperature"],
        "parameters": target_c(("19", "24", "0.5")),
    }
    deny = {"resources": ["res_203_trv"], "actions": ["climate.set_temperature"]}
    rules = RoleRules.model_validate({"grants": [grant], "denies": [deny]})
    index = trv_index(rules, climate())
    assert index.check_ranges(ctx(STAY_BINDING), "res_203_trv") == ((Decimal(19), Decimal(24)),)
    fenced = ctx(STAY_BINDING, fences=(fence(),))
    assert index.check_ranges(fenced, "res_203_trv") == ((Decimal(19), Decimal(22)),)
    below = ctx(STAY_BINDING, fences=(fence(parameters={"target_c": {"min": "0", "max": "5"}}),))
    assert index.check_ranges(below, "res_203_trv") == ()  # the grant starts at 19


@pytest.mark.parametrize("step", ["0", "-0.5", "0.0000001", "0.0000015", "NaN", "sNaN", "Infinity"])
def test_a_broken_device_step_offers_nothing(step: str) -> None:
    assert living_temperatures(climate(step=step)) == ()


def test_a_joint_step_of_exactly_one_million_offers_nothing() -> None:
    # 5^12 and 2^12 * 5^4 millionths meet the schema's tenths on exactly 10^12 millionths
    rule = temperature_rule({"min": "-10", "max": "10", "step": "244.140625"})
    assert trv_temperatures(rule, climate(low="-10", high="10", step="2.56")) == ()


@pytest.mark.parametrize(
    ("low", "high", "written"),
    [
        ("8", "30", ["8", "30"]),
        ("7.1", "29.999", ["7.1", "29.99"]),
        ("8.000", "30.000", ["8", "30"]),
    ],
)
def test_check_ranges_write_device_limits_plainly(low: str, high: str, written: list[str]) -> None:
    modes_only = RoleBinding("room_guest", 1, frozenset({"unit_lr"}))  # set_mode on res_lr_ac
    wide = ctx(modes_only, fences=(fence(parameters={"target_c": {"min": "0", "max": "50"}}),))
    ref = replace(RESOURCES["res_lr_ac"], capabilities=climate(low=low, high=high))
    index = index_of({("room_guest", 1): ROOM_GUEST}, {ref.id: ref}, {"unit_lr": {ref.id}})
    ranges = index.check_ranges(wide, ref.id)
    assert ranges is not None and [str(limit) for limit in ranges[0]] == written


BLIND = "res_203_blind"
COVER = DeviceCapabilities(
    "cover",
    frozenset(
        {Action.COVER_OPEN, Action.COVER_CLOSE, Action.COVER_STOP, Action.COVER_SET_POSITION}
    ),
)
OPEN_CLOSE = {"resources": [BLIND], "actions": ["cover.open", "cover.close"]}
ANY_POSITION = {"resources": [BLIND], "actions": ["cover.set_position"]}


def positions(*ranges: tuple[str, str, str]) -> dict[str, Any]:
    intervals = [{"min": low, "max": high, "step": step} for low, high, step in ranges]
    rule = {"position_pct": {"intervals": intervals}}
    return {"resources": [BLIND], "actions": ["cover.set_position"], "parameters": rule}


def blind_index(*grants: dict[str, Any], denies: tuple[dict[str, Any], ...] = ()) -> PolicyIndex:
    ref = ResourceRef(BLIND, "unit_203", "cover", "confirmed", COVER)
    rules = RoleRules.model_validate({"grants": [OPEN_CLOSE, *grants], "denies": list(denies)})
    return index_of({("room_guest", 1): rules}, {ref.id: ref}, {"unit_203": {ref.id}})


def position_fence(low: str, high: str) -> dict[str, Any]:
    bound = {"position_pct": {"min": low, "max": high}}
    return fence(action="cover.set_position", parameters=bound)


def ends(index: PolicyIndex, *fences: dict[str, Any]) -> tuple[DecisionCode | None, ...]:
    """The end codes of cover.open and cover.close."""
    context = ctx(STAY_BINDING, fences=fences)
    return tuple(index.end_code(context, BLIND, a) for a in (Action.COVER_OPEN, Action.COVER_CLOSE))


OUT, FENCED = DecisionCode.PARAM_OUT_OF_RANGE, DecisionCode.FENCE_VIOLATION


def test_check_ranges_take_any_number_unsnapped() -> None:
    index = blind_index(positions(("20", "80", "3")))
    action = Action.COVER_SET_POSITION
    assert index.check_ranges(ctx(STAY_BINDING), BLIND, action) == ((Decimal(20), Decimal(80)),)
    fenced = ctx(STAY_BINDING, fences=(position_fence("0", "70"),))
    assert index.check_ranges(fenced, BLIND, action) == ((Decimal(20), Decimal(70)),)
    free = blind_index(ANY_POSITION)
    assert free.check_ranges(ctx(STAY_BINDING), BLIND, action) == ((Decimal(0), Decimal(100)),)
    with pytest.raises(ValueError, match="takes no number"):
        index.check_ranges(ctx(STAY_BINDING), BLIND, Action.COVER_OPEN)


def test_without_a_position_rule_open_and_close_are_not_checked() -> None:
    assert ends(blind_index()) == (None, None)
    assert ends(blind_index(ANY_POSITION)) == (None, None)
    assert blind_index().end_code(ctx(STAY_BINDING), BLIND, Action.COVER_STOP) is None
    assert blind_index().end_code(ctx(STAY_BINDING), "res_unknown", Action.COVER_OPEN) is None


def test_a_position_grant_without_the_end_refuses_open_or_close() -> None:
    assert ends(blind_index(positions(("20", "80", "1")))) == (OUT, OUT)
    assert ends(blind_index(positions(("20", "100", "1")))) == (None, OUT)
    # unsnapped: 100 is an end of 0..100 although the steps of 3 stop at 99
    assert ends(blind_index(positions(("0", "100", "3")))) == (None, None)
    # grants are a union: another grant may hold the end
    assert ends(blind_index(positions(("20", "80", "1")), positions(("90", "100", "1")))) == (
        None,
        OUT,
    )
    assert ends(blind_index(positions(("20", "80", "1")), ANY_POSITION)) == (None, None)


def test_a_position_fence_without_the_end_refuses_open_or_close() -> None:
    assert ends(blind_index(), position_fence("0", "80")) == (FENCED, None)
    assert ends(blind_index(), position_fence("0", "50"), position_fence("0", "80")) == (
        FENCED,
        None,
    )
    assert ends(blind_index(), position_fence("20", "100")) == (None, FENCED)
    # each end fails on the one fence that excludes it
    both = (position_fence("0", "80"), position_fence("20", "100"))
    assert ends(blind_index(), *both) == (FENCED, FENCED)
    # the grant first, as in decide's order
    grant = positions(("20", "80", "1"))
    assert ends(blind_index(grant), position_fence("0", "50")) == (OUT, OUT)


def test_a_deny_on_set_position_never_widens_the_end_check() -> None:
    deny = {"resources": [BLIND], "actions": ["cover.set_position"]}
    assert ends(blind_index(positions(("20", "80", "1")), denies=(deny,))) == (OUT, OUT)


def test_the_capability_offers_no_end_its_check_refuses() -> None:
    index = blind_index(positions(("0", "80", "1")))
    context = ctx(STAY_BINDING)
    assert index.effective_capability(context, BLIND, Action.COVER_OPEN) is None
    assert index.effective_capability(context, BLIND, Action.COVER_CLOSE) == EffectiveCapability(
        matched=("room_guest@1:0",)
    )


def test_the_index_carries_its_policy_version() -> None:
    assert INDEX.policy_version == 7
    with pytest.raises(TypeError):
        PolicyIndex({}, {}, {})  # type: ignore[call-arg]


def test_a_resource_kind_must_match_its_capabilities() -> None:
    odd = replace(RESOURCES["res_203_trv"], kind="light")
    with pytest.raises(ValueError, match="kind"):
        index_of({}, {odd.id: odd}, {"unit_203": {odd.id}})
