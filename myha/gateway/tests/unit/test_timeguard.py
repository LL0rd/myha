"""Wall-clock plausibility guard (E26): high-water mark, monotonic jumps, host time sync."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from myha_gateway.db import (
    create_engine,
    ensure_instance,
    run_migrations,
    session_factory,
    write_session,
)
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.db.models import Instance
from myha_gateway.ha.supervisor import HostInfo, SupervisorUnavailable
from myha_gateway.services.timeguard import TimeGuard, system_audit

MARK = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    async with write_session(engine) as session:
        (await ensure_instance(session)).last_seen_clock = MARK
    yield engine
    await engine.dispose()


class Recorder:
    """Audit stub: records each operation name."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def __call__(self, session: AsyncSession, operation: str) -> None:
        self.events.append(operation)


async def instance(engine: AsyncEngine) -> Instance:
    async with session_factory(engine)() as session:
        return (await session.execute(select(Instance))).scalar_one()


def guard(engine: AsyncEngine, audit: Recorder, host: HostInfo | None = None) -> TimeGuard:
    async def host_info() -> HostInfo | None:
        return host

    return TimeGuard(engine, host_info, audit)


async def test_first_check_is_trusted_and_persists_the_mark(engine: AsyncEngine) -> None:
    audit = Recorder()
    now = MARK + timedelta(minutes=5)
    assert await guard(engine, audit).check(now, 100.0) is True
    row = await instance(engine)
    assert row.time_trusted is True
    assert row.last_seen_clock == now
    assert audit.events == []  # no transition, no audit


async def test_wall_clock_behind_the_mark_is_untrusted(engine: AsyncEngine) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit)
    assert await timeguard.check(MARK - timedelta(minutes=5), 100.0) is False
    assert await timeguard.check(MARK - timedelta(minutes=4), 160.0) is False
    row = await instance(engine)
    assert row.time_trusted is False
    assert row.last_seen_clock == MARK  # never moves backwards
    assert audit.events == ["time.untrusted"]  # once per transition, not per check


@pytest.mark.parametrize("offset_s", [61, -61])
async def test_jump_against_the_monotonic_clock_is_untrusted(
    engine: AsyncEngine, offset_s: int
) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit)
    start = MARK + timedelta(hours=1)
    assert await timeguard.check(start, 1000.0) is True
    # 60 s of monotonic time, 60 s + offset of wall time; both stay ahead of the mark
    assert await timeguard.check(start + timedelta(seconds=60 + offset_s), 1060.0) is False
    assert (await instance(engine)).time_trusted is False
    assert audit.events == ["time.untrusted"]


async def test_drift_up_to_60_s_is_tolerated(engine: AsyncEngine) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit)
    assert await timeguard.check(MARK + timedelta(seconds=1), 1000.0) is True
    assert await timeguard.check(MARK + timedelta(seconds=121), 1060.0) is True
    assert audit.events == []


async def test_host_clock_not_synchronized_is_untrusted(engine: AsyncEngine) -> None:
    audit = Recorder()
    now = MARK + timedelta(seconds=1)
    assert await guard(engine, audit, HostInfo(dt_synchronized=False)).check(now, 1.0) is False
    assert (await instance(engine)).time_trusted is False
    assert audit.events == ["time.untrusted"]


@pytest.mark.parametrize(
    "host",
    [
        None,
        HostInfo(dt_synchronized=None),
        HostInfo(dt_synchronized=0),  # type: ignore[arg-type]  # not a bool: no signal
        HostInfo(dt_synchronized="false"),  # type: ignore[arg-type]
    ],
)
async def test_unknown_host_clock_is_ignored(engine: AsyncEngine, host: HostInfo | None) -> None:
    now = MARK + timedelta(seconds=1)
    assert await guard(engine, Recorder(), host).check(now, 1.0) is True


async def test_unreachable_or_missing_supervisor_is_ignored(engine: AsyncEngine) -> None:
    async def unreachable() -> HostInfo | None:
        raise SupervisorUnavailable("GET /host/info: ConnectError")

    now = MARK + timedelta(seconds=1)
    assert await TimeGuard(engine, unreachable, Recorder()).check(now, 1.0) is True
    assert await TimeGuard(engine, None, Recorder()).check(now, 2.0) is True


async def test_plausible_check_restores_trust_with_one_audit(engine: AsyncEngine) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit)
    assert await timeguard.check(MARK - timedelta(seconds=10), 100.0) is False
    # the clock is corrected: ahead of the mark, consistent with the monotonic clock
    assert await timeguard.check(MARK + timedelta(seconds=50), 160.0) is True
    assert await timeguard.check(MARK + timedelta(seconds=110), 220.0) is True
    row = await instance(engine)
    assert row.time_trusted is True
    assert row.last_seen_clock == MARK + timedelta(seconds=50)  # written with the transition
    assert audit.events == ["time.untrusted", "time.trusted"]


async def test_forward_jump_does_not_move_the_mark(engine: AsyncEngine) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit)
    assert await timeguard.check(MARK + timedelta(minutes=1), 1000.0) is True
    assert await timeguard.check(MARK + timedelta(days=365, minutes=2), 1060.0) is False
    assert (await instance(engine)).last_seen_clock == MARK + timedelta(minutes=1)
    # the correction back is itself a jump against the last observation ...
    assert await timeguard.check(MARK + timedelta(minutes=3), 1120.0) is False
    # ... and the next check trusts the clock again: the mark never reached the future
    assert await timeguard.check(MARK + timedelta(minutes=4), 1180.0) is True
    row = await instance(engine)
    assert row.time_trusted is True
    assert row.last_seen_clock == MARK + timedelta(minutes=4)
    assert audit.events == ["time.untrusted", "time.trusted"]


@pytest.mark.parametrize("synchronized", [True, False])
async def test_synchronized_host_clock_is_authoritative_behind_the_mark(
    engine: AsyncEngine, synchronized: bool
) -> None:
    audit = Recorder()
    now = MARK - timedelta(hours=1)
    timeguard = guard(engine, audit, HostInfo(dt_synchronized=synchronized))
    assert await timeguard.check(now, 1.0) is synchronized
    row = await instance(engine)
    assert row.time_trusted is synchronized
    assert row.last_seen_clock == (now if synchronized else MARK)
    assert audit.events == ([] if synchronized else ["time.untrusted"])


async def test_synchronized_host_clock_is_authoritative_after_a_jump(engine: AsyncEngine) -> None:
    audit = Recorder()
    timeguard = guard(engine, audit, HostInfo(dt_synchronized=True))
    assert await timeguard.check(MARK + timedelta(minutes=1), 1000.0) is True
    assert await timeguard.check(MARK + timedelta(hours=2), 1060.0) is True
    assert (await instance(engine)).last_seen_clock == MARK + timedelta(hours=2)
    assert audit.events == []


async def test_mark_is_written_at_most_every_five_minutes(engine: AsyncEngine) -> None:
    updates: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.startswith("UPDATE instance"):
            updates.append(statement)

    timeguard = guard(engine, Recorder())
    for minute in (5, 6, 7):
        assert await timeguard.check(MARK + timedelta(minutes=minute), 60.0 * minute) is True
    assert len(updates) == 1
    assert (await instance(engine)).last_seen_clock == MARK + timedelta(minutes=5)


async def test_no_write_session_when_nothing_is_due(engine: AsyncEngine) -> None:
    begins: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement == "BEGIN IMMEDIATE":
            begins.append(statement)

    timeguard = guard(engine, Recorder())
    assert await timeguard.check(MARK + timedelta(minutes=1), 60.0) is True
    assert await timeguard.check(MARK + timedelta(minutes=2), 120.0) is True
    assert begins == []  # trusted, under five minutes of advance
    assert await timeguard.check(MARK - timedelta(minutes=10), 180.0) is False
    assert len(begins) == 1  # the transition
    assert await timeguard.check(MARK - timedelta(minutes=9), 240.0) is False
    assert await timeguard.check(MARK - timedelta(minutes=8), 300.0) is False
    assert len(begins) == 1  # still untrusted, nothing due


async def test_system_audit_writes_the_transition(engine: AsyncEngine) -> None:
    timeguard = TimeGuard(engine, None, system_audit)
    assert await timeguard.check(MARK - timedelta(minutes=1), 1.0) is False
    async with session_factory(engine)() as session:
        rows = (await session.execute(select(AuditRow))).scalars().all()
    assert [(row.actor, row.operation, row.result) for row in rows] == [
        ("system", "time.untrusted", "ok")
    ]


async def test_failed_transition_is_retried_and_audited_once(engine: AsyncEngine) -> None:
    audit = Recorder()
    calls: list[str] = []

    async def flaky(session: AsyncSession, operation: str) -> None:
        calls.append(operation)
        if len(calls) == 1:
            raise RuntimeError("audit failed")  # rolls the write session back
        await audit(session, operation)

    timeguard = TimeGuard(engine, None, flaky)
    with pytest.raises(RuntimeError, match="audit failed"):
        await timeguard.check(MARK - timedelta(minutes=2), 100.0)
    assert (await instance(engine)).time_trusted is True  # nothing committed
    assert await timeguard.check(MARK - timedelta(minutes=1), 160.0) is False
    assert (await instance(engine)).time_trusted is False
    assert audit.events == ["time.untrusted"]
