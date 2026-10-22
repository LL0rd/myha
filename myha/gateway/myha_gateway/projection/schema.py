"""The public dashboard view (design §4.4): allow-listed fields only, nothing from Home Assistant.

Every model forbids unknown fields. Serialise with ``exclude_unset=True``: ``state`` and
``capabilities`` are absent where the context may not see them, and a state carries only the
fields of its kind.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from myha_gateway.adapters.base import Action, Kind

DashboardStatus = Literal["online", "waiting", "expired"]


class _Public(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceState(_Public):
    """Every state field any kind may show; the projection sets those of the resource's kind."""

    availability: Literal["online", "offline", "unknown"]
    observed_at: datetime | None = None
    power: str | None = None
    level_pct: int | None = None
    current_c: str | None = None  # decimal string
    target_c: str | None = None  # decimal string
    hvac_mode: str | None = None
    position_pct: int | None = None
    cover_state: str | None = None
    value: str | None = None
    unit: str | None = None


class Interval(_Public):
    """Settable values ``origin + k * step`` within ``[min, max]``, as decimal strings."""

    min: str
    max: str
    step: str
    origin: str


class Capability(_Public):
    """``intervals`` for a number, ``allowed`` for a mode, neither for a plain action."""

    intervals: tuple[Interval, ...] | None = None
    allowed: tuple[str, ...] | None = None


class Resource(_Public):
    id: str
    kind: Kind
    label: str
    icon: str | None = None
    state: ResourceState | None = None  # absent without state.read
    capabilities: dict[Action, Capability] | None = None  # only the writes the context may use


class Room(_Public):
    id: str
    name: str
    resources: tuple[Resource, ...]


class DashboardView(_Public):
    status: DashboardStatus
    rooms: tuple[Room, ...]


def dump_public(model: BaseModel) -> dict[str, Any]:
    """The JSON form a client receives, over HTTP and WebSocket alike: unset fields stay absent."""
    return model.model_dump(mode="json", exclude_unset=True)
