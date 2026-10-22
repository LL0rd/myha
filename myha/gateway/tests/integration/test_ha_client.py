"""HAClient against the simulator and an in-test fake Core: snapshot, events, services,
reconnect, keepalive, registries, config, users, and its place in the admin app's lifespan."""

import asyncio
import contextlib
import itertools
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from websockets import ConnectionClosed, ServerConnection, serve

from myha_gateway.app import create_admin_app
from myha_gateway.deps import AppContext
from myha_gateway.ha import client as ha_client
from myha_gateway.ha.client import HAClient, HAProtocolError, HAServiceError, HAUnavailable
from myha_gateway.ha.messages import ConnectionStatus, StateChanged
from tests.helpers import SIM_TOKEN, SimHandle, fake_supervisor

STATE = {
    "entity_id": "light.a",
    "state": "on",
    "attributes": {},
    "last_changed": "2026-10-22T10:00:00+00:00",
    "last_updated": "2026-10-22T10:00:00+00:00",
    "context": {"id": "c1"},
}
BROKEN = {"entity_id": "light.b"}  # no state, attributes or timestamps


class Seen:
    """What a client reported; ``locked`` is its adapter_locked at each status report."""

    def __init__(self, raises: type[BaseException] | None) -> None:
        self.events: list[StateChanged] = []
        self.statuses: list[ConnectionStatus] = []
        self.locked: list[bool] = []
        self.client: HAClient | None = None
        self.raises = raises  # both handlers raise it after recording

    async def on_event(self, change: StateChanged) -> None:
        self.events.append(change)
        if self.raises is not None:
            raise self.raises("handler bug")

    async def on_status(self, status: ConnectionStatus) -> None:
        assert self.client is not None
        self.statuses.append(status)
        self.locked.append(self.client.adapter_locked)
        if self.raises is not None:
            raise self.raises("handler bug")


@asynccontextmanager
async def started(
    url: str, token: str = SIM_TOKEN, raises: type[BaseException] | None = None, **kwargs: Any
) -> AsyncIterator[tuple[HAClient, Seen]]:
    """A started client; afterwards it is stopped and none of its tasks may be left."""
    seen = Seen(raises)
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


class FakeCore:
    """A Core that answers the client's setup, counts commands and fires events on demand.

    Service calls stay unanswered or drop the connection; pings may stay unanswered too.
    """

    def __init__(
        self,
        *,
        unit: str = "°C",
        states: list[Any] | None = None,
        answer_pings: bool = True,
        drop_on_service: bool = False,
    ) -> None:
        self.unit, self.states = unit, [] if states is None else states
        self.answer_pings, self.drop_on_service = answer_pings, drop_on_service
        self.calls: Counter[str] = Counter()
        self.subscriptions: dict[str, int] = {}  # event type -> subscribe id, latest connection
        self.close_codes: list[int | None] = []
        self.ws: ServerConnection | None = None
        self.url = ""

    async def handler(self, ws: ServerConnection) -> None:
        self.ws = ws
        with contextlib.suppress(ConnectionClosed):  # the client may leave at any time
            await ws.send(json.dumps({"type": "auth_required"}))
            await ws.recv()
            await ws.send(json.dumps({"type": "auth_ok"}))
            async for raw in ws:
                await self.answer(ws, json.loads(raw))
        self.close_codes.append(ws.close_code)

    async def answer(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self.calls[msg["type"]] += 1
        if msg["type"] == "ping":
            if self.answer_pings:
                await ws.send(json.dumps({"id": msg["id"], "type": "pong"}))
            return
        if msg["type"] == "call_service":
            if self.drop_on_service:
                await ws.close(1012)
            return  # never answered
        if msg["type"] == "subscribe_events":
            self.subscriptions[msg["event_type"]] = msg["id"]
        config = {"unit_system": {"temperature": self.unit}}
        result = {"get_states": self.states, "get_config": config}.get(msg["type"], [])
        reply = {"id": msg["id"], "type": "result", "success": True, "result": result}
        await ws.send(json.dumps(reply))

    async def fire(self, event_type: str) -> None:
        assert self.ws is not None
        time_fired = datetime.now(UTC).isoformat()
        event = {"event_type": event_type, "data": {}, "time_fired": time_fired, "context": {}}
        message = {"id": self.subscriptions[event_type], "type": "event", "event": event}
        await self.ws.send(json.dumps(message))


@asynccontextmanager
async def fake_core(**options: Any) -> AsyncIterator[FakeCore]:
    core = FakeCore(**options)
    async with serve(core.handler, "127.0.0.1", 0) as server:
        core.url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        yield core


def test_reconnect_delays_double_up_to_30_s_with_jitter() -> None:
    delays = list(itertools.islice(ha_client._backoff(), 2000))  # a long outage stays at 30 s
    for delay, base in zip(delays, [1, 2, 4, 8, 16, 30], strict=False):
        assert 0.8 * base <= delay <= min(1.2 * base, 30)
    assert all(24 <= delay <= 30 for delay in delays[5:])


async def test_start_loads_snapshot_registries_and_config(sim: SimHandle) -> None:
    async with started(sim.ws_url) as (client, seen):
        assert client.connected and not client.adapter_locked
        states, registry = client.all_states(), client.registry()
        ceiling = client.state("light.living_ceiling")
    assert len(states) == 15 and ceiling is not None and states["light.living_ceiling"] == ceiling
    assert ceiling.state == "on" and ceiling.attributes["brightness"] == 140
    assert ceiling.last_changed.utcoffset() == timedelta(0) and ceiling.context_id
    assert (client.generation, client.temperature_unit, client.registry_version) == (1, "°C", 1)
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
    async with started(sim.ws_url, raises=RuntimeError) as (client, seen):
        await sim.control.post("state/light.hallway", {"state": "off"})
        await sim.control.post("state/sensor.outdoor_temp", {"state": "13.0"})
        await until(lambda: len(seen.events) == 2)  # a failing handler stops nothing
        hallway, outdoor = client.state("light.hallway"), client.state("sensor.outdoor_temp")
    change = seen.events[0]
    assert change.entity_id == "light.hallway" and change.new_state == hallway
    assert change.old_state is not None and change.old_state.state == "on"
    assert hallway is not None and hallway.state == "off"
    assert change.context_id == hallway.context_id and change.time_fired.utcoffset() == timedelta(0)
    assert outdoor is not None and outdoor.state == "13.0"
    assert "state_changed handler failed" in caplog.text


async def test_handlers_raising_cancelled_error_do_not_stop_the_client(
    sim: SimHandle, caplog: pytest.LogCaptureFixture
) -> None:
    async with started(sim.ws_url, raises=asyncio.CancelledError) as (client, seen):
        assert client.connected
        await sim.control.post("state/light.hallway", {"state": "off"})
        await sim.control.post("state/light.hallway", {"state": "on"})
        await until(lambda: len(seen.events) == 2)  # the reader outlived the first handler
        await sim.control.post("drop-connections")
        await until(lambda: client.generation == 2 and client.connected)
    assert [status.connected for status in seen.statuses] == [True, False, True]
    assert "state_changed handler was cancelled" in caplog.text
    assert "connection status handler was cancelled" in caplog.text


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


async def test_core_answers_the_keepalive_ping(
    sim: SimHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ha_client, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(ha_client, "PING_TIMEOUT", 0.2)
    async with started(sim.ws_url) as (client, seen):
        await asyncio.sleep(0.5)  # about ten pings, each answered with a pong in time
        assert client.connected
    assert seen.statuses == [ConnectionStatus(True, 1, None)]


async def test_unanswered_ping_counts_as_a_dead_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ha_client, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(ha_client, "PING_TIMEOUT", 0.1)
    async with fake_core(answer_pings=False) as core, started(core.url) as (client, seen):
        async with asyncio.timeout(1):
            while client.connected:  # noqa: ASYNC110 (catch the very moment the socket goes)
                await asyncio.sleep(0)
        assert client.generation == 1 and len(seen.statuses) == 1  # before status and reconnect
        await until(lambda: client.generation == 2)
    assert seen.statuses[:3] == [
        ConnectionStatus(True, 1, None),
        ConnectionStatus(False, 1, "ping_timeout"),
        ConnectionStatus(True, 2, None),
    ]
    assert core.calls["ping"] >= 1


async def test_stop_interrupts_a_handler_that_takes_long(sim: SimHandle) -> None:
    handling = asyncio.Event()

    async def slow(change: StateChanged) -> None:
        handling.set()
        await asyncio.sleep(3600)

    async def ignore(status: ConnectionStatus) -> None:
        pass

    client = HAClient(sim.ws_url, SIM_TOKEN, on_event=slow, on_status=ignore)
    before = asyncio.all_tasks()
    await client.start()
    await sim.control.post("state/light.hallway", {"state": "off"})
    async with asyncio.timeout(3):
        await handling.wait()
    async with asyncio.timeout(1):  # the handler's cancellation is passed on, not swallowed
        await client.stop()
    assert not [t for t in asyncio.all_tasks() - before if t.get_name().startswith("ha-client")]


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
    caplog.set_level(logging.DEBUG)  # the root logger: websockets would log every frame
    token = "wrong-secret-7f3a9c"
    async with started(sim.ws_url, token=token) as (client, seen):
        assert not client.connected and client.generation == 0 and client.adapter_locked
        assert seen.statuses == [ConnectionStatus(False, 0, "auth_invalid")]
        async with asyncio.timeout(0.1):  # at once, not after a command timeout
            with pytest.raises(HAUnavailable):
                await client.call_service("light", "turn_on", "light.hallway", {})
            with pytest.raises(HAUnavailable):
                await client.auth_list()
    assert client.all_states() == {}
    # The simulator's uvicorn logs the frames it receives; the client side must log none.
    client_side = [r.getMessage() for r in caplog.records if not r.name.startswith("uvicorn")]
    assert any("rejected the access token" in message for message in client_side)
    assert not any(token[:9] in message for message in client_side)


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


async def test_a_burst_of_registry_events_reloads_once() -> None:
    async with fake_core() as core, started(core.url, registry_debounce=0.1) as (client, _):
        for _ in range(5):
            await core.fire("entity_registry_updated")
        await until(lambda: client.registry_version == 2)
        await asyncio.sleep(0.3)  # three debounce periods later, still one reload
    assert core.calls["config/entity_registry/list"] == 2 and client.registry_version == 2


@pytest.mark.parametrize("event_type", ha_client.REGISTRY_EVENTS)
async def test_every_registry_event_reloads_the_registries(event_type: str) -> None:
    async with fake_core() as core, started(core.url, registry_debounce=0.05) as (client, _):
        await core.fire(event_type)
        await until(lambda: client.registry_version == 2)
    assert core.calls["config/area_registry/list"] == 2


async def test_registries_also_reload_periodically() -> None:
    async with fake_core() as core, started(core.url, registry_refresh=0.3) as (client, _):
        await until(lambda: client.registry_version >= 4, seconds=1.5)  # three reloads
    assert core.calls["config/entity_registry/list"] >= 4


async def test_temperature_unit_decides_the_adapter_lock_also_at_runtime() -> None:
    async with fake_core(unit="°F") as core, started(core.url) as (client, seen):
        assert client.connected and client.adapter_locked and client.temperature_unit == "°F"
        core.unit = "°C"
        await core.fire("core_config_updated")
        await until(lambda: not client.adapter_locked)
        core.unit = "°F"
        await core.fire("core_config_updated")
        await until(lambda: client.adapter_locked and client.temperature_unit == "°F")
    assert seen.locked == [True] and core.calls["get_config"] == 3


async def test_config_change_announced_during_get_config_keeps_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = Seen(None)
    client = HAClient("ws://unused", SIM_TOKEN, on_event=seen.on_event, on_status=seen.on_status)

    async def answer_while_a_change_is_announced(message: dict[str, Any]) -> Any:
        client._config_dirty.set()  # what the reader does on core_config_updated
        return {"unit_system": {"temperature": "°C"}}

    monkeypatch.setattr(client, "_result", answer_while_a_change_is_announced)
    await client._load_config()
    assert client.temperature_unit == "°C" and client.adapter_locked


async def test_snapshot_without_a_valid_state_is_no_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with fake_core(states=[BROKEN] * 200) as core, started(core.url) as (client, seen):
        assert not client.connected and client.all_states() == {} and client.adapter_locked
    reason = seen.statuses[0].reason or ""
    assert "get_states failed: malformed" in reason and "200 of 200 states malformed" in reason
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)  # no warning per state


async def test_malformed_states_are_skipped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with fake_core(states=[STATE, BROKEN, BROKEN]) as core, started(core.url) as (client, _):
        assert client.connected and list(client.all_states()) == ["light.a"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warnings[0].startswith("2 of 3 states malformed, first: ")


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
    async with fake_core() as core, started(core.url) as (client, _):
        with pytest.raises(HAUnavailable, match="no answer within 0.2 s"):
            await client.call_service("light", "turn_on", "light.any", {})
        assert client.connected


async def test_waiting_call_fails_at_once_when_the_connection_drops() -> None:
    async with fake_core(drop_on_service=True) as core, started(core.url) as (client, _):
        async with asyncio.timeout(2):  # long before the 15 s service timeout
            with pytest.raises(HAUnavailable, match="lost"):
                await client.call_service("light", "turn_on", "light.any", {})


async def test_stop_closes_normally_and_a_new_start_connects_again() -> None:
    async with fake_core() as core:
        async with started(core.url) as (client, _):
            pass
        await until(lambda: core.close_codes)
        await client.start()  # waits for its own first attempt again
        assert client.connected and client.generation == 2
        await client.stop()
        await until(lambda: len(core.close_codes) == 2)
    assert core.close_codes == [1000, 1000]


async def test_admin_lifespan_runs_the_ha_client(
    sim: SimHandle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_HA_WS_URL", sim.ws_url)
    monkeypatch.setenv("SUPERVISOR_TOKEN", SIM_TOKEN)
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        await until(lambda: ctx.ha.connected)
        assert (ctx.ha.connected, ctx.ha.generation) == (True, 1)
    assert not ctx.ha.connected  # stopped with the admin app


async def test_admin_app_starts_while_home_assistant_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYHA_HA_WS_URL", "ws://127.0.0.1:1/api/websocket")  # refused
    ctx = await AppContext.create()
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with asyncio.timeout(2), app.router.lifespan_context(app):  # not held up by HA
        assert not ctx.ha.connected and ctx.ha.generation == 0
