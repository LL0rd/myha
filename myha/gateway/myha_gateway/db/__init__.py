"""SQLite access: engine with pragmas, read and write sessions, migrations, instance row.

``synchronous=FULL`` syncs the WAL on every commit: a power loss must not roll back a committed
revocation, checkout, invite redemption or refresh rotation, and one fsync per write commit is
affordable at this write rate.
"""

import contextlib
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from myha_gateway.db.models import Instance
from myha_gateway.ids import new_id, new_secret

# busy_timeout first: switching to WAL briefly needs a lock that another connection may hold.
_PRAGMAS = (
    "PRAGMA busy_timeout=5000",
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA secure_delete=ON",
    "PRAGMA synchronous=FULL",
)
_MIGRATIONS = Path(__file__).parent / "migrations"


class _WriteLock:
    """Shared by every context copied from inside a write session: closing it frees them all."""

    open = True


# Set inside write_session; tasks created there inherit the same lock object.
_write_lock: ContextVar[_WriteLock | None] = ContextVar("write_lock", default=None)


def assert_no_write_session(what: str = "network I/O") -> None:
    """Raise ``RuntimeError`` if called inside a write session (see ``write_session``)."""
    lock = _write_lock.get()
    if lock is not None and lock.open:
        raise RuntimeError(f"{what} inside a write session")


class _ReadSession(AsyncSession):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        assert_no_write_session("read session")
        super().__init__(*args, **kwargs)


def create_engine(path: Path | str) -> AsyncEngine:
    """Engine for the SQLite file at ``path``."""
    # hide_parameters: errors must not echo bound values such as instance.server_secret.
    url = URL.create("sqlite+aiosqlite", database=str(path))
    engine = create_async_engine(url, hide_parameters=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # The driver must not emit BEGIN itself; _on_begin chooses the mode.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        for pragma in _PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(connection: Connection) -> None:
        write = connection.get_execution_options().get("myha_write", False)  # see write_session
        connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions for reading; their transactions start deferred.

    Keep them short: an open read transaction keeps the WAL from being checkpointed.
    """
    return async_sessionmaker(engine, class_=_ReadSession, expire_on_commit=False)


@asynccontextmanager
async def write_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One transaction that takes the write lock up front, commits on exit, rolls back on error.

    ``BEGIN IMMEDIATE`` makes concurrent writers wait on ``busy_timeout`` instead of failing
    with ``database is locked`` when a deferred read upgrades to a write. The lock blocks every
    other writer and SQLite's busy handler is not FIFO, so inside a write session:

    - use only this one session and pass it down to the services; no second session of any
      kind, not even a read session: it needs another pool connection while the writers queued
      behind the lock hold the rest, which stalls until ``busy_timeout`` and fails them with
      ``database is locked``;
    - no nested write session (it waits on its own lock);
    - no Home Assistant or other network I/O.

    ``assert_no_write_session`` enforces the rules: here against nesting, in the read session
    factory and in the network clients. It checks only where a read session is created, so one
    created before and first used inside the write session goes unnoticed.
    """
    assert_no_write_session("nested write session")
    writer = engine.execution_options(myha_write=True)
    lock = _WriteLock()
    token = _write_lock.set(lock)
    try:
        async with AsyncSession(writer, expire_on_commit=False) as session, session.begin():
            yield session
    finally:
        lock.open = False
        # An async generator finalised at loop shutdown runs in a foreign context.
        with contextlib.suppress(ValueError):
            _write_lock.reset(token)


async def run_migrations(engine: AsyncEngine) -> None:
    """Upgrade the schema to the newest revision through ``migrate``."""

    def upgrade(connection: Connection) -> None:
        config = Config()
        config.set_main_option("script_location", str(_MIGRATIONS))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    await migrate(engine, upgrade)


async def migrate(engine: AsyncEngine, step: Callable[[Connection], None]) -> None:
    """Run the schema change ``step`` atomically; the app and the Alembic CLI both use this.

    One ``BEGIN IMMEDIATE`` transaction with foreign keys off, because batch migrations rebuild
    tables by DROP and RENAME; ``PRAGMA foreign_keys`` is a no-op inside a transaction, so it
    is switched around it. ``PRAGMA foreign_key_check`` must come back empty before the commit.
    """

    def run(connection: Connection) -> None:
        _outside_transaction(connection, "PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                step(connection)
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                if violations:  # raising rolls the whole step back
                    raise RuntimeError(f"foreign key violations: {violations[:5]}")
        finally:
            _outside_transaction(connection, "PRAGMA foreign_keys=ON")

    async with engine.execution_options(myha_write=True).connect() as connection:
        await connection.run_sync(run)


def _outside_transaction(connection: Connection, sql: str) -> None:
    """Execute on the driver connection, bypassing the BEGIN that SQLAlchemy would emit."""
    cursor = connection.connection.cursor()
    cursor.execute(sql)
    cursor.close()


async def ensure_instance(session: AsyncSession) -> Instance:
    """The instance row, created with first-start defaults if missing; a second row raises.

    Call it in a ``write_session`` so that two concurrent starts cannot both insert.
    """
    instance = (await session.execute(select(Instance))).scalar_one_or_none()
    if instance is None:
        instance = Instance(
            id=new_id("inst"),
            short_id=secrets.token_hex(4),
            name="myHA",
            proxy_cidrs=[],
            trust_pins=[],
            timezone="UTC",
            policy_version=1,
            security_generation=1,
            setup_state="fresh",
            session_limits={
                "guest_browser_days": 30,
                "resident_browser_days": 30,
                "resident_refresh_days": 30,
                "display_days": 365,
                "native_access_minutes": 15,
            },
            audit_retention_days=30,
            time_trusted=True,
            last_seen_clock=datetime.now(UTC),
            server_secret=new_secret(),
        )
        session.add(instance)
        await session.flush()  # fills the defaults, e.g. version
    return instance
