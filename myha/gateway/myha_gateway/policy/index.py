"""The immutable index of one policy version and the effective capability (design §4.1)."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal
from types import MappingProxyType
from typing import Literal, get_args

from myha_gateway.adapters.base import Action, DeviceCapabilities
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.model import PARAM_SCHEMA, Bound, Fence, Grant, Interval, Mode, RoleRules

_MODES: frozenset[str] = frozenset(get_args(Mode))
# What the request schemas accept per number (model.py): min, max and a step from 0.
_SCHEMA: Mapping[str, tuple[Decimal, Decimal, Decimal]] = {
    "level_pct": (Decimal(0), Decimal(100), Decimal(1)),
    "position_pct": (Decimal(0), Decimal(100), Decimal(1)),
    "target_c": (Decimal("-99.9"), Decimal("99.9"), Decimal("0.1")),
}
_HALF, _CENT = Decimal("0.5"), Decimal("0.01")
_FAR = 1_000_000  # rule decimals have at most six integer digits ...
_MICRO = 1_000_000  # ... and six decimals, so each is a whole number of millionths
_CONTEXT = Context(prec=28)  # independent of the caller's decimal context
_NOTHING = (Decimal(1), Decimal(0))  # an empty check range: low > high, no target passes


@dataclass(frozen=True)
class ResourceRef:
    id: str
    unit_id: str
    kind: str
    status: Literal["confirmed", "needs_review", "blocked"]
    capabilities: DeviceCapabilities


@dataclass(frozen=True)
class EffectiveCapability:
    """What a context may do with one action on one resource, for projection and UI.

    ``intervals`` keeps the intervals of every grant apart (A10); ``matched`` names each grant
    that covers the resource and the action as ``<template>@<version>:<grant index>``.
    """

    intervals: tuple[Interval, ...] = ()
    allowed: frozenset[str] = frozenset()
    matched: tuple[str, ...] = ()


class PolicyIndex:
    """Rules, resources and units of one policy version; built once and never changed."""

    def __init__(
        self,
        role_versions: Mapping[tuple[str, int], RoleRules],
        resources: Mapping[str, ResourceRef],
        units: Mapping[str, frozenset[str]],  # unit_id -> resource ids
    ) -> None:
        self._role_versions = MappingProxyType(dict(role_versions))
        self._resources = MappingProxyType(dict(resources))
        self.units: Mapping[str, frozenset[str]] = MappingProxyType(dict(units))

    def rules_for(self, ctx: EffectiveContext) -> list[tuple[RoleBinding, RoleRules]]:
        """The rules of each binding.

        A role version missing from the index is a loader bug: skipping it would drop its
        denies, so it raises instead.
        """
        found = []
        for binding in ctx.role_bindings:
            rules = self._role_versions.get((binding.role_template_id, binding.role_version))
            if rules is None:
                name = f"{binding.role_template_id}@{binding.role_version}"
                raise ValueError(f"unknown role version {name}")
            found.append((binding, rules))
        return found

    def resource(self, resource_id: str) -> ResourceRef | None:
        return self._resources.get(resource_id)

    def in_scope(self, ctx: EffectiveContext, resource_id: str) -> bool:
        """Whether the resource's unit belongs to a binding; its status is the decision's."""
        resource = self.resource(resource_id)
        return resource is not None and any(
            resource.unit_id in binding.unit_ids for binding in ctx.role_bindings
        )

    def denied(self, ctx: EffectiveContext, resource_id: str, action: Action) -> bool:
        """Whether any deny of the context names resource and action (A12).

        A deny names its resources itself, so it counts whatever units its binding has.
        """
        return any(
            action in deny.actions and resource_id in deny.resources
            for _, rules in self.rules_for(ctx)
            for deny in rules.denies
        )

    def grants(self, ctx: EffectiveContext, resource_id: str, action: Action) -> dict[str, Grant]:
        """The grants naming resource and action, inside the units of their binding, by id."""
        resource = self.resource(resource_id)
        if resource is None:
            return {}
        return {
            f"{binding.role_template_id}@{binding.role_version}:{index}": grant
            for binding, rules in self.rules_for(ctx)
            if resource.unit_id in binding.unit_ids
            for index, grant in enumerate(rules.grants)
            if action in grant.actions and resource_id in grant.resources
        }

    def fences(self, ctx: EffectiveContext, resource_id: str, action: Action) -> list[Fence]:
        """The context's fences on this action that reach this resource."""
        resource = self.resource(resource_id)
        if resource is None:
            return []
        return [
            fence
            for fence in ctx.fences
            if fence.action == action
            and (fence.resource_ids is None or resource_id in fence.resource_ids)
            and (fence.scope_type != "unit" or fence.scope_id == resource.unit_id)
        ]

    def effective_capability(
        self, ctx: EffectiveContext, resource_id: str, action: Action
    ) -> EffectiveCapability | None:
        """Grants cut by fences and the device; ``None`` if no grant covers it or a deny does."""
        resource = self.resource(resource_id)
        grants = self.grants(ctx, resource_id, action)
        if resource is None or not grants or self.denied(ctx, resource_id, action):
            return None
        matched = tuple(grants)
        fences = self.fences(ctx, resource_id, action)
        name = next(iter(PARAM_SCHEMA[action].model_fields), None)
        if name is None:
            return EffectiveCapability(matched=matched)
        if name == "mode":
            allowed = frozenset[str]().union(
                *(
                    _MODES if g.parameters.mode is None else g.parameters.mode.allowed
                    for g in grants.values()
                )
            )
            for fence in fences:
                if fence.parameters.mode is not None:
                    allowed &= fence.parameters.mode.allowed
            confirmed = resource.capabilities.confirmed_modes
            return EffectiveCapability(allowed=allowed & confirmed, matched=matched)
        bounds: list[Bound] = [b for fence in fences if (b := getattr(fence.parameters, name))]
        intervals: dict[Interval, None] = {}  # ordered set: identical intervals appear once
        for cut in self.grant_intervals(ctx, resource_id, action).values():
            for interval in cut:
                fenced: Interval | None = interval
                for bound in bounds:  # a fence narrows the range, the grid stays the grant's
                    fenced = None if fenced is None else _on_grid(fenced, (), bound.min, bound.max)
                if fenced is not None:
                    intervals[fenced] = None
        return EffectiveCapability(intervals=tuple(intervals), matched=matched)

    def grant_intervals(
        self, ctx: EffectiveContext, resource_id: str, action: Action
    ) -> dict[str, tuple[Interval, ...]]:
        """Each covering grant's settable intervals for the action's number, before fences.

        An interval keeps the grant's values (a grant without a rule: all the request schema
        accepts) that lie within the device limits and on the grant's grid, the device step and
        the schema's step at once: the step is their least common multiple and both bounds sit
        on it. A grant left without an interval maps to an empty tuple: granted, but nothing is
        settable, as for every grant while a device limit lies beyond the rule format. Denies
        are not applied. Raises ``ValueError`` for an action without a number.
        """
        name = next(iter(PARAM_SCHEMA[action].model_fields), None)
        if name is None or name == "mode":
            raise ValueError(f"{action} takes no number")
        resource = self.resource(resource_id)
        if resource is None:
            return {}
        capabilities = resource.capabilities
        limits = _limits(name, capabilities)
        steps = _grid_steps(name, capabilities)
        result: dict[str, tuple[Interval, ...]] = {}
        for grant_id, grant in self.grants(ctx, resource_id, action).items():
            rule = getattr(grant.parameters, name)
            spans = (_full_range(name, capabilities),) if rule is None else rule.intervals
            cuts = [] if limits is None else [_on_grid(span, steps, *limits) for span in spans]
            result[grant_id] = tuple(cut for cut in cuts if cut is not None)
        return result

    def check_range(
        self, ctx: EffectiveContext, resource_id: str
    ) -> tuple[Decimal, Decimal] | None:
        """The range the reported target must lie in for ``climate.set_mode`` cool or heat (E7).

        With ``climate.set_temperature`` granted: the hull of its effective intervals. Without:
        the applicable set_temperature fences cut to the device range. Neither: ``None``, no
        check. ``low > high`` means that no target passes (fail closed).
        """
        action = Action.CLIMATE_SET_TEMPERATURE
        capability = self.effective_capability(ctx, resource_id, action)
        if capability is not None:
            if not capability.intervals:
                return _NOTHING
            return min(i.min for i in capability.intervals), max(
                i.max for i in capability.intervals
            )
        resource = self.resource(resource_id)
        fences = self.fences(ctx, resource_id, action)
        bounds = [b for fence in fences if (b := fence.parameters.target_c) is not None]
        if resource is None or not bounds:
            return None
        limits = _limits("target_c", resource.capabilities)
        if limits is None:
            return _NOTHING
        return max(limits[0], *(b.min for b in bounds)), min(limits[1], *(b.max for b in bounds))


def _full_range(name: str, capabilities: DeviceCapabilities) -> Interval:
    """A grant without a rule for ``name``: all the request schema accepts, before the grid."""
    low, high, step = _SCHEMA[name]
    if name == "target_c" and capabilities.target_c_step is None:
        step = _HALF  # the Home Assistant frontend's step for a device that names none
    return Interval(min=low, max=high, step=step)


def _grid_steps(name: str, capabilities: DeviceCapabilities) -> tuple[Decimal, ...]:
    """The steps from 0 every settable value lies on: the request schema's and the device's."""
    schema_step = _SCHEMA[name][2]
    device_step = capabilities.target_c_step if name == "target_c" else None
    return (schema_step,) if device_step is None else (schema_step, device_step)


def _limits(name: str, capabilities: DeviceCapabilities) -> tuple[Decimal, Decimal] | None:
    """The range the request schema and the device accept, device limits rounded inwards.

    Raw device numbers are only compared, never made rule decimals; a limit of 1e6 or more fits
    no rule decimal at all, so the result is ``None`` and nothing is offered (fail closed).
    """
    low, high, _ = _SCHEMA[name]
    if name != "target_c":
        return low, high
    device_low, device_high = capabilities.target_c_min, capabilities.target_c_max
    if any(limit is not None and not -_FAR < limit < _FAR for limit in (device_low, device_high)):
        return None
    if device_low is not None:
        low = max(low, device_low.quantize(_CENT, ROUND_CEILING, _CONTEXT))
    if device_high is not None:
        high = min(high, device_high.quantize(_CENT, ROUND_FLOOR, _CONTEXT))
    return low, high


def _on_grid(
    interval: Interval, steps: Iterable[Decimal], low: Decimal, high: Decimal
) -> Interval | None:
    """The values of ``interval`` within ``[low, high]`` that also lie on each step from 0.

    Grids meet on the least common multiple of their steps, from the origin the Chinese
    remainder theorem gives, or nowhere. The bounds move inwards onto the result. Exact in
    whole millionths, where every quotient is a whole number; ``None`` if no value is left.
    """
    step, origin = _micro(interval.step), _micro(interval.origin)
    for other in map(_micro, steps):
        common = math.gcd(step, other)
        if origin % common:
            return None  # no value lies on both grids
        modulus = other // common
        origin += step * (-(origin // common) * pow(step // common, -1, modulus) % modulus)
        step = step // common * other
        origin %= step
    if step >= _FAR * _MICRO:  # a step beyond the rule format: offer nothing (fail closed)
        return None
    first = -((origin - _micro(max(interval.min, low))) // step)  # rounded up
    last = (_micro(min(interval.max, high)) - origin) // step  # rounded down
    if first > last:
        return None
    return Interval(
        min=_decimal(origin + first * step),
        max=_decimal(origin + last * step),
        step=_decimal(step),
        origin=_decimal(origin),
    )


def _micro(value: Decimal) -> int:
    return int(value.scaleb(6, _CONTEXT))


def _decimal(micro: int) -> Decimal:
    return Decimal(micro).scaleb(-6, _CONTEXT).normalize(_CONTEXT)
