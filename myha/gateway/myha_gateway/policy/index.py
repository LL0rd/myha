"""The immutable index of one policy version and the effective capability (design §4.1)."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import ValidationError

from myha_gateway.adapters.base import Action, DeviceCapabilities
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.model import PARAM_SCHEMA, Bound, Fence, Grant, Interval, Mode, RoleRules

_MODES: frozenset[str] = frozenset(get_args(Mode))
_PERCENT = Interval(min=Decimal("0"), max=Decimal("100"), step=Decimal("1"))


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
        device = _device_range(name, resource.capabilities)
        if device is None:
            return EffectiveCapability(matched=matched)
        bounds: list[Bound] = [device]
        bounds += [bound for fence in fences if (bound := getattr(fence.parameters, name))]
        intervals: dict[Interval, None] = {}  # ordered set: identical intervals appear once
        for grant in grants.values():
            rule = getattr(grant.parameters, name)
            for interval in (device,) if rule is None else rule.intervals:
                clipped: Interval | None = interval
                for bound in bounds:
                    clipped = None if clipped is None else clipped.clip(bound)
                if clipped is not None:
                    intervals[clipped] = None
        return EffectiveCapability(intervals=tuple(intervals), matched=matched)


def _device_range(name: str, capabilities: DeviceCapabilities) -> Interval | None:
    """The range and step of the device; ``None`` while it reports no usable range."""
    if name != "target_c":
        return _PERCENT
    try:
        return Interval.model_validate(
            {
                "min": capabilities.target_c_min,
                "max": capabilities.target_c_max,
                "step": capabilities.target_c_step,
            }
        )
    except ValidationError:  # missing or unusable: offer nothing rather than guess
        return None
