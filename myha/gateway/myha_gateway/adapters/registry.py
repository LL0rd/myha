"""The adapter of each entity kind; one shared instance per kind."""

from myha_gateway.adapters.base import Adapter, Kind
from myha_gateway.adapters.climate import ClimateAdapter
from myha_gateway.adapters.cover import CoverAdapter
from myha_gateway.adapters.light import LightAdapter
from myha_gateway.adapters.sensor import SensorAdapter
from myha_gateway.adapters.switch import SwitchAdapter

_ADAPTERS: dict[Kind, Adapter] = {
    "light": LightAdapter(),
    "switch": SwitchAdapter(),
    "climate": ClimateAdapter(),
    "cover": CoverAdapter(),
    "sensor": SensorAdapter(),
}


_DOMAINS: dict[str, Kind] = {
    "light": "light",
    "switch": "switch",
    "climate": "climate",
    "cover": "cover",
    "sensor": "sensor",
    "binary_sensor": "sensor",  # on/off readings with a device class, read only
}


def detect_kind(entity_id: str) -> Kind | None:
    """The kind of an entity from its domain; ``None`` if no adapter handles the domain."""
    return _DOMAINS.get(entity_id.split(".", 1)[0])


def adapter_for(kind: Kind) -> Adapter:
    return _ADAPTERS[kind]
