"""The immutable index of one policy version and the effective capability (design §4.1)."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal
from types import MappingProxyType
from typing import Literal, get_args

from myha_gateway.adapters.base import Action, DeviceCapabilities
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.model import PARAM_SCHEMA, Bound, Fence, Grant, Interval, Mode, RoleRules

_MODES: frozenset[str] = frozenset(get_args(Mode))
_PERCENT = Interval(min=Decimal("0"), max=Decimal("100"), step=Decimal("1"))
_TARGET_C_MIN, _TARGET_C_MAX = Decimal("-99.9"), Decimal("99.9")  # all TargetCParams accepts
_HALF, _CENT = Decimal("0.5"), Decimal("0.01")
_FAR = Decimal("1000000")  # rule decimals have at most six integer digits
_ROUNDING = Context(prec=28)  # independent of the caller's decimal context


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
        """The rules of each binding; a role version the index does not know grants nothing."""
        found = [
            (binding, self._role_versions.get((binding.role_template_id, binding.role_version)))
            for binding in ctx.role_bindings
        ]
        return [(binding, rules) for binding, rules in found if rules is not None]

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
        capabilities = resource.capabilities
        limits = _device_limits(name, capabilities)
        if limits is None:  # a device limit beyond the rule format: offer nothing (fail closed)
            return EffectiveCapability(matched=matched)
        low, high = limits
        bounds: list[Bound] = [b for fence in fences if (b := getattr(fence.parameters, name))]
        intervals: dict[Interval, None] = {}  # ordered set: identical intervals appear once
        for grant in grants.values():
            rule = getattr(grant.parameters, name)
            for interval in (_full_range(name, capabilities),) if rule is None else rule.intervals:
                clipped = _within(interval, low, high)
                for bound in bounds:
                    clipped = None if clipped is None else clipped.clip(bound)
                if clipped is not None:
                    intervals[clipped] = None
        return EffectiveCapability(intervals=tuple(intervals), matched=matched)


def _full_range(name: str, capabilities: DeviceCapabilities) -> Interval:
    """What a grant without a rule for ``name`` allows: the schema range in device steps."""
    if name != "target_c":
        return _PERCENT
    step = capabilities.target_c_step or _HALF  # adapters keep it to 0.01..10 in hundredths
    return Interval(min=_TARGET_C_MIN, max=_TARGET_C_MAX, step=step)


def _device_limits(
    name: str, capabilities: DeviceCapabilities
) -> tuple[Decimal | None, Decimal | None] | None:
    """The device range rounded inwards to cents, ``None`` for a limit the device does not name.

    Raw device numbers need not fit a rule decimal: they are compared, and a limit becomes an
    interval bound only inside a grant, whose values do fit. A limit of 1e6 or more fits no
    rule decimal at all, so the whole result is ``None``.
    """
    if name != "target_c":
        return _PERCENT.min, _PERCENT.max
    low, high = capabilities.target_c_min, capabilities.target_c_max
    if any(limit is not None and not -_FAR < limit < _FAR for limit in (low, high)):
        return None
    return _cents(low, ROUND_CEILING), _cents(high, ROUND_FLOOR)


def _cents(value: Decimal | None, rounding: str) -> Decimal | None:
    if value is None:
        return None
    # normalize: 8.000000000 is 8 again and fits a rule decimal once inside a grant
    return value.quantize(_CENT, rounding=rounding, context=_ROUNDING).normalize(_ROUNDING)


def _within(interval: Interval, low: Decimal | None, high: Decimal | None) -> Interval | None:
    """``interval`` cut to the device limits; the result stays inside the grant's values."""
    low = interval.min if low is None else max(interval.min, low)
    high = interval.max if high is None else min(interval.max, high)
    if low > high:
        return None
    return Interval(min=low, max=high, step=interval.step, origin=interval.origin)
