"""Policy rule models and request parameter schemas (design §4.1).

Every model is frozen and forbids unknown fields. Decimals arrive as plain strings, never as
floats, so range and step checks stay exact.
"""

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from myha_gateway.adapters.base import Action

# At most six digits on each side of the point: with request values of at most three digits,
# ``(value - origin) % step`` then stays well inside the 28 digits of the default decimal context.
_RULE_DECIMAL = re.compile(r"-?[0-9]{1,6}(\.[0-9]{1,6})?")


def _rule_decimal(value: object) -> object:
    """Let only plain decimal strings and ``Decimal`` objects through to the decimal parser."""
    if isinstance(value, Decimal) or (isinstance(value, str) and _RULE_DECIMAL.fullmatch(value)):
        return value
    raise ValueError("expected a decimal string such as '21.5'")


RuleDecimal = Annotated[Decimal, BeforeValidator(_rule_decimal, json_schema_input_type=str)]
# Product ids such as res_…/unit_…; a "*" is rejected so that no rule can act as a wildcard.
ProductId = Annotated[str, Field(pattern=r"^[^\s*]+$")]
Mode = Literal["off", "cool", "heat"]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
FenceScope = Literal["instance", "unit", "stay", "principal", "session"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Interval(_Frozen):
    """Closed range ``[min, max]`` whose allowed values lie on ``origin + k * step``."""

    min: RuleDecimal
    max: RuleDecimal
    step: RuleDecimal
    origin: RuleDecimal = Decimal("0")

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        if self.step <= 0:
            raise ValueError("step must be positive")
        return self

    def contains(self, value: Decimal) -> bool:
        """``min <= value <= max``."""
        return self.min <= value <= self.max

    def on_step(self, value: Decimal) -> bool:
        """Whether ``(value - origin) / step`` is a whole number; nothing is rounded."""
        return (value - self.origin) % self.step == 0


class RangeRule(_Frozen):
    """Allowed numbers: the union of the intervals, each with its own step and origin."""

    intervals: tuple[Interval, ...] = Field(min_length=1)


class EnumRule(_Frozen):
    """Allowed ``mode`` values."""

    allowed: frozenset[Mode] = Field(min_length=1)


_RULE_KIND: Mapping[str, type[RangeRule | EnumRule]] = {
    "level_pct": RangeRule,
    "target_c": RangeRule,
    "mode": EnumRule,
    "position_pct": RangeRule,
}


def _known_parameters(rules: dict[str, RangeRule | EnumRule]) -> dict[str, RangeRule | EnumRule]:
    for name, rule in rules.items():
        kind = _RULE_KIND.get(name)
        if kind is None:
            raise ValueError(f"unknown parameter {name!r}")
        if not isinstance(rule, kind):
            raise ValueError(f"{name!r} needs {kind.__name__}")
    return rules


ParameterRules = Annotated[dict[str, RangeRule | EnumRule], AfterValidator(_known_parameters)]


class Grant(_Frozen):
    """One complete permission; several grants are a union of whole rules, never merged (A10)."""

    resources: tuple[ProductId, ...] = Field(min_length=1)
    actions: tuple[Action, ...] = Field(min_length=1)
    parameters: ParameterRules = {}


class Deny(_Frozen):
    """Refuses ``actions`` on ``resources`` whatever the grants allow (A12)."""

    resources: tuple[ProductId, ...] = Field(min_length=1)
    actions: tuple[Action, ...] = Field(min_length=1)


class Fence(_Frozen):
    """Binding limit on the parameters of ``action``; it narrows and never allows (A11)."""

    scope_type: FenceScope
    scope_id: ProductId | None = None
    resource_ids: Annotated[tuple[ProductId, ...], Field(min_length=1)] | None = None  # None: all
    action: Action
    parameters: ParameterRules = Field(min_length=1)  # without rules a fence would narrow nothing

    @model_validator(mode="after")
    def _scoped(self) -> Self:
        if self.scope_type != "instance" and self.scope_id is None:
            raise ValueError(f"a {self.scope_type} fence needs a scope_id")
        return self


class RoleRules(_Frozen):
    """Grants and denies of one role version."""

    grants: tuple[Grant, ...] = ()
    denies: tuple[Deny, ...] = ()


class LevelParams(_Frozen):
    level_pct: Percent


class TargetCParams(_Frozen):
    """Target temperature as a string: up to two digits and one decimal, no exponent."""

    # [0-9], not \d: the regex engine would also accept other Unicode digits.
    target_c: Annotated[str, Field(strict=True, pattern=r"^-?[0-9]{1,2}(\.[0-9])?$")]

    @property
    def value(self) -> Decimal:
        return Decimal(self.target_c)


class ModeParams(_Frozen):
    mode: Mode


class PositionParams(_Frozen):
    position_pct: Percent


class NoParams(_Frozen):
    """Actions without parameters accept only an empty object."""


PARAM_SCHEMA: Mapping[Action, type[BaseModel]] = {action: NoParams for action in Action} | {
    Action.LIGHT_SET_LEVEL: LevelParams,
    Action.CLIMATE_SET_TEMPERATURE: TargetCParams,
    Action.CLIMATE_SET_MODE: ModeParams,
    Action.COVER_SET_POSITION: PositionParams,
}


class ParamSchemaError(ValueError):
    """Request parameters that do not match the schema of the action (``PARAM_SCHEMA``)."""


def parse_params(action: Action, raw: Mapping[str, Any]) -> BaseModel:
    """Validate request parameters against the schema of ``action``."""
    try:
        return PARAM_SCHEMA[action].model_validate(raw)
    except ValidationError as exc:
        # Locations and reasons only: the values come from the client and stay out of messages.
        reasons = (f"{'.'.join(map(str, e['loc'])) or 'params'}: {e['msg']}" for e in exc.errors())
        raise ParamSchemaError("; ".join(reasons)) from exc
