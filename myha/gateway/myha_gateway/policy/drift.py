"""Drift between a binding and Home Assistant (design §4.1 "Drift-Erkennung", §4.2, A14-A16).

A confirmed binding whose device changed under it becomes ``needs_review``: abilities (the
fingerprint), ``unique_id``, effective area or existence. A pure rename, same ``unique_id``
under a new entity id of the same domain, moves the binding along. ``blocked`` never falls back
to ``needs_review``, which would allow reads of an entity that must stay hidden.

A binding without ``unique_id`` has no registry entry to follow: it counts as missing only
when a ``state_changed`` removes its state (``gone``), never because a check finds no state,
since Core restores states late while it starts.
"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.db import bump_policy_version
from myha_gateway.db.models import Instance, ResourceBinding
from myha_gateway.ha.client import CELSIUS, HAClient
from myha_gateway.ha.messages import HAState, Registry

# (session, operation, resource_id, policy_version, params); joins the caller's transaction.
Audit = Callable[[AsyncSession, str, str | None, int, dict[str, Any]], Awaitable[None]]


def capabilities_changed(binding: ResourceBinding, state: HAState, ha: HAClient) -> bool:
    """Whether ``state`` shows other abilities than the admin confirmed.

    Only under °C: an unknown or other unit is no change of the device, and the adapter lock
    keeps writes off meanwhile. Never for an unavailable device either: Core then reports only
    part of the attributes.
    """
    kind, unit = detect_kind(binding.entity_id), ha.temperature_unit
    if kind is None or unit != CELSIUS or state.state == "unavailable":
        return False
    entry = ha.registry().entities.get(binding.entity_id)
    found = adapter_for(kind).capabilities(state, entry, unit).fingerprint
    return found != binding.capability_fingerprint


def drift_reason(binding: ResourceBinding, ha: HAClient, gone: Collection[str] = ()) -> str | None:
    """Why the binding no longer matches Home Assistant; ``None`` if it still does.

    ``gone`` names the bindings whose state an event removed.
    """
    entity_id, registry = binding.entity_id, ha.registry()
    entry, state = registry.entities.get(entity_id), ha.state(entity_id)
    if binding.unique_id is None:  # bound without a registry entry: see the module docstring
        if binding.id in gone and state is None:
            return "missing"
    elif entry is None:
        return "missing"
    elif entry.unique_id != binding.unique_id:
        return "unique_id"
    elif registry.effective_area(entity_id) != binding.area_id:
        return "area"
    if state is not None and capabilities_changed(binding, state, ha):
        return "capabilities"
    return None


def renamed_to(binding: ResourceBinding, registry: Registry) -> str | None:
    """The new entity id of a binding whose entity id left the registry but not its entry."""
    if binding.unique_id is None or binding.entity_id in registry.entities:
        return None
    domain = binding.entity_id.split(".", 1)[0]
    return next(
        (
            entry.entity_id
            for entry in registry.entities.values()
            if entry.unique_id == binding.unique_id and entry.entity_id.split(".", 1)[0] == domain
        ),
        None,
    )


async def reconcile(
    session: AsyncSession, ha: HAClient, audit: Audit, gone: Collection[str] = ()
) -> tuple[Sequence[ResourceBinding], int]:
    """Follow renames and mark drifted bindings in the caller's write session.

    Any change raises ``policy_version`` once and is audited with it. Returns every binding,
    as stored now, and the policy version they belong to.
    """
    bindings = (await session.scalars(select(ResourceBinding).order_by(ResourceBinding.id))).all()
    taken = {binding.entity_id for binding in bindings}
    changes: list[tuple[str, str, dict[str, Any]]] = []
    for binding in bindings:
        before = len(changes)
        new_id = renamed_to(binding, ha.registry())
        if new_id is not None and new_id not in taken:  # else it counts as missing below
            changes.append(
                ("binding.renamed", binding.id, {"from": binding.entity_id, "to": new_id})
            )
            taken.add(new_id)
            binding.entity_id = new_id
        reason = drift_reason(binding, ha, gone) if binding.status == "confirmed" else None
        if reason is not None:
            binding.status = "needs_review"
            changes.append(("binding.needs_review", binding.id, {"reason": reason}))
        if len(changes) > before:
            binding.version += 1  # an admin edit based on the old row must conflict
    if not changes:
        return bindings, (await session.execute(select(Instance.policy_version))).scalar_one()
    version = await bump_policy_version(session)
    for operation, resource_id, params in changes:
        await audit(session, operation, resource_id, version, params)
    await audit(session, "policy.changed", None, version, {})
    return bindings, version
