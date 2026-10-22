"""SQLite access: engine with pragmas, read and write sessions, migrations, instance row."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    "PRAGMA synchronous=NORMAL",
)
_MIGRATIONS = Path(__file__).parent / "migrations"


def create_engine(path: Path | str) -> AsyncEngine:
    """Engine for the SQLite file at ``path`` (``":memory:"`` for a single shared connection)."""
    engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))

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
    """Sessions for reading; their transactions start deferred."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def write_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One transaction that takes the write lock up front, commits on exit, rolls back on error.

    ``BEGIN IMMEDIATE`` makes concurrent writers queue on ``busy_timeout``; a deferred read
    that later upgrades to a write fails with ``database is locked`` instead.
    """
    writer = engine.execution_options(myha_write=True)
    async with AsyncSession(writer, expire_on_commit=False) as session, session.begin():
        yield session


async def run_migrations(engine: AsyncEngine) -> None:
    """Upgrade the schema to the newest revision in one transaction."""

    def upgrade(connection: Connection) -> None:
        config = Config()
        config.set_main_option("script_location", str(_MIGRATIONS))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    # shortcut: foreign_keys stays ON in this transaction, so a batch rebuild of a referenced
    # table would fail; the first such migration must switch it OFF before BEGIN.
    async with engine.begin() as connection:
        await connection.run_sync(upgrade)


async def ensure_instance(session: AsyncSession) -> Instance:
    """The instance row, created with first-start defaults if missing.

    Call it in a ``write_session`` so that two concurrent starts cannot both insert.
    """
    instance = await session.scalar(select(Instance))
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
    return instance
