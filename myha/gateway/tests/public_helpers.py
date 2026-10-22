"""The public app beside the started admin app, on the room 203 world, for the guest auth tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.auth.cookies import session_cookie_name
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.ha.client import utcnow
from myha_gateway.services.stays import create_stay, issue_invites
from myha_gateway.settings import Settings
from tests.helpers import SIM_TOKEN, SimHandle, seed_room203

ORIGIN = "http://guest.test"
GUEST_IP = "203.0.113.7"


def secret_of(join_url: str) -> str:
    return join_url.split("#i=", 1)[1].split("&", 1)[0]


@dataclass
class World:
    ctx: AppContext
    public: FastAPI
    secrets: list[str]  # the dev seed's two invitations to room 203, active stay

    def client(self, ip: str = GUEST_IP) -> httpx.AsyncClient:
        """A guest's client; cookies are passed by hand, since the session cookie is Secure."""
        transport = httpx.ASGITransport(app=self.public, client=(ip, 1234))
        return httpx.AsyncClient(transport=transport, base_url=ORIGIN)

    @property
    def cookie_name(self) -> str:
        assert self.ctx.instance is not None
        return session_cookie_name(self.ctx.instance.short_id)


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
async def world(ctx: AppContext) -> AsyncIterator[World]:
    admin = create_admin_app(ctx)
    async with admin.router.lifespan_context(admin):
        urls = await seed_room203(ctx.engine, ctx.ha, ORIGIN)
        yield World(ctx, create_public_app(ctx), [secret_of(url) for url in urls])


async def new_stay(
    ctx: AppContext, *, starts_at: datetime, ends_at: datetime, count: int = 1
) -> tuple[str, list[str]]:
    """A confirmed stay in room 203 with ``count`` invitations: its id and their secrets."""
    assert ctx.instance is not None
    async with write_session(ctx.engine) as session:
        stay = await create_stay(
            session,
            unit_id="unit_203",
            role_template_id="room_guest",
            role_version=1,
            starts_at=starts_at,
            ends_at=ends_at,
            device_slots=count,
            actor="test",
        )
        issued = await issue_invites(
            session,
            stay,
            count=count,
            redeem_minutes=60,
            origin=ORIGIN,
            instance=ctx.instance,
            now=utcnow(),
            actor="test",
        )
    return stay.id, [secret_of(item.join_url) for item in issued]


async def change[T](ctx: AppContext, model: type[T], *where: Any, **values: Any) -> None:
    async with write_session(ctx.engine) as session:
        await session.execute(update(model).where(*where).values(**values))


async def rows[T](ctx: AppContext, model: type[T]) -> list[T]:
    async with ctx.sessions() as session:
        return list((await session.execute(select(model))).scalars())


async def redeem(
    client: httpx.AsyncClient, secret: str, client_kind: str = "browser", **extra: Any
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/redeem", json={"invite_secret": secret, "client": client_kind} | extra
    )


@dataclass
class Guest:
    """A redeemed browser session: what a browser would send back."""

    cookie: str
    csrf: str
    body: dict[str, Any]

    def headers(self, **extra: str) -> dict[str, str]:
        return {"Cookie": self.cookie, "X-MyHA-CSRF": self.csrf, "Origin": ORIGIN} | extra


async def browser_guest(world: World, client: httpx.AsyncClient, secret: str) -> Guest:
    r = await redeem(client, secret)
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].split(";", 1)[0]
    assert cookie.startswith(f"{world.cookie_name}=")
    return Guest(cookie, r.json()["csrf_token"], r.json())


def now() -> datetime:
    return datetime.now(UTC)


def near(a: datetime, b: datetime, seconds: float = 5) -> bool:
    return abs(a - b) <= timedelta(seconds=seconds)
