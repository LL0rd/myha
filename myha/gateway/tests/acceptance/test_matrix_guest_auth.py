"""Acceptance A22-A24 and the browser part of A35: guest redeem and session checks."""

import asyncio
from datetime import datetime, timedelta

from myha_gateway.auth.ratelimit import SlidingWindowLimiter
from myha_gateway.db import models as db
from tests import public_helpers
from tests.public_helpers import (
    Guest,
    World,
    browser_guest,
    change,
    new_stay,
    now,
    redeem,
    rows,
)

ctx, world = public_helpers.ctx, public_helpers.world  # the fixtures


async def test_a22_concurrent_redeem_single_winner(world: World) -> None:
    world.ctx.limiters["redeem"] = SlidingWindowLimiter(limit=1000, window_s=60)
    secret = world.secrets[0]
    async with world.client() as client:
        answers = await asyncio.gather(*(redeem(client, secret) for _ in range(20)))
    statuses = sorted(r.status_code for r in answers)
    assert statuses == [200] + [401] * 19, [r.text for r in answers if r.status_code != 401]
    assert len({r.content for r in answers if r.status_code == 401}) == 1
    assert "database is locked" not in " ".join(r.text for r in answers)

    invite = next(i for i in await rows(world.ctx, db.Invite) if i.consumed_at is not None)
    sessions = await rows(world.ctx, db.Session)
    principals = await rows(world.ctx, db.Principal)
    assert [s.id for s in sessions] == [invite.consumed_session_id]
    assert [(p.type, p.status, p.invite_id) for p in principals] == [("guest", "active", invite.id)]
    audit = [e for e in await rows(world.ctx, db.AuditEvent) if e.operation == "auth.redeem"]
    assert sorted(e.result for e in audit) == ["denied"] * 19 + ["ok"]
    assert secret not in repr([(e.normalized_params, e.anon_key) for e in audit])


async def test_a23_expired_redeem_window_keeps_existing_session(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        past = now() - timedelta(seconds=1)
        await change(world.ctx, db.Invite, db.Invite.stay_id.is_not(None), redeem_until=past)
        late = await redeem(client, world.secrets[1])
        assert (late.status_code, late.json()["error"]["code"]) == (401, "UNAUTHENTICATED")
        me = await client.get("/api/v1/me", headers={"Cookie": guest.cookie})
    assert me.status_code == 200, me.text
    assert me.json()["session"]["waiting"] is False


async def test_a24_redeem_before_stay_start_is_waiting(world: World) -> None:
    starts = now() + timedelta(days=2)
    _, secrets = await new_stay(world.ctx, starts_at=starts, ends_at=starts + timedelta(days=3))
    async with world.client() as client:
        guest = await browser_guest(world, client, secrets[0])
        assert guest.body["session"]["waiting"] is True
        assert datetime.fromisoformat(guest.body["session"]["not_before"]) == starts
        me = await client.get("/api/v1/me", headers={"Cookie": guest.cookie})
    assert me.status_code == 200, me.text
    assert me.json()["session"]["waiting"] is True
    session = (await rows(world.ctx, db.Session))[0]
    assert session.not_before == starts and session.unit_ids == ["unit_203"]


async def test_a35_csrf_origin_revoked(world: World) -> None:
    async with world.client() as client:
        guest: Guest = await browser_guest(world, client, world.secrets[0])
        missing = {"Cookie": guest.cookie, "Origin": guest.headers()["Origin"]}
        for path in ("/api/v1/ws-ticket", "/api/v1/auth/logout", "/api/v1/auth/session/renew"):
            r = await client.post(path, headers=missing)
            assert (r.status_code, r.json()["error"]["code"]) == (403, "UNAUTHENTICATED"), path
            r = await client.post(path, headers=guest.headers(**{"X-MyHA-CSRF": "x" * 43}))
            assert r.status_code == 403, path
        for origin in ("http://evil.test", "https://guest.test", "null"):
            r = await client.post("/api/v1/ws-ticket", headers=guest.headers(Origin=origin))
            assert r.status_code == 403, origin
        no_origin = {"Cookie": guest.cookie, "X-MyHA-CSRF": guest.csrf}
        assert (await client.post("/api/v1/ws-ticket", headers=no_origin)).status_code == 403
        cross = guest.headers(**{"Sec-Fetch-Site": "cross-site"})
        assert (await client.post("/api/v1/ws-ticket", headers=cross)).status_code == 403
        assert (await client.post("/api/v1/ws-ticket", headers=guest.headers())).status_code == 200

        # Revoked: logout, then the same cookie is refused.
        assert (
            await client.post("/api/v1/auth/logout", headers=guest.headers())
        ).status_code == 204
        for method, path in (("GET", "/api/v1/me"), ("POST", "/api/v1/ws-ticket")):
            r = await client.request(method, path, headers=guest.headers())
            assert (r.status_code, r.json()["error"]["code"]) == (401, "UNAUTHENTICATED"), path

        # Expired: past not_after.
        other = await browser_guest(world, client, world.secrets[1])
        past = now() - timedelta(seconds=1)
        await change(world.ctx, db.Session, db.Session.revoked_at.is_(None), not_after=past)
        r = await client.get("/api/v1/me", headers=other.headers())
        assert (r.status_code, r.json()["error"]["code"]) == (401, "ACCESS_EXPIRED")
