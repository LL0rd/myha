"""Device adapters: capabilities, translation, state views and expectations (design §4.2)."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from myha_gateway.adapters.base import Action, Expectation, HACall, StateView
from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.ha.messages import HAState, RegistryEntity

UPDATED = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
KINDS = ("light", "switch", "climate", "cover", "sensor")
LIGHT, SWITCH, CLIMATE, COVER, SENSOR = (adapter_for(kind) for kind in KINDS)


def ha_state(entity_id: str, state: str, **attributes: Any) -> HAState:
    changed = datetime(2026, 10, 22, 10, tzinfo=UTC)
    return HAState(entity_id, state, attributes, changed, UPDATED, None)


def edited(state: HAState, *drop: str, **changes: Any) -> HAState:
    """``state`` without the attributes named in ``drop`` and with ``changes``."""
    kept = {key: value for key, value in state.attributes.items() if key not in drop}
    return replace(state, attributes=kept | changes)


def entry(state: HAState, platform: str) -> RegistryEntity:
    return RegistryEntity(state.entity_id, f"{platform}-1", "dev_1", None, platform, False)


# Values of the simulator fixture (myha_simulator/fixtures.py).
CEILING = ha_state(
    "light.room_203_ceiling", "on", supported_color_modes=["color_temp", "rgb"], brightness=178
)
LAMP = ha_state("switch.living_floor_lamp", "off")
AC = ha_state(
    "climate.living_ac",
    "cool",
    hvac_modes=["auto", "cool", "dry", "fan_only", "heat", "off"],
    min_temp=8,
    max_temp=30,
    target_temp_step=1,
    temperature=22,
    current_temperature=24,
)
BLIND = ha_state("cover.room_203_blind", "open", supported_features=15, current_position=100)
HUMIDITY = ha_state(
    "sensor.room_203_humidity", "46", unit_of_measurement="%", device_class="humidity"
)
ON_OFF = {Action.LIGHT_ON, Action.LIGHT_OFF}


def test_kind_follows_the_domain() -> None:
    assert [detect_kind(f"{kind}.x") for kind in KINDS] == list(KINDS)
    assert detect_kind("person.owner") is None
    assert detect_kind("binary_sensor.door") is None
    assert [adapter_for(kind).kind for kind in KINDS] == list(KINDS)


@pytest.mark.parametrize(
    ("modes", "actions"),
    [
        (["color_temp", "rgb"], ON_OFF | {Action.LIGHT_SET_LEVEL}),
        (["brightness"], ON_OFF | {Action.LIGHT_SET_LEVEL}),
        (["onoff"], ON_OFF),
        (["unknown"], ON_OFF),
        (None, ON_OFF),
    ],
)
def test_light_dims_with_every_color_mode_but_onoff(
    modes: list[str] | None, actions: set[Action]
) -> None:
    state = edited(CEILING, supported_color_modes=modes)
    caps = LIGHT.capabilities(state, entry(CEILING, "govee"), None)
    assert (caps.kind, caps.actions, caps.blocked_reason) == ("light", actions, None)


@pytest.mark.parametrize(
    ("platform", "reason"), [("group", "group_entity"), ("template", "template_entity")]
)
def test_group_and_template_entities_are_blocked(platform: str, reason: str) -> None:
    for adapter, state in ((LIGHT, CEILING), (SWITCH, LAMP), (CLIMATE, AC), (COVER, BLIND)):
        caps = adapter.capabilities(state, entry(state, platform), "°C")
        assert (caps.blocked_reason, caps.actions) == (reason, frozenset())
    # A sensor only reports a value; reading a computed one switches nothing.
    assert SENSOR.capabilities(HUMIDITY, entry(HUMIDITY, platform), "°C").blocked_reason is None


def test_light_translation_and_required_actions() -> None:
    lid, set_level = CEILING.entity_id, Action.LIGHT_SET_LEVEL
    on_40 = HACall("light", "turn_on", lid, {"brightness_pct": 40})
    assert LIGHT.translate(set_level, {"level_pct": 40}, lid) == on_40
    assert LIGHT.translate(set_level, {"level_pct": 0}, lid) == HACall("light", "turn_off", lid, {})
    assert LIGHT.translate(Action.LIGHT_ON, {}, lid) == HACall("light", "turn_on", lid, {})
    assert LIGHT.translate(Action.LIGHT_OFF, {}, lid) == HACall("light", "turn_off", lid, {})
    assert LIGHT.required_actions(set_level, {"level_pct": 0}) == {set_level, Action.LIGHT_OFF}
    assert LIGHT.required_actions(set_level, {"level_pct": 40}) == {set_level, Action.LIGHT_ON}
    assert LIGHT.required_actions(Action.LIGHT_ON, {}) == {Action.LIGHT_ON}


def test_light_expectations_and_state_view() -> None:
    level_40 = Expectation(power="on", level_pct=40)
    assert LIGHT.expectation(Action.LIGHT_SET_LEVEL, {"level_pct": 40}) == level_40
    assert LIGHT.expectation(Action.LIGHT_SET_LEVEL, {"level_pct": 0}) == Expectation(power="off")
    assert LIGHT.expectation(Action.LIGHT_ON, {}) == Expectation(power="on")
    assert LIGHT.expectation(Action.LIGHT_OFF, {}) == Expectation(power="off")
    view = StateView("online", False, power="on", level_pct=70, observed_at=UPDATED)
    assert LIGHT.state_view(CEILING, False) == view
    off = edited(replace(CEILING, state="off"), brightness=None)
    assert LIGHT.state_view(off, False) == replace(view, power="off", level_pct=None)


@pytest.mark.parametrize(
    ("brightness", "level"),
    [(178, 70), (255, 100), (0, 0), (127.5, 50), (300, None), ("x", None), (float("nan"), None)],
)
def test_light_level_is_brightness_in_whole_percent(brightness: object, level: int | None) -> None:
    assert LIGHT.state_view(edited(CEILING, brightness=brightness), False).level_pct == level


def test_switch() -> None:
    sid = LAMP.entity_id
    caps = SWITCH.capabilities(LAMP, entry(LAMP, "shelly"), None)
    assert caps.actions == {Action.SWITCH_ON, Action.SWITCH_OFF}
    assert SWITCH.translate(Action.SWITCH_ON, {}, sid) == HACall("switch", "turn_on", sid, {})
    assert SWITCH.translate(Action.SWITCH_OFF, {}, sid) == HACall("switch", "turn_off", sid, {})
    assert SWITCH.expectation(Action.SWITCH_ON, {}) == Expectation(power="on")
    assert SWITCH.expectation(Action.SWITCH_OFF, {}) == Expectation(power="off")
    assert SWITCH.required_actions(Action.SWITCH_ON, {}) == {Action.SWITCH_ON}
    view = StateView("online", True, power="off", observed_at=UPDATED)
    assert SWITCH.state_view(LAMP, True) == view


def test_climate_capabilities_of_the_living_room_ac() -> None:
    caps = CLIMATE.capabilities(AC, entry(AC, "midea"), "°C")
    assert caps.blocked_reason is None
    assert caps.actions == {Action.CLIMATE_SET_TEMPERATURE, Action.CLIMATE_SET_MODE}
    limits = (caps.target_c_min, caps.target_c_max, caps.target_c_step)
    assert limits == (Decimal("8"), Decimal("30"), Decimal("1"))
    assert caps.hvac_modes == {"auto", "cool", "dry", "fan_only", "heat", "off"}
    assert caps.confirmed_modes == frozenset()


def test_climate_step_defaults_to_half_a_degree() -> None:
    def step(state: HAState) -> Decimal | None:
        return CLIMATE.capabilities(state, None, "°C").target_c_step

    assert step(edited(AC, "target_temp_step")) == Decimal("0.5")
    assert step(edited(AC, target_temp_step=0)) == Decimal("0.5")
    assert step(edited(AC, target_temp_step=0.1)) == Decimal("0.1")  # via str, not the float


@pytest.mark.parametrize(
    ("state", "unit", "reason"),
    [
        (edited(AC, target_temp_high=24, target_temp_low=20), "°C", "dual_setpoint"),
        (edited(AC, "temperature", target_temp_high=24, target_temp_low=20), "°C", "dual_setpoint"),
        (edited(AC, "temperature"), "°C", "no_setpoint"),
        (AC, "°F", "unit_not_celsius"),
        (AC, None, "unit_not_celsius"),  # unit not known yet
    ],
)
def test_climate_blocked_reasons(state: HAState, unit: str | None, reason: str) -> None:
    caps = CLIMATE.capabilities(state, None, unit)
    assert (caps.blocked_reason, caps.actions) == (reason, frozenset())


def test_climate_with_empty_range_setpoints_is_single_setpoint() -> None:
    state = edited(AC, target_temp_high=None, target_temp_low=None)
    assert CLIMATE.capabilities(state, None, "°C").blocked_reason is None


def test_climate_set_mode_needs_a_product_mode() -> None:
    caps = CLIMATE.capabilities(edited(AC, hvac_modes=["auto", "dry"]), None, "°C")
    assert caps.actions == {Action.CLIMATE_SET_TEMPERATURE}


def test_unavailable_climate_keeps_its_capabilities() -> None:
    # Home Assistant leaves out state attributes such as the setpoint while unavailable.
    offline = edited(replace(AC, state="unavailable"), "temperature", "current_temperature")
    assert CLIMATE.capabilities(offline, None, "°C") == CLIMATE.capabilities(AC, None, "°C")


def test_climate_translation_and_expectations() -> None:
    cid = AC.entity_id
    call = CLIMATE.translate(Action.CLIMATE_SET_TEMPERATURE, {"target_c": "21.5"}, cid)
    assert call == HACall("climate", "set_temperature", cid, {"temperature": 21.5})
    assert type(call.data["temperature"]) is float  # sent as JSON, a Decimal would not encode
    mode = CLIMATE.translate(Action.CLIMATE_SET_MODE, {"mode": "cool"}, cid)
    assert mode == HACall("climate", "set_hvac_mode", cid, {"hvac_mode": "cool"})
    cool = CLIMATE.expectation(Action.CLIMATE_SET_MODE, {"mode": "cool"})
    assert cool == Expectation(hvac_mode="cool")
    target = CLIMATE.expectation(Action.CLIMATE_SET_TEMPERATURE, {"target_c": "21.5"})
    assert target == Expectation(target_c=Decimal("21.5"))
    assert CLIMATE.required_actions(Action.CLIMATE_SET_MODE, {"mode": "cool"}) == {
        Action.CLIMATE_SET_MODE
    }


def test_climate_state_view() -> None:
    view = CLIMATE.state_view(AC, False)
    assert view == StateView(
        "online",
        False,
        target_c=Decimal("22"),
        current_c=Decimal("24"),
        hvac_mode="cool",
        observed_at=UPDATED,
    )
    assert CLIMATE.state_view(edited(AC, temperature=21.1), False).target_c == Decimal("21.1")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "unknown", None, True, [22]])
def test_climate_temperatures_are_finite_numbers_or_none(value: object) -> None:
    view = CLIMATE.state_view(edited(AC, temperature=value, current_temperature=value), False)
    assert (view.target_c, view.current_c) == (None, None)


@pytest.mark.parametrize(
    ("features", "actions"),
    [
        (15, {Action.COVER_OPEN, Action.COVER_CLOSE, Action.COVER_STOP, Action.COVER_SET_POSITION}),
        (11, {Action.COVER_OPEN, Action.COVER_CLOSE, Action.COVER_STOP}),
        (None, set()),
    ],
)
def test_cover_actions_follow_supported_features(
    features: int | None, actions: set[Action]
) -> None:
    state = edited(BLIND, supported_features=features)
    assert COVER.capabilities(state, entry(BLIND, "zha"), None).actions == actions


@pytest.mark.parametrize(
    ("position", "directions"),
    [
        (0, {Action.COVER_CLOSE}),
        (100, {Action.COVER_OPEN}),
        (40, {Action.COVER_OPEN, Action.COVER_CLOSE}),
    ],
)
def test_cover_set_position_needs_every_direction_it_may_move(
    position: int, directions: set[Action]
) -> None:  # A17
    needed = COVER.required_actions(Action.COVER_SET_POSITION, {"position_pct": position})
    assert needed == {Action.COVER_SET_POSITION} | directions


def test_cover_translation_expectations_and_state_view() -> None:
    bid = BLIND.entity_id
    assert COVER.translate(Action.COVER_OPEN, {}, bid) == HACall("cover", "open_cover", bid, {})
    assert COVER.translate(Action.COVER_CLOSE, {}, bid) == HACall("cover", "close_cover", bid, {})
    assert COVER.translate(Action.COVER_STOP, {}, bid) == HACall("cover", "stop_cover", bid, {})
    call = COVER.translate(Action.COVER_SET_POSITION, {"position_pct": 40}, bid)
    assert call == HACall("cover", "set_cover_position", bid, {"position": 40})
    at_40 = Expectation(position_pct=40)
    assert COVER.expectation(Action.COVER_SET_POSITION, {"position_pct": 40}) == at_40
    assert COVER.expectation(Action.COVER_OPEN, {}) == Expectation(cover_state="open")
    assert COVER.expectation(Action.COVER_CLOSE, {}) == Expectation(cover_state="closed")
    assert COVER.expectation(Action.COVER_STOP, {}) == Expectation()  # the call confirms it
    assert COVER.required_actions(Action.COVER_STOP, {}) == {Action.COVER_STOP}
    view = StateView("online", False, position_pct=100, cover_state="open", observed_at=UPDATED)
    assert COVER.state_view(BLIND, False) == view


def test_sensor_is_read_only() -> None:
    caps = SENSOR.capabilities(HUMIDITY, entry(HUMIDITY, "zha"), "°C")
    assert (caps.kind, caps.actions, caps.blocked_reason) == ("sensor", frozenset(), None)
    view = StateView("online", False, value="46", unit="%", observed_at=UPDATED)
    assert SENSOR.state_view(HUMIDITY, False) == view
    assert SENSOR.required_actions(Action.STATE_READ, {}) == {Action.STATE_READ}


@pytest.mark.parametrize("kind", KINDS)
def test_actions_of_other_kinds_are_refused(kind: str) -> None:
    adapter = adapter_for(kind)
    foreign = Action.LIGHT_ON if kind == "cover" else Action.COVER_OPEN
    for action in (foreign, Action.STATE_READ):
        with pytest.raises(ValueError, match="action"):
            adapter.translate(action, {}, f"{kind}.x")
        with pytest.raises(ValueError, match="action"):
            adapter.expectation(action, {})


@pytest.mark.parametrize("kind", KINDS)
def test_availability_and_observation_time(kind: str) -> None:
    adapter = adapter_for(kind)
    state = ha_state(f"{kind}.x", "on")
    view = adapter.state_view(state, False)
    assert (view.availability, view.observed_at) == ("online", UPDATED)
    # Neither unavailable nor unknown is a reading; the time of the observation stays.
    offline = adapter.state_view(replace(state, state="unavailable"), False)
    assert offline == StateView("offline", False, observed_at=UPDATED)
    unknown = adapter.state_view(replace(state, state="unknown"), True)
    assert unknown == StateView("unknown", True, observed_at=UPDATED)
    assert adapter.state_view(None, True) == StateView("unknown", True)  # entity missing


def test_fingerprint_follows_abilities_not_state() -> None:
    def fingerprint(state: HAState) -> str:
        adapter = CLIMATE if state.entity_id.startswith("climate.") else LIGHT
        return adapter.capabilities(state, None, "°C").fingerprint

    light = fingerprint(CEILING)
    assert len(light) == 64 and light == fingerprint(CEILING)
    assert light == fingerprint(edited(CEILING, brightness=3))
    assert light == fingerprint(edited(CEILING, supported_color_modes=["rgb", "color_temp"]))
    assert light == fingerprint(edited(CEILING, supported_features=0))  # missing means none
    assert light != fingerprint(edited(CEILING, supported_features=4))
    assert light != fingerprint(edited(CEILING, supported_color_modes=["hs"]))
    ac = fingerprint(AC)
    assert ac == fingerprint(edited(AC, temperature=19, max_temp=30.0))  # same value as 30
    assert ac != fingerprint(edited(AC, max_temp=31))
    assert ac != fingerprint(edited(AC, hvac_modes=["cool", "off"]))


def test_expectation_check_range() -> None:  # E7
    expected = Expectation(hvac_mode="cool", target_c_range=(Decimal(19), Decimal(24)))
    hot = StateView("online", False, hvac_mode="cool", target_c=Decimal(28))
    assert expected.violated_range(hot) and not expected.matches(hot)
    assert expected.violated_range(replace(hot, target_c=None))
    inside = replace(hot, target_c=Decimal(24))
    assert not expected.violated_range(inside) and expected.matches(inside)
    assert not Expectation(hvac_mode="cool").violated_range(hot)


def test_expectation_tolerances() -> None:
    level = Expectation(power="on", level_pct=40)
    view = StateView("online", False, power="on", level_pct=39)
    assert level.matches(view) and level.matches(replace(view, level_pct=41))
    assert not level.matches(replace(view, level_pct=38))
    assert not level.matches(replace(view, level_pct=None))
    assert not level.matches(replace(view, power="off"))
    position = Expectation(position_pct=40, position_tolerance=2)
    assert position.matches(StateView("online", False, position_pct=42))
    assert not position.matches(StateView("online", False, position_pct=43))
    opened = Expectation(cover_state="open")
    assert not opened.matches(StateView("online", False, cover_state="opening"))
    target = Expectation(target_c=Decimal("21.5"))
    assert target.matches(StateView("online", False, target_c=Decimal("21.50")))
    assert Expectation().matches(StateView("offline", False))
