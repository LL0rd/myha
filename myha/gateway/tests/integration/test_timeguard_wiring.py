"""The admin lifespan runs the time guard in the background and stops it on shutdown."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from myha_gateway.app import create_admin_app
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.db.models import Instance
from myha_gateway.deps import AppContext
from myha_gateway.services import timeguard
from myha_gateway.settings import Settings
from tests.helpers import fake_supervisor


async def test_admin_lifespan_runs_the_time_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(timeguard, "CHECK_INTERVAL_S", 0.01)
    checks: list[datetime] = []
    original = timeguard.TimeGuard.check

    async def counted(self: timeguard.TimeGuard, now_wall: datetime, now_mono: float) -> bool:
        checks.append(now_wall)
        return await original(self, now_wall, now_mono)

    monkeypatch.setattr(timeguard.TimeGuard, "check", counted)
    ctx = await AppContext.create(Settings(data_dir=tmp_path))
    ctx.supervisor = fake_supervisor()  # /host/info answers 403: no host time source
    # A mark an hour ahead: the guard's first check must flip the flag.
    ctx.settings.state_dir.mkdir(parents=True)
    await run_migrations(ctx.engine)
    async with write_session(ctx.engine) as session:
        (await ensure_instance(session)).last_seen_clock = datetime.now(UTC) + timedelta(hours=1)
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        assert ctx.timeguard is not None
        async with asyncio.timeout(5):
            while len(checks) < 2:  # noqa: ASYNC110 (no event to wait on)
                await asyncio.sleep(0.01)
        async with ctx.sessions() as session:
            instance = (await session.execute(select(Instance))).scalar_one()
            rows = (await session.execute(select(AuditRow))).scalars().all()
        assert instance.time_trusted is False
        events = [(row.actor, row.operation, row.result) for row in rows]
        assert events == [("system", "time.untrusted", "ok")]
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "timeguard"]


async def test_a_failing_check_never_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(timeguard, "CHECK_INTERVAL_S", 0.01)
    calls: list[int] = []

    async def failing(self: timeguard.TimeGuard, now_wall: datetime, now_mono: float) -> bool:
        calls.append(1)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(timeguard.TimeGuard, "check", failing)
    ctx = await AppContext.create(Settings(data_dir=tmp_path))
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(5):
            while len(calls) < 2:  # noqa: ASYNC110
                await asyncio.sleep(0.01)
    assert "time plausibility check failed" in caplog.text
