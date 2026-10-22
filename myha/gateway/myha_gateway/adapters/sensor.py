"""Sensors and binary sensors: a value with its unit, read only."""

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
    reading,
    text,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity


class SensorAdapter:
    kind = "sensor"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        # Not blocked for group or template platforms: reading a computed value switches nothing.
        return with_fingerprint(DeviceCapabilities("sensor", frozenset()), state.attributes)

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        view = observed(state, adapter_locked)
        if state is None:
            return view
        unit = text(state.attributes.get("unit_of_measurement"))
        return replace(view, value=reading(state), unit=unit)

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        raise ValueError(f"not a sensor action: {action}")

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        raise ValueError(f"not a sensor action: {action}")

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        return frozenset({action})
