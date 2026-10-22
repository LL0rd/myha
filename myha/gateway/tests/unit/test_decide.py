"""The decision function beyond the acceptance matrix: order, implicit reads, normalization."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import pytest

from myha_gateway.adapters.base import Action, StateView
from myha_gateway.policy.codes import EVALUATION_ORDER, PUBLIC_CODE, PublicError
from myha_gateway.policy.codes import DecisionCode as Code
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.decide import Decision, decide
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.policy.model import Interval, RoleRules
from tests.helpers import (
    DURING_STAY,
    LIVING_ROOM,
    ROOM_203,
    STAY_END,
    STAY_START,
    fixture_view,
    guest_context,
    resident_context,
    room203_index,
)

NOW = DURING_STAY


def run(
    index: PolicyIndex,
    resource: str,
    action: str,
    params: Mapping[str, Any] | None = None,
    ctx: EffectiveContext | None = None,
) -> Decision:
    context = resident_context() if ctx is None else ctx
    view = fixture_view(resource) if resource in ROOM_203 | LIVING_ROOM else None
    return decide(context, index, resource, action, params or {}, NOW, view, execute=True)


def grant(resource: str, *actions: str, **parameters: Any) -> dict[str, Any]:
    return {"resources": [resource], "actions": list(actions), "parameters": parameters}


def interval(low: str, high: str, step: str) -> dict[str, Any]:
    return {"intervals": [{"min": low, "max": high, "step": step}]}


# --- evaluation order ---

# Every check but the mode rule, which only climate.set_mode reaches (see the next test).
LADDER = [code for code in EVALUATION_ORDER if code is not Code.MODE_WOULD_BYPASS_FENCE]


def ladder_request(failing: set[Code]) -> Decision:
    """light.set_level 45 by the room 203 guest, failing exactly the checks in ``failing``."""
    resource = "res_203_bedside" if Code.CAPABILITY_MISSING in failing else "res_203_light"
    level = {
        "min": "50" if Code.PARAM_OUT_OF_RANGE in failing else "0",
        "max": "100",
        "step": "10" if Code.PARAM_STEP in failing else "5",
    }
    rule = {"level_pct": {"intervals": [level]}}
    grants = [] if Code.NO_GRANT in failing else [grant(resource, "light.set_level", **rule)]
    grants.append(grant(resource, "light.on"))
    denies = [{"resources": [resource], "actions": ["light.set_level"]}]
    status = "needs_review" if Code.RESOURCE_NEEDS_REVIEW in failing else "confirmed"
    index = room203_index(
        grants=grants,
        denies=denies if Code.DENIED_BY_RULE in failing else [],
        status={resource: "blocked" if Code.RESOURCE_UNCONFIRMED in failing else status},
    )
    fence = {
        "scope_type": "stay",
        "scope_id": "stay_203",
        "action": "light.set_level",
        "parameters": {"level_pct": {"min": "0", "max": "40"}},
    }
    unit = "unit_lr" if Code.RESOURCE_NOT_IN_SCOPE in failing else "unit_203"
    ctx = guest_context(
        *([fence] if Code.FENCE_VIOLATION in failing else []),
        role_bindings=(RoleBinding("room_guest", 1, frozenset({unit})),),
        purpose="preview" if Code.PREVIEW_CONTEXT in failing else "access",
        session_active=Code.SESSION_INACTIVE not in failing,
        time_trusted=Code.TIME_UNTRUSTED not in failing,
        not_before=NOW + timedelta(hours=1) if Code.STAY_NOT_STARTED in failing else STAY_START,
        not_after=NOW if Code.STAY_ENDED in failing else STAY_END,
    )
    action = "light.dim" if Code.UNKNOWN_ACTION in failing else "light.set_level"
    params = {"level_pct": "45" if Code.PARAM_SCHEMA in failing else 45}
    view = fixture_view(resource, adapter_locked=Code.ADAPTER_LOCKED in failing)
    return decide(ctx, index, resource, action, params, NOW, view, execute=True)


def test_checks_run_in_evaluation_order() -> None:
    """Fails every check, then repairs them in order: each repair reveals the next code."""
    for position, expected in enumerate(LADDER):
        decision = ladder_request(set(LADDER[position:]))
        assert decision.code is expected, f"with {expected} and every later check failing"
        if expected is Code.GRANTED:
            assert (decision.allowed, decision.public_code) == (True, None)
        else:
            assert (decision.allowed, decision.public_code) == (False, PUBLIC_CODE[expected])


def test_mode_rule_runs_after_review_and_before_the_adapter_lock() -> None:
    grants = [
        grant("res_lr_ac", "climate.set_temperature", target_c=interval("19", "24", "0.5")),
        grant("res_lr_ac", "climate.set_mode"),
    ]
    hot_and_locked = fixture_view("res_lr_ac", target_c=Decimal("28"), adapter_locked=True)
    ok_but_locked = fixture_view("res_lr_ac", target_c=Decimal("22"), adapter_locked=True)
    ctx = resident_context()

    def switch(index: PolicyIndex, view: StateView) -> Code:
        return decide(
            ctx, index, "res_lr_ac", "climate.set_mode", {"mode": "cool"}, NOW, view, execute=True
        ).code

    review = room203_index(grants=grants, status={"res_lr_ac": "needs_review"})
    assert switch(review, hot_and_locked) is Code.RESOURCE_NEEDS_REVIEW
    confirmed = room203_index(grants=grants)
    assert switch(confirmed, hot_and_locked) is Code.MODE_WOULD_BYPASS_FENCE
    assert switch(confirmed, ok_but_locked) is Code.ADAPTER_LOCKED


def test_an_earlier_check_wins_over_a_later_one() -> None:
    no_on = {"resources": ["res_203_light"], "actions": ["light.on", "light.set_level"]}
    blocked = room203_index(denies=[no_on], status={"res_203_light": "blocked"})
    assert run(blocked, "res_203_light", "light.on").code is Code.RESOURCE_UNCONFIRMED
    assert run(blocked, "res_203_light", "light.toggle").code is Code.RESOURCE_UNCONFIRMED
    denied = room203_index(denies=[no_on])
    schema_error = run(denied, "res_203_light", "light.set_level", {"level_pct": 40, "flash": 1})
    assert schema_error.code is Code.PARAM_SCHEMA


def test_a_revoked_or_expired_session_is_inactive() -> None:
    index = room203_index()
    for ctx in (
        guest_context(session_active=False),
        resident_context(session_active=False),
        resident_context(not_after=NOW),
        resident_context(not_before=NOW + timedelta(seconds=1)),
    ):
        decision = run(index, "res_203_light", "state.read", ctx=ctx)
        assert (decision.code, decision.public_code) == (Code.SESSION_INACTIVE, "ACCESS_EXPIRED")
    assert run(index, "res_203_light", "state.read", ctx=resident_context(not_before=NOW)).allowed


@pytest.mark.parametrize("status", ["pending", "", "Blocked"])
def test_an_unknown_binding_status_hides_the_resource(status: str) -> None:
    index = room203_index(status={"res_203_light": status})  # type: ignore[dict-item]
    for action in ("metadata.read", "state.read", "light.on"):
        decision = run(index, "res_203_light", action)
        assert (decision.code, decision.public_code) == (Code.RESOURCE_UNCONFIRMED, "NOT_FOUND")


def test_without_a_state_view_writes_count_as_locked() -> None:
    """The client locks after every reconnect, which is exactly when the state is missing."""
    index = room203_index()

    def unseen(action: str) -> Code:
        return decide(
            guest_context(), index, "res_203_light", action, {}, NOW, None, execute=True
        ).code

    assert unseen("light.on") is Code.ADAPTER_LOCKED
    assert unseen("state.read") is Code.GRANTED


def test_execute_has_no_default() -> None:
    """A forgotten flag must not let a preview context act: the call itself fails."""
    with pytest.raises(TypeError, match="execute"):
        decide(guest_context(), room203_index(), "res_203_light", "light.on", {}, NOW, None)  # type: ignore[call-arg]


def test_context_and_index_must_share_a_policy_version() -> None:
    """A context built for another version than the index fails closed instead of deciding."""
    for version in (0, 2):
        with pytest.raises(ValueError, match="policy version mismatch"):
            run(
                room203_index(),
                "res_203_light",
                "state.read",
                ctx=guest_context(policy_version=version),
            )
    assert run(room203_index(), "res_203_light", "state.read", ctx=guest_context()).allowed


def test_now_must_be_timezone_aware() -> None:
    naive = NOW.replace(tzinfo=None)
    for ctx in (guest_context(), resident_context()):  # a resident has no window to compare
        with pytest.raises(ValueError, match="timezone-aware"):
            decide(
                ctx, room203_index(), "res_203_light", "state.read", {}, naive, None, execute=False
            )


# --- implicit metadata.read ---


def test_metadata_read_follows_from_any_granted_action() -> None:
    off_only = room203_index(grants=[grant("res_203_light", "light.off")])
    implied = run(off_only, "res_203_light", "metadata.read")
    assert (implied.code, implied.matched) == (Code.GRANTED, ("room_guest@1:0",))
    assert run(off_only, "res_203_light", "state.read").code is Code.NO_GRANT  # not implied
    read_only = room203_index(grants=[grant("res_203_hum", "state.read")])
    assert run(read_only, "res_203_hum", "metadata.read").code is Code.GRANTED
    assert run(read_only, "res_203_trv", "metadata.read").code is Code.NO_GRANT


def test_metadata_read_is_not_implied_by_denied_actions_and_can_be_denied_itself() -> None:
    deny = {"resources": ["res_203_light"], "actions": ["light.off"]}
    denied_only = room203_index(grants=[grant("res_203_light", "light.off")], denies=[deny])
    assert run(denied_only, "res_203_light", "metadata.read").code is Code.NO_GRANT
    no_labels = {"resources": ["res_203_light"], "actions": ["metadata.read"]}
    hidden = room203_index(grants=[grant("res_203_light", "light.on")], denies=[no_labels])
    assert run(hidden, "res_203_light", "metadata.read").code is Code.DENIED_BY_RULE


def test_metadata_read_follows_only_from_state_read_or_a_write_the_device_can_do() -> None:
    history = room203_index(grants=[grant("res_203_light", "history.read")], denies=[])
    assert run(history, "res_203_light", "metadata.read").code is Code.NO_GRANT
    dimming = room203_index(grants=[grant("res_203_bedside", "light.set_level")])  # on/off only
    assert run(dimming, "res_203_bedside", "metadata.read").code is Code.NO_GRANT
    review = room203_index(
        grants=[grant("res_203_light", "light.off")], status={"res_203_light": "needs_review"}
    )
    assert run(review, "res_203_light", "metadata.read").code is Code.GRANTED  # read-only now
    assert run(review, "res_203_light", "light.off").code is Code.RESOURCE_NEEDS_REVIEW


# --- normalized parameters and matched grants ---


@pytest.mark.parametrize(
    ("action", "params", "normalized"),
    [
        ("climate.set_temperature", {"target_c": "21.5"}, {"target_c": "21.5"}),
        ("climate.set_temperature", {"target_c": "22.0"}, {"target_c": "22"}),
        ("climate.set_temperature", {"target_c": "20"}, {"target_c": "20"}),
        ("climate.set_temperature", {"target_c": "05"}, {"target_c": "5"}),
        ("climate.set_temperature", {"target_c": "-0.0"}, {"target_c": "0"}),
        ("climate.set_mode", {"mode": "heat"}, {"mode": "heat"}),
        ("state.read", {}, {}),
    ],
)
def test_normalized_params_come_from_the_parsed_model(
    action: str, params: dict[str, Any], normalized: dict[str, Any]
) -> None:
    decision = run(room203_index(), "res_203_trv", action, params)
    assert decision.normalized_params == normalized


def test_normalized_params_keep_ints_and_stay_empty_without_a_valid_body() -> None:
    index = room203_index()
    level = run(index, "res_203_light", "light.set_level", {"level_pct": 40})
    assert level.normalized_params == {"level_pct": 40}
    assert type(level.normalized_params["level_pct"]) is int
    as_float = run(index, "res_203_light", "light.set_level", {"level_pct": 4e1})
    assert (as_float.code, as_float.normalized_params) == (Code.PARAM_SCHEMA, {})
    assert run(index, "res_lr_x", "light.set_level", {"level_pct": 40}).normalized_params == {}


def test_normalization_ignores_the_callers_decimal_context() -> None:
    index = room203_index()
    with localcontext(prec=2):  # would round 21.5 to 22
        decision = run(index, "res_203_trv", "climate.set_temperature", {"target_c": "21.5"})
    assert (decision.code, decision.normalized_params["target_c"]) == (Code.GRANTED, "21.5")


def test_a_decision_is_read_only() -> None:
    index = room203_index()
    level = run(index, "res_203_light", "light.set_level", {"level_pct": 40})
    with pytest.raises(TypeError):
        level.normalized_params["level_pct"] = 100  # type: ignore[index]
    denial = run(index, "res_203_light", "light.set_level", {"level_pct": 101})
    with pytest.raises(TypeError):
        denial.normalized_params["level_pct"] = 100  # type: ignore[index]
    assert denial.public_code is PublicError.INVALID_PARAMETERS


def test_matched_names_every_contributing_grant_once() -> None:
    together = room203_index(grants=[grant("res_203_light", "light.set_level", "light.on")])
    assert run(together, "res_203_light", "light.set_level", {"level_pct": 40}).matched == (
        "room_guest@1:0",
    )
    apart = room203_index(
        grants=[
            grant("res_203_light", "light.on"),
            grant("res_203_light", "light.set_level"),
            grant("res_203_light", "light.off"),
        ]
    )
    on = run(apart, "res_203_light", "light.set_level", {"level_pct": 40})
    assert on.matched == ("room_guest@1:1", "room_guest@1:0")  # the action's grant first
    off = run(apart, "res_203_light", "light.set_level", {"level_pct": 0})
    assert off.matched == ("room_guest@1:1", "room_guest@1:2")
    assert run(apart, "res_203_light", "light.on").matched == ("room_guest@1:0",)


def test_matched_holds_only_grants_whose_interval_and_step_admit_the_value() -> None:
    index = room203_index(
        grants=[
            grant("res_203_trv", "climate.set_temperature", target_c=interval("18", "24", "1")),
            grant("res_203_trv", "climate.set_temperature", target_c=interval("20", "22", "0.5")),
        ]
    )

    def matched(value: str) -> tuple[str, ...]:
        decision = run(index, "res_203_trv", "climate.set_temperature", {"target_c": value})
        assert decision.allowed
        return decision.matched

    assert matched("21") == ("room_guest@1:0", "room_guest@1:1")
    assert matched("21.5") == ("room_guest@1:1",)
    assert matched("23") == ("room_guest@1:0",)


def test_denials_match_no_grant() -> None:
    index = room203_index()
    for decision in (
        run(index, "res_203_trv", "climate.set_temperature", {"target_c": "25"}),
        run(index, "res_203_light", "cover.open"),
        run(index, "res_lr_ac", "state.read"),
    ):
        assert not decision.allowed and decision.matched == ()


# --- parameter checks agree with the effective capability ---


@pytest.mark.parametrize(
    ("resource", "grants", "fences"),
    [
        (
            "res_203_trv",
            [grant("res_203_trv", "climate.set_temperature", target_c=interval("19", "24", "0.5"))],
            [{"min": "0", "max": "22"}],
        ),
        (
            "res_203_trv",
            [
                grant(
                    "res_203_trv", "climate.set_temperature", target_c=interval("18", "20", "0.5")
                ),
                grant("res_203_trv", "climate.set_temperature", target_c=interval("22", "40", "1")),
            ],
            [],
        ),
        (
            "res_lr_ac",
            [grant("res_lr_ac", "climate.set_temperature")],
            [{"min": "10", "max": "26"}],
        ),
    ],
)
def test_temperatures_allowed_are_exactly_the_effective_capability(
    resource: str, grants: list[dict[str, Any]], fences: list[dict[str, str]]
) -> None:
    index = room203_index(grants=grants)
    ctx = resident_context(
        *(
            {
                "scope_type": "instance",
                "action": "climate.set_temperature",
                "parameters": {"target_c": bound},
            }
            for bound in fences
        )
    )
    capability = index.effective_capability(ctx, resource, Action.CLIMATE_SET_TEMPERATURE)
    assert capability is not None
    for tenths in range(-20, 450):
        value = Decimal(tenths) / 10
        decision = run(index, resource, "climate.set_temperature", {"target_c": str(value)}, ctx)
        offered = any(iv.contains(value) and iv.on_step(value) for iv in capability.intervals)
        assert decision.allowed is offered, f"{value}: {decision.code}"


def test_device_limits_refuse_values_a_grant_allows() -> None:
    rule = interval("0", "40", "0.5")
    index = room203_index(grants=[grant("res_203_trv", "climate.set_temperature", target_c=rule)])

    def set_to(value: str) -> Code:  # the radiator takes 7-30
        return run(index, "res_203_trv", "climate.set_temperature", {"target_c": value}).code

    assert (set_to("6.5"), set_to("7"), set_to("30"), set_to("30.5")) == (
        Code.PARAM_OUT_OF_RANGE,
        Code.GRANTED,
        Code.GRANTED,
        Code.PARAM_OUT_OF_RANGE,
    )


def test_a_value_must_lie_on_the_grant_device_and_schema_grids_at_once() -> None:
    """The step is the least common multiple of the grant's, the device's and the schema's."""

    def set_to(index: PolicyIndex, resource: str, value: str) -> Code:
        return run(index, resource, "climate.set_temperature", {"target_c": value}).code

    halves = interval("19", "24", "0.5")
    ac = room203_index(grants=[grant("res_lr_ac", "climate.set_temperature", target_c=halves)])
    assert set_to(ac, "res_lr_ac", "21.5") is Code.PARAM_STEP  # the AC steps in whole degrees
    assert set_to(ac, "res_lr_ac", "21") is Code.GRANTED
    thirds = interval("19", "24", "0.3")  # meets the radiator's 0.5 only every 1.5 degrees
    trv = room203_index(grants=[grant("res_203_trv", "climate.set_temperature", target_c=thirds)])
    assert set_to(trv, "res_203_trv", "21.3") is Code.PARAM_STEP
    assert set_to(trv, "res_203_trv", "21") is Code.GRANTED
    no_rule = room203_index(grants=[grant("res_lr_ac", "climate.set_temperature")])  # 8-30
    assert (
        set_to(no_rule, "res_lr_ac", "21"),
        set_to(no_rule, "res_lr_ac", "21.5"),
        set_to(no_rule, "res_lr_ac", "31"),
    ) == (Code.GRANTED, Code.PARAM_STEP, Code.PARAM_OUT_OF_RANGE)


def test_a_failing_step_computation_is_a_param_step(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Interval, value: Decimal) -> bool:
        raise InvalidOperation

    monkeypatch.setattr(Interval, "on_step", broken)
    decision = run(room203_index(), "res_203_trv", "climate.set_temperature", {"target_c": "21"})
    assert decision.code is Code.PARAM_STEP


# --- modes and device capabilities ---


def test_unconfirmed_modes_are_never_granted() -> None:
    index = room203_index(grants=[grant("res_lr_ac", "climate.set_mode")], modes={"res_lr_ac": ()})
    for mode in ("cool", "heat", "off"):
        decision = run(index, "res_lr_ac", "climate.set_mode", {"mode": mode})
        assert decision.code is Code.PARAM_OUT_OF_RANGE
    heat_only = room203_index(
        grants=[grant("res_lr_ac", "climate.set_mode")], modes={"res_lr_ac": ("heat",)}
    )
    assert run(heat_only, "res_lr_ac", "climate.set_mode", {"mode": "cool"}).code is (
        Code.PARAM_OUT_OF_RANGE
    )
    assert run(heat_only, "res_lr_ac", "climate.set_mode", {"mode": "heat"}).allowed


def test_modes_outside_the_grant_or_a_fence_are_refused() -> None:
    index = room203_index(
        grants=[grant("res_lr_ac", "climate.set_mode", mode={"allowed": ["off", "heat"]})]
    )
    assert run(index, "res_lr_ac", "climate.set_mode", {"mode": "cool"}).code is (
        Code.PARAM_OUT_OF_RANGE
    )
    no_heat = {
        "scope_type": "unit",
        "scope_id": "unit_lr",
        "action": "climate.set_mode",
        "parameters": {"mode": {"allowed": ["off"]}},
    }
    fenced = resident_context(no_heat)
    heat = run(index, "res_lr_ac", "climate.set_mode", {"mode": "heat"}, fenced)
    assert heat.code is Code.FENCE_VIOLATION
    assert run(index, "res_lr_ac", "climate.set_mode", {"mode": "off"}, fenced).allowed


def test_the_mode_rule_without_a_state_or_with_an_empty_temperature_range() -> None:
    temperature = grant("res_lr_ac", "climate.set_temperature", target_c=interval("19", "24", "1"))
    index = room203_index(grants=[temperature, grant("res_lr_ac", "climate.set_mode")])
    blind = decide(
        resident_context(),
        index,
        "res_lr_ac",
        "climate.set_mode",
        {"mode": "heat"},
        NOW,
        None,
        execute=True,
    )
    assert blind.code is Code.MODE_WOULD_BYPASS_FENCE  # no state view: the target is unknown
    above = {
        "scope_type": "instance",
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "25", "max": "30"}},
    }
    nothing_left = run(
        index, "res_lr_ac", "climate.set_mode", {"mode": "heat"}, resident_context(above)
    )
    assert (nothing_left.code, nothing_left.check_range) == (Code.MODE_WOULD_BYPASS_FENCE, None)


def test_actions_the_device_cannot_do_are_capability_missing() -> None:
    index = room203_index(
        grants=[
            grant("res_203_bedside", "light.set_level", "light.on"),
            grant("res_203_hum", "light.on"),
        ]
    )
    dim = run(index, "res_203_bedside", "light.set_level", {"level_pct": 50})
    assert (dim.code, dim.public_code) == (Code.CAPABILITY_MISSING, "ACTION_NOT_ALLOWED")
    assert run(index, "res_203_hum", "light.on").code is Code.CAPABILITY_MISSING


# --- actions a request needs besides its own, and history ---


def test_a_deny_on_an_action_the_request_needs_wins() -> None:
    light = [grant("res_203_light", "light.on", "light.off", "light.set_level")]
    no_off = room203_index(
        grants=light, denies=[{"resources": ["res_203_light"], "actions": ["light.off"]}]
    )
    to_zero = run(no_off, "res_203_light", "light.set_level", {"level_pct": 0})
    assert to_zero.code is Code.DENIED_BY_RULE  # dimming to zero would bypass the deny
    assert run(no_off, "res_203_light", "light.set_level", {"level_pct": 30}).allowed
    blind = [grant("res_203_blind", "cover.open", "cover.close", "cover.set_position")]
    no_close = room203_index(
        grants=blind, denies=[{"resources": ["res_203_blind"], "actions": ["cover.close"]}]
    )
    for position, expected in [(40, Code.DENIED_BY_RULE), (100, Code.GRANTED)]:
        params = {"position_pct": position}
        assert run(no_close, "res_203_blind", "cover.set_position", params).code is expected


def test_history_is_never_granted_in_the_mvp() -> None:
    granted = room203_index(grants=[grant("res_203_light", "history.read")], denies=[])
    assert run(granted, "res_203_light", "history.read").code is Code.CAPABILITY_MISSING
    assert run(room203_index(), "res_203_light", "history.read").code is Code.DENIED_BY_RULE
    assert run(room203_index(), "res_203_bedside", "history.read").code is Code.NO_GRANT


def test_granted_but_nothing_admissible_is_out_of_range_or_fenced_never_no_grant() -> None:
    """An index entry without intervals is still a grant: the value is refused, not the action."""
    climate = room203_index().resource("res_lr_ac")
    assert climate is not None
    huge = replace(climate.capabilities, target_c_max=Decimal("1E+6"))
    resources = {"res_lr_ac": replace(climate, capabilities=huge)}
    both = grant("res_lr_ac", "climate.set_temperature", "climate.set_mode")
    rules = RoleRules.model_validate({"grants": [both]})
    units = {"unit_lr": frozenset(resources)}
    beyond = PolicyIndex({("room_guest", 1): rules}, resources, units, policy_version=1)
    ctx = resident_context()
    # a device limit beyond the rule format: the index offers nothing (fail closed)
    capability = beyond.effective_capability(ctx, "res_lr_ac", Action.CLIMATE_SET_TEMPERATURE)
    assert capability is not None and capability.intervals == ()
    value = run(beyond, "res_lr_ac", "climate.set_temperature", {"target_c": "21"})
    assert value.code is Code.PARAM_OUT_OF_RANGE
    switch = run(beyond, "res_lr_ac", "climate.set_mode", {"mode": "cool"})
    assert switch.code is Code.MODE_WOULD_BYPASS_FENCE  # an empty range keeps no target inside
    # a fence that leaves nothing of the grant
    rule = interval("19", "24", "0.5")
    index = room203_index(grants=[grant("res_203_trv", "climate.set_temperature", target_c=rule)])
    above = resident_context(
        {
            "scope_type": "instance",
            "action": "climate.set_temperature",
            "parameters": {"target_c": {"min": "25", "max": "30"}},
        }
    )
    fenced = index.effective_capability(above, "res_203_trv", Action.CLIMATE_SET_TEMPERATURE)
    assert fenced is not None and fenced.intervals == ()
    inside_grant = run(index, "res_203_trv", "climate.set_temperature", {"target_c": "21"}, above)
    assert inside_grant.code is Code.FENCE_VIOLATION
    outside = run(index, "res_203_trv", "climate.set_temperature", {"target_c": "26"}, above)
    assert outside.code is Code.PARAM_OUT_OF_RANGE


# --- several fences, non-finite targets and the modes against the effective capability ---


@pytest.mark.parametrize("reverse", [False, True])
def test_every_fence_narrows_whatever_their_order(reverse: bool) -> None:
    def fence(action: str, scope: tuple[str, str | None], **rule: Any) -> dict[str, Any]:
        return {"scope_type": scope[0], "scope_id": scope[1], "action": action, "parameters": rule}

    fences = [
        fence("climate.set_temperature", ("instance", None), target_c={"min": "0", "max": "24"}),
        fence("climate.set_temperature", ("unit", "unit_lr"), target_c={"min": "0", "max": "22"}),
        fence("climate.set_mode", ("instance", None), mode={"allowed": ["off", "heat"]}),
        fence("climate.set_mode", ("principal", "prn_res"), mode={"allowed": ["off", "cool"]}),
    ]
    ctx = resident_context(*(reversed(fences) if reverse else fences))
    index = room203_index(
        grants=[grant("res_lr_ac", "climate.set_temperature", "climate.set_mode")]
    )

    def code(action: str, **params: str) -> Code:
        return run(index, "res_lr_ac", action, params, ctx).code

    assert code("climate.set_temperature", target_c="23") is Code.FENCE_VIOLATION
    assert code("climate.set_temperature", target_c="22") is Code.GRANTED
    assert code("climate.set_mode", mode="heat") is Code.FENCE_VIOLATION
    assert code("climate.set_mode", mode="off") is Code.GRANTED


@pytest.mark.parametrize("target", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_a_non_finite_target_counts_as_outside(target: str) -> None:
    temperature = grant("res_lr_ac", "climate.set_temperature", target_c=interval("19", "24", "1"))
    index = room203_index(grants=[temperature, grant("res_lr_ac", "climate.set_mode")])
    view = fixture_view("res_lr_ac", target_c=Decimal(target))
    params = {"mode": "heat"}
    decision = decide(
        resident_context(), index, "res_lr_ac", "climate.set_mode", params, NOW, view, execute=True
    )
    assert decision.code is Code.MODE_WOULD_BYPASS_FENCE


@pytest.mark.parametrize(
    ("grants", "fence_modes", "confirmed"),
    [
        ([grant("res_lr_ac", "climate.set_mode")], [], None),
        (
            [grant("res_lr_ac", "climate.set_mode", mode={"allowed": ["off", "heat"]})],
            [["off", "cool"]],
            None,
        ),
        (
            [
                grant("res_lr_ac", "climate.set_mode", mode={"allowed": ["off"]}),
                grant("res_lr_ac", "climate.set_mode", mode={"allowed": ["cool"]}),
            ],
            [],
            None,
        ),
        ([grant("res_lr_ac", "climate.set_mode")], [], ("heat",)),
        ([grant("res_lr_ac", "climate.set_mode")], [], ()),
    ],
)
def test_modes_allowed_are_exactly_the_effective_capability(
    grants: list[dict[str, Any]], fence_modes: list[list[str]], confirmed: tuple[str, ...] | None
) -> None:
    index = room203_index(
        grants=grants, modes=None if confirmed is None else {"res_lr_ac": confirmed}
    )
    ctx = resident_context(
        *(
            {
                "scope_type": "instance",
                "action": "climate.set_mode",
                "parameters": {"mode": {"allowed": allowed}},
            }
            for allowed in fence_modes
        )
    )
    capability = index.effective_capability(ctx, "res_lr_ac", Action.CLIMATE_SET_MODE)
    assert capability is not None
    for mode in ("off", "cool", "heat", "dry"):
        decision = run(index, "res_lr_ac", "climate.set_mode", {"mode": mode}, ctx)
        assert decision.allowed is (mode in capability.allowed), f"{mode}: {decision.code}"
