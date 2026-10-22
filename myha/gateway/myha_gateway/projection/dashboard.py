"""The dashboard of one context (design §4.4): every field from an allow-list, decided per call.

Pure except for ``load_layout``. Nothing is cached, so a view projected for one principal can
never be served to another (A21); callers that cache must key on principal and policy version.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.adapters.base import WRITE_ACTIONS, Action, StateView
from myha_gateway.db import models as db
from myha_gateway.policy.context import EffectiveContext
from myha_gateway.policy.decide import decide
from myha_gateway.policy.index import EffectiveCapability, PolicyIndex
from myha_gateway.policy.model import Interval, parameter_name
from myha_gateway.projection.schema import (
    Capability,
    DashboardStatus,
    DashboardView,
    Resource,
    ResourceState,
    Room,
)
from myha_gateway.projection.schema import Interval as PublicInterval

# State fields by kind, besides availability and observed_at; nothing else leaves the gateway.
STATE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "light": ("power", "level_pct"),
        "switch": ("power",),
        "climate": ("current_c", "target_c", "hvac_mode"),
        "cover": ("position_pct", "cover_state"),
        "sensor": ("value", "unit"),
    }
)
_COMMON = ("availability", "observed_at")
_WRITES = tuple(action for action in Action if action in WRITE_ACTIONS)  # declaration order

StateViews = Callable[[str], StateView | None]  # such as PolicyLoader.state_view


@dataclass(frozen=True)
class Card:
    """A bound resource as the dashboard shows it: label and icon from its binding."""

    resource_id: str
    label: str
    icon: str | None


@dataclass(frozen=True)
class UnitLayout:
    id: str
    name: str
    cards: tuple[Card, ...]  # in display order


async def load_layout(session: AsyncSession) -> tuple[UnitLayout, ...]:
    """Units by ``sort``, each with its cards in ``dashboard.layout`` order, else binding order.

    A dashboard layout is ``{"order": [resource_id, ...]}``; ids it does not name follow in
    binding order. Entity ids are never loaded.
    """
    units = (
        await session.execute(select(db.Unit.id, db.Unit.name).order_by(db.Unit.sort, db.Unit.id))
    ).all()
    columns = (db.ResourceBinding.unit_id, db.ResourceBinding.id)
    cards = (
        await session.execute(
            select(*columns, db.ResourceBinding.label, db.ResourceBinding.icon).order_by(
                db.ResourceBinding.id
            )
        )
    ).all()
    ranks = {
        row.unit_id: _rank(row.layout)
        for row in (await session.scalars(select(db.Dashboard))).all()
    }
    result = []
    for unit_id, name in units:
        rank = ranks.get(unit_id, {})
        own = [Card(rid, label, icon) for unit, rid, label, icon in cards if unit == unit_id]
        own.sort(key=lambda card: rank.get(card.resource_id, len(rank)))  # stable
        result.append(UnitLayout(unit_id, name, tuple(own)))
    return tuple(result)


def _rank(layout: Any) -> dict[str, int]:
    """Positions from a layout's ``order``; anything malformed orders nothing."""
    order = layout.get("order") if isinstance(layout, dict) else None
    if not isinstance(order, list):
        return {}
    ids = [rid for rid in order if isinstance(rid, str)]
    return {rid: n for n, rid in reversed(list(enumerate(ids)))}  # the first mention wins


def dashboard_status(ctx: EffectiveContext, now: datetime) -> DashboardStatus:
    """``expired`` once inactive or past the window's end, ``waiting`` before its start."""
    if not ctx.session_active or (ctx.not_after is not None and now >= ctx.not_after):
        return "expired"
    if ctx.not_before is not None and now < ctx.not_before:
        return "waiting"
    return "online"


def project_dashboard(
    ctx: EffectiveContext,
    index: PolicyIndex,
    views: StateViews,
    layout: Sequence[UnitLayout],
    now: datetime,
) -> DashboardView:
    """The rooms of ``layout`` with at least one resource ``ctx`` may see; none unless online.

    Raises what ``decide`` raises, such as ``ValueError`` for another policy version.
    """
    status = dashboard_status(ctx, now)
    if status != "online":
        return DashboardView(status=status, rooms=())
    rooms = []
    for unit in layout:
        found = (
            project_resource(ctx, index, views(card.resource_id), card, now) for card in unit.cards
        )
        shown = tuple(resource for resource in found if resource is not None)
        if shown:
            rooms.append(Room(id=unit.id, name=unit.name, resources=shown))
    return DashboardView(status=status, rooms=tuple(rooms))


def project_resource(
    ctx: EffectiveContext,
    index: PolicyIndex,
    view: StateView | None,
    card: Card,
    now: datetime,
) -> Resource | None:
    """One resource as ``ctx`` may see it; ``None`` when hidden (not in scope, blocked, ...)."""
    resource_id = card.resource_id

    def allowed(action: Action) -> bool:
        # only decide knows the implied metadata.read (design §4.1)
        return decide(ctx, index, resource_id, action, {}, now, view, execute=False).allowed

    ref = index.resource(resource_id)
    if ref is None or not allowed(Action.METADATA_READ):
        return None
    fields: dict[str, Any] = {
        "id": ref.id,
        "kind": ref.kind,
        "label": card.label,
        "icon": card.icon,
    }
    if allowed(Action.STATE_READ):
        fields["state"] = _state(ref.kind, view)
    # decide refuses every write without a view or while locked (ADAPTER_LOCKED): no controls
    writable = view is not None and not view.adapter_locked
    capabilities = {
        action: capability
        for action in (_WRITES if writable else ())
        if (capability := _capability(action, index.effective_capability(ctx, resource_id, action)))
    }
    if capabilities:
        fields["capabilities"] = capabilities
    return Resource.model_validate(fields)


def _state(kind: str, view: StateView | None) -> ResourceState:
    if view is None:  # the entity is unknown to the loader
        return ResourceState(availability="unknown")
    values = {name: getattr(view, name) for name in (*_COMMON, *STATE_FIELDS[kind])}
    return ResourceState.model_validate(
        {name: format(v, "f") if isinstance(v, Decimal) else v for name, v in values.items()}
    )


def _capability(action: Action, effective: EffectiveCapability | None) -> Capability | None:
    """A control only if something is left to use: no interval or no mode means none."""
    if effective is None:
        return None
    name = parameter_name(action)
    if name is None:
        return Capability()
    if name == "mode":
        return Capability(allowed=tuple(sorted(effective.allowed))) if effective.allowed else None
    if not effective.intervals:
        return None
    return Capability(intervals=tuple(_interval(i) for i in effective.intervals))


def _interval(interval: Interval) -> PublicInterval:
    """All four numbers with as many decimals as the finest of them: step 0.5 gives ``22.0``."""
    numbers = (interval.min, interval.max, interval.step, interval.origin)
    places = max(_places(number) for number in numbers)
    text = [f"{number:.{places}f}" for number in numbers]
    return PublicInterval(min=text[0], max=text[1], step=text[2], origin=text[3])


def _places(number: Decimal) -> int:
    exponent = number.normalize().as_tuple().exponent
    assert isinstance(exponent, int)  # rule decimals are finite
    return max(0, -exponent)
