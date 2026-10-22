"""Climate devices with a single setpoint in °C; modes off, cool and heat (design E7)."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    availability,
    number,
    platform_block,
    reading,
    strings,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity

_CELSIUS = "°C"
_PRODUCT_MODES = frozenset({"off", "cool", "heat"})
_DEFAULT_STEP = Decimal("0.5")  # what the Home Assistant frontend uses for °C without a step


def _blocked(state: HAState, entity: RegistryEntity | None, unit: str | None) -> str | None:
    attributes = state.attributes
    if reason := platform_block(entity):
        return reason
    if unit != _CELSIUS:
        return "unit_not_celsius"
    if state.state == "unavailable":
        return None  # Home Assistant leaves the setpoints out while the device is unavailable
    if (
        attributes.get("target_temp_high") is not None
        or attributes.get("target_temp_low") is not None
    ):
        return "dual_setpoint"
    return None if "temperature" in attributes else "no_setpoint"


def _target_c(params: Mapping[str, Any]) -> Decimal:
    return Decimal(str(params["target_c"]))  # the policy hands over a string such as "21.5"


class ClimateAdapter:
    kind = "climate"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        attributes = state.attributes
        blocked = _blocked(state, entity, temperature_unit)
        hvac_modes = strings(attributes.get("hvac_modes"))
        actions = {Action.CLIMATE_SET_TEMPERATURE}
        if hvac_modes & _PRODUCT_MODES:
            actions.add(Action.CLIMATE_SET_MODE)
        step = number(attributes.get("target_temp_step"))
        capabilities = DeviceCapabilities(
            "climate",
            frozenset() if blocked else frozenset(actions),
            target_c_min=number(attributes.get("min_temp")),
            target_c_max=number(attributes.get("max_temp")),
            target_c_step=step if step is not None and step > 0 else _DEFAULT_STEP,
            hvac_modes=hvac_modes,
            blocked_reason=blocked,
        )
        return with_fingerprint(capabilities, attributes)

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        if state is None:
            return StateView("unknown", adapter_locked)
        return StateView(
            availability(state),
            adapter_locked,
            target_c=number(state.attributes.get("temperature")),
            current_c=number(state.attributes.get("current_temperature")),
            hvac_mode=reading(state),
            observed_at=state.last_updated,
        )

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        match action:
            case Action.CLIMATE_SET_TEMPERATURE:
                data = {"temperature": float(_target_c(params))}
                return HACall("climate", "set_temperature", entity_id, data)
            case Action.CLIMATE_SET_MODE:  # set_hvac_mode only, never turn_on/off (E7)
                return HACall("climate", "set_hvac_mode", entity_id, {"hvac_mode": params["mode"]})
        raise ValueError(f"not a climate action: {action}")

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        match action:
            case Action.CLIMATE_SET_TEMPERATURE:
                return Expectation(target_c=_target_c(params))
            case Action.CLIMATE_SET_MODE:
                return Expectation(hvac_mode=params["mode"])
        raise ValueError(f"not a climate action: {action}")

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        return frozenset({action})
