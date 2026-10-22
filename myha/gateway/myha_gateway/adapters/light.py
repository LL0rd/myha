"""Lights: on, off and brightness in percent."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    number,
    observed,
    percent,
    platform_block,
    reading,
    strings,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity

# Home Assistant's color modes with brightness: every valid mode except onoff.
_DIMMABLE = frozenset({"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"})


def _level(state: HAState) -> int | None:
    """Brightness 0..255 in whole percent; a lit lamp shows at least 1 %, never 0 %."""
    brightness = number(state.attributes.get("brightness"))
    level = percent(brightness, 255)
    if level == 0 and brightness is not None and brightness >= 1 and state.state == "on":
        return 1
    return level


class LightAdapter:
    kind = "light"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        actions = {Action.LIGHT_ON, Action.LIGHT_OFF}
        if strings(state.attributes.get("supported_color_modes")) & _DIMMABLE:
            actions.add(Action.LIGHT_SET_LEVEL)
        capabilities = DeviceCapabilities(
            "light", frozenset(actions), blocked_reason=platform_block(entity)
        )
        return with_fingerprint(capabilities, state.attributes)

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        view = observed(state, adapter_locked)
        if state is None:
            return view
        return replace(view, power=reading(state), level_pct=_level(state))

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
