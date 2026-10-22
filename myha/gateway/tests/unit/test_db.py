"""SQLite engine, pragmas, schema, the initial migration and the instance row."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, event, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.db import (
    create_engine,
    ensure_instance,
    migrate,
    run_migrations,
    session_factory,
    write_session,
)
from myha_gateway.db.models import (
    Base,
    Command,
    Credential,
    Instance,
    Invite,
    Membership,
    Principal,
    Session,
)

GATEWAY = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
TABLES = {
    "instance",
    "unit",
    "resource_binding",
    "principal",
    "credential",
    "group",
    "membership",
    "role_template",
    "role_version",
    "assignment",
    "fence",
    "stay",
    "invite",
    "session",
    "refresh_token",
    "ws_ticket",
    "pairing_request",
    "dashboard",
    "command",
    "audit_event",
}


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    yield engine
    await engine.dispose()


def principal(principal_id: str) -> Principal:
    return Principal(
        id=principal_id, type="resident", status="active", display_name="P", created_at=NOW
    )


def invite(invite_id: str, secret_hash: str, redeem_until: datetime = NOW) -> Invite:
    return Invite(
        id=invite_id,
        secret_hash=secret_hash,
        purpose="guest_test",
        device_slot=1,
        redeem_until=redeem_until,
        created_at=NOW,
    )


def command(command_id: str, idempotency_key: str) -> Command:
    return Command(
        id=command_id,
        session_id="ses_1",
        idempotency_key=idempotency_key,
        body_hash="body",
        resource_id="res_1",
        action="light.on",
        normalized_params={},
        policy_version=1,
        status="accepted",
        created_at=NOW,
        updated_at=NOW,
    )


async def test_every_connection_sets_pragmas(engine: AsyncEngine) -> None:
    expected = {
        "journal_mode": "wal",
        "foreign_keys": 1,  # also back on for the connection that ran the migrations
        "secure_delete": 1,
        "busy_timeout": 5000,
        "synchronous": 2,  # FULL
    }
    async with engine.connect() as first, engine.connect() as second:
        for connection in (first, second):
            pragmas = {
                name: (await connection.exec_driver_sql(f"PRAGMA {name}")).scalar()
                for name in expected
            }
            assert pragmas == expected


async def test_migrations_create_all_tables_and_rerun_as_no_op(engine: AsyncEngine) -> None:
    await run_migrations(engine)
    async with engine.connect() as connection:
        result = await connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = set(result.scalars())
    assert tables == TABLES | {"alembic_version"}
    assert set(Base.metadata.tables) == TABLES


async def test_migrated_schema_matches_models(engine: AsyncEngine) -> None:
    def diff(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(diff) == []


async def test_foreign_keys_are_enforced(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        session.add(principal("prn_1"))
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        async with write_session(engine) as session:
            session.add(
                Membership(id="mem_1", principal_id="prn_1", group_id="grp_x", valid_from=NOW)
            )


async def test_unique_constraints(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        session.add_all([principal("prn_1"), principal("prn_2")])
        session.add(
            Session(
                id="ses_1",
                token_hash="token",
                audience="browser",
                purpose="test",
                unit_ids=[],
                not_after=NOW,
                last_seen_at=NOW,
                csrf_secret="csrf",
                created_at=NOW,
            )
        )
    async with write_session(engine) as session:
        session.add_all([invite("inv_1", "hash"), command("cmd_1", "key")])
        session.add(
            Credential(
                principal_id="prn_1",
                username_normalized="anna",
                password_hash="secret-hash",
                changed_at=NOW,
                must_reset=False,
            )
        )
    duplicates = [
        (
            Credential(
                principal_id="prn_2",
                username_normalized="anna",
                password_hash="secret-hash",
                changed_at=NOW,
                must_reset=False,
            ),
            "credential.username_normalized",
        ),
        (invite("inv_2", "hash"), "invite.secret_hash"),
        (command("cmd_2", "key"), "command.session_id, command.idempotency_key"),
    ]
    for row, columns in duplicates:
        with pytest.raises(IntegrityError, match=f"UNIQUE constraint failed: {columns}") as error:
            async with write_session(engine) as session:
                session.add(row)
        assert "secret-hash" not in str(error.value)  # bound parameters stay hidden


async def test_ensure_instance_creates_the_row_once_with_defaults(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        first = await ensure_instance(session)
        assert first.version == 1  # flushed, so the defaults are filled
    async with write_session(engine) as session:
        second = await ensure_instance(session)
        assert await ensure_instance(session) is second

    assert (second.id, second.server_secret) == (first.id, first.server_secret)
    assert second.last_seen_clock == first.last_seen_clock
    assert second.last_seen_clock.tzinfo is UTC
    assert first.id.startswith("inst_")
    assert len(first.short_id) == 8
    assert len(first.server_secret) == 43
    assert second.name == "myHA"
    assert (second.policy_version, second.security_generation, second.version) == (1, 1, 1)
    assert second.session_limits == {
        "guest_browser_days": 30,
        "resident_browser_days": 30,
        "resident_refresh_days": 30,
        "display_days": 365,
        "native_access_minutes": 15,
    }
    assert (second.audit_retention_days, second.time_trusted) == (30, True)
    assert (second.setup_state, second.timezone) == ("fresh", "UTC")
    assert (second.tls_mode, second.public_origin, second.operator_contact) == (None, None, None)
    assert (second.trust_pins, second.proxy_cidrs) == ([], [])
    async with engine.connect() as connection:
        rows = await connection.exec_driver_sql("SELECT count(*), time_trusted FROM instance")
        assert rows.one() == (1, 1)  # one row; booleans stored as integers


async def test_utc_datetime_round_trip_and_naive_rejection(engine: AsyncEngine) -> None:
    local = datetime(2026, 10, 22, 12, 30, tzinfo=timezone(timedelta(hours=3)))
    async with write_session(engine) as session:
        session.add(invite("inv_1", "hash-1", redeem_until=local))
    async with session_factory(engine)() as session:
        stored = await session.scalar(text("SELECT redeem_until FROM invite"))
        loaded = await session.scalar(select(Invite.redeem_until))
    assert stored == "2026-10-22T09:30:00.000000Z"
    assert loaded == local
    assert loaded is not None and loaded.tzinfo is UTC

    with pytest.raises(StatementError, match="naive datetime"):
        async with write_session(engine) as session:
            session.add(invite("inv_2", "hash-2", redeem_until=datetime(2026, 10, 22, 12, 30)))

    async with write_session(engine) as session:
        await session.execute(text("UPDATE invite SET redeem_until = '2026-10-22T09:30:00'"))
    with pytest.raises(ValueError, match="naive datetime '2026-10-22T09:30:00' in the database"):
        async with session_factory(engine)() as session:
            await session.scalar(select(Invite.redeem_until))


async def test_write_sessions_begin_immediate_and_reads_stay_deferred(
    engine: AsyncEngine,
) -> None:
    begins: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.startswith("BEGIN"):
            begins.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    async with write_session(engine) as session:
        await session.scalar(select(Instance))
    async with session_factory(engine)() as session:
        await session.scalar(select(Instance))
    assert begins == ["BEGIN IMMEDIATE", "BEGIN"]


async def test_parallel_writers_queue_instead_of_failing(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        start = (await ensure_instance(session)).policy_version

    async def bump() -> None:
        async with write_session(engine) as session:
            instance = await ensure_instance(session)
            instance.policy_version += 1

    await asyncio.gather(*(bump() for _ in range(15)))
    async with session_factory(engine)() as session:
        assert await session.scalar(select(Instance.policy_version)) == start + 15


async def test_concurrent_first_starts_create_one_instance(engine: AsyncEngine) -> None:
    async def start() -> str:
        async with write_session(engine) as session:
            return (await ensure_instance(session)).id

    first, second = await asyncio.gather(start(), start())
    assert first == second
    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("SELECT count(*) FROM instance")).scalar() == 1


async def test_raw_inserts_get_server_defaults(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        await session.execute(text("INSERT INTO unit (id, name, sort) VALUES ('unit_1', 'A', 0)"))
        assert await session.scalar(text("SELECT version FROM unit")) == 1


async def test_migrate_rolls_back_a_step_that_breaks_foreign_keys(engine: AsyncEngine) -> None:
    def orphan(connection: Connection) -> None:  # accepted only because foreign keys are off
        connection.exec_driver_sql(
            "INSERT INTO membership (id, principal_id, group_id, valid_from) "
            "VALUES ('mem_1', 'prn_x', 'grp_x', '2026-10-22T12:00:00.000000Z')"
        )

    with pytest.raises(RuntimeError, match="foreign key violations"):
        await migrate(engine, orphan)
    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("SELECT count(*) FROM membership")).scalar() == 0
        assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1


def test_cli_migrates_with_the_app_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "cli.sqlite"
    monkeypatch.setenv("MYHA_DB_URL", f"sqlite+aiosqlite:///{database}")
    config = Config(str(GATEWAY / "alembic.ini"))
    alembic_command.upgrade(config, "head")
    alembic_command.check(config)  # raises when models and migrations drift apart
    with closing(sqlite3.connect(database)) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("wal",)  # the app's pragmas
