from decimal import Decimal, localcontext
from typing import Any

import pytest
from pydantic import ValidationError

from myha_gateway.adapters.base import READ_ACTIONS, WRITE_ACTIONS, Action
from myha_gateway.policy.codes import (
    EVALUATION_ORDER,
    PUBLIC_CODE,
    DecisionCode,
    PublicError,
    public_error,
)
from myha_gateway.policy.model import (
    PARAM_SCHEMA,
    Bound,
    Deny,
    EnumRule,
    Fence,
    FenceRules,
    Grant,
    Interval,
    LevelParams,
    ModeParams,
    NoParams,
    ParameterRules,
    ParamSchemaError,
    PositionParams,
    RangeRule,
    RoleRules,
    TargetCParams,
    parse_params,
)

TARGET_19_24 = {"intervals": [{"min": "19", "max": "24", "step": "0.5", "origin": "0"}]}
PARAMETRIC = [
    "light.set_level",
    "climate.set_temperature",
    "climate.set_mode",
    "cover.set_position",
]


def interval(**fields: Any) -> Interval:
    return Interval.model_validate({"min": "19", "max": "24", "step": "0.5"} | fields)


def grant(**fields: Any) -> Grant:
    return Grant.model_validate({"resources": ["res_1"], "actions": ["light.on"]} | fields)


def fence(**fields: Any) -> Fence:
    base = {
        "scope_type": "unit",
        "scope_id": "unit_203",
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "19", "max": "22"}},
    }
    return Fence.model_validate(base | fields)


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
        *["21.55", "NaN", "Infinity", "1e1", "abc", "", " 21", "21\n", "١٢", "121"],
        *[21.5, 21, True, None],
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


def test_schema_error_is_short_and_free_of_client_keys_and_values() -> None:
    assert issubclass(ParamSchemaError, ValueError)
    hostile = "<img src=x>\n\x1b[31mX"
    raw = {"level_pct": 12345, hostile: 1} | {f"key{i}": i for i in range(2000)}
    with pytest.raises(ParamSchemaError) as caught:
        parse_params(Action.LIGHT_SET_LEVEL, raw)
    message = str(caught.value)
    assert [reason.split(":")[0] for reason in message.split("; ")] == ["level_pct", "params"]
    assert len(message) < 200
    for leaked in ["12345", "img", "\x1b", "\n", "key1"]:
        assert leaked not in message
    with pytest.raises(ParamSchemaError, match="^params: "):
        parse_params(Action.LIGHT_ON, [hostile])


def test_every_action_has_a_parameter_schema() -> None:
    assert set(PARAM_SCHEMA) == set(Action)
    assert PARAM_SCHEMA[Action.LIGHT_SET_LEVEL] is LevelParams
    assert PARAM_SCHEMA[Action.CLIMATE_SET_TEMPERATURE] is TargetCParams
    assert PARAM_SCHEMA[Action.CLIMATE_SET_MODE] is ModeParams
    assert PARAM_SCHEMA[Action.COVER_SET_POSITION] is PositionParams
    assert PARAM_SCHEMA[Action.COVER_OPEN] is NoParams


# --- bounds, intervals and rules ---


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
        {"min": "19\n"},
        {"min": "1_9"},
        {"step": "0.0000001"},  # more than six decimals
        {"max": "1234567"},
        {"step": Decimal("1E-30")},  # Decimal objects obey the same limits
        {"max": Decimal("1E+100")},
        {"min": Decimal(0.1)},  # an HA float turned Decimal: 0.1000000000000000055…
        {"min": Decimal("NaN")},
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


def test_decimal_objects_are_stored_without_exponent() -> None:
    parsed = interval(min=Decimal("2E+1"), max=Decimal("24"))
    assert parsed.model_dump(mode="json")["min"] == "20"
    assert Interval.model_validate(parsed.model_dump(mode="json")) == parsed


def test_min_may_equal_max_but_not_exceed_it() -> None:
    assert Bound.model_validate({"min": "21", "max": "21"}).contains(Decimal("21"))
    assert interval(min="21", max="21").contains(Decimal("21"))
    with pytest.raises(ValidationError):
        Bound.model_validate({"min": "21.5", "max": "21"})


def test_rule_decimal_schema_shows_the_pattern() -> None:
    schema = Interval.model_json_schema()["properties"]["step"]
    assert schema == {
        "type": "string",
        "pattern": r"^-?[0-9]{1,6}(\.[0-9]{1,6})?$",
        "title": "Step",
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
    quarter = interval(origin="0.25")
    assert quarter.on_step(Decimal("21.25"))
    assert not quarter.on_step(Decimal("21.5"))
    tenth = interval(origin="0.1")  # value + origin would put 21.1 off and 21.5 on the grid
    assert tenth.on_step(Decimal("21.1"))
    assert not tenth.on_step(Decimal("21.5"))
    tenths = interval(step="0.1")
    assert tenths.on_step(Decimal("21.3"))  # a float remainder would not be exactly zero here


def test_on_step_ignores_the_ambient_decimal_context() -> None:
    rule = interval()
    with localcontext(prec=2):  # 21.3 - 0 would round to 21, which is on the grid
        assert not rule.on_step(Decimal("21.3"))
        assert rule.on_step(Decimal("21.5"))


def test_an_interval_in_a_bound_field_is_revalidated_and_refused() -> None:
    """A fence has no step of its own; an Interval must not smuggle one in."""
    with pytest.raises(ValidationError):
        FenceRules(target_c=interval())
    with pytest.raises(ValidationError):
        FenceRules.model_validate({"target_c": interval()})
    assert FenceRules(target_c=Bound(min=Decimal("19"), max=Decimal("22"))).target_c == Bound(
        min=Decimal("19"), max=Decimal("22")
    )


def test_models_are_frozen() -> None:
    rule = interval()
    with pytest.raises(ValidationError):
        rule.min = Decimal("0")
    with pytest.raises(ValidationError):
        fence().parameters.target_c = None


def test_range_and_enum_rules_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        RangeRule(intervals=())
    with pytest.raises(ValidationError):
        EnumRule(allowed=frozenset())
    with pytest.raises(ValidationError):
        EnumRule.model_validate({"allowed": ["off", "auto"]})
    assert EnumRule.model_validate({"allowed": ["off", "cool", "off"]}).allowed == {"off", "cool"}


def test_enum_rule_is_stored_sorted() -> None:
    forward = EnumRule.model_validate({"allowed": ["off", "cool", "heat"]})
    backward = EnumRule.model_validate({"allowed": ["heat", "cool", "off"]})
    assert forward.model_dump(mode="json") == backward.model_dump(mode="json")
    assert forward.model_dump(mode="json") == {"allowed": ["cool", "heat", "off"]}
    # model_construct keeps the given order, so the serialiser alone has to sort.
    unsorted = EnumRule.model_construct(allowed=("off", "heat", "cool"))
    assert unsorted.model_dump(mode="json") == {"allowed": ["cool", "heat", "off"]}


# --- grants, denies, fences ---


def test_grant_parses_the_stored_json_shape() -> None:
    parsed = grant(
        resources=["res_203_trv"],
        actions=["climate.set_temperature", "climate.set_mode"],
        parameters={"target_c": TARGET_19_24, "mode": {"allowed": ["off", "heat"]}},
    )
    assert parsed.actions == (Action.CLIMATE_SET_TEMPERATURE, Action.CLIMATE_SET_MODE)
    assert parsed.parameters == ParameterRules(
        target_c=RangeRule(intervals=(interval(),)),
        mode=EnumRule(allowed=frozenset({"off", "heat"})),
    )
    assert grant().parameters == ParameterRules()


def test_rules_round_trip_through_their_stored_json() -> None:
    stored_grant = {
        "resources": ["res_203_trv"],
        "actions": ["climate.set_temperature", "climate.set_mode"],
        "parameters": {"target_c": TARGET_19_24, "mode": {"allowed": ["heat", "off"]}},
    }
    parsed_grant = Grant.model_validate(stored_grant)
    assert parsed_grant.model_dump(mode="json", exclude_none=True) == stored_grant
    stored_fence = {
        "scope_type": "principal",
        "scope_id": "prn_1",
        "resource_ids": ["res_203_trv"],
        "action": "climate.set_temperature",
        "parameters": {"target_c": {"min": "19", "max": "22"}},
    }
    parsed_fence = Fence.model_validate(stored_fence)
    assert parsed_fence.model_dump(mode="json", exclude_none=True) == stored_fence


def test_rules_are_hashable() -> None:
    climate = grant(actions=["climate.set_temperature"], parameters={"target_c": TARGET_19_24})
    rules = RoleRules(
        grants=(climate,), denies=(Deny(resources=("res_1",), actions=(Action.HISTORY_READ,)),)
    )
    assert hash(climate) == hash(
        grant(actions=["climate.set_temperature"], parameters={"target_c": TARGET_19_24})
    )
    assert len({fence(), fence(), fence(scope_id="unit_204")}) == 2
    assert hash(rules) == hash(rules.model_copy())


@pytest.mark.parametrize(
    "fields",
    [
        {"resources": ["*"]},
        {"resources": ["res_*"]},
        {"resources": ["res 1"]},
        {"resources": [""]},
        {"resources": ["res\x00"]},
        {"resources": ["res‮1"]},  # bidi override
        {"resources": ["r" * 65]},
        {"resources": ["r" * 100_000]},
        {"resources": []},
        {"actions": ["*"]},
        {"actions": ["light.*"]},
        {"actions": ["light.toggle"]},
        {"actions": []},
        {"priority": 1},
    ],
)
def test_grant_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        grant(**fields)


def test_product_ids_are_short_ascii_words() -> None:
    assert grant(resources=["res_203-trv", "r" * 64]).resources == ("res_203-trv", "r" * 64)


@pytest.mark.parametrize(
    "parameters",
    [
        {"brightness": TARGET_19_24},
        {"mode": TARGET_19_24},
        {"target_c": {"allowed": ["off"]}},
        {"target_c": {"intervals": []}},
        {"target_c": {"min": "19", "max": "24"}},  # a bound is a fence rule
        {"mode": {"allowed": ["auto"]}},
    ],
)
def test_parameter_rules_reject_unknown_names_and_wrong_kinds(parameters: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        grant(actions=PARAMETRIC, parameters=parameters)


def test_rule_names_must_belong_to_the_actions() -> None:
    level = {"intervals": [{"min": "0", "max": "50", "step": "1"}]}
    with pytest.raises(ValidationError, match="level_pct: not a parameter"):
        grant(actions=["cover.set_position"], parameters={"level_pct": level})
    with pytest.raises(ValidationError, match="level_pct: not a parameter"):
        fence(action="cover.set_position", parameters={"level_pct": {"min": "0", "max": "50"}})
    with pytest.raises(ValidationError, match="target_c: not a parameter"):
        fence(action="climate.set_mode")  # a temperature fence hangs on set_temperature
    accepted = grant(
        actions=["light.set_level", "cover.set_position"],
        parameters={"level_pct": level, "position_pct": level},
    )
    assert accepted.parameters.level_pct == accepted.parameters.position_pct
    assert fence().parameters == FenceRules(target_c=Bound(min=Decimal("19"), max=Decimal("22")))


@pytest.mark.parametrize(
    "fields", [{"resources": ["*"]}, {"actions": ["*"]}, {"actions": []}, {"parameters": {}}]
)
def test_deny_rejects(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Deny.model_validate({"resources": ["res_1"], "actions": ["history.read"]} | fields)


@pytest.mark.parametrize("scope", ["unit", "stay", "principal", "session"])
def test_scoped_fences_need_a_scope_id(scope: str) -> None:
    assert fence(scope_type=scope).scope_id == "unit_203"
    with pytest.raises(ValidationError):
        fence(scope_type=scope, scope_id=None)


def test_instance_fences_have_no_scope_id() -> None:
    assert fence(scope_type="instance", scope_id=None).resource_ids is None
    with pytest.raises(ValidationError):
        fence(scope_type="instance")


@pytest.mark.parametrize(
    "fields",
    [
        {"scope_type": "room"},
        {"scope_id": ""},
        {"resource_ids": []},
        {"resource_ids": ["*"]},
        {"action": "climate.*"},
        {"parameters": {}},  # a fence without rules would narrow nothing
        {"parameters": {"target_c": None}},
        {"parameters": {"target_c": {"min": "19", "max": "22", "step": "0.5"}}},  # grant's step
        {"parameters": {"target_c": TARGET_19_24}},
        {"parameters": {"target_c": {"min": "23", "max": "22"}}},
        {"parameters": {"brightness": {"min": "0", "max": "1"}}},
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


def test_public_code_table_matches_the_design() -> None:
    code, error = DecisionCode, PublicError
    assert dict(PUBLIC_CODE) == {
        code.RESOURCE_NOT_IN_SCOPE: error.NOT_FOUND,
        code.RESOURCE_UNCONFIRMED: error.NOT_FOUND,
        code.UNKNOWN_ACTION: error.NOT_FOUND,
        code.NO_GRANT: error.ACTION_NOT_ALLOWED,
        code.DENIED_BY_RULE: error.ACTION_NOT_ALLOWED,
        code.FENCE_VIOLATION: error.ACTION_NOT_ALLOWED,
        code.CAPABILITY_MISSING: error.ACTION_NOT_ALLOWED,
        code.MODE_WOULD_BYPASS_FENCE: error.ACTION_NOT_ALLOWED,
        code.PREVIEW_CONTEXT: error.ACTION_NOT_ALLOWED,
        code.PARAM_SCHEMA: error.INVALID_PARAMETERS,
        code.PARAM_OUT_OF_RANGE: error.INVALID_PARAMETERS,
        code.PARAM_STEP: error.INVALID_PARAMETERS,
        code.SESSION_INACTIVE: error.ACCESS_EXPIRED,
        code.STAY_NOT_STARTED: error.ACCESS_EXPIRED,
        code.STAY_ENDED: error.ACCESS_EXPIRED,
        code.ADAPTER_LOCKED: error.UPSTREAM_UNAVAILABLE,
        code.RESOURCE_NEEDS_REVIEW: error.UPSTREAM_UNAVAILABLE,
        code.TIME_UNTRUSTED: error.UPSTREAM_UNAVAILABLE,
    }
    assert PUBLIC_CODE[code.NO_GRANT] == "ACTION_NOT_ALLOWED"
    assert public_error(code.GRANTED) is None
    assert public_error(code.PREVIEW_CONTEXT) is error.ACTION_NOT_ALLOWED


def test_every_denial_has_a_public_code() -> None:
    for code in DecisionCode:
        if code is not DecisionCode.GRANTED:
            assert public_error(code) in set(PublicError), code
    assert DecisionCode.GRANTED not in PUBLIC_CODE


def test_public_errors_are_the_eight_of_the_spec() -> None:
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
