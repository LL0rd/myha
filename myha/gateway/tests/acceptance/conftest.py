"""The admin app wired to the simulator and started with its real lifespan, seen from ingress."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from myha_gateway.app import create_admin_app
from myha_gateway.auth.admin import AdminGuard
from myha_gateway.deps import AppContext
from myha_gateway.settings import Settings
from myha_simulator.fixtures import USERS
from tests.helpers import SIM_TOKEN, TRUSTED, SimHandle

OWNER = USERS[0]["id"]  # the simulator's owner, an active system-admin


class Clocks:
    """The guard's monotonic and UTC clocks; ``advance`` moves both."""

    def __init__(self) -> None:
        self.seconds = 1000.0
        self.now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def utcnow(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clocks() -> Clocks:
    return Clocks()


@pytest.fixture
async def ctx(sim: SimHandle, tmp_path: Path) -> AppContext:
    """Supervisor API and Core WebSocket of the simulator, with its admin token."""
    settings = Settings(
        data_dir=tmp_path,
        supervisor_url=sim.http_url,
        ha_ws_url=sim.ws_url,
        supervisor_token=SIM_TOKEN,
    )
    return await AppContext.create(settings)


@pytest.fixture
async def admin_app(ctx: AppContext, clocks: Clocks) -> AsyncIterator[FastAPI]:
    """Started like in production: database, instance row, HA client connected."""
    app = create_admin_app(ctx)
    app.state.admin_guard = AdminGuard(ctx, monotonic=clocks.monotonic, clock=clocks.utcnow)
    async with app.router.lifespan_context(app):
        assert ctx.ha.connected
        yield app


@pytest.fixture
async def quiet_timeguard(admin_app: FastAPI) -> None:
    """Cancel the lifespan's time guard, so a hand-set ``instance.time_trusted`` stays.

    Request it in tests that set the flag or the mark themselves: the running guard writes
    the flag back on its next transition or mark write (real wall clock, every 60 s).
    """
    tasks = [task for task in asyncio.all_tasks() if task.get_name() == "timeguard"]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
async def admin_client(admin_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Requests as the Supervisor ingress proxy forwards them for the owner."""
    transport = httpx.ASGITransport(app=admin_app, client=(TRUSTED, 1))
    headers = {"X-Remote-User-Id": OWNER}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        yield client
