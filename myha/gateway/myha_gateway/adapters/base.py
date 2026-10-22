"""Types and helpers shared by the device adapters and the policy (design §4.1, §4.2).

Adapters are pure: they turn Home Assistant states and registry entries into product values and
product actions into service calls, without I/O, so ``decide`` can use them directly.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal, Protocol

from myha_gateway.ha.messages import HAState, RegistryEntity


class Action(StrEnum):
    """The closed set of product actions (design §4.1); any other string is unknown."""

    METADATA_READ = "metadata.read"
    STATE_READ = "state.read"
    HISTORY_READ = "history.read"
    SWITCH_ON = "switch.on"
    SWITCH_OFF = "switch.off"
    LIGHT_ON = "light.on"
    LIGHT_OFF = "light.off"
    LIGHT_SET_LEVEL = "light.set_level"
    CLIMATE_SET_TEMPERATURE = "climate.set_temperature"
    CLIMATE_SET_MODE = "climate.set_mode"
    COVER_OPEN = "cover.open"
    COVER_CLOSE = "cover.close"
    COVER_STOP = "cover.stop"
    COVER_SET_POSITION = "cover.set_position"


READ_ACTIONS: frozenset[Action] = frozenset(
    {Action.METADATA_READ, Action.STATE_READ, Action.HISTORY_READ}
)
WRITE_ACTIONS: frozenset[Action] = frozenset(Action) - READ_ACTIONS

Kind = Literal["light", "switch", "climate", "cover", "sensor"]


@dataclass(frozen=True)
class DeviceCapabilities:
    """What a device can do according to Home Assistant, before any admin decision."""

    kind: Kind
    actions: frozenset[Action]  # physically possible write actions; none while blocked
    target_c_min: Decimal | None = None
    target_c_max: Decimal | None = None
    target_c_step: Decimal | None = None
    hvac_modes: frozenset[str] = frozenset()  # raw from the device
    # Admin-confirmed subset of {off, cool, heat}; the binding fills it, capabilities() never does.
    confirmed_modes: frozenset[str] = frozenset()
    # group_entity | template_entity | wrapper_entity (platform) or, for climate devices,
    # unit_not_celsius | invalid_range | dual_setpoint | no_setpoint
    blocked_reason: str | None = None
    fingerprint: str = ""  # changes whenever the abilities change (drift, A14-A16)


@dataclass(frozen=True)
class StateView:
    """The current state of one entity in product terms; ``None`` means not known."""

    availability: Literal["online", "offline", "unknown"]
    adapter_locked: bool
    power: str | None = None
    level_pct: int | None = None
    target_c: Decimal | None = None  # finite whenever set
    current_c: Decimal | None = None  # finite whenever set
    hvac_mode: str | None = None
    position_pct: int | None = None
    cover_state: str | None = None
    value: str | None = None
    unit: str | None = None
    device_class: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class HACall:
    """One explicit Home Assistant service call on one entity."""

    domain: str
    service: str
    entity_id: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Expectation:
    """All set fields must hold; target_c_range is set by the caller (policy check range)."""

    power: str | None = None
    level_pct: int | None = None
    level_tolerance: int = 1
    hvac_mode: str | None = None
    target_c: Decimal | None = None
    target_c_range: tuple[Decimal, Decimal] | None = None
    position_pct: int | None = None
    position_tolerance: int = 1
    cover_state: str | None = None

    def matches(self, view: StateView) -> bool:
        """Whether ``view`` shows every expected value, percentages within their tolerance."""
        return (
            _equal(self.power, view.power)
            and _near(self.level_pct, view.level_pct, self.level_tolerance)
            and _equal(self.hvac_mode, view.hvac_mode)
            and _equal(self.target_c, view.target_c)
            and _near(self.position_pct, view.position_pct, self.position_tolerance)
            and _equal(self.cover_state, view.cover_state)
            and not self.violated_range(view)
        )

    def violated_range(self, view: StateView) -> bool:
        """True if a check range is set and the target temperature is outside it or unknown."""
        if self.target_c_range is None:
            return False
        low, high = self.target_c_range
        return view.target_c is None or not low <= view.target_c <= high


def _equal(expected: object, actual: object) -> bool:
    return expected is None or expected == actual


def _near(expected: int | None, actual: int | None, tolerance: int) -> bool:
    return expected is None or (actual is not None and abs(actual - expected) <= tolerance)


class Adapter(Protocol):
    """Everything specific to one kind of Home Assistant entity."""

    kind: str

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        """What the device can do; ``temperature_unit`` is ``None`` while not known."""
        ...

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        """The product view of ``state``; ``None`` stands for an entity missing in HA."""
        ...

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        """The service call for ``action``; ``params`` as the policy parsed them.

        Raises ``ValueError`` for an action of another kind; the policy refuses those first.
        """
        ...

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        """What the state shows once ``action`` took effect; empty: the call confirms it."""
        ...

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        """Every action a grant must cover for this request (design §4.2, A17)."""
        ...


_BLOCKED_PLATFORMS = {
    "group": "group_entity",
    "template": "template_entity",
    "switch_as_x": "wrapper_entity",
}
# Far beyond any device value; keeps normalize() and the policy's step arithmetic in range.
_MAGNITUDE = Decimal("1e6")


def platform_block(entity: RegistryEntity | None) -> str | None:
    """Groups, templates and switch_as_x wrappers act through other entities: never bound."""
    return None if entity is None else _BLOCKED_PLATFORMS.get(entity.platform)


def _availability(state: HAState) -> Literal["online", "offline", "unknown"]:
    if state.state == "unavailable":
        return "offline"
    return "unknown" if state.state == "unknown" else "online"


def observed(state: HAState | None, adapter_locked: bool) -> StateView:
    """The fields every kind shares; adapters add their own with ``dataclasses.replace``."""
    if state is None:  # the entity is missing in Home Assistant
        return StateView("unknown", adapter_locked)
    return StateView(
        _availability(state),
        adapter_locked,
        device_class=text(state.attributes.get("device_class")),
        observed_at=state.last_updated,
    )


def reading(state: HAState) -> str | None:
    """The state itself, unless it is ``unavailable`` or ``unknown``."""
    return None if state.state in ("unavailable", "unknown") else state.state


def number(value: Any) -> Decimal | None:
    """A finite number of magnitude below a million; via ``str``, so 21.1 stays 21.1."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):  # ValueError: an int too long for str()
        return None
    return parsed if parsed.is_finite() and -_MAGNITUDE < parsed < _MAGNITUDE else None


def percent(value: Any, full_scale: int = 100) -> int | None:
    """``value`` on the scale ``0..full_scale`` in whole percent; ``None`` off the scale."""
    parsed = number(value)
    if parsed is None or not 0 <= parsed <= full_scale:
        return None
    return round(parsed * 100 / full_scale)


def text(value: Any) -> str | None:
    """A string attribute such as ``device_class``; anything else is unknown."""
    return value if isinstance(value, str) else None


def strings(value: Any) -> frozenset[str]:
    """The strings of a list attribute such as ``hvac_modes``; anything else is empty."""
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def features(attributes: Mapping[str, Any]) -> int:
    """The ``supported_features`` bitmask; anything but an int of 0 to 32 bits means none."""
    value = attributes.get("supported_features")
    # Home Assistant's feature flags fit in 32 bits; a larger int is malformed.
    return value if type(value) is int and 0 <= value < 2**32 else 0


def with_fingerprint(
    capabilities: DeviceCapabilities, attributes: Mapping[str, Any]
) -> DeviceCapabilities:
    """``capabilities`` as stored: without actions while blocked, and with the fingerprint.

    The fingerprint hashes everything whose change needs a new admin review.
    """
    if capabilities.blocked_reason is not None:
        capabilities = replace(capabilities, actions=frozenset())
    limits = (capabilities.target_c_min, capabilities.target_c_max, capabilities.target_c_step)
    payload = {
        "kind": capabilities.kind,
        "actions": sorted(capabilities.actions),
        # normalize(): 30 and 30.0 are the same limit and must not look like drift
        "ranges": [None if limit is None else str(limit.normalize()) for limit in limits],
        "hvac_modes": sorted(capabilities.hvac_modes),
        "supported_features": features(attributes),
        "supported_color_modes": sorted(strings(attributes.get("supported_color_modes"))),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return replace(capabilities, fingerprint=hashlib.sha256(canonical.encode()).hexdigest())
