"""The HA simulator speaks the Core WebSocket protocol and follows its ``/_sim`` control API."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
import websockets

from tests.conftest import SimHandle

TOKEN = "sim-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def receive(ws: websockets.ClientConnection) -> Any:
    async with asyncio.timeout(5):
        return json.loads(await ws.recv())


async def command(ws: websockets.ClientConnection, msg: dict[str, Any]) -> Any:
    """Send one command and return the next message (subscribers listen on other connections)."""
    await ws.send(json.dumps(msg))
    return await receive(ws)


@asynccontextmanager
async def connect(sim: SimHandle, token: str = TOKEN) -> AsyncIterator[websockets.ClientConnection]:
    async with websockets.connect(sim.ws_url) as ws:
        assert await receive(ws) == {"type": "auth_required", "ha_version": "2026.10.2"}
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        assert await receive(ws) == {"type": "auth_ok", "ha_version": "2026.10.2"}
        yield ws


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
        await receive(ws)
        await ws.send(json.dumps({"type": "auth", "access_token": "wrong"}))
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
    assert {"last_changed", "last_updated", "context"} <= states[0].keys()
    lamp = next(e for e in entities if e["entity_id"] == "switch.living_floor_lamp")
    assert lamp["area_id"] is None and lamp["device_id"] == "dev_living_lamp"
    assert {
        "id": "dev_living_lamp",
        "name": "Living room floor lamp",
        "area_id": "area_living",
    } in devices
    assert config["unit_system"]["temperature"] == "°C" and config["version"] == "2026.10.2"


async def test_protocol_errors(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        await command(ws, {"id": 5, "type": "ping"})
        reuse = await command(ws, {"id": 5, "type": "ping"})
        unknown = await command(ws, {"id": 6, "type": "nope"})
    assert reuse["success"] is False and reuse["error"]["code"] == "id_reuse"
    assert unknown == {
        "id": 6,
        "type": "result",
        "success": False,
        "error": {"code": "unknown_command", "message": "Unknown command."},
    }


async def test_call_service_changes_state_notifies_and_records(sim: SimHandle) -> None:
    async with connect(sim) as listener, connect(sim) as ws:
        subscribe = {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        assert (await command(listener, subscribe))["success"]
        reply = await command(
            ws, call_service(1, "light.turn_on", "light.room_203_ceiling", brightness_pct=40)
        )
        event = await receive(listener)
    context_id = reply["result"]["context"]["id"]
    assert reply["success"] and len(context_id) == 32
    assert event["id"] == 1 and event["type"] == "event"
    assert event["event"]["event_type"] == "state_changed"
    assert event["event"]["context"]["id"] == context_id
    new_state = event["event"]["data"]["new_state"]
    assert new_state["attributes"]["brightness"] == 102 and new_state["context"]["id"] == context_id
    assert event["event"]["data"]["old_state"]["attributes"]["brightness"] == 178
    calls = await sim.control.get("service-calls")
    assert calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.room_203_ceiling",
            "data": {"brightness_pct": 40},
            "context_id": context_id,
            "ts": calls[0]["ts"],
        }
    ]


@pytest.mark.parametrize(
    ("service", "entity_id", "data", "code"),
    [
        (
            "climate.set_temperature",
            "climate.room_203_trv",
            {"temperature": 30.5},
            "invalid_format",
        ),
        ("climate.set_hvac_mode", "climate.room_203_trv", {"hvac_mode": "cool"}, "invalid_format"),
        ("light.turn_on", "light.nope", {}, "not_found"),
        ("light.turn_on", "switch.living_floor_lamp", {}, "not_found"),
        ("light.explode", "light.hallway", {}, "service_not_found"),
    ],
)
async def test_call_service_errors(
    sim: SimHandle, service: str, entity_id: str, data: dict[str, Any], code: str
) -> None:
    async with connect(sim) as ws:
        reply = await command(ws, call_service(1, service, entity_id, **data))
    assert reply["success"] is False and reply["error"]["code"] == code
    assert await sim.control.get("service-calls") == []


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
    assert len(broken) == 3 and not any("group_ids" in user for user in broken)
    assert failed["success"] is False and failed["error"]["code"] == "unknown_error"


async def test_fail_next_service_fails_exactly_once(sim: SimHandle) -> None:
    await sim.control.post("fail-next-service", {"error": "service_not_found"})
    async with connect(sim) as ws:
        first = await command(ws, call_service(1, "switch.turn_on", "switch.living_floor_lamp"))
        second = await command(ws, call_service(2, "switch.turn_on", "switch.living_floor_lamp"))
        lamp = await state_of(ws, 3, "switch.living_floor_lamp")
    assert first["success"] is False and first["error"]["code"] == "service_not_found"
    assert second["success"] is True and lamp["state"] == "on"
    assert len(await sim.control.get("service-calls")) == 1


async def test_offline_entity_ignores_calls_and_online_restores(sim: SimHandle) -> None:
    assert (await sim.control.post("offline/light.hallway"))["state"] == "unavailable"
    async with connect(sim) as ws:
        reply = await command(ws, call_service(1, "light.turn_off", "light.hallway"))
        offline = await state_of(ws, 2, "light.hallway")
    assert reply["success"] and offline["state"] == "unavailable"
    assert (await sim.control.post("online/light.hallway"))["state"] == "on"


async def test_drop_connections_closes_with_1012(sim: SimHandle) -> None:
    async with connect(sim) as ws:
        assert await sim.control.get("connections") == {"connections": 1}
        assert await sim.control.post("drop-connections") == {"closed": 1}
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await receive(ws)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 1012
    assert await sim.control.get("connections") == {"connections": 0}


async def test_mode_setpoint_jumps_on_hvac_mode_change(sim: SimHandle) -> None:
    jump = {"entity_id": "climate.living_ac", "mode": "heat", "temperature": 28}
    await sim.control.post("climate/mode-setpoint", jump)
    async with connect(sim) as ws:
        reply = await command(
            ws, call_service(1, "climate.set_hvac_mode", "climate.living_ac", hvac_mode="heat")
        )
        ac = await state_of(ws, 2, "climate.living_ac")
    assert reply["success"] and ac["state"] == "heat" and ac["attributes"]["temperature"] == 28


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


async def test_registry_area_moves_device_of_entity_without_area(sim: SimHandle) -> None:
    move = {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
    async with connect(sim) as listener, connect(sim) as ws:
        await command(
            listener, {"id": 1, "type": "subscribe_events", "event_type": "device_registry_updated"}
        )
        await sim.control.post("registry/area", move)
        event = (await receive(listener))["event"]
        devices = (await command(ws, {"id": 1, "type": "config/device_registry/list"}))["result"]
    assert event["data"] == {
        "action": "update",
        "device_id": "dev_living_lamp",
        "changes": {"area_id": "area_living"},
    }
    assert next(d for d in devices if d["id"] == "dev_living_lamp")["area_id"] == "area_kitchen"


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


async def test_clock_skew_shifts_time_fired(sim: SimHandle) -> None:
    await sim.control.post("clock-skew", {"seconds": 3600})
    async with connect(sim) as listener:
        await command(
            listener, {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
        )
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        event = (await receive(listener))["event"]
    skew = datetime.fromisoformat(event["time_fired"]) - datetime.now(UTC)
    assert 3590 < skew.total_seconds() <= 3600
    assert event["data"]["new_state"]["state"] == "13.0"


async def test_reset_restores_fixture_and_notifies(sim: SimHandle) -> None:
    rename = {"entity_id": "light.hallway", "new_entity_id": "light.hall_ceiling"}
    await sim.control.post("registry/rename", rename)
    await sim.control.post("auth/list-mode", {"mode": "error"})
    async with connect(sim) as listener, connect(sim) as ws:
        await command(listener, {"id": 1, "type": "subscribe_events"})
        await sim.control.post("reset")
        events = [(await receive(listener))["event"] for _ in range(4)]
        hallway = await state_of(ws, 1, "light.hallway")
        users = await command(ws, {"id": 2, "type": "config/auth/list"})
    assert [(e["event_type"], e["data"].get("action")) for e in events] == [
        ("entity_registry_updated", "remove"),
        ("entity_registry_updated", "create"),
        ("state_changed", None),
        ("state_changed", None),
    ]
    assert hallway["state"] == "on" and users["success"]


async def test_supervisor_info_requires_token(sim: SimHandle) -> None:
    client = sim.control.client
    denied = await client.get("/info")
    assert denied.status_code == 401
    assert denied.json() == {"result": "error", "message": "Unauthorized"}
    assert (await client.get("/info", headers=AUTH)).json()["data"]["supervisor"] == "2026.10.3"
    await sim.control.post("supervisor/version", {"version": "2026.10.2"})
    assert (await client.get("/info", headers=AUTH)).json()["data"]["supervisor"] == "2026.10.2"
    addon = await client.get("/addons/self/info", headers=AUTH)
    assert addon.json()["data"]["ingress_entry"] == "/api/hassio_ingress/simtoken"


async def test_host_info_mode(sim: SimHandle) -> None:
    client = sim.control.client
    assert (await client.get("/host/info", headers=AUTH)).status_code == 403
    await sim.control.post("host-info-mode", {"mode": "ok"})
    host = await client.get("/host/info", headers=AUTH)
    assert host.json() == {
        "result": "ok",
        "data": {"dt_synchronized": True, "operating_system": "Home Assistant OS 18.3"},
    }
