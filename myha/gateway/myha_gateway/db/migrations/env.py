"""Alembic environment. The app hands in its connection through ``run_migrations``; the CLI
opens ``MYHA_DB_URL`` (else the placeholder in ``alembic.ini``) with the app's engine. Both run
through ``myha_gateway.db.migrate``: one IMMEDIATE transaction, foreign keys checked."""

import asyncio
import os
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import Connection, make_url

from myha_gateway.db import create_engine, migrate
from myha_gateway.db.models import Base
from myha_gateway.db.types import UtcDateTime


def _render_item(kind: str, obj: Any, autogen: AutogenContext) -> str | Literal[False]:
    """Render ``UtcDateTime`` as the text it stores, so migrations never import app code."""
    if kind == "type" and isinstance(obj, UtcDateTime):
        return "sa.String()"
    return False


def _migrate(connection: Connection) -> None:
    # render_as_batch: SQLite alters most columns only by rebuilding the table. Rebuilding
    # audit_event needs table_kwargs={"sqlite_autoincrement": True}, or AUTOINCREMENT is lost.
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        render_item=_render_item,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _migrate_cli(url: str) -> None:
    database = make_url(url).database
    assert database, "set MYHA_DB_URL to sqlite+aiosqlite:///<path>"
    engine = create_engine(database)
    try:
        await migrate(engine, _migrate)
    finally:
        await engine.dispose()


_connection = context.config.attributes.get("connection")
if _connection is not None:
    _migrate(_connection)
else:
    _url = os.environ.get("MYHA_DB_URL") or context.config.get_main_option("sqlalchemy.url", "")
    asyncio.run(_migrate_cli(_url))
