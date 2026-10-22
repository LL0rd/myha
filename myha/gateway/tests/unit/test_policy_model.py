from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from myha_gateway.adapters.base import READ_ACTIONS, WRITE_ACTIONS, Action
from myha_gateway.policy.codes import (
    EVALUATION_ORDER,
    HTTP_STATUS,
    PUBLIC_CODE,
    DecisionCode,
    PublicError,
    public_error,
)
from myha_gateway.policy.model import (
    PARAM_SCHEMA,
    Deny,
    EnumRule,
    Fence,
    Grant,
    Interval,
    LevelParams,
    ModeParams,
    NoParams,
    ParamSchemaError,
    PositionParams,
    RangeRule,
    RoleRules,
    TargetCParams,
    parse_params,
)

TARGET_19_24 = {"target_c": {"intervals": [{"min": "19", "max": "24", "step": "0.5"}]}}


def interval(**fields: Any) -> Interval:
    return Interval.model_validate({"min": "19", "max": "24", "step": "0.5"} | fields)


# --- request parameter schemas ---


@pytest.mark.parametrize(("raw", "value"), [("21.5", "21.5"), ("21", "21"), ("-5.0", "-5.0")])
def test_target_c_accepts_plain_decimal_strings(raw: str, value: str) -> None:
    params = parse_params(Action.CLIMATE_SET_TEMPERATURE, {"target_c": raw})
    assert params == TargetCParams(target_c=raw)
    assert isinstance(params, TargetCParams)
    assert params.value == Decimal(value)


@pytest.mark.parametrize(
    "raw",
    [
        "21.55",
        "NaN",
        "Infinity",
        "1e1",
        "abc",
        "",
        " 21",
        "21\n",
        "١٢",
        "121",
        21.5,
        21,
        True,
        None,
    ],
)
def test_target_c_rejects_everything_but_short_decimal_strings(raw: object) -> None:
    with pytest.raises(ParamSchemaError):
        parse_params(Action.CLIMATE_SET_TEMPERATURE, {"target_c": raw})


@pytest.mark.parametrize("action", [Action.LIGHT_SET_LEVEL, Action.COVER_SET_POSITION])
@pytest.mark.parametrize("raw", [101, -1, "40", True, 40.0, None])
def test_percent_rejects_out_of_range_and_non_ints(action: Action, raw: object) -> None:
    name = "level_pct" if action is Action.LIGHT_SET_LEVEL else "position_pct"
    with pytest.raises(ParamSchemaError):
        parse_params(action, {name: raw})


def test_percent_accepts_the_bounds() -> None:
    assert parse_params(Action.LIGHT_SET_LEVEL, {"level_pct": 0}) == LevelParams(level_pct=0)
    assert parse_params(Action.COVER_SET_POSITION, {"position_pct": 100}) == PositionParams(
        position_pct=100
    )


def test_mode_is_one_of_off_cool_heat() -> None:
    assert parse_params(Action.CLIMATE_SET_MODE, {"mode": "cool"}) == ModeParams(mode="cool")
    for raw in ["auto", "COOL", ""]:
        with pytest.raises(ParamSchemaError):
            parse_params(Action.CLIMATE_SET_MODE, {"mode": raw})


@pytest.mark.parametrize(
    ("action", "raw"),
    [
        (Action.LIGHT_SET_LEVEL, {"level_pct": 40, "flash": "short"}),
        (Action.CLIMATE_SET_TEMPERATURE, {"target_c": "21.5", "hvac_mode": "heat"}),
        (Action.LIGHT_ON, {"x": 1}),
        (Action.LIGHT_SET_LEVEL, {}),
    ],
)
def test_extra_or_missing_fields_are_schema_errors(action: Action, raw: dict[str, Any]) -> None:
    with pytest.raises(ParamSchemaError):
        parse_params(action, raw)


def test_actions_without_parameters_take_an_empty_object() -> None:
    assert parse_params(Action.LIGHT_ON, {}) == NoParams()
    assert parse_params(Action.STATE_READ, {}) == NoParams()


def test_schema_error_is_a_value_error_without_the_input() -> None:
    assert issubclass(ParamSchemaError, ValueError)
    with pytest.raises(ParamSchemaError) as caught:
        parse_params(Action.LIGHT_SET_LEVEL, {"level_pct": 12345})
    assert "level_pct" in str(caught.value)
    assert "12345" not in str(caught.value)


def test_every_action_has_a_parameter_schema() -> None:
    assert set(PARAM_SCHEMA) == set(Action)
    assert PARAM_SCHEMA[Action.LIGHT_SET_LEVEL] is LevelParams
    assert PARAM_SCHEMA[Action.CLIMATE_SET_TEMPERATURE] is TargetCParams
    assert PARAM_SCHEMA[Action.CLIMATE_SET_MODE] is ModeParams
    assert PARAM_SCHEMA[Action.COVER_SET_POSITION] is PositionParams
    assert PARAM_SCHEMA[Action.COVER_OPEN] is NoParams


# --- intervals and rules ---


@pytest.mark.parametrize(
    "fields",
    [
        {"min": "24", "max": "19"},
        {"step": "0"},
        {"step": "-0.5"},
        {"min": 19.5},  # float: already inexact
        {"min": 19},  # int: decimals come as strings
        {"min": "NaN"},
        {"max": "Infinity"},
        {"step": "1e-1"},
        {"min": " 19"},
        {"min": "1_9"},
        {"step": "0.0000001"},  # more than six decimals: step checks would lose exactness
        {"max": "1234567"},
        {"origin": True},
        {"unit": "C"},
    ],
)
def test_interval_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        interval(**fields)


def test_interval_parses_decimal_strings_and_keeps_them_exact() -> None:
    parsed = interval(origin="0.25")
    assert parsed == Interval(
        min=Decimal("19"), max=Decimal("24"), step=Decimal("0.5"), origin=Decimal("0.25")
    )
    assert interval().origin == Decimal("0")
    assert parsed.model_dump(mode="json") == {
        "min": "19",
        "max": "24",
        "step": "0.5",
        "origin": "0.25",
    }


def test_interval_contains_is_inclusive() -> None:
    rule = interval()
    assert rule.contains(Decimal("19")) and rule.contains(Decimal("24"))
    assert not rule.contains(Decimal("18.9")) and not rule.contains(Decimal("24.1"))


def test_interval_on_step_is_exact_relative_to_origin() -> None:
    rule = interval()
    assert rule.on_step(Decimal("21.5"))
    assert rule.on_step(Decimal("-5.0"))
    assert not rule.on_step(Decimal("21.3"))
    shifted = interval(origin="0.25")
    assert shifted.on_step(Decimal("21.25"))
    assert not shifted.on_step(Decimal("21.5"))
    tenths = interval(step="0.1")
    assert tenths.on_step(Decimal("21.3"))  # a float remainder would not be exactly zero here


def test_models_are_frozen() -> None:
    rule = interval()
    with pytest.raises(ValidationError):
        rule.min = Decimal("0")  # type: ignore[misc]


def test_range_and_enum_rules_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        RangeRule(intervals=())
    with pytest.raises(ValidationError):
        EnumRule(allowed=frozenset())
    with pytest.raises(ValidationError):
        EnumRule.model_validate({"allowed": ["off", "auto"]})
    assert EnumRule.model_validate({"allowed": ["off", "cool", "off"]}).allowed == {"off", "cool"}


# --- grants, denies, fences ---


def test_grant_parses_the_stored_json_shape() -> None:
    grant = Grant.model_validate(
        {
            "resources": ["res_203_trv"],
            "actions": ["climate.set_temperature", "climate.set_mode"],
            "parameters": TARGET_19_24 | {"mode": {"allowed": ["off", "heat"]}},
        }
    )
    assert grant.actions == (Action.CLIMATE_SET_TEMPERATURE, Action.CLIMATE_SET_MODE)
    assert grant.parameters["target_c"] == RangeRule(intervals=(interval(),))
    assert grant.parameters["mode"] == EnumRule(allowed=frozenset({"off", "heat"}))
    assert Grant(resources=("res_1",), actions=(Action.LIGHT_ON,)).parameters == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"resources": ["*"]},
        {"resources": ["res_*"]},
        {"resources": ["res 1"]},
        {"resources": [""]},
        {"resources": []},
        {"actions": ["*"]},
        {"actions": ["light.*"]},
        {"actions": ["light.toggle"]},
        {"actions": []},
        {"parameters": {"brightness": {"intervals": [{"min": "0", "max": "1", "step": "1"}]}}},
        {"parameters": {"mode": {"intervals": [{"min": "0", "max": "1", "step": "1"}]}}},
        {"parameters": {"target_c": {"allowed": ["off"]}}},
        {"parameters": {"target_c": {"intervals": []}}},
        {"priority": 1},
    ],
)
def test_grant_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Grant.model_validate({"resources": ["res_1"], "actions": ["light.on"]} | fields)


@pytest.mark.parametrize(
    "fields", [{"resources": ["*"]}, {"actions": ["*"]}, {"actions": []}, {"parameters": {}}]
)
def test_deny_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Deny.model_validate({"resources": ["res_1"], "actions": ["history.read"]} | fields)


def fence(**fields: Any) -> Fence:
    base = {"scope_type": "unit", "scope_id": "unit_203", "action": "climate.set_temperature"}
    return Fence.model_validate(base | {"parameters": TARGET_19_24} | fields)


@pytest.mark.parametrize("scope", ["unit", "stay", "principal", "session"])
def test_fence_needs_a_scope_id_unless_instance_wide(scope: str) -> None:
    with pytest.raises(ValidationError):
        fence(scope_type=scope, scope_id=None)
    assert fence(scope_type="instance", scope_id=None).resource_ids is None
    assert fence(scope_type=scope).scope_id == "unit_203"


@pytest.mark.parametrize(
    "fields",
    [
        {"scope_type": "room"},
        {"scope_id": ""},
        {"resource_ids": []},
        {"resource_ids": ["*"]},
        {"action": "climate.*"},
        {"parameters": {}},  # a fence without parameter rules would narrow nothing
    ],
)
def test_fence_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        fence(**fields)


def test_role_rules_parse_grants_and_denies() -> None:
    rules = RoleRules.model_validate(
        {
            "grants": [{"resources": ["res_1"], "actions": ["metadata.read", "state.read"]}],
            "denies": [{"resources": ["res_1"], "actions": ["history.read"]}],
        }
    )
    assert rules.denies == (Deny(resources=("res_1",), actions=(Action.HISTORY_READ,)),)
    assert RoleRules().grants == ()


# --- actions and decision codes ---


def test_read_and_write_actions_partition_the_action_set() -> None:
    assert READ_ACTIONS == {Action.METADATA_READ, Action.STATE_READ, Action.HISTORY_READ}
    assert WRITE_ACTIONS | READ_ACTIONS == set(Action)
    assert not WRITE_ACTIONS & READ_ACTIONS


def test_evaluation_order_follows_the_design() -> None:
    assert [code.value for code in EVALUATION_ORDER] == [
        "PREVIEW_CONTEXT",
        "SESSION_INACTIVE",
        "TIME_UNTRUSTED",
        "STAY_NOT_STARTED",
        "STAY_ENDED",
        "RESOURCE_NOT_IN_SCOPE",
        "RESOURCE_UNCONFIRMED",
        "UNKNOWN_ACTION",
        "PARAM_SCHEMA",
        "DENIED_BY_RULE",
        "NO_GRANT",
        "PARAM_OUT_OF_RANGE",
        "PARAM_STEP",
        "FENCE_VIOLATION",
        "CAPABILITY_MISSING",
        "RESOURCE_NEEDS_REVIEW",
        "MODE_WOULD_BYPASS_FENCE",
        "ADAPTER_LOCKED",
        "GRANTED",
    ]
    assert EVALUATION_ORDER[0] is DecisionCode.PREVIEW_CONTEXT
    assert EVALUATION_ORDER[-1] is DecisionCode.GRANTED


def test_public_codes() -> None:
    assert PUBLIC_CODE[DecisionCode.NO_GRANT] == PublicError.ACTION_NOT_ALLOWED
    assert PUBLIC_CODE[DecisionCode.NO_GRANT] == "ACTION_NOT_ALLOWED"
    assert PUBLIC_CODE[DecisionCode.RESOURCE_NEEDS_REVIEW] == PublicError.UPSTREAM_UNAVAILABLE
    assert PUBLIC_CODE[DecisionCode.RESOURCE_UNCONFIRMED] == PublicError.NOT_FOUND
    assert PUBLIC_CODE[DecisionCode.PARAM_STEP] == PublicError.INVALID_PARAMETERS
    assert PUBLIC_CODE[DecisionCode.STAY_ENDED] == PublicError.ACCESS_EXPIRED
    assert PUBLIC_CODE[DecisionCode.TIME_UNTRUSTED] == PublicError.UPSTREAM_UNAVAILABLE
    assert public_error(DecisionCode.GRANTED) is None
    assert public_error(DecisionCode.PREVIEW_CONTEXT) is PublicError.ACTION_NOT_ALLOWED


def test_every_denial_has_a_public_code() -> None:
    for code in DecisionCode:
        if code is not DecisionCode.GRANTED:
            assert public_error(code) in set(PublicError), code
    assert DecisionCode.GRANTED not in PUBLIC_CODE


def test_public_errors_and_http_status() -> None:
    assert [error.value for error in PublicError] == [
        "UNAUTHENTICATED",
        "NOT_FOUND",
        "ACTION_NOT_ALLOWED",
        "INVALID_PARAMETERS",
        "ACCESS_EXPIRED",
        "UPSTREAM_UNAVAILABLE",
        "RATE_LIMITED",
        "CONFLICT",
    ]
    assert dict(HTTP_STATUS) == {
        PublicError.UNAUTHENTICATED: 401,
        PublicError.NOT_FOUND: 404,
        PublicError.ACTION_NOT_ALLOWED: 403,
        PublicError.INVALID_PARAMETERS: 400,
        PublicError.ACCESS_EXPIRED: 403,
        PublicError.UPSTREAM_UNAVAILABLE: 503,
        PublicError.RATE_LIMITED: 429,
        PublicError.CONFLICT: 409,
    }
