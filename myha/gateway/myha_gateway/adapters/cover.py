"""Covers: open, close, stop and position in percent."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from myha_gateway.adapters.base import (
    Action,
    DeviceCapabilities,
    Expectation,
    HACall,
    StateView,
    features,
    observed,
    percent,
    platform_block,
    reading,
    with_fingerprint,
)
from myha_gateway.ha.messages import HAState, RegistryEntity

# Home Assistant's CoverEntityFeature bit and service of each action.
_FEATURE = {
    Action.COVER_OPEN: 1,
    Action.COVER_CLOSE: 2,
    Action.COVER_SET_POSITION: 4,
    Action.COVER_STOP: 8,
}
_SERVICE = {
    Action.COVER_OPEN: "open_cover",
    Action.COVER_CLOSE: "close_cover",
    Action.COVER_SET_POSITION: "set_cover_position",
    Action.COVER_STOP: "stop_cover",
}


class CoverAdapter:
    kind = "cover"

    def capabilities(
        self, state: HAState, entity: RegistryEntity | None, temperature_unit: str | None
    ) -> DeviceCapabilities:
        mask = features(state.attributes)
        possible = frozenset(action for action, bit in _FEATURE.items() if mask & bit)
        capabilities = DeviceCapabilities("cover", possible, blocked_reason=platform_block(entity))
        return with_fingerprint(capabilities, state.attributes)

    def state_view(self, state: HAState | None, adapter_locked: bool) -> StateView:
        view = observed(state, adapter_locked)
        if state is None:
            return view
        return replace(
            view,
            position_pct=percent(state.attributes.get("current_position")),
            cover_state=reading(state),
        )

    def translate(self, action: Action, params: Mapping[str, Any], entity_id: str) -> HACall:
        if action not in _SERVICE:
            raise ValueError(f"not a cover action: {action}")
        data = {"position": params["position_pct"]} if action is Action.COVER_SET_POSITION else {}
        return HACall("cover", _SERVICE[action], entity_id, data)

    def expectation(self, action: Action, params: Mapping[str, Any]) -> Expectation:
        match action:
            case Action.COVER_OPEN:
                return Expectation(cover_state="open")
            case Action.COVER_CLOSE:
                return Expectation(cover_state="closed")
            case Action.COVER_SET_POSITION:
                return Expectation(position_pct=params["position_pct"])
            case Action.COVER_STOP:
                return Expectation()  # nothing to observe: the successful call confirms it
        raise ValueError(f"not a cover action: {action}")

    def required_actions(self, action: Action, params: Mapping[str, Any]) -> frozenset[Action]:
        """``set_position`` needs every direction it may move: 0 close, 100 open, between both."""
        if action is not Action.COVER_SET_POSITION:
            return frozenset({action})
        needed: set[Action] = {action}
        if params["position_pct"] < 100:
            needed.add(Action.COVER_CLOSE)
        if params["position_pct"] > 0:
            needed.add(Action.COVER_OPEN)
        return frozenset(needed)
