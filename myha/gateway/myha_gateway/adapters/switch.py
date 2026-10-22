"""Switches: on and off."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    observed,
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
        capabilities = DeviceCapabilities(
            "switch", frozenset(_POWER), blocked_reason=platform_block(entity)
        )
        return with_fingerprint(capabilities, state.attributes)

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        view = observed(state, adapter_locked)
        if state is None:
            return view
        return replace(view, power=reading(state))

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        return HACall("switch", f"turn_{_power(action)}", entity_id, {})

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        return Expectation(power=_power(action))

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        return frozenset({action})
