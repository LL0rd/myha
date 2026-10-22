"""The HA simulator speaks the Core WebSocket protocol and follows its ``/_sim`` control API."""

from datetime import UTC, datetime
from typing import Any

import pytest
import websockets

from tests.helpers import SIM_TOKEN, SimHandle
from tests.helpers import ws_command as command
from tests.helpers import ws_connect as connect
from tests.helpers import ws_receive as receive

AUTH = {"Authorization": f"Bearer {SIM_TOKEN}"}
TRV = "climate.room_203_trv"


def call_service(msg_id: int, service: str, entity_id: str, **data: Any) -> dict[str, Any]:
    domain, name = service.split(".")
    return {
        "id": msg_id,
        "type": "call_service",
        "domain": domain,
        "service": name,
        "service_data": data,
        "target": {"entity_id": entity_id},
    }


async def state_of(ws: websockets.ClientConnection, msg_id: int, entity_id: str) -> Any:
    states = (await command(ws, {"id": msg_id, "type": "get_states"}))["result"]
    return next(state for state in states if state["entity_id"] == entity_id)


async def test_auth_ok_and_invalid_token_closes(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        assert await command(ws, {"id": 1, "type": "ping"}) == {"id": 1, "type": "pong"}
    async with websockets.connect(sim.ws_url) as ws:
        assert await receive(ws) == {"type": "auth_required", "ha_version": "2026.10.2"}
        await ws.send('{"type": "auth", "access_token": "wrong"}')
        assert (await receive(ws))["type"] == "auth_invalid"
        with pytest.raises(websockets.ConnectionClosedOK):
            await ws.recv()


async def test_snapshot_registries_and_config(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        states = (await command(ws, {"id": 1, "type": "get_states"}))["result"]
        entities = (await command(ws, {"id": 2, "type": "config/entity_registry/list"}))["result"]
        devices = (await command(ws, {"id": 3, "type": "config/device_registry/list"}))["result"]
        areas = (await command(ws, {"id": 4, "type": "config/area_registry/list"}))["result"]
        config = (await command(ws, {"id": 5, "type": "get_config"}))["result"]
    assert len(states) == len(entities) == len(devices) == 15 and len(areas) == 5
    first = states[0]
    assert first["last_changed"] == first["last_reported"] == first["last_updated"]
    assert len(first["context"]["id"]) == 26 and first["context"]["user_id"] is None
    lamp = next(e for e in entities if e["entity_id"] == "switch.living_floor_lamp")
    assert lamp["area_id"] is None and lamp["device_id"] == "dev_living_lamp"
    lamp_device = {
        "id": "dev_living_lamp",
        "name": "Living room floor lamp",
        "area_id": "area_living",
    }
    assert lamp_device in devices
    assert config["unit_system"]["temperature"] == "°C" and config["version"] == "2026.10.2"


async def test_protocol_errors(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        zero = await command(ws, {"id": 0, "type": "ping"})
        unknown = await command(ws, {"id": 7, "type": "nope"})
        pong = await command(ws, {"id": 7, "type": "ping"})  # an unknown command keeps its id
        reuse = await command(ws, {"id": 7, "type": "ping"})
    assert zero["success"] is False and zero["error"]["code"] == "invalid_format"
    assert unknown == {
        "id": 7,
        "type": "result",
        "success": False,
        "error": {"code": "unknown_command", "message": "Unknown command."},
    }
    assert pong == {"id": 7, "type": "pong"}
    assert reuse["success"] is False and reuse["error"]["code"] == "id_reuse"


async def test_bad_frames_close_the_connection(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        await ws.send("{not json")
        with pytest.raises(websockets.ConnectionClosed) as invalid:
            await receive(ws)
    async with websockets.connect(sim.ws_url) as ws:
        await receive(ws)
        await ws.send(b'{"type": "auth"}')
        with pytest.raises(websockets.ConnectionClosed) as binary:
            await receive(ws)
    assert invalid.value.rcvd is not None and invalid.value.rcvd.code == 1007
    assert binary.value.rcvd is not None and binary.value.rcvd.code == 1003


async def test_crashing_command_answers_unknown_error_and_keeps_the_connection(
    sim: SimHandle,
) -> None:
    remove = {"entity_id": "climate.living_ac", "attributes": {"hvac_modes": None}}
    ac = await sim.control.post("entity/attributes", remove)
    async with connect(sim) as ws:
        reply = await command(
            ws, call_service(1, "climate.set_hvac_mode", "climate.living_ac", hvac_mode="heat")
        )
        pong = await command(ws, {"id": 2, "type": "ping"})
    assert "hvac_modes" not in ac["attributes"] and ac["attributes"]["min_temp"] == 8
    assert reply["success"] is False and reply["error"]["code"] == "unknown_error"
    assert pong == {"id": 2, "type": "pong"}
    [attempt] = await sim.control.get("service-calls")
    assert attempt["error"] == "unknown_error" and attempt["context_id"] is None


async def test_call_service_changes_state_notifies_and_records(sim: SimHandle) -> None:
    async with connect(sim) as listener, connect(sim) as ws:
        subscribe = {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        assert (await command(listener, subscribe))["success"]
        reply = await command(
            ws, call_service(1, "light.turn_on", "light.room_203_ceiling", brightness_pct=40)
        )
        event = await receive(listener)
    context = reply["result"]["context"]
    assert reply["success"] and len(context["id"]) == 26 and context["user_id"] == "sup"
    assert event["id"] == 1 and event["type"] == "event"
    assert event["event"]["event_type"] == "state_changed"
    assert event["event"]["context"] == context
    new_state = event["event"]["data"]["new_state"]
    assert new_state["attributes"]["brightness"] == 102 and new_state["context"] == context
    assert event["event"]["data"]["old_state"]["attributes"]["brightness"] == 178
    calls = await sim.control.get("service-calls")
    assert calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.room_203_ceiling",
            "data": {"brightness_pct": 40},
            "context_id": context["id"],
            "error": None,
            "ts": calls[0]["ts"],
        }
    ]


@pytest.mark.parametrize(
    ("service", "entity_id", "data", "code"),
    [
        ("climate.set_temperature", TRV, {"temperature": 5}, "service_validation_error"),
        ("climate.set_hvac_mode", TRV, {"hvac_mode": "cool"}, "service_validation_error"),
        ("climate.set_hvac_mode", TRV, {"hvac_mode": "warm"}, "invalid_format"),
        ("climate.set_temperature", TRV, {"temperature": "warm"}, "invalid_format"),
        ("light.turn_on", "light.room_203_ceiling", {"brightness_pct": 101}, "invalid_format"),
        ("cover.set_cover_position", "cover.room_203_blind", {"position": -1}, "invalid_format"),
        ("light.explode", "light.hallway", {}, "not_found"),
    ],
)
async def test_call_service_errors_are_answered_and_logged(
    sim: SimHandle, service: str, entity_id: str, data: dict[str, Any], code: str
) -> None:
    async with connect(sim) as ws:
        reply = await command(ws, call_service(1, service, entity_id, **data))
    assert reply["success"] is False and reply["error"]["code"] == code
    [attempt] = await sim.control.get("service-calls")
    assert attempt["error"] == code and attempt["context_id"] is None


async def test_validation_errors_read_like_core(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        cold = await command(ws, call_service(1, "climate.set_temperature", TRV, temperature=5))
        cool = await command(ws, call_service(2, "climate.set_hvac_mode", TRV, hvac_mode="cool"))
    assert cold["error"] == {
        "code": "service_validation_error",
        "message": "Validation error: Provided temperature 5.0 is not valid."
        " Accepted range is 7 to 30.",
        "translation_key": "temp_out_of_range",
        "translation_placeholders": {"check_temp": "5.0", "min_temp": "7", "max_temp": "30"},
        "translation_domain": "climate",
    }
    assert cool["error"]["message"] == (
        "Validation error: HVAC mode cool is not valid. Valid HVAC modes are: heat, off."
    )
    assert cool["error"]["translation_key"] == "not_valid_hvac_mode"
    assert cool["error"]["translation_placeholders"] == {"mode": "cool", "modes": "heat, off"}


async def test_cover_services_need_their_feature(sim: SimHandle) -> None:
    blind = "cover.kitchen_blind"  # features 11: open, close and stop, no set_position
    async with connect(sim) as ws:
        position = await command(
            ws, call_service(1, "cover.set_cover_position", blind, position=40)
        )
        stop = await command(ws, call_service(2, "cover.stop_cover", blind))
        no_stop = {"entity_id": blind, "attributes": {"supported_features": 3}}
        await sim.control.post("entity/attributes", no_stop)
        refused = await command(ws, call_service(3, "cover.stop_cover", blind))
    assert position["success"] is False and position["error"] == {
        "code": "service_validation_error",
        "message": "Validation error: Entity cover.kitchen_blind does not support action"
        " cover.set_cover_position.",
        "translation_key": "service_not_supported",
        "translation_placeholders": {
            "domain": "cover",
            "service": "set_cover_position",
            "entity_id": blind,
        },
        "translation_domain": "homeassistant",
    }
    assert stop["success"] is True
    assert refused["error"]["code"] == "service_validation_error"
    assert refused["error"]["translation_placeholders"]["service"] == "stop_cover"
    calls = await sim.control.get("service-calls")
    assert [call["error"] for call in calls] == [
        "service_validation_error",
        None,
        "service_validation_error",
    ]


async def test_call_without_matching_entity_succeeds_without_effect(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        missing = await command(ws, call_service(1, "light.turn_on", "light.nope"))
        other = await command(ws, call_service(2, "light.turn_on", "switch.living_floor_lamp"))
        lamp = await state_of(ws, 3, "switch.living_floor_lamp")
    assert missing["success"] and other["success"] and lamp["state"] == "off"
    calls = await sim.control.get("service-calls")
    assert [(call["entity_id"], call["error"]) for call in calls] == [
        ("light.nope", None),
        ("switch.living_floor_lamp", None),
    ]


async def test_no_op_call_fires_no_event_but_reports(sim: SimHandle) -> None:
    async with connect(sim) as listener, connect(sim) as ws:
        await command(
            listener, {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        )
        before = await state_of(ws, 1, "switch.living_floor_lamp")
        reply = await command(ws, call_service(2, "switch.turn_off", "switch.living_floor_lamp"))
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        event = await receive(listener)  # an event for the lamp would have come first
        after = await state_of(ws, 3, "switch.living_floor_lamp")
    assert reply["success"] and event["event"]["data"]["entity_id"] == "sensor.outdoor_temp"
    assert after["last_updated"] == before["last_updated"]
    assert after["last_reported"] > before["last_reported"]


async def test_fan_out_survives_a_hard_closed_subscriber(sim: SimHandle) -> None:
    subscribe = {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
    async with connect(sim) as first, connect(sim) as broken, connect(sim) as last:
        for ws in (first, broken, last):
            await command(ws, subscribe)
        broken.transport.abort()  # gone without a close frame
        for value in ("1", "2", "3"):
            await sim.control.post("state/sensor.outdoor_temp", {"state": value})
        received = [
            [(await receive(ws))["event"]["data"]["new_state"]["state"] for _ in range(3)]
            for ws in (first, last)
        ]
    assert received == [["1", "2", "3"], ["1", "2", "3"]]


async def test_subscribe_to_all_events_and_unsubscribe(sim: SimHandle) -> None:
    move = {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
    async with connect(sim) as ws:
        await command(ws, {"id": 1, "type": "subscribe_events"})
        await command(ws, {"id": 2, "type": "subscribe_events", "event_type": "state_changed"})
        await sim.control.post("registry/area", move)
        registry = await receive(ws)  # only the subscription to all events gets it
        removed = await command(ws, {"id": 3, "type": "unsubscribe_events", "subscription": 1})
        again = await command(ws, {"id": 4, "type": "unsubscribe_events", "subscription": 1})
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        state = await receive(ws)
    assert registry["id"] == 1
    assert registry["event"]["event_type"] == "device_registry_updated"
    assert removed["success"] and again["error"]["code"] == "not_found"
    assert state["id"] == 2  # subscription 1 would have come first


async def test_auth_list_needs_admin_and_follows_list_mode(sim: SimHandle) -> None:
    async with connect(sim, "sim-user-token") as ws:
        denied = await command(ws, {"id": 1, "type": "config/auth/list"})
    assert denied["success"] is False and denied["error"]["code"] == "unauthorized"
    async with connect(sim) as ws:
        users = (await command(ws, {"id": 1, "type": "config/auth/list"}))["result"]
        await sim.control.post("auth/list-mode", {"mode": "schema_missing_groups"})
        broken = (await command(ws, {"id": 2, "type": "config/auth/list"}))["result"]
        await sim.control.post("auth/list-mode", {"mode": "error"})
        failed = await command(ws, {"id": 3, "type": "config/auth/list"})
    assert [user["id"] for user in users] == ["owner1", "user1", "sup"]
    assert not any("is_admin" in user for user in users)
    assert users[0]["credentials"] == [{"type": "homeassistant"}]
    assert len(broken) == 3 and not any("group_ids" in user for user in broken)
    assert failed["success"] is False and failed["error"]["code"] == "unknown_error"


async def test_fail_next_service_fails_exactly_once(sim: SimHandle) -> None:
    await sim.control.post("fail-next-service", {"error": "home_assistant_error"})
    async with connect(sim) as ws:
        first = await command(ws, call_service(1, "switch.turn_on", "switch.living_floor_lamp"))
        second = await command(ws, call_service(2, "switch.turn_on", "switch.living_floor_lamp"))
        lamp = await state_of(ws, 3, "switch.living_floor_lamp")
    assert first["success"] is False and first["error"]["code"] == "home_assistant_error"
    assert second["success"] is True and lamp["state"] == "on"
    calls = await sim.control.get("service-calls")
    assert [(call["error"], call["context_id"]) for call in calls] == [
        ("home_assistant_error", None),
        (None, second["result"]["context"]["id"]),
    ]


async def test_offline_keeps_capabilities_and_online_restores(sim: SimHandle) -> None:
    await sim.control.post(f"state/{TRV}", {"attributes": {"temperature": 19.5}})
    offline = await sim.control.post(f"offline/{TRV}")
    async with connect(sim) as ws:
        reply = await command(ws, call_service(1, "climate.set_temperature", TRV, temperature=22))
        during = await state_of(ws, 2, TRV)
    online = await sim.control.post(f"online/{TRV}")
    assert offline["state"] == during["state"] == "unavailable" and reply["success"]
    assert offline["attributes"] == {
        "friendly_name": "Room 203 radiator",
        "hvac_modes": ["heat", "off"],
        "min_temp": 7,
        "max_temp": 30,
        "target_temp_step": 0.5,
    }
    assert online["state"] == "heat" and online["attributes"]["temperature"] == 19.5
    assert online["attributes"]["current_temperature"] == 21.0


async def test_drop_connections_closes_with_1012(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        assert await sim.control.get("connections") == {"connections": 1}
        assert await sim.control.post("drop-connections") == {"closed": 1}
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await receive(ws)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 1012
    assert await sim.control.get("connections") == {"connections": 0}


async def test_mode_setpoint_jumps_only_when_the_mode_changes(sim: SimHandle) -> None:
    ac = "climate.living_ac"
    for mode, temperature in (("cool", 16), ("heat", 28)):
        await sim.control.post(
            "climate/mode-setpoint", {"entity_id": ac, "mode": mode, "temperature": temperature}
        )
    async with connect(sim) as ws:
        await command(ws, call_service(1, "climate.set_hvac_mode", ac, hvac_mode="cool"))  # as is
        same = await state_of(ws, 2, ac)
        await command(ws, call_service(3, "climate.set_hvac_mode", ac, hvac_mode="heat"))
        changed = await state_of(ws, 4, ac)
    assert (same["state"], same["attributes"]["temperature"]) == ("cool", 22)
    assert (changed["state"], changed["attributes"]["temperature"]) == ("heat", 28)


async def test_registry_rename_keeps_unique_id(sim: SimHandle) -> None:
    rename = {"entity_id": "light.hallway", "new_entity_id": "light.hall_ceiling"}
    async with connect(sim) as listener, connect(sim) as ws:
        await command(listener, {"id": 1, "type": "subscribe_events"})
        await sim.control.post("registry/rename", rename)
        events = [(await receive(listener))["event"] for _ in range(3)]
        entities = (await command(ws, {"id": 1, "type": "config/entity_registry/list"}))["result"]
    assert events[0]["event_type"] == "entity_registry_updated"
    assert events[0]["data"] == {
        "action": "update",
        "entity_id": "light.hall_ceiling",
        "changes": {"entity_id": "light.hallway"},
        "old_entity_id": "light.hallway",
    }
    removed, added = events[1]["data"], events[2]["data"]
    assert removed["entity_id"] == "light.hallway" and removed["new_state"] is None
    assert added["entity_id"] == "light.hall_ceiling" and added["old_state"] is None
    renamed = next(e for e in entities if e["entity_id"] == "light.hall_ceiling")
    assert renamed["unique_id"] == "shelly-hallway"
    assert not any(e["entity_id"] == "light.hallway" for e in entities)


@pytest.mark.parametrize("new_entity_id", ["switch.hallway", "light.Hall", "hallway"])
async def test_registry_rename_rejects_bad_ids(sim: SimHandle, new_entity_id: str) -> None:
    rename = {"entity_id": "light.hallway", "new_entity_id": new_entity_id}
    response = await sim.control.client.post("/_sim/registry/rename", json=rename)
    assert response.status_code == 422


async def test_registry_replace_puts_entity_on_new_device(sim: SimHandle) -> None:
    replace = {"entity_id": "light.living_ceiling", "new_unique_id": "govee-new"}
    async with connect(sim) as listener, connect(sim) as ws:
        await command(listener, {"id": 1, "type": "subscribe_events"})
        await sim.control.post("registry/replace", replace)
        events = [(await receive(listener))["event"]["event_type"] for _ in range(2)]
        entities = (await command(ws, {"id": 1, "type": "config/entity_registry/list"}))["result"]
        devices = (await command(ws, {"id": 2, "type": "config/device_registry/list"}))["result"]
    assert events == ["device_registry_updated", "entity_registry_updated"]
    entry = next(e for e in entities if e["entity_id"] == "light.living_ceiling")
    assert entry["unique_id"] == "govee-new" and entry["device_id"] == "dev_govee-new"
    assert {
        "id": "dev_govee-new",
        "name": "Living room ceiling",
        "area_id": "area_living",
    } in devices


async def test_registry_area_moves_device_of_entity_without_area(sim: SimHandle) -> None:
    move = {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
    subscribe = {"id": 1, "type": "subscribe_events", "event_type": "device_registry_updated"}
    async with connect(sim) as listener, connect(sim) as ws:
        await command(listener, subscribe)
        await sim.control.post("registry/area", move)
        event = (await receive(listener))["event"]
        devices = (await command(ws, {"id": 1, "type": "config/device_registry/list"}))["result"]
    assert event["data"] == {
        "action": "update",
        "device_id": "dev_living_lamp",
        "changes": {"area_id": "area_living"},
    }
    assert next(d for d in devices if d["id"] == "dev_living_lamp")["area_id"] == "area_kitchen"


async def test_clock_skew_moves_every_timestamp(sim: SimHandle) -> None:
    await sim.control.post("clock-skew", {"seconds": 3600})
    async with connect(sim) as listener:
        await command(
            listener, {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        )
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        event = (await receive(listener))["event"]
    new_state = event["data"]["new_state"]
    stamps = {new_state[key] for key in ("last_changed", "last_reported", "last_updated")}
    assert stamps == {event["time_fired"]}
    skew = datetime.fromisoformat(event["time_fired"]) - datetime.now(UTC)
    assert 3590 < skew.total_seconds() <= 3600


async def test_reset_restores_fixture_and_notifies(sim: SimHandle) -> None:
    rename = {"entity_id": "light.hallway", "new_entity_id": "light.hall_ceiling"}
    await sim.control.post("registry/rename", rename)
    move = {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
    await sim.control.post("registry/area", move)
    await sim.control.post("auth/list-mode", {"mode": "error"})
    async with connect(sim) as listener, connect(sim) as ws:
        await command(listener, {"id": 1, "type": "subscribe_events"})
        await sim.control.post("reset")
        events = [(await receive(listener))["event"] for _ in range(5)]
        hallway = await state_of(ws, 1, "light.hallway")
        users = await command(ws, {"id": 2, "type": "config/auth/list"})
    assert [(e["event_type"], e["data"].get("action")) for e in events] == [
        ("entity_registry_updated", "remove"),
        ("entity_registry_updated", "create"),
        ("device_registry_updated", "update"),
        ("state_changed", None),
        ("state_changed", None),
    ]
    assert events[2]["data"]["changes"] == {"area_id": "area_kitchen"}
    assert hallway["state"] == "on" and users["success"]


async def test_supervisor_answers_like_aiohttp(sim: SimHandle) -> None:
    client = sim.control.client
    missing = await client.get("/info")
    wrong = await client.get("/info", headers={"Authorization": "Bearer nope"})
    info = await client.get("/info", headers=AUTH)
    assert (missing.status_code, missing.text) == (401, "401: Unauthorized")
    assert (wrong.status_code, wrong.text) == (403, "403: Forbidden")
    assert info.json()["data"] == {
        "supervisor": "2026.10.3",
        "channel": "stable",
        "arch": "amd64",
        "supported": True,
    }
    await sim.control.post("supervisor/version", {"version": "2026.10.2"})
    assert (await client.get("/info", headers=AUTH)).json()["data"]["supervisor"] == "2026.10.2"
    addon = await client.get("/addons/self/info", headers=AUTH)
    assert addon.json()["data"]["ingress_entry"] == "/api/hassio_ingress/simtoken"


async def test_host_info_mode(sim: SimHandle) -> None:
    client = sim.control.client
    forbidden = await client.get("/host/info", headers=AUTH)
    assert (forbidden.status_code, forbidden.text) == (403, "403: Forbidden")
    await sim.control.post("host-info-mode", {"mode": "ok"})
    host = await client.get("/host/info", headers=AUTH)
    assert host.json() == {
        "result": "ok",
        "data": {"dt_synchronized": True, "operating_system": "Home Assistant OS 18.3"},
    }
