"""Lights: on, off and brightness in percent."""

from collections.abc import Mapping
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    availability,
    percent,
    platform_block,
    reading,
    strings,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity

# Home Assistant's color modes with brightness: every valid mode except onoff.
_DIMMABLE = frozenset({"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"})


class LightAdapter:
    kind = "light"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        blocked = platform_block(entity)
        actions = {Action.LIGHT_ON, Action.LIGHT_OFF}
        if strings(state.attributes.get("supported_color_modes")) & _DIMMABLE:
            actions.add(Action.LIGHT_SET_LEVEL)
        possible = frozenset() if blocked else frozenset(actions)
        return with_fingerprint(
            DeviceCapabilities("light", possible, blocked_reason=blocked), state.attributes
        )

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        if state is None:
            return StateView("unknown", adapter_locked)
        return StateView(
            availability(state),
            adapter_locked,
            power=reading(state),
            level_pct=percent(state.attributes.get("brightness"), 255),
            observed_at=state.last_updated,
        )

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        match action:
            case Action.LIGHT_ON:
                return HACall("light", "turn_on", entity_id, {})
            case Action.LIGHT_OFF:
                return HACall("light", "turn_off", entity_id, {})
            case Action.LIGHT_SET_LEVEL if params["level_pct"] > 0:
                return HACall(
                    "light", "turn_on", entity_id, {"brightness_pct": params["level_pct"]}
                )
            case Action.LIGHT_SET_LEVEL:
                return HACall("light", "turn_off", entity_id, {})
        raise ValueError(f"not a light action: {action}")

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        match action:
            case Action.LIGHT_ON:
                return Expectation(power="on")
            case Action.LIGHT_OFF:
                return Expectation(power="off")
            case Action.LIGHT_SET_LEVEL if params["level_pct"] > 0:
                return Expectation(power="on", level_pct=params["level_pct"])
            case Action.LIGHT_SET_LEVEL:
                return Expectation(power="off")
        raise ValueError(f"not a light action: {action}")

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        """``set_level`` 0 switches off and needs ``light.off``; other levels need ``light.on``."""
        if action is not Action.LIGHT_SET_LEVEL:
            return frozenset({action})
        return frozenset({action, Action.LIGHT_ON if params["level_pct"] > 0 else Action.LIGHT_OFF})
