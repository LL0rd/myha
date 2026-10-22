"""Policy rule models and request parameter schemas (design §4.1).

Every model is frozen and forbids unknown fields. Rule decimals arrive as plain strings, never as
floats, and step checks run in an exact decimal context.
"""

import re
from collections.abc import Iterable, Mapping
from decimal import Context, Decimal, DivisionByZero, Inexact, InvalidOperation
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    ValidationError,
    model_validator,
)

from myha_gateway.adapters.base import Action

Mode = Literal["off", "cool", "heat"]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
FenceScope = Literal["instance", "unit", "stay", "principal", "session"]
# Product ids such as res_…/unit_…: short ASCII words, so no "*" or control character gets in.
ProductId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

# Six digits on each side of the point keep every step check far inside the precision of _EXACT.
_RULE_DECIMAL = r"^-?[0-9]{1,6}(\.[0-9]{1,6})?$"
# Independent of the caller's decimal context; a result that would need rounding raises.
_EXACT = Context(prec=40, traps=[Inexact, InvalidOperation, DivisionByZero])


def _rule_decimal(value: object) -> object:
    """Plain decimal strings only; a ``Decimal`` is written out first, so no exponent survives."""
    text = format(value, "f") if isinstance(value, Decimal) else value
    if isinstance(text, str) and re.fullmatch(_RULE_DECIMAL, text):
        return text
    raise ValueError("expected a decimal string such as '21.5'")


RuleDecimal = Annotated[
    Decimal,
    BeforeValidator(
        _rule_decimal,
        json_schema_input_type=Annotated[str, StringConstraints(pattern=_RULE_DECIMAL)],
    ),
]


class _Frozen(BaseModel):
    # Subclass instances are checked again: an Interval in a Bound field fails on its step.
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="subclass-instances"
    )


# Request parameters: one schema per action.


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


def parameter_name(action: Action) -> str | None:
    """The one parameter ``action`` takes, such as ``target_c``; ``None`` if it takes none."""
    return next(iter(PARAM_SCHEMA[action].model_fields), None)


class ParamSchemaError(ValueError):
    """Request parameters that do not match the schema of the action (``PARAM_SCHEMA``)."""


def _where(loc: tuple[int | str, ...], fields: Mapping[str, object]) -> str:
    """The schema field an error is about; anything else, such as a client's key, is "params"."""
    return str(loc[0]) if loc and loc[0] in fields else "params"


def parse_params(action: Action, raw: Mapping[str, Any]) -> BaseModel:
    """Validate request parameters against the schema of ``action``."""
    schema = PARAM_SCHEMA[action]
    try:
        return schema.model_validate(raw)
    except ValidationError as exc:
        # Schema field names and reasons only: client keys and values never reach the text.
        reasons = {f"{_where(e['loc'], schema.model_fields)}: {e['msg']}" for e in exc.errors()}
        raise ParamSchemaError("; ".join(sorted(reasons))) from exc


# Rules: grants and denies of a role version, and fences.


class Bound(_Frozen):
    """Closed range ``[min, max]``; ``min == max`` pins a single value."""

    min: RuleDecimal
    max: RuleDecimal

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self

    def contains(self, value: Decimal) -> bool:
        """``min <= value <= max``."""
        return self.min <= value <= self.max


class Interval(Bound):
    """A bound whose allowed values lie on ``origin + k * step``."""

    step: RuleDecimal
    origin: RuleDecimal = Decimal("0")

    @model_validator(mode="after")
    def _positive_step(self) -> Self:
        if self.step <= 0:
            raise ValueError("step must be positive")
        return self

    def on_step(self, value: Decimal) -> bool:
        """Whether ``(value - origin) / step`` is a whole number; nothing is rounded."""
        return _EXACT.remainder(_EXACT.subtract(value, self.origin), self.step).is_zero()


class RangeRule(_Frozen):
    """Allowed numbers: the union of the intervals, each with its own step and origin."""

    intervals: tuple[Interval, ...] = Field(min_length=1)


class EnumRule(_Frozen):
    """Allowed ``mode`` values."""

    # Sorted in JSON: set order follows the hash seed, stored rules must not.
    allowed: Annotated[frozenset[Mode], PlainSerializer(sorted, when_used="json")] = Field(
        min_length=1
    )


class ParameterRules(_Frozen):
    """Grant rules by parameter; stored as ``model_dump(mode="json", exclude_none=True)``."""

    level_pct: RangeRule | None = None
    target_c: RangeRule | None = None
    mode: EnumRule | None = None
    position_pct: RangeRule | None = None


class FenceRules(_Frozen):
    """Fence limits by parameter: bounds only, the step stays the grant's (design §4.1)."""

    level_pct: Bound | None = None
    target_c: Bound | None = None
    mode: EnumRule | None = None
    position_pct: Bound | None = None


def _check_rule_names(rules: BaseModel, actions: Iterable[Action]) -> None:
    """A rule for a parameter that none of ``actions`` takes would restrict nothing."""
    names = {name for name, rule in rules if rule is not None}
    stray = names - {field for action in actions for field in PARAM_SCHEMA[action].model_fields}
    if stray:
        raise ValueError(f"{', '.join(sorted(stray))}: not a parameter of the rule's actions")


class Grant(_Frozen):
    """One complete permission; several grants are a union of whole rules, never merged (A10)."""

    resources: tuple[ProductId, ...] = Field(min_length=1)
    actions: tuple[Action, ...] = Field(min_length=1)
    parameters: ParameterRules = ParameterRules()

    @model_validator(mode="after")
    def _rules_fit_actions(self) -> Self:
        _check_rule_names(self.parameters, self.actions)
        return self


class Deny(_Frozen):
    """Refuses ``actions`` on ``resources`` whatever the grants allow (A12)."""

    resources: tuple[ProductId, ...] = Field(min_length=1)
    actions: tuple[Action, ...] = Field(min_length=1)


class Fence(_Frozen):
    """Binding limit on the parameters of ``action``; it narrows and never allows (A11)."""

    scope_type: FenceScope
    scope_id: ProductId | None = None  # None exactly for the instance scope
    resource_ids: Annotated[tuple[ProductId, ...], Field(min_length=1)] | None = None  # None: all
    action: Action
    parameters: FenceRules

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.scope_type == "instance") != (self.scope_id is None):
            raise ValueError("every scope but instance needs a scope_id; instance has none")
        if all(rule is None for _, rule in self.parameters):
            raise ValueError("a fence without parameter rules would narrow nothing")
        _check_rule_names(self.parameters, (self.action,))
        return self


class RoleRules(_Frozen):
    """Grants and denies of one role version."""

    grants: tuple[Grant, ...] = ()
    denies: tuple[Deny, ...] = ()
