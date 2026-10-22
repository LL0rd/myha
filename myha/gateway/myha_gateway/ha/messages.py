"""Home Assistant WebSocket payloads as typed values, parsed at the trust boundary.

The parsers raise one of ``MALFORMED`` for shapes Core never sends. List parsers skip and log a
malformed entry instead, so one odd entity cannot take the whole snapshot down.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

MALFORMED = (AttributeError, KeyError, TypeError, ValueError)


@dataclass(frozen=True)
class HAState:
    entity_id: str
    state: str
    attributes: Mapping[str, Any]
    last_changed: datetime
    last_updated: datetime
    context_id: str | None


@dataclass(frozen=True)
class StateChanged:
    entity_id: str
    old_state: HAState | None  # None: the entity is new
    new_state: HAState | None  # None: the entity was removed
    time_fired: datetime
    context_id: str | None


@dataclass(frozen=True)
class ConnectionStatus:
    connected: bool
    generation: int
    reason: str | None  # why it is not connected, e.g. "auth_invalid"


@dataclass(frozen=True)
class ServiceResult:
    context_id: str | None  # None only if Core leaves out the context of the call


@dataclass(frozen=True)
class HAUser:
    id: str
    name: str | None
    username: str | None  # None for system users
    is_owner: bool
    is_active: bool
    system_generated: bool
    group_ids: tuple[str, ...]


@dataclass(frozen=True)
class RegistryEntity:
    entity_id: str
    unique_id: str
    device_id: str | None
    area_id: str | None  # None: the entity takes its device's area
    platform: str
    disabled: bool


@dataclass(frozen=True)
class RegistryDevice:
    id: str
    area_id: str | None
    name: str | None


@dataclass(frozen=True)
class RegistryArea:
    id: str
    name: str


@dataclass(frozen=True)
class Registry:
    entities: Mapping[str, RegistryEntity]  # by entity_id
    devices: Mapping[str, RegistryDevice]  # by id
    areas: Mapping[str, RegistryArea]  # by id

    def effective_area(self, entity_id: str) -> str | None:
        """The entity's own area, else its device's area, as Home Assistant shows it."""
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        if entity.area_id is not None or entity.device_id is None:
            return entity.area_id
        device = self.devices.get(entity.device_id)
        return None if device is None else device.area_id


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {type(value).__name__}")
    return value


def _opt_str(value: Any) -> str | None:
    return None if value is None else _str(value)


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"expected a boolean, got {type(value).__name__}")
    return value


def _each[T](items: Any, parse: Callable[[Any], T], what: str) -> list[T]:
    """``parse`` applied to every entry of the list ``items``; malformed entries are skipped."""
    if not isinstance(items, list):
        raise TypeError(f"expected a list of {what}s, got {type(items).__name__}")
    parsed: list[T] = []
    for item in items:
        try:
            parsed.append(parse(item))
        except MALFORMED as exc:
            logger.warning("ignoring a malformed %s from Home Assistant: %r", what, exc)
    return parsed


def parse_time(value: Any) -> datetime:
    """ISO 8601 with offset, as Core sends every timestamp."""
    parsed = datetime.fromisoformat(_str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value}")
    return parsed


def context_id(context: Any) -> str | None:
    """``context.id``; only used to match events to calls, so a missing one is no error."""
    value = context.get("id") if isinstance(context, dict) else None
    return value if isinstance(value, str) else None


def parse_state(data: Any) -> HAState:
    """A state object from ``get_states`` or a ``state_changed`` event."""
    attributes = data["attributes"]
    if not isinstance(attributes, dict):
        raise TypeError("attributes is not an object")
    return HAState(
        _str(data["entity_id"]),
        _str(data["state"]),
        attributes,
        parse_time(data["last_changed"]),
        parse_time(data["last_updated"]),
        context_id(data.get("context")),
    )


def parse_states(result: Any) -> dict[str, HAState]:
    return {state.entity_id: state for state in _each(result, parse_state, "state")}


def parse_state_changed(event: Any) -> StateChanged:
    data = event["data"]
    old, new = data["old_state"], data["new_state"]
    return StateChanged(
        _str(data["entity_id"]),
        None if old is None else parse_state(old),
        None if new is None else parse_state(new),
        parse_time(event["time_fired"]),
        context_id(event.get("context")),
    )


def _entity(data: Any) -> RegistryEntity:
    return RegistryEntity(
        _str(data["entity_id"]),
        _str(data["unique_id"]),
        _opt_str(data.get("device_id")),
        _opt_str(data.get("area_id")),
        _str(data["platform"]),
        data.get("disabled_by") is not None,
    )


def _device(data: Any) -> RegistryDevice:
    name = _opt_str(data.get("name_by_user")) or _opt_str(data.get("name"))
    return RegistryDevice(_str(data["id"]), _opt_str(data.get("area_id")), name)


def _area(data: Any) -> RegistryArea:
    return RegistryArea(_str(data["area_id"]), _str(data["name"]))


def parse_registry(entities: Any, devices: Any, areas: Any) -> Registry:
    """The three ``config/*_registry/list`` results."""
    return Registry(
        {entity.entity_id: entity for entity in _each(entities, _entity, "registry entry")},
        {device.id: device for device in _each(devices, _device, "device")},
        {area.id: area for area in _each(areas, _area, "area")},
    )


def parse_temperature_unit(config: Any) -> str:
    """``unit_system.temperature`` of the ``get_config`` result."""
    return _str(config["unit_system"]["temperature"])


def parse_user(data: Any) -> HAUser:
    """A ``config/auth/list`` entry; every field the role check needs is required."""
    group_ids = data["group_ids"]
    if not isinstance(group_ids, list):
        raise TypeError("group_ids is not a list")
    return HAUser(
        _str(data["id"]),
        _opt_str(data.get("name")),
        _opt_str(data.get("username")),
        _bool(data["is_owner"]),
        _bool(data["is_active"]),
        _bool(data["system_generated"]),
        tuple(_str(group_id) for group_id in group_ids),
    )
