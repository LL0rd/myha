"""Admin audit listing: fields, filters, keyset pages, and no IP or secret ever (design §6, A45)."""

import base64
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from myha_gateway.app import create_admin_app
from myha_gateway.db import write_session
from myha_gateway.ha.client import utcnow
from myha_gateway.services.audit import AuditEvent, audit
from myha_simulator.fixtures import USERS
from tests import public_helpers
from tests.acceptance.test_matrix_admin import denied, owner_changed
from tests.helpers import TRUSTED, SimHandle
from tests.public_helpers import GUEST_IP, World, redeem

ctx, world = public_helpers.ctx, public_helpers.world  # the fixtures
AUDIT = "/api/v1/admin/audit"
FIELDS = {"id", "ts", "actor", "effective_principal", "operation", "result", "reason_code"}
FIELDS |= {"resource_id", "session_id", "policy_version", "aggregate_count", "normalized_params"}
ANON = AuditEvent(
    operation="test.anon",
    result="denied",
    reason_code="UNAUTHENTICATED",
    anon_key=f"test.anon|UNAUTHENTICATED|minute|{GUEST_IP}",
)
GUEST = AuditEvent(
    actor="prn_guest",
    effective_principal="prn_guest",
    session_id="ses_guest",
    operation="test.guest",
    result="denied",
    reason_code="OUT_OF_SCOPE",
    resource_id="res_203_light",
    policy_version=4,
    normalized_params={"level_pct": 40},
)


@pytest.fixture
async def admin(world: World) -> AsyncIterator[httpx.AsyncClient]:
    """The owner through the ingress."""
    app: FastAPI = create_admin_app(world.ctx)
    transport = httpx.ASGITransport(app=app, client=(TRUSTED, 1))
    headers = {"X-Remote-User-Id": USERS[0]["id"]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        yield client


async def write(world: World, *events: AuditEvent, now: datetime | None = None) -> None:
    for event in events:
        async with write_session(world.ctx.engine) as session:
            await audit(session, event, aggregate=True, now=now)


def cursor_of(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


async def listing(admin: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    r = await admin.get(AUDIT, params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_rows_carry_the_audit_fields_and_fold_counts(
    world: World, admin: httpx.AsyncClient
) -> None:
    await write(world, ANON, ANON, GUEST)
    body = await listing(admin, filter="operation=test.guest")
    [guest] = body["events"]
    assert set(guest) == FIELDS
    assert guest["effective_principal"] == guest["actor"] == "prn_guest"
    assert (guest["session_id"], guest["policy_version"]) == ("ses_guest", 4)
    assert guest["normalized_params"] == {"level_pct": 40}
    [anon] = (await listing(admin, filter="operation=test.anon"))["events"]
    assert (anon["actor"], anon["effective_principal"], anon["aggregate_count"]) == (
        "anonymous",
        None,
        2,
    )
    assert body["anonymous_excluded"] is False


async def test_failed_redeem_shows_neither_ip_nor_secret(
    world: World, admin: httpx.AsyncClient
) -> None:
    attempted = secrets.token_urlsafe(32)  # well-formed, but no invitation has it
    async with world.client() as client:
        assert (await redeem(client, attempted)).status_code != 200
    r = await admin.get(AUDIT, params={"filter": "operation=auth.redeem"})
    assert r.status_code == 200
    assert r.json()["events"], "the failed redeem is audited"
    assert GUEST_IP not in r.text and attempted not in r.text and "anon_key" not in r.text


async def test_principal_filter_excludes_anonymous_rows_and_says_so(
    world: World, admin: httpx.AsyncClient
) -> None:
    await write(world, ANON, GUEST)
    body = await listing(admin, filter="principal=prn_guest")
    assert [row["operation"] for row in body["events"]] == ["test.guest"]
    assert body["anonymous_excluded"] is True


async def test_filters_combine(world: World, admin: httpx.AsyncClient) -> None:
    ok = AuditEvent(**(GUEST.__dict__ | {"result": "ok", "reason_code": None}))
    await write(world, GUEST, ok, AuditEvent(**(GUEST.__dict__ | {"session_id": "ses_other"})))
    body = await listing(admin, filter="session=ses_guest,result=ok")
    assert [(row["session_id"], row["result"]) for row in body["events"]] == [("ses_guest", "ok")]
    hour = timedelta(hours=1)
    since = (utcnow() - hour).isoformat()
    until = (utcnow() + hour).isoformat()
    window = await listing(admin, filter=f"operation=test.guest,since={since},until={until}")
    assert len(window["events"]) == 3
    future = await listing(admin, filter=f"operation=test.guest,since={until}")
    assert future["events"] == []
    past = await listing(admin, filter=f"operation=test.guest,until={since}")
    assert past["events"] == []


async def test_keyset_pages_walk_every_row_newest_first(
    world: World, admin: httpx.AsyncClient
) -> None:
    await write(world, *(AuditEvent(operation="test.page", result="ok") for _ in range(5)))
    seen: list[int] = []
    cursor = None
    while True:
        params = {"filter": "operation=test.page", "limit": 2}
        body = await listing(admin, **params | ({"cursor": cursor} if cursor else {}))
        seen += [row["id"] for row in body["events"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5 and seen == sorted(seen, reverse=True)


async def test_keyset_pages_split_rows_of_the_same_timestamp(
    world: World, admin: httpx.AsyncClient
) -> None:
    same = utcnow()
    await write(world, *(AuditEvent(operation="test.tie", result="ok") for _ in range(5)), now=same)
    seen: list[int] = []
    cursor = None
    while True:
        params = {"filter": "operation=test.tie", "limit": 2}
        body = await listing(admin, **params | ({"cursor": cursor} if cursor else {}))
        assert {row["ts"] for row in body["events"]} <= {same.isoformat().replace("+00:00", "Z")}
        seen += [row["id"] for row in body["events"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == list(range(seen[0], seen[0] - 5, -1))  # all five, descending, no gaps


@pytest.mark.parametrize(
    "params",
    [
        {"filter": "anon_key=x"},
        {"filter": "operation"},
        {"filter": "operation="},
        {"filter": "result=ok,result=denied"},
        {"filter": "since=2026-10-22T12:00:00"},
        {"filter": "until=yesterday"},
        {"filter": ","},
        {"filter": "operation=a,,result=ok"},
        {"filter": "operation=a,"},
        {"filter": "since=0001-01-01T00:00:00+14:00"},
        {"cursor": "not base64 !"},
        {"cursor": cursor_of(f"2026-10-22T12:00:00+00:00|{2**64}")},
        {"cursor": cursor_of(f"2026-10-22T12:00:00+00:00|{2**63}")},
        {"cursor": cursor_of("2026-10-22T12:00:00+00:00|" + "9" * 30)},
        {"cursor": cursor_of("2026-10-22T12:00:00+00:00|0")},
        {"cursor": cursor_of("2026-10-22T12:00:00+00:00|-5")},
        {"cursor": cursor_of("2026-10-22T12:00:00+00:00|1_0")},
        {"cursor": cursor_of("0001-01-01T00:00:00+14:00|5")},
        {"cursor": base64.urlsafe_b64encode(b"2026-10-22T12:00:00+00:00|x").decode()},
        {"cursor": base64.urlsafe_b64encode(b"2026-10-22T12:00:00|1").decode()},
        {"limit": 0},
        {"limit": 201},
    ],
)
async def test_malformed_queries_are_refused(
    world: World, admin: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    r = await admin.get(AUDIT, params=params)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "INVALID_PARAMETERS")


async def test_listing_needs_a_current_admin(
    world: World, admin: httpx.AsyncClient, sim: SimHandle
) -> None:
    assert (await admin.get(AUDIT)).status_code == 200
    await sim.control.post("auth/users", owner_changed(is_owner=False, group_ids=["system-users"]))
    assert denied(await admin.get(AUDIT))
