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


def detect_kind(entity_id: str) -> Kind | None:
    """The kind of an entity from its domain; ``None`` if no adapter handles the domain."""
    domain = entity_id.split(".", 1)[0]
    return next((kind for kind in _ADAPTERS if kind == domain), None)


def adapter_for(kind: Kind) -> Adapter:
    return _ADAPTERS[kind]
