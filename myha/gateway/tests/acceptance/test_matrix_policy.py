"""Acceptance matrix A06-A18 of the decision function (design §4.1, §10; spec §19).

Runs against the simulator's fixture world as a policy index, without a running simulator.
"""

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from myha_gateway.adapters.base import StateView
from myha_gateway.policy.codes import DecisionCode as Code
from myha_gateway.policy.context import EffectiveContext
from myha_gateway.policy.decide import Decision, decide
from myha_gateway.policy.index import PolicyIndex
from tests.helpers import (
    DURING_STAY,
    LIVING_ROOM,
    ROOM_203,
    ROOM_GUEST_GRANTS,
    STAY_END,
    STAY_START,
    fixture_view,
    guest_context,
    resident_context,
    room203_index,
)

INDEX = room203_index()


def check(
    action: str,
    params: Mapping[str, Any] | None = None,
    *,
    resource: str = "res_203_light",
    ctx: EffectiveContext | None = None,
    index: PolicyIndex = INDEX,
    now: datetime = DURING_STAY,
    view: StateView | None = None,
    execute: bool = False,
) -> Decision:
    """The decision for the guest of room 203 during the stay, unless told otherwise.

    Without ``view``, a known resource shows its fixture state; an unknown one has none.
    """
    context = guest_context() if ctx is None else ctx
    if view is None and resource in ROOM_203 | LIVING_ROOM:
        view = fixture_view(resource)
    return decide(context, index, resource, action, params or {}, now, view, execute=execute)


def only(*actions: str, resource: str = "res_203_light") -> PolicyIndex:
    return room203_index(grants=[{"resources": [resource], "actions": list(actions)}])


def target_c(*ranges: tuple[str, str, str]) -> dict[str, Any]:
    intervals = [{"min": low, "max": high, "step": step} for low, high, step in ranges]
    return {"target_c": {"intervals": intervals}}


def temperature_grant(
    *ranges: tuple[str, str, str], resource: str = "res_203_trv"
) -> dict[str, Any]:
    return {
        "resources": [resource],
        "actions": ["climate.set_temperature"],
        "parameters": target_c(*ranges),
    }


def temperature_fence(scope_type: str, scope_id: str | None, high: str) -> dict[str, Any]:
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "0", "max": high}},
    }


@pytest.mark.parametrize("action", ["state.read", "light.on"])
def test_a06_foreign_resource_is_not_found(action: str) -> None:
    foreign = check(action, resource="res_lr_ceiling")
    unknown = check(action, resource="res_unknown")
    assert foreign.code is Code.RESOURCE_NOT_IN_SCOPE
    assert foreign.public_code == "NOT_FOUND" and not foreign.allowed
    assert foreign == unknown  # same message, nothing about the resource


def test_a07_off_only() -> None:
    index = only("light.off")
    assert check("light.on", index=index).code is Code.NO_GRANT
    assert check("light.set_level", {"level_pct": 40}, index=index).code is Code.NO_GRANT
    granted = check("light.off", index=index)
    assert (granted.code, granted.allowed, granted.public_code) == (Code.GRANTED, True, None)


def test_a08_on_only() -> None:
    index = only("light.on")
    assert check("light.off", index=index).code is Code.NO_GRANT
    assert check("light.set_level", {"level_pct": 0}, index=index).code is Code.NO_GRANT
    for action, params in [
        ("light.set_level", {"level_pct": 40, "flash": "short"}),
        ("light.set_level", {"brightness_step_pct": 10}),
        ("light.on", {"flash": "short"}),
    ]:
        refused = check(action, params, index=index)
        assert (refused.code, refused.public_code) == (Code.PARAM_SCHEMA, "INVALID_PARAMETERS")
    # dimming to zero switches off, which needs light.off even with a dimming grant
    dimming = only("light.on", "light.set_level")
    assert check("light.set_level", {"level_pct": 0}, index=dimming).code is Code.NO_GRANT
    assert check("light.set_level", {"level_pct": 40}, index=dimming).code is Code.GRANTED


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"target_c": "21.5"}, Code.GRANTED),
        ({"target_c": "24.1"}, Code.PARAM_OUT_OF_RANGE),
        ({"target_c": "30"}, Code.PARAM_OUT_OF_RANGE),
        ({"target_c": "21.3"}, Code.PARAM_STEP),
        ({"target_c": "NaN"}, Code.PARAM_SCHEMA),
        ({"target_c": "Infinity"}, Code.PARAM_SCHEMA),
        ({"target_c": 21.5}, Code.PARAM_SCHEMA),
        ({"target_c": "21.5", "hvac_mode": "cool"}, Code.PARAM_SCHEMA),
    ],
)
def test_a09_temperature_range_and_step(params: dict[str, Any], expected: Code) -> None:
    decision = check("climate.set_temperature", params, resource="res_203_trv")
    assert decision.code is expected


def test_a10_two_grants_do_not_merge() -> None:
    grants = [temperature_grant(("18", "20", "0.5")), temperature_grant(("22", "24", "0.5"))]
    index = room203_index(grants=grants)

    def set_to(value: str) -> Decision:
        return check(
            "climate.set_temperature", {"target_c": value}, resource="res_203_trv", index=index
        )

    assert set_to("21").code is Code.PARAM_OUT_OF_RANGE
    low, high = set_to("19"), set_to("23")
    assert (low.code, low.matched) == (Code.GRANTED, ("room_guest@1:0",))
    assert (high.code, high.matched) == (Code.GRANTED, ("room_guest@1:1",))


def test_a11_fence_narrows_wide_grant() -> None:
    index = room203_index(grants=[temperature_grant(("19", "23", "0.5"))])
    ctx = resident_context(temperature_fence("principal", "prn_res", "22"))

    def set_to(value: str) -> Decision:
        params = {"target_c": value}
        return check(
            "climate.set_temperature", params, resource="res_203_trv", ctx=ctx, index=index
        )

    fenced = set_to("22.5")
    assert (fenced.code, fenced.public_code) == (Code.FENCE_VIOLATION, "ACTION_NOT_ALLOWED")
    assert set_to("22").code is Code.GRANTED


def test_a12_deny_wins() -> None:
    index = room203_index(denies=[{"resources": ["res_203_light"], "actions": ["light.on"]}])
    denied = check("light.on", index=index)
    assert (denied.code, denied.public_code) == (Code.DENIED_BY_RULE, "ACTION_NOT_ALLOWED")
    assert check("light.off", index=index).code is Code.GRANTED


@pytest.mark.parametrize(
    ("action", "resource", "expected"),
    [
        ("light.toggle", "res_203_light", Code.UNKNOWN_ACTION),
        ("*", "res_203_light", Code.UNKNOWN_ACTION),
        ("light.*", "res_203_light", Code.UNKNOWN_ACTION),
        ("homeassistant.turn_on", "res_203_light", Code.UNKNOWN_ACTION),
        ("LIGHT.ON", "res_203_light", Code.UNKNOWN_ACTION),
        ("light.on", "*", Code.RESOURCE_NOT_IN_SCOPE),
        ("light.on", "res_203_*", Code.RESOURCE_NOT_IN_SCOPE),
        ("light.on", "unit_203", Code.RESOURCE_NOT_IN_SCOPE),
        ("light.on", "area_203", Code.RESOURCE_NOT_IN_SCOPE),
        ("light.on", "light.room_203_ceiling", Code.RESOURCE_NOT_IN_SCOPE),
    ],
)
def test_a13_unknown_action_and_wildcards(action: str, resource: str, expected: Code) -> None:
    decision = check(action, resource=resource)
    assert (decision.code, decision.public_code) == (expected, "NOT_FOUND")


def test_a14_new_device_not_granted() -> None:
    # A grant naming an id that no binding has grants nothing, nor does the raw entity id.
    named = {"resources": ["res_203_new"], "actions": ["state.read", "light.on"]}
    index = room203_index(grants=[*ROOM_GUEST_GRANTS, named])
    for resource in ("res_203_new", "light.hallway"):
        for action in ("state.read", "light.on"):
            decision = check(action, resource=resource, index=index)
            assert decision.public_code == "NOT_FOUND"


def test_a15_group_entity_blocked() -> None:
    actions = ["metadata.read", "state.read", "light.on"]
    index = only(*actions, resource="res_203_group")
    ref = index.resource("res_203_group")
    assert ref is not None and ref.status == "blocked"
    assert ref.capabilities.blocked_reason == "group_entity"
    for action in actions:
        decision = check(action, resource="res_203_group", index=index)
        assert (decision.code, decision.public_code) == (Code.RESOURCE_UNCONFIRMED, "NOT_FOUND")
        assert decision.message == check(action, resource="res_unknown").message


def test_a16_needs_review_blocks_writes_only() -> None:
    index = room203_index(status={"res_203_light": "needs_review"})
    write = check("light.on", index=index)
    assert (write.code, write.public_code) == (Code.RESOURCE_NEEDS_REVIEW, "UPSTREAM_UNAVAILABLE")
    assert check("state.read", index=index).code is Code.GRANTED
    assert check("metadata.read", index=index).code is Code.GRANTED


def test_a16b_adapter_locked() -> None:
    locked = fixture_view("res_203_light", adapter_locked=True)
    write = check("light.on", view=locked)
    assert (write.code, write.public_code) == (Code.ADAPTER_LOCKED, "UPSTREAM_UNAVAILABLE")
    assert check("state.read", view=locked).code is Code.GRANTED
    assert check("light.on", view=fixture_view("res_203_light")).code is Code.GRANTED


def test_a17_cover_position_requires_both_directions() -> None:
    index = only("cover.open", "cover.set_position", resource="res_203_blind")

    def position(pct: int) -> Code:
        params = {"position_pct": pct}
        return check("cover.set_position", params, resource="res_203_blind", index=index).code

    assert position(100) is Code.GRANTED
    assert position(0) is Code.NO_GRANT
    assert position(40) is Code.NO_GRANT


def test_a17b_open_and_close_stay_inside_the_position_range() -> None:
    """cover.open drives to 100, cover.close to 0: a position range must allow that end."""
    at_most_half = {
        "scope_type": "stay",
        "scope_id": "stay_203",
        "action": "cover.set_position",
        "parameters": {"position_pct": {"min": "0", "max": "50"}},
    }
    fenced = guest_context(at_most_half)

    def move(action: str, params: dict[str, int] | None = None, **kwargs: Any) -> Code:
        return check(action, params, resource="res_203_blind", **kwargs).code

    assert (move("cover.open"), move("cover.close")) == (Code.GRANTED, Code.GRANTED)  # no rule
    assert move("cover.open", ctx=fenced) is Code.FENCE_VIOLATION
    assert move("cover.close", ctx=fenced) is Code.GRANTED
    assert move("cover.set_position", {"position_pct": 40}, ctx=fenced) is Code.GRANTED
    assert move("cover.stop", ctx=fenced) is Code.GRANTED  # stopping moves nowhere
    middle = {
        "resources": ["res_203_blind"],
        "actions": ["cover.set_position"],
        "parameters": {"position_pct": {"intervals": [{"min": "20", "max": "80", "step": "1"}]}},
    }
    ends = {"resources": ["res_203_blind"], "actions": ["cover.open", "cover.close"]}
    ranged = room203_index(grants=[middle, ends])
    assert move("cover.open", index=ranged) is Code.PARAM_OUT_OF_RANGE
    assert move("cover.close", index=ranged) is Code.PARAM_OUT_OF_RANGE
    # without a set_position grant the fence alone bounds the ends, as for the mode rule
    no_position = room203_index(grants=[ends])
    assert move("cover.open", index=no_position, ctx=fenced) is Code.FENCE_VIOLATION
    assert move("cover.close", index=no_position, ctx=fenced) is Code.GRANTED
    # a deny on set_position takes the action away, never the limit of the ends
    denied = room203_index(
        grants=[middle, ends],
        denies=[{"resources": ["res_203_blind"], "actions": ["cover.set_position"]}],
    )
    assert move("cover.open", index=denied) is Code.PARAM_OUT_OF_RANGE
    assert move("cover.close", index=denied) is Code.PARAM_OUT_OF_RANGE
    # the ends count against the unsnapped range: 0-100 in steps of 3 still reaches 100
    thirds = {
        "resources": ["res_203_blind"],
        "actions": ["cover.set_position"],
        "parameters": {"position_pct": {"intervals": [{"min": "0", "max": "100", "step": "3"}]}},
    }
    coarse = room203_index(grants=[thirds, ends])
    assert (move("cover.open", index=coarse), move("cover.close", index=coarse)) == (
        Code.GRANTED,
        Code.GRANTED,
    )


def position_fence(scope_type: str, scope_id: str, low: str, high: str) -> dict[str, Any]:
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "action": "cover.set_position",
        "parameters": {"position_pct": {"min": low, "max": high}},
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_a17b_every_position_fence_bounds_the_ends(reverse: bool) -> None:
    """Each fence must hold the end on its own: one fence allowing it is not enough."""

    def move(action: str, *fences: dict[str, Any]) -> Code:
        ctx = guest_context(*(reversed(fences) if reverse else fences))
        return check(action, resource="res_203_blind", ctx=ctx).code

    below_half = position_fence("stay", "stay_203", "0", "50")
    above_fifth = position_fence("unit", "unit_203", "20", "100")
    assert move("cover.open", below_half, above_fifth) is Code.FENCE_VIOLATION
    assert move("cover.close", below_half, above_fifth) is Code.FENCE_VIOLATION
    below_four_fifths = position_fence("unit", "unit_203", "0", "80")
    assert move("cover.open", below_half, below_four_fifths) is Code.FENCE_VIOLATION
    assert move("cover.close", below_half, below_four_fifths) is Code.GRANTED
    params = {"position_pct": 40}
    ctx = guest_context(below_half, above_fifth)
    position = check("cover.set_position", params, resource="res_203_blind", ctx=ctx)
    assert position.code is Code.GRANTED


AC_GRANTS = (
    temperature_grant(("19", "24", "0.5"), resource="res_lr_ac"),
    {
        "resources": ["res_lr_ac"],
        "actions": ["climate.set_mode"],
        "parameters": {"mode": {"allowed": ["off", "cool", "heat"]}},
    },
)


def test_a18_mode_cannot_bypass_fence() -> None:
    both = room203_index(grants=AC_GRANTS)
    mode_only = room203_index(grants=AC_GRANTS[1:])
    fenced = resident_context(temperature_fence("instance", None, "24"))

    def switch(
        mode: str,
        target: str | None,
        index: PolicyIndex = both,
        ctx: EffectiveContext | None = None,
    ) -> Decision:
        view = fixture_view("res_lr_ac", target_c=None if target is None else Decimal(target))
        return check(
            "climate.set_mode",
            {"mode": mode},
            resource="res_lr_ac",
            ctx=resident_context() if ctx is None else ctx,
            index=index,
            view=view,
        )

    hot = switch("cool", "28")
    assert (hot.code, hot.public_code) == (Code.MODE_WOULD_BYPASS_FENCE, "ACTION_NOT_ALLOWED")
    assert switch("heat", "28").code is Code.MODE_WOULD_BYPASS_FENCE
    within = switch("cool", "22")
    assert (within.code, within.check_range) == (Code.GRANTED, (Decimal("19"), Decimal("24")))
    off = switch("off", "28")
    assert (off.code, off.check_range) == (Code.GRANTED, None)
    assert switch("cool", None).code is Code.MODE_WOULD_BYPASS_FENCE  # unknown counts as outside
    # neither a temperature grant nor a fence: nothing the mode change could bypass
    free = switch("cool", "28", mode_only)
    assert (free.code, free.check_range) == (Code.GRANTED, None)
    # no temperature grant, but an instance fence: fence ∩ device range (8-30)
    assert switch("cool", "28", mode_only, fenced).code is Code.MODE_WOULD_BYPASS_FENCE
    inside = switch("cool", "21", mode_only, fenced)
    assert (inside.code, inside.check_range) == (Code.GRANTED, (Decimal("8"), Decimal("24")))


def test_a18_mode_cannot_keep_a_target_between_two_grants() -> None:
    """A10 meets E7: with 18-20 and 22-24 granted, 21 is not settable, so heat may not keep it."""
    mode = {"resources": ["res_203_trv"], "actions": ["climate.set_mode"]}
    index = room203_index(
        grants=[temperature_grant(("18", "20", "1")), temperature_grant(("22", "24", "1")), mode]
    )

    def heat(target: str) -> Decision:
        view = fixture_view("res_203_trv", target_c=Decimal(target))
        params = {"mode": "heat"}
        return check("climate.set_mode", params, resource="res_203_trv", index=index, view=view)

    set_21 = check(
        "climate.set_temperature", {"target_c": "21"}, resource="res_203_trv", index=index
    )
    assert set_21.code is Code.PARAM_OUT_OF_RANGE
    assert heat("21").code is Code.MODE_WOULD_BYPASS_FENCE
    inside = heat("19")
    assert (inside.code, inside.check_range) == (Code.GRANTED, (Decimal("18"), Decimal("24")))
    assert heat("19.5").code is Code.GRANTED  # inside a range: the step does not matter


def test_a18_mode_check_ranges_are_not_snapped_to_the_grid() -> None:
    """A device target between grid points passes when it lies inside the fence (E7)."""
    mode = {"resources": ["res_203_trv"], "actions": ["climate.set_mode"]}
    index = room203_index(grants=[temperature_grant(("19", "24", "0.5")), mode])
    ctx = guest_context(temperature_fence("stay", "stay_203", "21.3"))
    view = fixture_view("res_203_trv", target_c=Decimal("21.2"))
    params = {"mode": "heat"}
    heat = check(
        "climate.set_mode", params, resource="res_203_trv", ctx=ctx, index=index, view=view
    )
    assert (heat.code, heat.check_range) == (Code.GRANTED, (Decimal("19"), Decimal("21.3")))


def test_a18_a_deny_on_set_temperature_does_not_lift_the_mode_check() -> None:
    """A deny takes set_temperature away; the granted range still bounds the mode switch."""
    mode = {"resources": ["res_203_trv"], "actions": ["climate.set_mode"]}
    no_setpoint = {"resources": ["res_203_trv"], "actions": ["climate.set_temperature"]}
    index = room203_index(
        grants=[temperature_grant(("19", "24", "0.5")), mode], denies=[no_setpoint]
    )

    def heat(target: str) -> Code:
        view = fixture_view("res_203_trv", target_c=Decimal(target))
        params = {"mode": "heat"}
        return check(
            "climate.set_mode", params, resource="res_203_trv", index=index, view=view
        ).code

    assert heat("30") is Code.MODE_WOULD_BYPASS_FENCE
    assert heat("21") is Code.GRANTED
    setpoint = check(
        "climate.set_temperature", {"target_c": "21"}, resource="res_203_trv", index=index
    )
    assert setpoint.code is Code.DENIED_BY_RULE


def test_stay_window() -> None:
    before = check("state.read", now=STAY_START - timedelta(microseconds=1))
    assert (before.code, before.public_code) == (Code.STAY_NOT_STARTED, "ACCESS_EXPIRED")
    assert check("state.read", now=STAY_START).code is Code.GRANTED  # the start is inclusive
    assert check("state.read", now=STAY_END - timedelta(microseconds=1)).code is Code.GRANTED
    after = check("state.read", now=STAY_END)  # the end is exclusive
    assert (after.code, after.public_code) == (Code.STAY_ENDED, "ACCESS_EXPIRED")


def test_time_untrusted_blocks_stays_only() -> None:
    guest = check("light.on", ctx=guest_context(time_trusted=False))
    assert (guest.code, guest.public_code) == (Code.TIME_UNTRUSTED, "UPSTREAM_UNAVAILABLE")
    assert check("light.on", ctx=resident_context(time_trusted=False)).code is Code.GRANTED
    # a resident's session expiry is a window too, yet only a stay needs trusted time
    expiring = resident_context(time_trusted=False, not_after=STAY_END)
    assert check("light.on", ctx=expiring).code is Code.GRANTED


def test_preview_context_rejected_on_execute() -> None:
    preview = guest_context(purpose="preview")
    executed = check("light.on", ctx=preview, execute=True)
    assert (executed.code, executed.public_code) == (Code.PREVIEW_CONTEXT, "ACTION_NOT_ALLOWED")
    assert check("light.on", ctx=preview).code is Code.GRANTED
    assert check("light.on", ctx=preview, now=STAY_END).code is Code.STAY_ENDED
    assert check("light.on", execute=True).code is Code.GRANTED


def test_dst_boundary() -> None:
    sofia = ZoneInfo("Europe/Sofia")
    end = datetime(2026, 3, 29, 3, 0, tzinfo=sofia)  # clocks jump from 03:00 EET to 04:00 EEST
    assert end.astimezone(UTC) == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    ctx = guest_context(
        not_before=datetime(2026, 3, 27, 12, tzinfo=UTC), not_after=end.astimezone(UTC)
    )

    def at(now: datetime) -> Code:
        return check("state.read", ctx=ctx, now=now).code

    # 02:30 EET: inside, although the summer offset would place the end at 00:00 UTC
    assert at(datetime(2026, 3, 29, 0, 30, tzinfo=UTC)) is Code.GRANTED
    assert at(datetime(2026, 3, 29, 0, 59, 59, 999999, tzinfo=UTC)) is Code.GRANTED
    assert at(datetime(2026, 3, 29, 1, 0, tzinfo=UTC)) is Code.STAY_ENDED
    assert at(datetime(2026, 3, 29, 4, 0, tzinfo=sofia)) is Code.STAY_ENDED  # the same instant


def test_decide_p95_under_20ms() -> None:
    ctx = guest_context(temperature_fence("stay", "stay_203", "23"))
    requests = [
        ("res_203_trv", "climate.set_temperature", {"target_c": "21.5"}),
        ("res_203_trv", "climate.set_mode", {"mode": "heat"}),
        ("res_203_light", "light.set_level", {"level_pct": 40}),
        ("res_203_blind", "cover.set_position", {"position_pct": 40}),
        ("res_203_hum", "metadata.read", {}),
        ("res_lr_ac", "state.read", {}),
    ]
    views = {resource: fixture_view(resource) for resource, _, _ in requests}
    timings = []
    for i in range(1000):
        resource, action, params = requests[i % len(requests)]
        start = time.perf_counter()
        decide(ctx, INDEX, resource, action, params, DURING_STAY, views[resource], execute=True)
        timings.append(time.perf_counter() - start)
    assert sorted(timings)[949] < 0.020
