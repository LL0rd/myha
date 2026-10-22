"""Admin API for stays and one-time invitations, behind the ingress, against the simulator."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from myha_gateway.app import create_admin_app
from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.ids import hash_secret
from myha_gateway.services.stays import stay_status
from myha_gateway.settings import Settings
from myha_simulator.fixtures import USERS
from tests.helpers import SIM_TOKEN, TRUSTED, SimHandle, seed_room203

ORIGIN = "http://guest.test"
STAYS = "/api/v1/admin/stays"


@pytest.fixture
async def ctx(sim: SimHandle, tmp_path: Path) -> AppContext:
    settings = Settings(
        data_dir=tmp_path,
        supervisor_url=sim.http_url,
        ha_ws_url=sim.ws_url,
        supervisor_token=SIM_TOKEN,
        public_enabled=True,
        public_origin=ORIGIN,
    )
    return await AppContext.create(settings)


@pytest.fixture
async def app(ctx: AppContext) -> AsyncIterator[FastAPI]:
    """The admin app started with its lifespan, on the room 203 world."""
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        await seed_room203(ctx.engine, ctx.ha, ORIGIN)
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The owner through the ingress, with the CSRF token of the session endpoint."""
    transport = httpx.ASGITransport(app=app, client=(TRUSTED, 1))
    headers = {"X-Remote-User-Id": USERS[0]["id"]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        token = (await client.get("/api/v1/admin/session")).json()["csrf_token"]
        client.headers["X-MyHA-CSRF"] = token
        yield client


def now() -> datetime:
    return datetime.now(UTC)


async def create(client: httpx.AsyncClient, **changes: Any) -> httpx.Response:
    body = {
        "unit_id": "unit_203",
        "role_template_id": "room_guest",
        "starts_at": (now() - timedelta(hours=1)).isoformat(),
        "ends_at": (now() + timedelta(days=1)).isoformat(),
        "device_slots": 2,
    } | changes
    return await client.post(STAYS, json=body)


async def rows[T](ctx: AppContext, model: type[T]) -> list[T]:
    async with ctx.sessions() as session:
        return list((await session.execute(select(model))).scalars())


async def policy_version(ctx: AppContext) -> int:
    return (await rows(ctx, db.Instance))[0].policy_version


def code(r: httpx.Response) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


async def test_stay_and_invites_secret_only_in_the_answer(
    ctx: AppContext, client: httpx.AsyncClient
) -> None:
    before = await policy_version(ctx)
    r = await create(client)
    assert r.status_code == 201, r.text
    stay = r.json()
    assert (stay["status"], stay["role_version"], stay["invites"]) == ("draft", 1, [])
    assert await policy_version(ctx) == before + 1

    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 2})
    assert r.status_code == 201, r.text
    issued = r.json()["invites"]
    assert [invite["device_slot"] for invite in issued] == [1, 2]
    instance = (await rows(ctx, db.Instance))[0]
    secrets = []
    for invite in issued:
        prefix = f"{ORIGIN}/join#i="
        assert invite["join_url"].startswith(prefix)
        secret, rest = invite["join_url"].removeprefix(prefix).split("&", 1)
        assert rest == f"inst={instance.id}&fp="
        assert invite["qr_svg"].startswith("<svg")
        assert invite["usable_until"] == stay["ends_at"]
        redeem_until = datetime.fromisoformat(invite["redeem_until"])
        assert timedelta(minutes=14) < redeem_until - now() <= timedelta(minutes=15)
        secrets.append(secret)
    assert await policy_version(ctx) == before + 2
    confirmed = next(row for row in await rows(ctx, db.Stay) if row.id == stay["id"])
    assert (confirmed.status, confirmed.version) == ("confirmed", 2)

    stored = {invite.id: invite for invite in await rows(ctx, db.Invite)}
    for invite, secret in zip(issued, secrets, strict=True):
        assert stored[invite["invite_id"]].secret_hash == hash_secret(secret)
    listed = (await client.get(STAYS)).text
    audit = [(e.operation, e.normalized_params) for e in await rows(ctx, db.AuditEvent)]
    for secret in secrets:  # nowhere but in the one answer
        assert secret not in listed and secret not in repr(audit)
    events = [
        (op, params or {}) for op, params in audit if (params or {}).get("stay_id") == stay["id"]
    ]
    assert [op for op, _ in events] == ["stay.create", "invite.issue", "invite.issue"]
    assert all(set(params) == {"stay_id", "invite_id", "device_slot"} for _, params in events[1:])

    mine = next(s for s in (await client.get(STAYS)).json()["stays"] if s["id"] == stay["id"])
    assert mine["status"] == "active"  # confirmed by the invites, inside its window
    assert [(i["redeemed"], i["revoked"]) for i in mine["invites"]] == [(False, False)] * 2
    assert all("join_url" not in i and "secret_hash" not in i for i in mine["invites"])

    # All slots taken: no third invitation for two devices.
    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 1})
    assert code(r) == (409, "CONFLICT")


async def test_primary_pin_round_trips_through_the_fragment(
    ctx: AppContext, client: httpx.AsyncClient
) -> None:
    pin = "q+7/AbC=="  # base64 SPKI pins carry + / =
    pins = [{"spki_sha256": "backup+/=", "role": "backup"}, {"spki_sha256": pin, "role": "primary"}]
    async with write_session(ctx.engine) as session:
        await session.execute(update(db.Instance).values(trust_pins=pins))
    stay = (await create(client)).json()
    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 1})
    fragment = urlsplit(r.json()["invites"][0]["join_url"]).fragment
    assert parse_qs(fragment)["fp"] == [pin]


async def test_redeem_window_is_capped(ctx: AppContext, client: httpx.AsyncClient) -> None:
    stay = (await create(client)).json()
    r = await client.post(
        f"{STAYS}/{stay['id']}/invites", json={"count": 1, "redeem_minutes": 2000}
    )
    assert code(r) == (400, "INVALID_PARAMETERS")
    assert all(invite.stay_id != stay["id"] for invite in await rows(ctx, db.Invite))

    ends = now() + timedelta(minutes=10)
    short = (await create(client, ends_at=ends.isoformat())).json()
    r = await client.post(f"{STAYS}/{short['id']}/invites", json={"count": 1, "redeem_minutes": 60})
    assert r.status_code == 201
    assert r.json()["invites"][0]["redeem_until"] == short["ends_at"]  # never past the stay


async def test_revoke(ctx: AppContext, client: httpx.AsyncClient) -> None:
    stay = (await create(client)).json()
    issued = (await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 1})).json()
    invite_id = issued["invites"][0]["invite_id"]
    before = await policy_version(ctx)

    assert (await client.post(f"/api/v1/admin/invites/{invite_id}/revoke")).status_code == 204
    assert await policy_version(ctx) == before + 1
    mine = next(s for s in (await client.get(STAYS)).json()["stays"] if s["id"] == stay["id"])
    assert mine["invites"][0]["revoked"] is True
    assert "invite.revoke" in [e.operation for e in await rows(ctx, db.AuditEvent)]
    # Again: nothing left to do. The slot is free for a new invitation.
    assert (await client.post(f"/api/v1/admin/invites/{invite_id}/revoke")).status_code == 204
    assert await policy_version(ctx) == before + 1
    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 2})
    assert [i["device_slot"] for i in r.json()["invites"]] == [1, 2]

    assert code(await client.post("/api/v1/admin/invites/inv_unknown/revoke")) == (404, "NOT_FOUND")


async def test_revoking_a_redeemed_invite_conflicts(
    ctx: AppContext, client: httpx.AsyncClient
) -> None:
    async with ctx.sessions() as session, session.begin():
        invite = (await session.execute(select(db.Invite))).scalars().first()
        assert invite is not None  # one of the seed's
        invite.consumed_at = now()
    r = await client.post(f"/api/v1/admin/invites/{invite.id}/revoke")
    assert code(r) == (409, "CONFLICT")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"ends_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}, 400),  # before start
        ({"unit_id": "unit_unknown"}, 400),
        ({"role_template_id": "unknown"}, 400),
        ({"role_version": 2}, 400),  # never published
        ({"starts_at": "2026-11-01T14:00:00"}, 400),  # no timezone
        ({"starts_at": "0001-01-01T00:00:00+01:00"}, 400),  # before year 1 in UTC
        ({"device_slots": 0}, 400),
    ],
)
async def test_create_validation(
    ctx: AppContext, client: httpx.AsyncClient, changes: dict[str, Any], expected: int
) -> None:
    stays = len(await rows(ctx, db.Stay))
    assert code(await create(client, **changes)) == (expected, "INVALID_PARAMETERS")
    assert len(await rows(ctx, db.Stay)) == stays


@pytest.mark.parametrize(
    ("changes", "field"),
    [({"role_template_id": "unknown"}, "role_template_id"), ({"role_version": 2}, "role_version")],
)
async def test_unknown_role_names_its_field(
    client: httpx.AsyncClient, changes: dict[str, Any], field: str
) -> None:
    assert (await create(client, **changes)).json()["error"]["details"] == {"field": field}


async def test_default_role_version_is_the_newest(
    ctx: AppContext, client: httpx.AsyncClient
) -> None:
    async with write_session(ctx.engine) as session:
        session.add(
            db.RoleVersion(
                id="room_guest@2",
                role_template_id="room_guest",
                version_number=2,
                grants=[],
                denies=[],
                published_at=now(),
                based_on_version=1,
            )
        )
    assert (await create(client)).json()["role_version"] == 2
    assert (await create(client, role_version=1)).json()["role_version"] == 1


async def set_invite(ctx: AppContext, invite_id: str, **values: Any) -> None:
    async with write_session(ctx.engine) as session:
        await session.execute(update(db.Invite).where(db.Invite.id == invite_id).values(**values))


@pytest.mark.parametrize("ending", ["revoked_at", "not_after"])
async def test_slot_held_only_by_redeemable_or_live_invites(
    ctx: AppContext, client: httpx.AsyncClient, ending: str
) -> None:
    stay = (await create(client)).json()
    url = f"{STAYS}/{stay['id']}/invites"
    first, second = (await client.post(url, json={"count": 2})).json()["invites"]
    assert code(await client.post(url, json={"count": 1})) == (409, "CONFLICT")

    await set_invite(ctx, first["invite_id"], redeem_until=now() - timedelta(seconds=1))
    r = await client.post(url, json={"count": 1})  # expired, never redeemed: slot 1 is free
    assert [i["device_slot"] for i in r.json()["invites"]] == [1]

    async with write_session(ctx.engine) as session:
        session.add(
            db.Session(
                id="ses_guest",
                token_hash="hash-ses_guest",
                audience="browser",
                purpose="access",
                stay_id=stay["id"],
                not_after=now() + timedelta(days=1),
                last_seen_at=now(),
                csrf_secret="csrf",
                created_at=now(),
            )
        )
    past = now() - timedelta(seconds=1)  # the window has passed since the redemption
    values = {"consumed_at": past, "consumed_session_id": "ses_guest", "redeem_until": past}
    await set_invite(ctx, second["invite_id"], **values)
    assert code(await client.post(url, json={"count": 1})) == (409, "CONFLICT")  # live session

    async with write_session(ctx.engine) as session:
        query = update(db.Session).where(db.Session.id == "ses_guest")
        await session.execute(query.values({ending: now() - timedelta(seconds=1)}))
    r = await client.post(url, json={"count": 1})  # the session was revoked or has expired
    assert [i["device_slot"] for i in r.json()["invites"]] == [2]


async def test_concurrent_issues_share_the_slots(client: httpx.AsyncClient) -> None:
    async def both(count: int) -> list[httpx.Response]:
        stay = (await create(client)).json()
        url = f"{STAYS}/{stay['id']}/invites"
        return list(
            await asyncio.gather(*(client.post(url, json={"count": count}) for _ in range(2)))
        )

    slots = {r.json()["invites"][0]["device_slot"] for r in await both(1)}
    assert slots == {1, 2}
    assert sorted(r.status_code for r in await both(2)) == [201, 409]


async def test_unknown_or_closed_stay(ctx: AppContext, client: httpx.AsyncClient) -> None:
    r = await client.post(f"{STAYS}/stay_unknown/invites", json={"count": 1})
    assert code(r) == (404, "NOT_FOUND")
    stay = (await create(client)).json()
    async with ctx.sessions() as session, session.begin():
        row = await session.get(db.Stay, stay["id"])
        assert row is not None
        row.status = "cancelled"
    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 1})
    assert code(r) == (409, "CONFLICT") and r.json()["error"]["details"]["reason"] == "cancelled"

    starts = now() - timedelta(days=2)
    window = {"starts_at": starts.isoformat(), "ends_at": (starts + timedelta(days=1)).isoformat()}
    draft = (await create(client, **window)).json()
    r = await client.post(f"{STAYS}/{draft['id']}/invites", json={"count": 1})
    assert r.json()["error"]["details"]["reason"] == "ended"  # a draft past its end

    ctx.public_origin = None  # public listener off: a join URL would lead nowhere
    stay = (await create(client)).json()
    r = await client.post(f"{STAYS}/{stay['id']}/invites", json={"count": 1})
    assert code(r) == (409, "CONFLICT")


async def test_mutations_need_csrf(client: httpx.AsyncClient) -> None:
    del client.headers["X-MyHA-CSRF"]
    assert code(await create(client)) == (403, "UNAUTHENTICATED")
    assert (await client.get(STAYS)).status_code == 200


def test_derived_status() -> None:
    start, end = datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 4, tzinfo=UTC)
    stay = db.Stay(starts_at=start, ends_at=end, status="confirmed")
    assert stay_status(stay, start - timedelta(seconds=1)) == "planned"
    assert stay_status(stay, start) == "active"
    assert stay_status(stay, end) == "ended"  # ends_at is exclusive
    for stored in ("draft", "checked_out", "cancelled"):
        stay.status = stored
        assert stay_status(stay, start) == stored
