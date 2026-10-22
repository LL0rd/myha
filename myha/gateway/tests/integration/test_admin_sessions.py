"""Admin session list and revoke, and revoking a whole stay (design §4.3, §6)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from myha_gateway.app import create_admin_app
from myha_gateway.auth.admin import AdminGuard
from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.events.bus import Event, SessionRevoked
from myha_gateway.ha.client import utcnow
from myha_gateway.ids import new_id
from myha_gateway.services.sessions import revoke_stay_sessions
from myha_gateway.services.stays import issue_invites
from myha_simulator.fixtures import USERS
from tests import public_helpers
from tests.acceptance.conftest import Clocks
from tests.acceptance.test_matrix_admin import csrf, denied, owner_changed
from tests.helpers import TRUSTED, SimHandle
from tests.public_helpers import World, change, new_stay, redeem, rows

ctx, world = public_helpers.ctx, public_helpers.world  # the fixtures
SESSIONS = "/api/v1/admin/sessions"
FIELDS = {"id", "principal", "audience", "purpose", "created_at", "last_seen_at", "not_after"}
FIELDS |= {"revoked_at", "reason"}


@pytest.fixture
def clocks() -> Clocks:
    return Clocks()


@pytest.fixture
async def admin(world: World, clocks: Clocks) -> AsyncIterator[httpx.AsyncClient]:
    """The owner through the ingress; the role cache runs on the test's clock."""
    app: FastAPI = create_admin_app(world.ctx)
    app.state.admin_guard = AdminGuard(world.ctx, monotonic=clocks.monotonic, clock=clocks.utcnow)
    transport = httpx.ASGITransport(app=app, client=(TRUSTED, 1))
    headers = {"X-Remote-User-Id": USERS[0]["id"]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        client.headers["X-MyHA-CSRF"] = await csrf(client)
        yield client


async def guests(world: World) -> tuple[str, str]:
    """A browser and a native session of the seeded stay: their ids."""
    async with world.client() as client:
        for secret, kind in zip(world.secrets, ("browser", "native"), strict=True):
            assert (await redeem(client, secret, kind)).status_code == 200
    by_audience = {row.audience: row.id for row in await rows(world.ctx, db.Session)}
    return by_audience["browser"], by_audience["native"]


def published(queue: asyncio.Queue[Event]) -> list[Event]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def test_list_sessions_of_a_stay_without_secrets(
    world: World, admin: httpx.AsyncClient
) -> None:
    browser, native = await guests(world)
    other_stay, secrets = await new_stay(
        world.ctx, starts_at=utcnow(), ends_at=utcnow() + timedelta(days=1)
    )
    async with world.client() as client:
        assert (await redeem(client, secrets[0])).status_code == 200
    stay_id = next(row.stay_id for row in await rows(world.ctx, db.Session) if row.id == browser)

    listed = (await admin.get(SESSIONS, params={"stay_id": stay_id})).json()["sessions"]
    assert sorted(item["id"] for item in listed) == sorted([browser, native])
    assert all(set(item) == FIELDS for item in listed)
    item = next(item for item in listed if item["id"] == browser)
    assert (item["audience"], item["purpose"], item["revoked_at"]) == ("browser", "access", None)
    assert item["principal"]["type"] == "guest"
    assert len((await admin.get(SESSIONS)).json()["sessions"]) == 3
    assert other_stay != stay_id


async def test_revoke_session_with_its_family(world: World, admin: httpx.AsyncClient) -> None:
    browser, native = await guests(world)
    sessions = {row.id: row for row in await rows(world.ctx, db.Session)}
    family = sessions[native].family_id
    assert family is not None
    rotated = new_id("ses")  # a rotated session of the same refresh family (Plan 3)
    async with write_session(world.ctx.engine) as session:
        old = sessions[native]
        session.add(
            db.Session(
                **{c.name: getattr(old, c.name) for c in db.Session.__table__.columns}
                | {"id": rotated, "token_hash": "rotated-hash"}
            )
        )
    queue = world.ctx.bus.subscribe()

    r = await admin.post(f"{SESSIONS}/{native}/revoke")
    assert r.status_code == 204, r.text
    assert (await admin.post(f"{SESSIONS}/{native}/revoke")).status_code == 204  # again: no-op
    states = {
        row.id: (row.revoked_at is not None, row.revoke_reason)
        for row in await rows(world.ctx, db.Session)
    }
    assert states == {
        browser: (False, None),
        native: (True, "revoked"),
        rotated: (True, "revoked"),
    }
    expected = [SessionRevoked("revoked", session_id=s) for s in sorted([native, rotated])]
    assert published(queue) == expected
    audits = [a for a in await rows(world.ctx, db.AuditEvent) if a.operation == "session.revoke"]
    assert [(a.actor, a.session_id, a.reason_code) for a in audits] == [
        (f"admin:{USERS[0]['id']}", native, "revoked")
    ]
    r = await admin.post(f"{SESSIONS}/ses_unknown/revoke")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")


async def test_admin_revoke_endpoint_requires_fresh_admin(
    world: World, admin: httpx.AsyncClient, sim: SimHandle, clocks: Clocks
) -> None:
    browser, _ = await guests(world)
    without_csrf = {"X-MyHA-CSRF": ""}
    assert denied(await admin.post(f"{SESSIONS}/{browser}/revoke", headers=without_csrf))
    demoted = owner_changed(is_owner=False, group_ids=["system-users"])
    await sim.control.post("auth/users", demoted)
    clocks.advance(1)  # within the 5 s a role is reused for checks that need not be fresh
    assert denied(await admin.get(SESSIONS))  # the listing asks Core afresh
    assert denied(await admin.post(f"{SESSIONS}/{browser}/revoke"))
    row = next(row for row in await rows(world.ctx, db.Session) if row.id == browser)
    assert row.revoked_at is None


async def test_revoke_stay_sessions_in_one_transaction(world: World) -> None:
    browser, native = await guests(world)
    stay_id = next(row.stay_id for row in await rows(world.ctx, db.Session) if row.id == browser)
    assert stay_id is not None
    other, _ = await new_stay(world.ctx, starts_at=utcnow(), ends_at=utcnow() + timedelta(days=1))
    await change(world.ctx, db.Stay, db.Stay.id == stay_id, device_slots=3)
    async with write_session(world.ctx.engine) as session:  # an open invitation of the stay
        stay = await session.get(db.Stay, stay_id)
        instance = (await session.execute(select(db.Instance))).scalar_one()
        assert stay is not None
        await issue_invites(
            session,
            stay,
            count=1,
            redeem_minutes=15,
            origin=public_helpers.ORIGIN,
            instance=instance,
            now=utcnow(),
            actor="test",
        )
    queue = world.ctx.bus.subscribe()

    await revoke_stay_sessions(world.ctx, stay_id, "checkout", actor="admin:test")

    sessions = {row.id: row.revoke_reason for row in await rows(world.ctx, db.Session)}
    assert sessions == {browser: "checkout", native: "checkout"}
    invites = {
        (row.stay_id, row.consumed_at is not None): row for row in await rows(world.ctx, db.Invite)
    }
    assert invites[(stay_id, False)].revoked_at is not None  # the open one
    assert all(row.revoked_at is None for (stay, used), row in invites.items() if used)
    assert invites[(other, False)].revoked_at is None
    assert published(queue) == [SessionRevoked("checkout", stay_id=stay_id)]
    audit = next(a for a in await rows(world.ctx, db.AuditEvent) if a.operation == "stay.revoke")
    assert audit.normalized_params == {
        "stay_id": stay_id,
        "session_ids": sorted([browser, native]),
        "invite_ids": [invites[(stay_id, False)].id],
    }
