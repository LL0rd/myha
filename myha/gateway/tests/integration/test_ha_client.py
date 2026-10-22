"""HAClient against the simulator (and a silent fake Core): snapshot, events, services,
reconnect, registries, users, and its place in the admin app's lifespan."""

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from myha_gateway.app import create_admin_app
from myha_gateway.deps import AppContext
from myha_gateway.ha import client as ha_client
from myha_gateway.ha.client import HAClient, HAProtocolError, HAServiceError, HAUnavailable
from myha_gateway.ha.messages import ConnectionStatus, StateChanged
from tests.conftest import SimHandle
from tests.helpers import call, fake_supervisor

TOKEN = "sim-token"


class Seen:
    """What a client reported; ``locked`` is its adapter_locked at each status report."""

    def __init__(self) -> None:
        self.events: list[StateChanged] = []
        self.statuses: list[ConnectionStatus] = []
        self.locked: list[bool] = []
        self.client: HAClient | None = None
        self.fail_first_event = False

    async def on_event(self, change: StateChanged) -> None:
        self.events.append(change)
        if self.fail_first_event and len(self.events) == 1:
            raise RuntimeError("handler bug")

    async def on_status(self, status: ConnectionStatus) -> None:
        assert self.client is not None
        self.statuses.append(status)
        self.locked.append(self.client.adapter_locked)


@asynccontextmanager
async def started(
    url: str, token: str = TOKEN, **kwargs: Any
) -> AsyncIterator[tuple[HAClient, Seen]]:
    """A started client; afterwards it is stopped and none of its tasks may be left."""
    seen = Seen()
    client = HAClient(url, token, on_event=seen.on_event, on_status=seen.on_status, **kwargs)
    seen.client = client
    before = asyncio.all_tasks()
    async with asyncio.timeout(2):  # start() returns once the first attempt is decided
        await client.start()
    try:
        yield client, seen
    finally:
        await client.stop()
        left = [t for t in asyncio.all_tasks() - before if t.get_name().startswith("ha-client")]
        assert left == [] and not client.connected


async def until(condition: Callable[[], object], seconds: float = 3) -> None:
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110 (nothing to wait on but the client's state)
            await asyncio.sleep(0.01)


@asynccontextmanager
async def fake_core(unit: str = "°C", drop_on_service: bool = False) -> AsyncIterator[str]:
    """A Core that answers the client's setup but no service call; it may drop instead."""

    async def handler(ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "call_service":
                if drop_on_service:
                    await ws.close(1012)
                continue
            config = {"unit_system": {"temperature": unit}}
            result = {"get_states": [], "get_config": config}.get(msg["type"], [])
            reply = {"id": msg["id"], "type": "result", "success": True, "result": result}
            await ws.send(json.dumps(reply))

    async with serve(handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def test_reconnect_delays_double_up_to_30_s_with_jitter() -> None:
    delays = list(itertools.islice(ha_client._backoff(), 2000))  # a long outage stays at 30 s
    for delay, base in zip(delays, [1, 2, 4, 8, 16, 30, 30], strict=False):
        assert 0.8 * base <= delay <= 1.2 * base
    assert all(24 <= delay <= 36 for delay in delays[5:])


async def test_start_loads_snapshot_registries_and_config(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, seen):
        assert client.connected and not client.adapter_locked
        states, registry = client.all_states(), client.registry()
        ceiling = client.state("light.living_ceiling")
    assert len(states) == 15 and ceiling is not None and states["light.living_ceiling"] == ceiling
    assert ceiling.state == "on" and ceiling.attributes["brightness"] == 140
    assert ceiling.last_changed.utcoffset() == timedelta(0) and len(ceiling.context_id or "") == 32
    assert (client.generation, client.temperature_unit) == (1, "°C")
    assert seen.statuses == [ConnectionStatus(True, 1, None)] and seen.locked == [False]
    assert (len(registry.entities), len(registry.devices), len(registry.areas)) == (15, 15, 5)
    lamp = registry.entities["switch.living_floor_lamp"]
    assert (lamp.unique_id, lamp.device_id, lamp.area_id, lamp.platform, lamp.disabled) == (
        "shelly-living_floor_lamp",
        "dev_living_lamp",
        None,
        "shelly",
        False,
    )
    assert registry.effective_area("switch.living_floor_lamp") == "area_living"  # its device's
    assert registry.effective_area("light.room_203_ceiling") == "area_203"
    assert registry.effective_area("light.nope") is None
    assert registry.devices["dev_living_lamp"].name == "Living room floor lamp"
    assert registry.areas["area_hall"].name == "Hallway"


async def test_state_changes_update_the_snapshot_and_reach_the_handler(
    sim: SimHandle, caplog: pytest.LogCaptureFixture
) -> None:
    async with started(sim.ws_url) as (client, seen):
        seen.fail_first_event = True  # a failing handler is logged, the reader carries on
        await sim.control.post("state/light.hallway", {"state": "off"})
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        await until(lambda: len(seen.events) == 2)
        hallway, outdoor = client.state("light.hallway"), client.state("sensor.outdoor_temp")
    change = seen.events[0]
    assert change.entity_id == "light.hallway" and change.new_state == hallway
    assert change.old_state is not None and change.old_state.state == "on"
    assert hallway is not None and hallway.state == "off"
    assert change.context_id == hallway.context_id and change.time_fired.utcoffset() == timedelta(0)
    assert outdoor is not None and outdoor.state == "13.0"
    assert "state_changed handler failed" in caplog.text


async def test_call_service_returns_the_context_and_the_state_follows(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, seen):
        data = {"brightness_pct": 40}
        result = await client.call_service("light", "turn_on", "light.room_203_ceiling", data)
        await until(lambda: seen.events)
        ceiling = client.state("light.room_203_ceiling")
    [sent] = await sim.control.get("service-calls")
    assert (sent["entity_id"], sent["data"]) == ("light.room_203_ceiling", {"brightness_pct": 40})
    assert result.context_id == sent["context_id"] == seen.events[0].context_id
    assert ceiling is not None and ceiling.attributes["brightness"] == 102


async def test_refused_service_call_raises_service_error(sim: SimHandle) -> None:
    await sim.control.post("fail-next-service", {"error": "service_not_found"})
    async with started(sim.ws_url) as (client, _):
        with pytest.raises(HAServiceError) as refused:
            await client.call_service("switch", "turn_on", "switch.living_floor_lamp", {})
        assert client.connected  # a refused call says nothing about the connection
    assert (refused.value.code, refused.value.message) == (
        "service_not_found",
        "Simulated service failure",
    )


async def test_reconnects_after_the_connection_drops(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, seen):
        dropped = asyncio.get_running_loop().time()
        await sim.control.post("drop-connections")
        await until(lambda: not client.connected)
        # The stale snapshot stays readable; commands fail at once instead of waiting.
        assert client.state("light.living_ceiling") is not None and client.adapter_locked
        async with asyncio.timeout(0.1):
            with pytest.raises(HAUnavailable):
                await client.call_service("light", "turn_off", "light.hallway", {})
        await until(lambda: client.connected)
        assert asyncio.get_running_loop().time() - dropped < 3
        assert client.generation == 2 and not client.adapter_locked
        await sim.control.post("state/light.hallway", {"state": "off"})  # subscribed again
        await until(lambda: seen.events)
    statuses = [(status.connected, status.generation) for status in seen.statuses]
    assert statuses == [(True, 1), (False, 1), (True, 2)]
    assert seen.locked == [False, True, False]
    assert "1012" in (seen.statuses[1].reason or "")


async def test_auth_list_returns_the_users(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, _):
        users = await client.auth_list()
    assert [user.id for user in users] == ["owner1", "user1", "sup"]
    owner, user, supervisor = users
    assert owner.is_owner and owner.is_active and owner.group_ids == ("system-admin",)
    assert (owner.name, owner.username, owner.system_generated) == ("Owner", "owner", False)
    assert not user.is_owner and user.group_ids == ("system-users",)
    assert supervisor.system_generated and supervisor.username is None


@pytest.mark.parametrize("mode", ["schema_missing_groups", "error"])
async def test_auth_list_failure_is_a_protocol_error(sim: SimHandle, mode: str) -> None:
    await sim.control.post("auth/list-mode", {"mode": mode})
    async with started(sim.ws_url) as (client, _):
        with pytest.raises(HAProtocolError):
            await client.auth_list()
        assert client.connected


async def test_wrong_token_reports_auth_invalid_and_never_logs_it(
    sim: SimHandle, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="myha_gateway")
    async with started(sim.ws_url, token="wrong-secret-7f3a") as (client, seen):
        assert not client.connected and client.generation == 0 and client.adapter_locked
        assert seen.statuses == [ConnectionStatus(False, 0, "auth_invalid")]
        async with asyncio.timeout(0.1):  # at once, not after a command timeout
            with pytest.raises(HAUnavailable):
                await client.call_service("light", "turn_on", "light.hallway", {})
            with pytest.raises(HAUnavailable):
                await client.auth_list()
    assert client.all_states() == {}
    assert "rejected the access token" in caplog.text and "wrong-secret-7f3a" not in caplog.text


async def test_registry_follows_renames_and_area_changes(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, _):
        rename = {"entity_id": "light.hallway", "new_entity_id": "light.hall_ceiling"}
        await sim.control.post("registry/rename", rename)
        await until(lambda: "light.hall_ceiling" in client.registry().entities, seconds=1)
        entities = client.registry().entities
        assert entities["light.hall_ceiling"].unique_id == "shelly-hallway"
        assert "light.hallway" not in entities and client.state("light.hallway") is None
        assert client.state("light.hall_ceiling") is not None
        move = {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
        await sim.control.post("registry/area", move)
        await until(lambda: client.registry().devices["dev_living_lamp"].area_id == "area_kitchen")
        assert client.registry().effective_area("switch.living_floor_lamp") == "area_kitchen"


async def test_event_skew_compares_time_fired_with_the_clock(sim: SimHandle) -> None:
    await sim.control.post("clock-skew", {"seconds": 120})

    def clock() -> datetime:  # 20 s ahead as well: the skew uses the injected clock
        return datetime.now(UTC) + timedelta(seconds=20)

    async with started(sim.ws_url, clock=clock) as (client, seen):
        assert client.last_event_skew_seconds() is None
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        await until(lambda: seen.events)
    skew = client.last_event_skew_seconds()
    assert skew is not None and 99 < skew <= 100


async def test_unanswered_call_times_out_but_keeps_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ha_client, "SERVICE_TIMEOUT", 0.2)
    async with fake_core() as url, started(url) as (client, _):
        with pytest.raises(HAUnavailable, match="no answer within 0.2 s"):
            await client.call_service("light", "turn_on", "light.any", {})
        assert client.connected


async def test_waiting_call_fails_at_once_when_the_connection_drops() -> None:
    async with fake_core(drop_on_service=True) as url, started(url) as (client, _):
        async with asyncio.timeout(2):  # long before the 15 s service timeout
            with pytest.raises(HAUnavailable, match="lost"):
                await client.call_service("light", "turn_on", "light.any", {})


async def test_unit_other_than_celsius_keeps_adapters_locked() -> None:
    async with fake_core(unit="°F") as url, started(url) as (client, seen):
        assert client.connected and client.adapter_locked and client.temperature_unit == "°F"
        assert seen.locked == [True]


async def test_admin_lifespan_runs_the_ha_client(
    sim: SimHandle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_HA_WS_URL", sim.ws_url)
    monkeypatch.setenv("SUPERVISOR_TOKEN", TOKEN)
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        await until(lambda: ctx.ha.connected)
        probe = (await call(app, "GET", "/api/v1/admin/probe")).json()
    assert (probe["ha_connected"], probe["ha_generation"]) == (True, 1)
    assert not ctx.ha.connected  # stopped with the admin app


async def test_admin_app_serves_while_home_assistant_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_HA_WS_URL", "ws://127.0.0.1:1/api/websocket")  # refused
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        r = await call(app, "GET", "/api/v1/admin/probe")
    assert r.status_code == 200
    assert (r.json()["ha_connected"], r.json()["ha_generation"]) == (False, 0)
