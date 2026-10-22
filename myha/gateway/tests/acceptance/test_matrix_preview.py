"""A28, A29, A50 and the preview routes: an admin sees and tries another context (design §4.5).

The preview router is mounted on the started admin app here; ``app.py`` mounts it later.
"""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from myha_gateway.api.admin import preview
from myha_gateway.auth.admin import csrf_token
from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.services.stays import create_stay
from myha_simulator.fixtures import USERS
from tests.acceptance.conftest import OWNER, Clocks
from tests.helpers import SimHandle, seed_room203

PREVIEW = "/api/v1/admin/preview"
OTHER_ADMIN = "admin2"


def csrf(ctx: AppContext, clocks: Clocks, user_id: str) -> str:
    assert ctx.instance is not None
    return csrf_token(ctx.instance.server_secret, user_id, "", clocks.now.date())


@pytest.fixture
async def client(
    admin_app: FastAPI, admin_client: httpx.AsyncClient, ctx: AppContext, clocks: Clocks
) -> httpx.AsyncClient:
    """The owner's ingress client on the dev seed world, with the preview routes mounted."""
    admin_app.include_router(preview.router)
    await seed_room203(ctx.engine, ctx.ha, "https://guest.test")
    admin_client.headers["X-MyHA-CSRF"] = csrf(ctx, clocks, OWNER)
    return admin_client


async def rows[T](ctx: AppContext, model: type[T]) -> list[T]:
    async with ctx.sessions() as session:
        return list((await session.execute(select(model))).scalars())


async def seed_stay(ctx: AppContext) -> str:
    (stay,) = await rows(ctx, db.Stay)
    return stay.id


async def open_preview(client: httpx.AsyncClient, target: dict[str, str], **extra: Any) -> str:
    r = await client.post(PREVIEW, json={"target": target} | extra)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def dashboard(client: httpx.AsyncClient, preview_id: str, **params: str) -> dict[str, Any]:
    r = await client.get(f"{PREVIEW}/{preview_id}/dashboard", params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def simulate(
    client: httpx.AsyncClient, preview_id: str, resource_id: str, action: str, **extra: Any
) -> dict[str, Any]:
    body = {"resource_id": resource_id, "action": action, "parameters": {}} | extra
    r = await client.post(f"{PREVIEW}/{preview_id}/simulate", json=body)
    assert r.status_code == 200, r.text
    answer: dict[str, Any] = r.json()
    return answer


def room_ids(body: dict[str, Any]) -> list[str]:
    return [room["id"] for room in body["dashboard"]["rooms"]]


def resource(body: dict[str, Any], resource_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        res
        for room in body["dashboard"]["rooms"]
        for res in room["resources"]
        if res["id"] == resource_id
    )
    return found


async def test_a28_simulation_never_calls_service(
    client: httpx.AsyncClient, ctx: AppContext, sim: SimHandle
) -> None:
    await sim.control.post("service-calls/reset")
    preview_id = await open_preview(client, {"stay_id": await seed_stay(ctx)})

    allowed = await simulate(
        client, preview_id, "res_203_light", "light.set_level", parameters={"level_pct": 40}
    )
    assert allowed == {
        "would_allow": True,
        "code": "GRANTED",
        "public_code": None,
        "normalized_parameters": {"level_pct": 40},
        "matched": allowed["matched"],
        "message": "The action is allowed.",
    }
    assert allowed["matched"]
    too_warm = await simulate(
        client, preview_id, "res_203_trv", "climate.set_temperature", parameters={"target_c": "30"}
    )
    assert (too_warm["would_allow"], too_warm["code"], too_warm["public_code"]) == (
        False,
        "PARAM_OUT_OF_RANGE",
        "INVALID_PARAMETERS",
    )
    foreign = await simulate(client, preview_id, "res_lr_ceiling", "light.on")
    assert (foreign["code"], foreign["matched"]) == ("RESOURCE_NOT_IN_SCOPE", [])

    assert await sim.control.get("service-calls") == []
    assert await rows(ctx, db.Command) == []
    events = [e for e in await rows(ctx, db.AuditEvent) if e.operation == "preview.simulate"]
    assert [(e.reason_code, e.resource_id) for e in events] == [
        ("GRANTED", "res_203_light"),
        ("PARAM_OUT_OF_RANGE", "res_203_trv"),
        ("RESOURCE_NOT_IN_SCOPE", "res_lr_ceiling"),
    ]
    first = events[0]
    assert (first.actor, first.session_id, first.effective_principal) == (
        f"admin:{OWNER}",
        preview_id,
        None,
    )
    assert first.normalized_params is not None
    assert first.normalized_params["purpose"] == "preview"
    assert first.normalized_params["parameters"] == {"level_pct": 40}


async def test_a29_hypothetical_future_time_changes_only_policy(
    client: httpx.AsyncClient, ctx: AppContext
) -> None:
    now = datetime.now(UTC)
    async with write_session(ctx.engine) as session:
        stay = await create_stay(  # a draft: a preview shows it before it is confirmed
            session,
            unit_id="unit_203",
            role_template_id="room_guest",
            role_version=None,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=4),
            device_slots=1,
            actor="test",
        )
    ctx.policy.invalidate()
    preview_id = await open_preview(client, {"stay_id": stay.id})
    later = (now + timedelta(days=3)).isoformat()

    today = await dashboard(client, preview_id)
    assert (today["dashboard"]["status"], room_ids(today)) == ("waiting", [])
    future = await dashboard(client, preview_id, at=later)
    assert (future["dashboard"]["status"], room_ids(future)) == ("online", ["unit_203"])
    live = ctx.policy.state_view("res_203_trv")
    assert live is not None
    state = resource(future, "res_203_trv")["state"]
    assert state["target_c"] == format(live.target_c, "f")  # the device as measured now
    assert state["observed_at"] == (
        None if live.observed_at is None else live.observed_at.isoformat().replace("+00:00", "Z")
    )
    assert any("hypothetical" in fact for fact in future["context"])
    assert any("currently measured" in fact for fact in future["context"])

    assert (await simulate(client, preview_id, "res_203_light", "light.on"))["code"] == (
        "STAY_NOT_STARTED"
    )
    hypothetical = await simulate(client, preview_id, "res_203_light", "light.on", at=later)
    assert (hypothetical["would_allow"], hypothetical["code"]) == (True, "GRANTED")

    # The time given at creation is the preview's default; ?at= still overrides it.
    planned = await open_preview(client, {"stay_id": stay.id}, at=later)
    assert (await dashboard(client, planned))["dashboard"]["status"] == "online"
    after = (now + timedelta(days=5)).isoformat()
    assert (await dashboard(client, planned, at=after))["dashboard"]["status"] == "expired"


async def test_a50_invite_preview_uses_planned_context(
    client: httpx.AsyncClient, ctx: AppContext
) -> None:
    invite = (await rows(ctx, db.Invite))[0]
    preview_id = await open_preview(client, {"invite_id": invite.id})
    body = await dashboard(client, preview_id)

    assert room_ids(body) == ["unit_203"]
    assert body["banner"]["hypothetical"] is True
    assert body["banner"]["purpose"] == "preview"
    assert "Room 203" in body["banner"]["who"]
    redeem_until = invite.redeem_until.isoformat()
    assert any(redeem_until in fact for fact in body["context"])
    assert (await simulate(client, preview_id, "res_203_light", "light.on"))["would_allow"]

    # Only the preview session exists: no guest, no access session, the invite unredeemed.
    sessions = await rows(ctx, db.Session)
    assert [(s.id, s.purpose) for s in sessions] == [(preview_id, "preview")]
    row = sessions[0]
    assert (row.principal_id, row.stay_id, row.actor_admin_id, row.audience) == (
        None,
        None,
        OWNER,
        "admin",
    )
    assert row.unit_ids == ["unit_203"]
    assert row.preview_target == {"target": {"invite_id": invite.id}, "at": None}
    assert timedelta(minutes=14) < row.not_after - row.created_at <= timedelta(minutes=15)
    assert await rows(ctx, db.Principal) == []
    (again,) = [i for i in await rows(ctx, db.Invite) if i.id == invite.id]
    assert (again.consumed_at, again.consumed_session_id) == (None, None)

    both = {"invite_id": invite.id, "stay_id": invite.stay_id}  # one form only
    r = await client.post(PREVIEW, json={"target": both})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAMETERS"


async def test_group_preview_is_marked_hypothetical(
    client: httpx.AsyncClient, ctx: AppContext
) -> None:
    async with write_session(ctx.engine) as session:
        session.add(db.Group(id="grp_family", name="Family", description=None))
        await session.flush()
        session.add(
            db.Assignment(
                id="asg_family",
                subject_type="group",
                subject_id="grp_family",
                role_template_id="room_guest",
                role_version=1,
                unit_ids=["unit_203"],
                status="active",
            )
        )
    preview_id = await open_preview(client, {"group_id": "grp_family"})
    body = await dashboard(client, preview_id)

    assert body["banner"]["hypothetical"] is True
    assert body["banner"]["who"] == "Hypothetical member of Family"
    assert any("hypothetical" in fact.lower() for fact in body["context"])
    assert room_ids(body) == ["unit_203"]
    assert "blocked" in body["hidden_note"]  # the group light of room 203 (A15)
    assert (await simulate(client, preview_id, "res_203_light", "light.on"))["code"] == "GRANTED"
    (row,) = await rows(ctx, db.Session)
    assert (row.principal_id, row.unit_ids) == (None, None)


async def test_principal_preview_uses_the_real_principal(
    client: httpx.AsyncClient, ctx: AppContext
) -> None:
    now = datetime.now(UTC)
    async with write_session(ctx.engine) as session:
        session.add(
            db.Principal(
                id="prn_res",
                type="resident",
                status="active",
                display_name="Resident",
                locale=None,
                theme=None,
                stay_id=None,
                invite_id=None,
                created_at=now,
            )
        )
        await session.flush()
        session.add(
            db.Assignment(
                id="asg_res",
                subject_type="principal",
                subject_id="prn_res",
                role_template_id="room_guest",
                role_version=1,
                unit_ids=["unit_203"],
                status="active",
            )
        )
    preview_id = await open_preview(client, {"principal_id": "prn_res"})
    body = await dashboard(client, preview_id)
    assert (body["banner"]["who"], body["banner"]["hypothetical"]) == ("Resident", False)
    assert room_ids(body) == ["unit_203"]
    await simulate(client, preview_id, "res_203_light", "light.on")

    (row,) = await rows(ctx, db.Session)
    assert (row.principal_id, row.unit_ids) == ("prn_res", ["unit_203"])
    (event,) = [e for e in await rows(ctx, db.AuditEvent) if e.operation == "preview.simulate"]
    assert event.effective_principal == "prn_res"

    # Only access sessions of that principal: never a preview of a preview.
    other = {"principal_id": "prn_res", "session_id": preview_id}
    r = await client.post(PREVIEW, json={"target": other})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAMETERS"


@pytest.mark.parametrize(
    ("target", "status"),
    [
        ({"stay_id": "stay_unknown"}, 404),
        ({"invite_id": "inv_unknown"}, 404),
        ({"group_id": "grp_unknown"}, 404),
        ({"principal_id": "prn_unknown"}, 404),
        ({}, 400),
    ],
)
async def test_preview_target_must_name_one_known_context(
    client: httpx.AsyncClient, target: dict[str, str], status: int
) -> None:
    r = await client.post(PREVIEW, json={"target": target})
    assert r.status_code == status, r.text


async def test_preview_other_admin_cannot_use(
    client: httpx.AsyncClient, ctx: AppContext, sim: SimHandle, clocks: Clocks
) -> None:
    preview_id = await open_preview(client, {"stay_id": await seed_stay(ctx)})
    users = copy.deepcopy(USERS)
    users.append(dict(users[0], id=OTHER_ADMIN, username="admin2", name="Second admin"))
    await sim.control.post("auth/users", users)
    other = {"X-Remote-User-Id": OTHER_ADMIN, "X-MyHA-CSRF": csrf(ctx, clocks, OTHER_ADMIN)}

    r = await client.get(f"{PREVIEW}/{preview_id}/dashboard", headers=other)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    body = {"resource_id": "res_203_light", "action": "light.on", "parameters": {}}
    r = await client.post(f"{PREVIEW}/{preview_id}/simulate", json=body, headers=other)
    assert r.status_code == 404
    assert [e for e in await rows(ctx, db.AuditEvent) if e.operation == "preview.simulate"] == []

    # The owner still can, until the preview expires; unknown ids are as unknown as foreign.
    assert (await dashboard(client, preview_id))["banner"]["purpose"] == "preview"
    assert (await client.get(f"{PREVIEW}/ses_unknown/dashboard")).status_code == 404
    async with write_session(ctx.engine) as session:
        expired = await session.get(db.Session, preview_id)
        assert expired is not None
        expired.not_after = datetime.now(UTC)
    assert (await client.get(f"{PREVIEW}/{preview_id}/dashboard")).status_code == 404
    # Without the CSRF proof neither creating nor simulating passes.
    del client.headers["X-MyHA-CSRF"]
    assert (await client.post(PREVIEW, json={"target": {"group_id": "x"}})).status_code == 403
    r = await client.post(f"{PREVIEW}/{preview_id}/simulate", json=body)
    assert r.status_code == 403
