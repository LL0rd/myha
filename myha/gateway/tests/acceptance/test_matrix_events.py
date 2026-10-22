"""A19, A20, A25, A52 and the event protocol of one client connection (design §4.3).

The connection runs on the real loader, bus and HA client against the simulator; the
transport is a fake with Starlette's ``WebSocket`` methods, the ticket check a stand-in.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, update

from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.events import connection
from myha_gateway.events.bus import CommandUpdated, Overflow, PolicyChanged
from myha_gateway.events.connection import ClientConnection
from myha_gateway.ha.client import utcnow
from myha_gateway.services.sessions import revoke_session, revoke_stay_sessions
from myha_simulator import fixtures
from tests.helpers import SimHandle
from tests.policy_world import GUEST_SESSION, RESIDENT_SESSION, policy_version, world

# A copy of the wire contract (web/src/api/schema.ts, events): every key of every message.
CONTRACT = {
    "snapshot": {"type", "generation", "sequence", "policy_version", "dashboard"},
    "resource.updated": {"type", "sequence", "room_id", "resource"},
    "resource.removed": {"type", "sequence", "resource_id"},
    "policy.changed": {"type", "policy_version"},
    "session.revoked": {"type", "reason"},
    "connection.status": {"type", "status"},
    "command.updated": {"type", "command"},
}
COMMAND = {"id", "resource_id", "action", "status", "created_at", "updated_at", "public_code"}
CLOSED = "closed"


class FakeTransport:
    """The client end: ``inbox`` holds its frames (``None`` leaves), ``outbox`` what it got."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.outbox: asyncio.Queue[Any] = asyncio.Queue()
        self.close_code: int | None = None

    async def receive_text(self) -> str:
        frame = await self.inbox.get()
        if frame is None:
            raise ConnectionResetError("client left")
        return frame

    async def send_text(self, data: str) -> None:
        assert self.close_code is None, "sent after close"
        self.outbox.put_nowait(json.loads(data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_code = code
        self.outbox.put_nowait(CLOSED)

    async def next(self, seconds: float = 5) -> Any:
        async with asyncio.timeout(seconds):
            message = await self.outbox.get()
        if message != CLOSED:
            assert set(message) <= CONTRACT[message["type"]], message
            assert_no_entity_id(json.dumps(message))
        return message


def assert_no_entity_id(text: str) -> None:
    for entity in fixtures.ENTITIES:
        assert entity["entity_id"] not in text


async def authenticate(ticket: str) -> str | None:
    """Stand-in for the ticket check: ``ticket-<session id>``."""
    return ticket.removeprefix("ticket-") if ticket.startswith("ticket-") else None


@pytest.fixture
async def app(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    async with world(sim, tmp_path) as ctx:
        yield ctx


@asynccontextmanager
async def connect(
    app: AppContext, session_id: str = GUEST_SESSION, first: str | None = None
) -> AsyncIterator[FakeTransport]:
    """A running connection; the client sends ``first`` or the session's ticket, then leaves."""
    transport = FakeTransport()
    frame = json.dumps({"type": "auth", "ticket": f"ticket-{session_id}"})
    transport.inbox.put_nowait(frame if first is None else first)
    task = asyncio.create_task(ClientConnection(app, transport, authenticate).run())
    try:
        yield transport
    finally:
        transport.inbox.put_nowait(None)
        async with asyncio.timeout(5):
            await task  # also after a close by the server: run() ends without an error


def rooms(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    dashboard = snapshot["dashboard"]
    return {room["id"]: [res["id"] for res in room["resources"]] for room in dashboard["rooms"]}


async def set_session(app: AppContext, session_id: str, **values: Any) -> None:
    async with write_session(app.engine) as session:
        await session.execute(update(db.Session).where(db.Session.id == session_id).values(values))


async def test_snapshot_generation_and_sequence(app: AppContext, sim: SimHandle) -> None:
    async with connect(app) as client:
        snapshot = await client.next()
        assert snapshot["type"] == "snapshot"
        assert (snapshot["generation"], snapshot["sequence"]) == (app.ha.generation, 0)
        assert snapshot["policy_version"] == await policy_version(app.engine)
        assert snapshot["dashboard"]["status"] == "online"
        assert list(rooms(snapshot)) == ["unit_203"]
        await sim.control.post("state/light.room_203_ceiling", {"state": "off"})
        await sim.control.post("state/light.room_203_ceiling", {"state": "on"})
        updates = [await client.next(), await client.next()]
    assert [(m["type"], m["sequence"], m["room_id"]) for m in updates] == [
        ("resource.updated", 1, "unit_203"),
        ("resource.updated", 2, "unit_203"),
    ]
    assert [m["resource"]["state"]["power"] for m in updates] == ["off", "on"]
    assert updates[0]["resource"]["id"] == "res_203_light"


async def test_a19_foreign_room_event_not_delivered(app: AppContext, sim: SimHandle) -> None:
    async with connect(app) as client:
        await client.next()
        await sim.control.post("state/light.living_ceiling", {"state": "off"})  # living room
        await sim.control.post("state/light.room_203_bedside", {"state": "off"})
        message = await client.next()  # events arrive in order: the living room's came first
        assert (message["sequence"], message["resource"]["id"]) == (1, "res_203_bedside")
        assert client.outbox.empty()


async def test_a20_role_change_during_connection(app: AppContext) -> None:
    async with write_session(app.engine) as session:  # the family group also gets room 203
        query = update(db.Assignment).where(db.Assignment.id == "asg_family")
        await session.execute(query.values(unit_ids=["unit_203", "unit_lr"]))
    async with connect(app, RESIDENT_SESSION) as client:
        assert "res_203_light" in rooms(await client.next())["unit_203"]
        async with write_session(app.engine) as session:
            await session.execute(delete(db.Assignment).where(db.Assignment.id == "asg_family"))
            await session.execute(update(db.Instance).values(policy_version=7))
        app.bus.publish(PolicyChanged(7))
        assert await client.next() == {"type": "policy.changed", "policy_version": 7}
        snapshot = await client.next()
    assert (snapshot["type"], snapshot["sequence"], snapshot["policy_version"]) == (
        "snapshot",
        0,
        7,
    )
    assert rooms(snapshot) == {}


async def test_a25_a52_revocation_closes_within_5s(app: AppContext) -> None:
    async with connect(app) as guest, connect(app, RESIDENT_SESSION) as resident:
        await guest.next()
        await resident.next()
        await revoke_stay_sessions(app, "stay_203", "checkout", actor="admin:test")
        assert await guest.next() == {"type": "session.revoked", "reason": "checkout"}
        assert (await guest.next(), guest.close_code) == (CLOSED, 4401)
        assert resident.outbox.empty()  # another principal's connection stays
        await revoke_session(app, RESIDENT_SESSION, "revoked", actor="admin:test")
        assert await resident.next() == {"type": "session.revoked", "reason": "revoked"}
        assert (await resident.next(), resident.close_code) == (CLOSED, 4401)


async def test_revocation_without_event_closes_at_the_next_check(
    app: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connection, "RECHECK_SECONDS", 0.2)
    async with connect(app) as client:
        await client.next()
        await set_session(app, GUEST_SESSION, revoked_at=utcnow())  # past the bus
        assert await client.next() == {"type": "session.revoked", "reason": "revoked"}
        assert (await client.next(), client.close_code) == (CLOSED, 4401)


async def test_policy_change_without_event_sends_a_snapshot_at_the_next_check(
    app: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connection, "RECHECK_SECONDS", 0.2)
    async with connect(app) as client:
        await client.next()
        async with write_session(app.engine) as session:  # another process, such as dev-seed
            await session.execute(update(db.Instance).values(policy_version=9))
        assert await client.next() == {"type": "policy.changed", "policy_version": 9}
        assert (await client.next())["policy_version"] == 9


async def test_expiry_timer_closes_at_not_after(app: AppContext) -> None:
    not_after = utcnow() + timedelta(seconds=0.5)
    await set_session(app, GUEST_SESSION, not_after=not_after)
    async with connect(app) as client:
        assert (await client.next())["dashboard"]["status"] == "online"
        assert await client.next(2) == {"type": "session.revoked", "reason": "expired"}
        assert utcnow() >= not_after
        assert (await client.next(), client.close_code) == (CLOSED, 4401)


async def test_waiting_guest_gets_a_snapshot_when_the_stay_starts(app: AppContext) -> None:
    async with write_session(app.engine) as session:
        starts_at = utcnow() + timedelta(seconds=0.4)
        await session.execute(update(db.Stay).values(starts_at=starts_at))
    async with connect(app) as client:
        assert (await client.next())["dashboard"] == {"status": "waiting", "rooms": []}
        snapshot = await client.next(2)
    assert (snapshot["dashboard"]["status"], list(rooms(snapshot))) == ("online", ["unit_203"])


async def test_auth_timeout_closes(app: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection, "AUTH_SECONDS", 0.1)
    transport = FakeTransport()  # sends nothing
    async with asyncio.timeout(2):
        await ClientConnection(app, transport, authenticate).run()
    assert (await transport.next(), transport.close_code) == (CLOSED, 4401)


@pytest.mark.parametrize(
    "first",
    [
        json.dumps({"type": "auth", "ticket": "forged"}),
        json.dumps({"type": "hello", "ticket": f"ticket-{GUEST_SESSION}"}),
        json.dumps({"type": "auth", "ticket": 7}),
        "not json",
        json.dumps({"type": "auth", "ticket": "ticket-" + "x" * connection.MAX_FRAME}),
    ],
)
async def test_auth_needs_a_valid_ticket_in_the_first_frame(app: AppContext, first: str) -> None:
    async with connect(app, first=first) as client:
        assert (await client.next(), client.close_code) == (CLOSED, 4401)


async def test_ha_reconnect_sends_a_fresh_snapshot(app: AppContext, sim: SimHandle) -> None:
    async with connect(app) as client:
        assert (await client.next())["generation"] == 1
        await sim.control.post("drop-connections")
        assert await client.next() == {"type": "connection.status", "status": "stale"}
        assert await client.next() == {"type": "connection.status", "status": "online"}
        snapshot = await client.next()
    assert (snapshot["type"], snapshot["generation"], snapshot["sequence"]) == ("snapshot", 2, 0)


async def test_command_updates_go_to_their_session_only(app: AppContext) -> None:
    command = {
        "id": "cmd_1",
        "resource_id": "res_203_light",
        "action": "light.on",
        "status": "confirmed",
        "created_at": "2026-10-22T12:00:00Z",
        "updated_at": "2026-10-22T12:00:01Z",
        "public_code": None,
    }
    async with connect(app) as client:
        await client.next()
        app.bus.publish(CommandUpdated(RESIDENT_SESSION, command))
        app.bus.publish(CommandUpdated(GUEST_SESSION, command))
        message = await client.next()
    assert message == {"type": "command.updated", "command": command}
    assert set(message["command"]) == COMMAND


async def test_hidden_resource_is_removed(
    app: AppContext, sim: SimHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with connect(app) as client:
        await client.next()
        monkeypatch.setattr(connection, "project_resource", lambda *args: None)
        await sim.control.post("state/light.room_203_ceiling", {"state": "off"})
        await sim.control.post("state/light.room_203_ceiling", {"state": "on"})  # gone already
        await sim.control.post("state/light.room_203_bedside", {"state": "off"})
        removed = [await client.next(), await client.next()]
    assert removed == [
        {"type": "resource.removed", "sequence": 1, "resource_id": "res_203_light"},
        {"type": "resource.removed", "sequence": 2, "resource_id": "res_203_bedside"},
    ]


async def test_overflow_closes_with_1013(app: AppContext) -> None:
    async with connect(app) as client:
        await client.next()
        app.bus.publish(Overflow())
        assert (await client.next(), client.close_code) == (CLOSED, 1013)


async def test_oversized_frame_closes_with_1009(app: AppContext) -> None:
    async with connect(app) as client:
        await client.next()
        client.inbox.put_nowait("x" * (connection.MAX_FRAME + 1))
        assert (await client.next(), client.close_code) == (CLOSED, 1009)
