"""Denial folding: one row per fold key and UTC minute, NULL-safe for anonymous events."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.db import create_engine, run_migrations, session_factory, write_session
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.services.audit import AuditEvent, audit

ANON = AuditEvent(
    operation="auth.redeem",
    result="denied",
    reason_code="UNAUTHENTICATED",
    anon_key="auth.redeem|UNAUTHENTICATED|203.0.113.9",
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


MINUTE_END = datetime(2026, 10, 22, 12, 0, 59, 999_999, tzinfo=UTC)  # a fold over minutes shows


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    yield engine
    await engine.dispose()


async def write(
    engine: AsyncEngine, *events: AuditEvent, aggregate: bool = True, now: datetime = MINUTE_END
) -> None:
    for event in events:
        async with write_session(engine) as session:
            await audit(session, event, aggregate=aggregate, now=now)


async def rows(engine: AsyncEngine) -> list[AuditRow]:
    async with session_factory(engine)() as session:
        return list((await session.execute(select(AuditRow).order_by(AuditRow.id))).scalars())


async def test_anonymous_denials_in_one_minute_fold_into_one_row(engine: AsyncEngine) -> None:
    await write(engine, ANON, ANON, ANON)
    [row] = await rows(engine)
    assert row.aggregate_count == 3
    assert (row.actor, row.effective_principal, row.session_id) == ("anonymous", None, None)


async def test_anonymous_denials_in_different_minutes_do_not_fold(engine: AsyncEngine) -> None:
    await write(engine, ANON)
    await write(engine, ANON, now=MINUTE_END + timedelta(microseconds=1))
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_anonymous_denials_with_different_keys_do_not_fold(engine: AsyncEngine) -> None:
    other = AuditEvent(
        operation="auth.redeem",
        result="denied",
        reason_code="UNAUTHENTICATED",
        anon_key="auth.redeem|UNAUTHENTICATED|198.51.100.7",
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


async def test_session_denials_in_different_minutes_do_not_fold(engine: AsyncEngine) -> None:
    await write(engine, GUEST)
    await write(engine, GUEST, now=MINUTE_END + timedelta(microseconds=1))
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_successful_events_never_fold(engine: AsyncEngine) -> None:
    ok = AuditEvent(**(GUEST.__dict__ | {"result": "ok", "reason_code": None}))
    await write(engine, ok, ok)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_without_aggregate_every_denial_is_a_row(engine: AsyncEngine) -> None:
    await write(engine, ANON, ANON, aggregate=False)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


async def test_fold_keeps_the_first_timestamp(engine: AsyncEngine) -> None:
    await write(engine, GUEST, now=MINUTE_END - timedelta(seconds=30))
    await write(engine, GUEST, now=MINUTE_END - timedelta(seconds=1))
    [row] = await rows(engine)
    assert (row.ts, row.aggregate_count) == (datetime(2026, 10, 22, 12, 0, 29, 999_999, UTC), 2)


async def test_parallel_denials_fold_into_one_row(engine: AsyncEngine) -> None:
    async def one() -> None:
        async with write_session(engine) as session:
            await audit(session, ANON, aggregate=True, now=MINUTE_END)

    await asyncio.gather(one(), one())
    assert [row.aggregate_count for row in await rows(engine)] == [2]


async def test_anonymous_fold_does_not_mix_operations(engine: AsyncEngine) -> None:
    other = AuditEvent(**(ANON.__dict__ | {"operation": "auth.other"}))
    await write(engine, ANON, other)
    assert [row.aggregate_count for row in await rows(engine)] == [1, 1]


@pytest.mark.parametrize(
    "fields",
    [
        {"actor": "prn_guest", "anon_key": "k"},
        {"session_id": "ses_1", "anon_key": "k"},
        {"effective_principal": "prn_guest", "anon_key": "k"},
        {"session_id": "ses_1"},  # actor "anonymous" by default
        {"effective_principal": "prn_guest"},
    ],
)
def test_anonymous_events_have_the_anonymous_actor_and_no_session(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="anonymous"):
        AuditEvent(operation="auth.redeem", result="denied", **fields)


def test_system_events_need_no_session() -> None:
    AuditEvent(operation="policy.changed", result="ok", actor="system")
