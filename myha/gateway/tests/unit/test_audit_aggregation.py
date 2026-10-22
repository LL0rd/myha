"""Denial folding: one row per fold key and UTC minute, NULL-safe for anonymous events."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.db import create_engine, run_migrations, session_factory, write_session
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.services import audit as audit_module
from myha_gateway.services.audit import AuditEvent, audit

ANON = AuditEvent(
    operation="auth.redeem",
    result="denied",
    reason_code="UNAUTHENTICATED",
    anon_key="auth.redeem|UNAUTHENTICATED|2026-10-22T12:00|203.0.113.9",
)
GUEST = AuditEvent(
    actor="prn_guest",
    effective_principal="prn_guest",
    session_id="ses_1",
    operation="action.execute",
    result="denied",
    reason_code="OUT_OF_SCOPE",
    resource_id="res_203_light",
)


class Clock(datetime):
    """Stands in for ``datetime`` in the audit module: ``now`` is whatever the test set."""

    current = datetime(2026, 10, 22, 12, 0, 59, 999_999, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object = None) -> "Clock":
        return cls.current


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> type[Clock]:
    """Frozen at the last microsecond of a minute: a fold that spans minutes would show."""
    monkeypatch.setattr(audit_module, "datetime", Clock)
    monkeypatch.setattr(Clock, "current", Clock.current)
    return Clock


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    yield engine
    await engine.dispose()


async def write(engine: AsyncEngine, *events: AuditEvent, aggregate: bool = True) -> None:
    for event in events:
        async with write_session(engine) as session:
            await audit(session, event, aggregate=aggregate)


async def rows(engine: AsyncEngine) -> list[AuditRow]:
    async with session_factory(engine)() as session:
        return list((await session.execute(select(AuditRow).order_by(AuditRow.id))).scalars())


async def test_anonymous_denials_in_one_minute_fold_into_one_row(engine: AsyncEngine) -> None:
    await write(engine, ANON, ANON, ANON)
    [row] = await rows(engine)
    assert row.aggregate_count == 3
    assert (row.actor, row.effective_principal, row.session_id) == ("anonymous", None, None)


async def test_anonymous_denials_in_different_minutes_do_not_fold(
    engine: AsyncEngine, clock: type[Clock]
) -> None:
    await write(engine, ANON)
    clock.current += timedelta(microseconds=1)
    await write(engine, ANON)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_anonymous_denials_with_different_keys_do_not_fold(engine: AsyncEngine) -> None:
    other = AuditEvent(
        operation="auth.redeem",
        result="denied",
        reason_code="UNAUTHENTICATED",
        anon_key="auth.redeem|UNAUTHENTICATED|2026-10-22T12:00|198.51.100.7",
    )
    await write(engine, ANON, other)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_anonymous_denial_without_a_fold_key_is_refused(engine: AsyncEngine) -> None:
    keyless = AuditEvent(operation="auth.redeem", result="denied", reason_code="UNAUTHENTICATED")
    with pytest.raises(ValueError, match="anon_key"):
        await write(engine, keyless)


async def test_session_denials_fold_by_session_operation_and_reason(engine: AsyncEngine) -> None:
    other_reason = AuditEvent(**(GUEST.__dict__ | {"reason_code": "TIME_WINDOW"}))
    other_session = AuditEvent(**(GUEST.__dict__ | {"session_id": "ses_2"}))
    await write(engine, GUEST, GUEST, other_reason, other_session, GUEST)
    counts = [(row.session_id, row.reason_code, row.aggregate_count) for row in await rows(engine)]
    assert counts == [
        ("ses_1", "OUT_OF_SCOPE", 3),
        ("ses_1", "TIME_WINDOW", 1),
        ("ses_2", "OUT_OF_SCOPE", 1),
    ]


async def test_session_denials_without_a_reason_fold_null_safe(engine: AsyncEngine) -> None:
    no_reason = AuditEvent(**(GUEST.__dict__ | {"reason_code": None}))
    await write(engine, no_reason, no_reason)
    assert [row.aggregate_count for row in await rows(engine)] == [2]


async def test_session_denials_in_different_minutes_do_not_fold(
    engine: AsyncEngine, clock: type[Clock]
) -> None:
    await write(engine, GUEST)
    clock.current += timedelta(microseconds=1)
    await write(engine, GUEST)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_successful_events_never_fold(engine: AsyncEngine) -> None:
    ok = AuditEvent(**(GUEST.__dict__ | {"result": "ok", "reason_code": None}))
    await write(engine, ok, ok)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_without_aggregate_every_denial_is_a_row(engine: AsyncEngine) -> None:
    await write(engine, ANON, ANON, aggregate=False)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_fold_keeps_the_first_timestamp(engine: AsyncEngine, clock: type[Clock]) -> None:
    clock.current -= timedelta(seconds=30)
    await write(engine, GUEST)
    clock.current += timedelta(seconds=29)
    await write(engine, GUEST)
    [row] = await rows(engine)
    assert (row.ts, row.aggregate_count) == (datetime(2026, 10, 22, 12, 0, 29, 999_999, UTC), 2)
