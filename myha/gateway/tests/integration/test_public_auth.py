"""Public guest auth: redeem, cookie, native tokens, /me, logout, WS tickets, renew, limits."""

import re
from datetime import datetime, timedelta, tzinfo

import httpx
import pytest
from starlette.requests import Request

from myha_gateway.auth import public
from myha_gateway.auth.public import client_key
from myha_gateway.db import models as db
from myha_gateway.ids import hash_secret, new_secret
from myha_gateway.services import audit as audit_module
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
    counts = {reason: 0 for reason in ("invite_invalid", "stay_closed")}
    for e in denied:  # folded per client and minute
        counts[str(e.reason_code)] += e.aggregate_count
    assert counts == {"invite_invalid": 5, "stay_closed": 2}
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
        r = await redeem(client, world.secrets[0], "native", device_label=None)
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
        out = await client.post("/api/v1/auth/logout", headers=bearer)
        assert out.status_code == 204 and "set-cookie" not in out.headers
    session = next(s for s in await rows(world.ctx, db.Session) if s.audience == "native")
    assert session.revoke_reason == "logout"
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
    renewals = [e for e in await rows(ctx, db.AuditEvent) if e.operation == "auth.renew"]
    assert len(renewals) == 3 and all(e.session_id for e in renewals)


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
    # The web client's strict schema: exactly these keys, timestamps with an offset.
    assert set(body) == {"principal", "session", "csrf_token", "stay", "instance"}
    assert set(body["session"]) == {"not_before", "not_after", "waiting"}
    assert set(body["stay"]) == {"unit_name", "ends_at"}
    session, ends_at = body["session"], body["stay"]["ends_at"]
    for value in (session["not_before"], session["not_after"], ends_at):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+(\+00:00|Z)", value), value
    assert set(guest.body) == {"session", "csrf_token"}


async def test_me_of_a_session_without_stay_or_start(world: World) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        await change(
            world.ctx, db.Session, db.Session.id.is_not(None), stay_id=None, not_before=None
        )
        r = await client.get("/api/v1/me", headers={"Cookie": guest.cookie})
    assert r.status_code == 200, r.text
    (session,) = await rows(world.ctx, db.Session)
    assert r.json()["stay"] is None
    assert datetime.fromisoformat(r.json()["session"]["not_before"]) == session.created_at
    assert r.json()["session"]["waiting"] is False


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


async def test_logout_clears_the_cookie_of_a_dead_session_but_not_on_csrf_failure(
    world: World,
) -> None:
    async with world.client() as client:
        guest = await browser_guest(world, client, world.secrets[0])
        bad_csrf = guest.headers(**{"X-MyHA-CSRF": "x" * 43})
        r = await client.post("/api/v1/auth/logout", headers=bad_csrf)
        assert error(r) == (403, "UNAUTHENTICATED") and "set-cookie" not in r.headers
        assert (
            await client.post("/api/v1/auth/logout", headers=guest.headers())
        ).status_code == 204
        again = await client.post("/api/v1/auth/logout", headers=guest.headers())
        assert error(again) == (401, "UNAUTHENTICATED")
        assert "max-age=0" in again.headers["set-cookie"].lower()
        nothing = await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        assert error(nothing) == (401, "UNAUTHENTICATED") and "set-cookie" in nothing.headers


async def test_redeem_replaces_the_browser_session_it_carries(world: World) -> None:
    async with world.client() as client:
        first = await browser_guest(world, client, world.secrets[0])
        r = await client.post(
            "/api/v1/auth/redeem",
            json={"invite_secret": world.secrets[1], "client": "browser"},
            headers={"Cookie": first.cookie},
        )
        assert r.status_code == 200, r.text
        second = r.headers["set-cookie"].split(";", 1)[0]
        assert error(await client.get("/api/v1/me", headers={"Cookie": first.cookie})) == (
            401,
            "UNAUTHENTICATED",
        )
        assert (await client.get("/api/v1/me", headers={"Cookie": second})).status_code == 200
    sessions = await rows(world.ctx, db.Session)
    assert sorted(str(s.revoke_reason) for s in sessions) == ["None", "replaced"]
    replaced = next(s.id for s in sessions if s.revoke_reason == "replaced")
    redeems = [e for e in await rows(world.ctx, db.AuditEvent) if e.operation == "auth.redeem"]
    assert [(e.normalized_params or {}).get("replaced_session") for e in redeems] == [
        None,
        replaced,
    ]


async def test_lone_surrogate_label_is_a_400(world: World) -> None:
    body = '{"invite_secret": "%s", "client": "browser", "device_label": "\\ud800"}'
    async with world.client() as client:
        r = await client.post(
            "/api/v1/auth/redeem",
            content=body % world.secrets[0],
            headers={"Content-Type": "application/json"},
        )
    assert error(r) == (400, "INVALID_PARAMETERS")
    assert all(i.consumed_at is None for i in await rows(world.ctx, db.Invite))


async def test_denials_of_one_client_fold_per_minute(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid_minute = now().replace(second=30)  # both clocks fixed: no minute boundary in between

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "Clock":
            return cls.fromtimestamp(mid_minute.timestamp(), tz)

    monkeypatch.setattr(public, "utcnow", lambda: mid_minute)
    monkeypatch.setattr(audit_module, "datetime", Clock)
    async with world.client() as client:
        for _ in range(2):
            assert (await redeem(client, new_secret())).status_code == 401
    (row,) = [e for e in await rows(world.ctx, db.AuditEvent) if e.result == "denied"]
    assert row.aggregate_count == 2 and row.reason_code == "invite_invalid"


async def test_denial_audit_never_breaks_the_uniform_401(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with world.client("2001:db8:1:2::9") as client:
        normal = await redeem(client, new_secret())
        (row,) = [e for e in await rows(world.ctx, db.AuditEvent) if e.result == "denied"]
        assert row.anon_key is not None and row.anon_key.endswith("|2001:db8:1:2::/64")

        async def broken(session: object, event: object, **kwargs: object) -> None:
            raise RuntimeError("database is locked")

        monkeypatch.setattr(public, "audit", broken)
        secret = new_secret()
        r = await redeem(client, secret)
    assert (r.status_code, r.content) == (normal.status_code, normal.content)
    assert "redeem denial not audited" in caplog.text and secret not in caplog.text


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
        await change(ctx, db.Stay, db.Stay.status == "checked_out", status="confirmed")
        await change(ctx, db.Session, db.Session.id.is_not(None), principal_id=None)
        assert await me() == (401, "UNAUTHENTICATED")


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
