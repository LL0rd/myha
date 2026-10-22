"""A07, A24, A31, A32 and the command lifecycle through the public API (design §4.5).

Each test drives ``POST /api/v1/actions`` against the simulator and waits for the background
send with ``commands.settle()``; the simulator's service-call log shows what reached Core.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from myha_gateway.app import create_admin_app
from myha_gateway.db import bump_policy_version, write_session
from myha_gateway.db import models as db
from myha_gateway.deps import AppContext
from myha_gateway.events.bus import CommandUpdated, Event, PolicyChanged
from myha_gateway.services import audit as audit_module
from myha_gateway.services import commands
from myha_gateway.services.sessions import revoke_session
from tests import public_helpers
from tests.helpers import SimHandle
from tests.public_helpers import Guest, World, browser_guest, change, new_stay, now, rows

ctx, world = public_helpers.ctx, public_helpers.world  # the fixtures
ACTIONS = "/api/v1/actions"
LIGHT = "light.room_203_ceiling"
TRV = "climate.room_203_trv"


@pytest.fixture
async def guest(world: World) -> AsyncIterator[tuple[httpx.AsyncClient, Guest]]:
    """A browser guest of room 203 on the public app with the command routes."""
    async with world.client() as client:
        yield client, await browser_guest(world, client, world.secrets[0])
    await commands.settle()


async def act(
    client: httpx.AsyncClient,
    guest: Guest,
    resource_id: str,
    action: str,
    parameters: dict[str, Any] | None = None,
    key: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"resource_id": resource_id, "action": action}
    if parameters is not None:
        body["parameters"] = parameters
    headers = guest.headers(**{"Idempotency-Key": key or str(uuid.uuid4())})
    return await client.post(ACTIONS, json=body, headers=headers)


async def accepted(r: httpx.Response) -> str:
    """The command id of a 202, after its send and observation ended."""
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "accepted"
    await commands.settle()
    return str(r.json()["command_id"])


async def command(client: httpx.AsyncClient, guest: Guest, command_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/v1/commands/{command_id}", headers=guest.headers())
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def refused(r: httpx.Response, status: int, code: str) -> None:
    assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text


async def service_calls(sim: SimHandle) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = await sim.control.get("service-calls")
    return calls


async def audit_rows(world: World, operation: str) -> list[db.AuditEvent]:
    query = select(db.AuditEvent).where(db.AuditEvent.operation == operation)
    async with world.ctx.sessions() as session:
        return list((await session.execute(query.order_by(db.AuditEvent.id))).scalars())


def drained(queue: asyncio.Queue[Event]) -> list[Event]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def test_a31_idempotency_same_and_different_body(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    await sim.control.post("service-calls/reset")
    key = str(uuid.uuid4()).upper()  # the canonical lowercase form is the same key
    first = await accepted(
        await act(client, me, "res_203_light", "light.set_level", {"level_pct": 40}, key)
    )
    again = await act(
        client, me, "res_203_light", "light.set_level", {"level_pct": 40}, key.lower()
    )
    assert again.status_code == 202
    assert (again.json()["command_id"], again.json()["status"]) == (first, "confirmed")
    other = await act(client, me, "res_203_light", "light.set_level", {"level_pct": 50}, key)
    refused(other, 409, "CONFLICT")
    await commands.settle()
    assert len(await service_calls(sim)) == 1  # no second send
    assert len(await rows(world.ctx, db.Command)) == 1


async def test_a32_restart_marks_inflight_unknown(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    session_id = (await rows(world.ctx, db.Session))[0].id
    at = now()
    async with write_session(world.ctx.engine) as session:
        for n, status in enumerate(("accepted", "sent", "confirmed")):
            session.add(
                db.Command(
                    id=f"cmd_{status}",
                    session_id=session_id,
                    idempotency_key=str(uuid.UUID(int=n)),
                    body_hash="x",
                    resource_id="res_203_light",
                    action="light.on",
                    normalized_params={},
                    policy_version=1,
                    status=status,
                    created_at=at,
                    updated_at=at,
                )
            )
    await sim.control.post("service-calls/reset")
    restarted = await AppContext.create(world.ctx.settings)  # a new process on the same data
    admin = create_admin_app(restarted)
    async with admin.router.lifespan_context(admin):
        assert restarted.ha.connected
    statuses = {row.id: row.status for row in await rows(world.ctx, db.Command)}
    assert statuses == {
        "cmd_accepted": "unknown",
        "cmd_sent": "unknown",
        "cmd_confirmed": "confirmed",
    }
    assert await service_calls(sim) == []  # nothing is sent again
    body = await command(client, me, "cmd_sent")
    assert (body["status"], body["public_code"]) == ("unknown", "UPSTREAM_UNAVAILABLE")
    restarts = [
        row for row in await audit_rows(world, "command.status") if row.reason_code == "restart"
    ]
    assert len(restarts) == 2 and all(row.actor == "system" for row in restarts)


async def test_command_lifecycle_confirmed(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    queue = world.ctx.bus.subscribe()
    try:
        command_id = await accepted(
            await act(client, me, "res_203_light", "light.set_level", {"level_pct": 40})
        )
    finally:
        world.ctx.bus.unsubscribe(queue)
    updates = [event for event in drained(queue) if isinstance(event, CommandUpdated)]
    assert [event.command["status"] for event in updates] == ["accepted", "sent", "confirmed"]
    [session] = await rows(world.ctx, db.Session)
    assert {event.session_id for event in updates} == {session.id}
    last = updates[-1].command
    assert set(last) == {
        "id",
        "resource_id",
        "action",
        "status",
        "created_at",
        "updated_at",
        "public_code",
    }
    assert (last["id"], last["resource_id"], last["action"]) == (
        command_id,
        "res_203_light",
        "light.set_level",
    )
    assert last["public_code"] is None
    assert await command(client, me, command_id) == last
    [row] = await rows(world.ctx, db.Command)
    [call] = await service_calls(sim)
    assert row.ha_context_id == call["context_id"] is not None
    assert row.normalized_params == {"level_pct": 40}
    # another guest's session sees no such command
    async with world.client() as other_client:
        other = await browser_guest(world, other_client, world.secrets[1])
        r = await other_client.get(f"/api/v1/commands/{command_id}", headers=other.headers())
    refused(r, 404, "NOT_FOUND")


async def test_command_confirmed_when_already_in_target_state(
    world: World, guest: tuple[httpx.AsyncClient, Guest], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, me = guest
    await accepted(await act(client, me, "res_203_light", "light.on"))
    # The light is on now: the next light.on changes nothing, the first check confirms it.
    monkeypatch.setattr(commands, "DEFAULT_TIMEOUT", 0.0)
    command_id = await accepted(await act(client, me, "res_203_light", "light.on"))
    assert (await command(client, me, command_id))["status"] == "confirmed"


async def test_command_unknown_when_offline(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    await sim.control.post(f"offline/{LIGHT}")
    command_id = await accepted(await act(client, me, "res_203_light", "light.on"))
    body = await command(client, me, command_id)
    assert (body["status"], body["public_code"]) == ("unknown", "UPSTREAM_UNAVAILABLE")


async def test_command_unknown_after_timeout(
    world: World, guest: tuple[httpx.AsyncClient, Guest], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, me = guest
    monkeypatch.setattr(commands, "DEFAULT_TIMEOUT", 0.05)
    monkeypatch.setattr(commands, "_outcome", lambda expectation, view: None)  # never there
    command_id = await accepted(await act(client, me, "res_203_light", "light.on"))
    body = await command(client, me, command_id)
    assert (body["status"], body["public_code"]) == ("unknown", "UPSTREAM_UNAVAILABLE")
    [row] = await rows(world.ctx, db.Command)
    assert row.reason == "timeout"


async def test_command_failed_on_service_error(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    await sim.control.post("fail-next-service", {"error": "home_assistant_error"})
    command_id = await accepted(await act(client, me, "res_203_light", "light.on"))
    body = await command(client, me, command_id)
    assert (body["status"], body["public_code"]) == ("failed", "UPSTREAM_UNAVAILABLE")
    [row] = await rows(world.ctx, db.Command)
    assert row.reason == "service_error"


async def test_a07_execute_path_rejects_disallowed(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    off_only = [
        {"resources": ["res_203_light"], "actions": ["metadata.read", "state.read"]},
        {"resources": ["res_203_light"], "actions": ["light.off"]},
    ]
    await change(world.ctx, db.RoleVersion, db.RoleVersion.id == "room_guest@1", grants=off_only)
    async with write_session(world.ctx.engine) as session:
        await bump_policy_version(session)
    await sim.control.post("service-calls/reset")
    refused(await act(client, me, "res_203_light", "light.on"), 403, "ACTION_NOT_ALLOWED")
    level = await act(client, me, "res_203_light", "light.set_level", {"level_pct": 40})
    refused(level, 403, "ACTION_NOT_ALLOWED")
    refused(await act(client, me, "res_203_bedside", "light.off"), 403, "ACTION_NOT_ALLOWED")
    refused(await act(client, me, "res_lr_ceiling", "light.off"), 404, "NOT_FOUND")
    await commands.settle()
    assert await service_calls(sim) == []
    assert await rows(world.ctx, db.Command) == []
    await accepted(await act(client, me, "res_203_light", "light.off"))
    assert [call["service"] for call in await service_calls(sim)] == ["turn_off"]


async def test_a24_action_before_stay_start_access_expired(world: World, sim: SimHandle) -> None:
    start = now() + timedelta(hours=2)
    _, [secret] = await new_stay(world.ctx, starts_at=start, ends_at=start + timedelta(days=1))
    await sim.control.post("service-calls/reset")
    async with world.client() as client:
        early = await browser_guest(world, client, secret)
        assert early.body["session"]["waiting"] is True
        r = await act(client, early, "res_203_light", "light.on")
    refused(r, 403, "ACCESS_EXPIRED")
    assert await service_calls(sim) == []


async def test_audit_has_actor_and_effective_principal(
    world: World, guest: tuple[httpx.AsyncClient, Guest]
) -> None:
    client, me = guest
    await accepted(await act(client, me, "res_203_light", "light.on"))
    refused(await act(client, me, "res_203_bedside", "light.on"), 403, "ACTION_NOT_ALLOWED")
    [session] = await rows(world.ctx, db.Session)
    principal = session.principal_id
    statuses = await audit_rows(world, "command.status")
    assert [row.result for row in statuses] == ["accepted", "sent", "confirmed"]
    [denial] = await audit_rows(world, "action.execute")
    for row in [*statuses, denial]:
        assert (row.actor, row.effective_principal, row.session_id) == (
            principal,
            principal,
            session.id,
        )
    assert (denial.result, denial.reason_code, denial.resource_id) == (
        "denied",
        "NO_GRANT",
        "res_203_bedside",
    )


class _Minute(datetime):
    @classmethod
    def now(cls, tz: object = None) -> "_Minute":
        return cls(2026, 10, 22, 12, 0, 30, tzinfo=UTC)


async def test_audit_aggregates_denials(
    world: World, guest: tuple[httpx.AsyncClient, Guest], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, me = guest
    monkeypatch.setattr(audit_module, "datetime", _Minute)  # all in one minute
    for _ in range(3):
        refused(await act(client, me, "res_203_bedside", "light.on"), 403, "ACTION_NOT_ALLOWED")
    refused(await act(client, me, "res_203_bedside", "light.off"), 403, "ACTION_NOT_ALLOWED")
    [denial] = await audit_rows(world, "action.execute")  # same session, operation, reason
    assert denial.aggregate_count == 4


async def test_mode_change_setpoint_jump_marks_needs_review(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle
) -> None:
    client, me = guest
    await sim.control.post(f"state/{TRV}", {"state": "off"})
    jump = {"entity_id": TRV, "mode": "heat", "temperature": 28}  # outside the grant's 19..24
    await sim.control.post("climate/mode-setpoint", jump)
    [before] = [row.policy_version for row in await rows(world.ctx, db.Instance)]
    queue = world.ctx.bus.subscribe()
    try:
        command_id = await accepted(
            await act(client, me, "res_203_trv", "climate.set_mode", {"mode": "heat"})
        )
    finally:
        world.ctx.bus.unsubscribe(queue)
    body = await command(client, me, command_id)
    assert (body["status"], body["public_code"]) == ("failed", "UPSTREAM_UNAVAILABLE")
    [row] = await rows(world.ctx, db.Command)
    assert row.reason == "range_violated"
    bindings = {binding.id: binding.status for binding in await rows(world.ctx, db.ResourceBinding)}
    assert bindings["res_203_trv"] == "needs_review"
    [after] = [row.policy_version for row in await rows(world.ctx, db.Instance)]
    assert after == before + 1
    assert PolicyChanged(after) in drained(queue)
    [flagged] = await audit_rows(world, "binding.needs_review")
    assert (flagged.resource_id, flagged.policy_version) == ("res_203_trv", after)
    assert flagged.normalized_params == {"reason": "range_violated"}
    # the binding needs review now: the next write is refused before anything is sent
    retry = await act(client, me, "res_203_trv", "climate.set_temperature", {"target_c": "21"})
    refused(retry, 503, "UPSTREAM_UNAVAILABLE")


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Holds every background send until the test sets it: room for a change in between."""
    gate = asyncio.Event()
    run = commands._run

    async def held(*args: Any) -> None:
        await gate.wait()
        await run(*args)

    monkeypatch.setattr(commands, "_run", held)
    return gate


async def test_revoked_between_decide_and_send_fails(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle, gate: asyncio.Event
) -> None:
    client, me = guest
    await sim.control.post("service-calls/reset")
    r = await act(client, me, "res_203_light", "light.on")
    assert r.status_code == 202
    [session] = await rows(world.ctx, db.Session)
    await revoke_session(world.ctx, session.id, "revoked", actor="admin:test")
    gate.set()
    await commands.settle()
    [row] = await rows(world.ctx, db.Command)
    assert (row.status, row.reason) == ("failed", "SESSION_INACTIVE")
    assert await service_calls(sim) == []


async def test_policy_change_between_decide_and_send_fails(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle, gate: asyncio.Event
) -> None:
    client, me = guest
    await sim.control.post("service-calls/reset")
    command_id = (await act(client, me, "res_203_light", "light.on")).json()["command_id"]
    async with write_session(world.ctx.engine) as session:
        await bump_policy_version(session)
    gate.set()
    await commands.settle()
    body = await command(client, me, command_id)
    assert (body["status"], body["public_code"]) == ("failed", "UPSTREAM_UNAVAILABLE")
    [row] = await rows(world.ctx, db.Command)
    assert row.reason == "policy_changed"
    assert await service_calls(sim) == []


async def test_ha_disconnect_before_send_fails(
    world: World, guest: tuple[httpx.AsyncClient, Guest], sim: SimHandle, gate: asyncio.Event
) -> None:
    client, me = guest
    await sim.control.post("service-calls/reset")
    assert (await act(client, me, "res_203_light", "light.on")).status_code == 202
    queue = world.ctx.bus.subscribe()
    await sim.control.post("drop-connections")
    async with asyncio.timeout(5):  # the reconnect waits at least 0.8 s
        while getattr(await queue.get(), "status", None) != "offline":
            pass
    world.ctx.bus.unsubscribe(queue)
    gate.set()
    await commands.settle()
    [row] = await rows(world.ctx, db.Command)
    assert (row.status, row.reason) == ("failed", "ha_disconnected")
    assert await service_calls(sim) == []


async def test_decide_error_is_a_500_with_an_audit_row(
    world: World, guest: tuple[httpx.AsyncClient, Guest], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, me = guest

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("policy version mismatch")

    monkeypatch.setattr(commands, "decide", broken)
    refused(await act(client, me, "res_203_light", "light.on"), 500, "UPSTREAM_UNAVAILABLE")
    [row] = await audit_rows(world, "decide_error")
    assert row.session_id is not None and row.resource_id == "res_203_light"
    assert await rows(world.ctx, db.Command) == []


async def test_requests_are_checked_before_deciding(
    world: World, guest: tuple[httpx.AsyncClient, Guest]
) -> None:
    client, me = guest
    body = {"resource_id": "res_203_light", "action": "light.on"}
    r = await client.post(ACTIONS, json=body, headers=me.headers())  # no Idempotency-Key
    refused(r, 400, "INVALID_PARAMETERS")
    refused(
        await act(client, me, "res_203_light", "light.on", key="nope"), 400, "INVALID_PARAMETERS"
    )
    refused(await act(client, me, "res 203", "light.on"), 400, "INVALID_PARAMETERS")
    headers = {
        "Cookie": me.cookie,
        "Origin": public_helpers.ORIGIN,
        "Idempotency-Key": str(uuid.uuid4()),
    }
    refused(await client.post(ACTIONS, json=body, headers=headers), 403, "UNAUTHENTICATED")
    assert await rows(world.ctx, db.Command) == []
