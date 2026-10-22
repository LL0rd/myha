"""Switches: on and off."""

from collections.abc import Mapping
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    availability,
    platform_block,
    reading,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity

_POWER = {Action.SWITCH_ON: "on", Action.SWITCH_OFF: "off"}


def _power(action: Action) -> str:
    if action not in _POWER:
        raise ValueError(f"not a switch action: {action}")
    return _POWER[action]


class SwitchAdapter:
    kind = "switch"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        blocked = platform_block(entity)
        possible = frozenset() if blocked else frozenset(_POWER)
        return with_fingerprint(
            DeviceCapabilities("switch", possible, blocked_reason=blocked), state.attributes
        )

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        if state is None:
            return StateView("unknown", adapter_locked)
        return StateView(
            availability(state),
            adapter_locked,
            power=reading(state),
            observed_at=state.last_updated,
        )

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        return HACall("switch", f"turn_{_power(action)}", entity_id, {})

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        return Expectation(power=_power(action))

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        return frozenset({action})
