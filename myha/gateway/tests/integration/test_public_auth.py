"""Public guest auth: redeem, cookie, native tokens, /me, logout, WS tickets, renew, limits."""

from datetime import datetime, timedelta

import httpx
import pytest
from starlette.requests import Request

from myha_gateway.auth.public import client_key
from myha_gateway.db import models as db
from myha_gateway.ids import hash_secret, new_secret
from tests import public_helpers
from tests.public_helpers import (
    GUEST_IP,
    ORIGIN,
    Guest,
    World,
    browser_guest,
    change,
    near,
    new_stay,
    now,
    redeem,
    rows,
)

ctx, world = public_helpers.ctx, public_helpers.world  # the fixtures

DAY = timedelta(days=1)


def error(r: httpx.Response) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


async def test_redeem_uniform_errors(world: World) -> None:
    ctx = world.ctx
    _, secrets = await new_stay(ctx, starts_at=now() - DAY, ends_at=now() + DAY, count=4)
    checked_out, (closed,) = await new_stay(ctx, starts_at=now() - DAY, ends_at=now() + DAY)
    cancelled, (dropped,) = await new_stay(ctx, starts_at=now() - DAY, ends_at=now() + DAY)
    consumed, revoked, expired, test_qr = secrets
    await change(ctx, db.Invite, db.Invite.secret_hash == hash_secret(revoked), revoked_at=now())
    past = now() - timedelta(seconds=1)
    await change(ctx, db.Invite, db.Invite.secret_hash == hash_secret(expired), redeem_until=past)
    await change(
        ctx, db.Invite, db.Invite.secret_hash == hash_secret(test_qr), purpose="guest_test"
    )
    await change(ctx, db.Stay, db.Stay.id == checked_out, status="checked_out")
    await change(ctx, db.Stay, db.Stay.id == cancelled, status="cancelled")
    async with world.client() as client:
        assert (await redeem(client, consumed)).status_code == 200
        answers = [
            await redeem(client, secret)
            for secret in (new_secret(), consumed, revoked, expired, test_qr, closed, dropped)
        ]

    def shape(r: httpx.Response) -> tuple[int, list[tuple[str, str]], bytes]:
        headers = sorted(
            (k, v) for k, v in r.headers.items() if k not in ("date", "content-length")
        )
        return r.status_code, headers, r.content

    assert len({repr(shape(r)) for r in answers}) == 1
    assert error(answers[0]) == (401, "UNAUTHENTICATED")
    assert "set-cookie" not in answers[0].headers
    # A refusal for the stay rolls the consumption back.
    invites = {i.secret_hash: i for i in await rows(ctx, db.Invite)}
    assert invites[hash_secret(closed)].consumed_at is None
    assert invites[hash_secret(dropped)].consumed_at is None
    denied = [e for e in await rows(ctx, db.AuditEvent) if e.result == "denied"]
    assert [e.reason_code for e in denied] == ["invite_invalid"] * 5 + ["stay_closed"] * 2
    assert all(e.anon_key and e.anon_key.endswith(f"|{GUEST_IP}") for e in denied)
    assert all(e.anon_key and e.anon_key.startswith("auth.redeem|") for e in denied)


async def test_cookie_attributes(world: World) -> None:
    async with world.client() as client:
        r = await redeem(client, world.secrets[0], device_label="Phone")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    name_value, *attributes = [part.strip() for part in r.headers["set-cookie"].split(";")]
    name, token = name_value.split("=", 1)
    assert name == world.cookie_name
    assert name.startswith("__Host-myha_") and len(name) == len("__Host-myha_12345678_session")
    attrs = {a.split("=", 1)[0].lower(): (a.split("=", 1) + [""])[1] for a in attributes}
    assert set(attrs) == {"httponly", "max-age", "path", "samesite", "secure"}
    assert (attrs["path"], attrs["samesite"]) == ("/", "lax")
    body = r.json()
    assert set(body) == {"session", "csrf_token"} and token not in r.text
    stay = (await rows(world.ctx, db.Stay))[0]
    session = (await rows(world.ctx, db.Session))[0]
    assert session.token_hash == hash_secret(token) and session.audience == "browser"
    assert session.not_after == stay.ends_at  # the stay ends long before guest_browser_days
    assert datetime.fromisoformat(body["session"]["not_after"]) == stay.ends_at
    assert abs(int(attrs["max-age"]) - (stay.ends_at - now()).total_seconds()) < 5
    assert body["session"]["waiting"] is False and body["csrf_token"] == session.csrf_secret
    principal = (await rows(world.ctx, db.Principal))[0]
    assert (principal.display_name, principal.stay_id) == ("Phone", stay.id)
    assert session.device_label == "Phone" and session.principal_id == principal.id
    audit = [e for e in await rows(world.ctx, db.AuditEvent) if e.operation == "auth.redeem"]
    assert [(e.result, e.session_id, e.effective_principal) for e in audit] == [
        ("ok", session.id, principal.id)
    ]


async def test_native_redeem_returns_tokens_no_cookie(world: World) -> None:
    async with world.client() as client:
        r = await redeem(client, world.secrets[0], "native")
        assert r.status_code == 200, r.text
        assert "set-cookie" not in r.headers
        body = r.json()
        assert set(body) == {"access_token", "refresh_token", "expires_in"}
        assert 890 < body["expires_in"] <= 900  # native_access_minutes, default 15
        bearer = {"Authorization": f"Bearer {body['access_token']}"}
        assert (await client.get("/api/v1/me", headers=bearer)).status_code == 200
        # No CSRF token and no Origin for the native audience.
        assert (await client.post("/api/v1/ws-ticket", headers=bearer)).status_code == 200
        renew = await client.post("/api/v1/auth/session/renew", headers=bearer)
        assert error(renew) == (400, "INVALID_PARAMETERS")
        # A native token is no cookie, a browser cookie is no bearer token.
        cookie = {"Cookie": f"{world.cookie_name}={body['access_token']}"}
        assert error(await client.get("/api/v1/me", headers=cookie)) == (401, "UNAUTHENTICATED")
        guest = await browser_guest(world, client, world.secrets[1])
        token = guest.cookie.split("=", 1)[1]
        stolen = {"Authorization": f"Bearer {token}"}
        assert error(await client.get("/api/v1/me", headers=stolen)) == (401, "UNAUTHENTICATED")
    session = next(s for s in await rows(world.ctx, db.Session) if s.audience == "native")
    assert session.token_hash == hash_secret(body["access_token"])
    assert near(session.not_after, now() + timedelta(minutes=15))
    (refresh,) = await rows(world.ctx, db.RefreshToken)
    assert refresh.token_hash == hash_secret(body["refresh_token"])
    assert (refresh.session_id, refresh.family_id) == (session.id, session.family_id)


async def test_renew_never_extends_beyond_stay(world: World) -> None:
    ctx = world.ctx
    renew = "/api/v1/auth/session/renew"
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        stay = (await rows(ctx, db.Stay))[0]
        await change(ctx, db.Session, db.Session.stay_id == stay.id, not_after=now() + DAY)
        r = await client.post(renew, headers=guest.headers())
        assert r.status_code == 200, r.text
        assert datetime.fromisoformat(r.json()["session"]["not_after"]) == stay.ends_at
        assert "max-age=" in r.headers["set-cookie"].lower()

        shorter = now() + timedelta(hours=2)
        await change(ctx, db.Stay, db.Stay.id == stay.id, ends_at=shorter)
        r = await client.post(renew, headers=guest.headers())
        assert datetime.fromisoformat(r.json()["session"]["not_after"]) == shorter

        # A long stay: guest_browser_days caps the session instead.
        long_id, (secret,) = await new_stay(ctx, starts_at=now() - DAY, ends_at=now() + 60 * DAY)
        long_guest = await browser_guest(world, client, secret)
        cap = now() + 30 * DAY
        assert near(datetime.fromisoformat(long_guest.body["session"]["not_after"]), cap)
        r = await client.post(renew, headers=long_guest.headers())
        assert near(datetime.fromisoformat(r.json()["session"]["not_after"]), cap)
    session = next(s for s in await rows(ctx, db.Session) if s.stay_id == long_id)
    assert near(session.not_after, cap)


async def test_public_config_and_rate_limit(world: World) -> None:
    async with world.client() as client:
        r = await client.get("/api/v1/public/config")
        assert r.json() == {
            "instance_name": "myHA",
            "languages": ["de", "en"],
            "theme_default": "system",
            "operator_contact": None,
        }
        for _ in range(59):
            assert (await client.get("/api/v1/public/config")).status_code == 200
        assert error(await client.get("/api/v1/public/config")) == (429, "RATE_LIMITED")
    async with world.client("2001:db8:1:2::9") as other:
        assert (await other.get("/api/v1/public/config")).status_code == 200


async def test_redeem_rate_limit(world: World) -> None:
    async with world.client() as client:
        for _ in range(9):
            assert (await redeem(client, new_secret())).status_code == 401
        assert (await redeem(client, "malformed")).status_code == 400  # counts as well
        assert error(await redeem(client, world.secrets[0])) == (429, "RATE_LIMITED")
    async with world.client("198.51.100.1") as other:
        assert (await redeem(other, world.secrets[0])).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"invite_secret": "a" * 42, "client": "browser"},
        {"invite_secret": "a" * 44, "client": "browser"},
        {"invite_secret": "a" * 42 + "!", "client": "browser"},
        {"invite_secret": "a" * 43, "client": "tv"},
        {"invite_secret": "a" * 43},
        {"invite_secret": "a" * 43, "client": "browser", "device_label": "x" * 61},
        {"invite_secret": "a" * 43, "client": "browser", "role": "admin"},
    ],
)
async def test_redeem_validation(world: World, body: dict[str, str]) -> None:
    async with world.client() as client:
        r = await client.post("/api/v1/auth/redeem", json=body)
    assert error(r) == (400, "INVALID_PARAMETERS")
    assert "details" not in r.json()["error"]


async def test_device_label_loses_control_characters(world: World) -> None:
    label = "‮" + "x" * 59 + "\x00y\n"
    async with world.client() as client:
        r = await redeem(client, world.secrets[0], device_label=label)
    assert r.status_code == 200, r.text
    (principal,) = await rows(world.ctx, db.Principal)
    assert principal.display_name == "x" * 59 + "y"


async def test_me(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        r = await client.get("/api/v1/me", headers={"Cookie": guest.cookie})
    assert r.status_code == 200, r.text
    body = r.json()
    (principal,) = await rows(world.ctx, db.Principal)
    stay = (await rows(world.ctx, db.Stay))[0]
    assert body["principal"] == {
        "id": principal.id,
        "type": "guest",
        "display_name": "",
        "locale": None,
        "theme": None,
    }
    assert body["session"] == guest.body["session"] and body["csrf_token"] == guest.csrf
    assert body["stay"]["unit_name"] == "Room 203"
    assert datetime.fromisoformat(body["stay"]["ends_at"]) == stay.ends_at
    assert body["instance"] == {"name": "myHA", "operator_contact": None}


async def test_logout(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        r = await client.post("/api/v1/auth/logout", headers=guest.headers())
    assert r.status_code == 204 and r.content == b""
    cleared = r.headers["set-cookie"].lower()
    assert cleared.startswith(f"{world.cookie_name.lower()}=") and "max-age=0" in cleared
    (session,) = await rows(world.ctx, db.Session)
    assert session.revoked_at is not None and session.revoke_reason == "logout"
    events = [e.operation for e in await rows(world.ctx, db.AuditEvent)]
    assert "auth.logout" in events


async def test_ws_ticket(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        first = (await client.post("/api/v1/ws-ticket", headers=guest.headers())).json()
        second = (await client.post("/api/v1/ws-ticket", headers=guest.headers())).json()
    assert first["expires_in"] == 30 and first["ticket"] != second["ticket"]
    tickets = {t.ticket_hash: t for t in await rows(world.ctx, db.WsTicket)}
    ticket = tickets[hash_secret(first["ticket"])]
    (session,) = await rows(world.ctx, db.Session)
    assert ticket.session_id == session.id and ticket.used_at is None
    assert near(ticket.expires_at, now() + timedelta(seconds=30))


async def test_last_seen_is_written_at_most_every_minute(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        recent = now() - timedelta(seconds=30)
        await change(world.ctx, db.Session, db.Session.id.is_not(None), last_seen_at=recent)
        assert (await client.get("/api/v1/me", headers=guest.headers())).status_code == 200
        assert (await rows(world.ctx, db.Session))[0].last_seen_at == recent
        older = now() - timedelta(seconds=61)
        await change(world.ctx, db.Session, db.Session.id.is_not(None), last_seen_at=older)
        assert (await client.get("/api/v1/me", headers=guest.headers())).status_code == 200
    assert near((await rows(world.ctx, db.Session))[0].last_seen_at, now())


async def test_session_refusals(world: World) -> None:
    ctx = world.ctx
    async with world.client() as client:
        guest: Guest = await browser_guest(world, client, world.secrets[0])

        async def me(headers: dict[str, str] | None = None) -> tuple[int, str]:
            return error(await client.get("/api/v1/me", headers=headers or guest.headers()))

        assert await me({"Origin": ORIGIN}) == (401, "UNAUTHENTICATED")
        assert await me({"Authorization": f"Bearer {new_secret()}"}) == (401, "UNAUTHENTICATED")
        assert await me({"Authorization": "Basic abc"}) == (401, "UNAUTHENTICATED")
        await change(ctx, db.Session, db.Session.id.is_not(None), purpose="preview")
        assert await me() == (401, "UNAUTHENTICATED")
        await change(ctx, db.Session, db.Session.id.is_not(None), purpose="access")
        await change(ctx, db.Principal, db.Principal.id.is_not(None), status="disabled")
        assert await me() == (401, "UNAUTHENTICATED")
        await change(ctx, db.Principal, db.Principal.id.is_not(None), status="active")
        await change(ctx, db.Stay, db.Stay.status == "confirmed", status="checked_out")
        assert await me() == (401, "ACCESS_EXPIRED")


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb::2", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("testclient", "testclient"),
        (None, ""),
    ],
)
def test_client_key(host: str | None, key: str) -> None:
    scope = {"type": "http", "client": None if host is None else (host, 1)}
    assert client_key(Request(scope)) == key
