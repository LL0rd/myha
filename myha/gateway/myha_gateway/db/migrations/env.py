"""Alembic environment: migrates the connection handed in by ``run_migrations``, else the
database at ``MYHA_DB_URL`` (or the placeholder in ``alembic.ini``) for the CLI."""

import asyncio
import os
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from myha_gateway.db.models import Base
from myha_gateway.db.types import UtcDateTime


def _render_item(kind: str, obj: Any, autogen: AutogenContext) -> str | Literal[False]:
    """Render our column types with an import instead of a bare module path."""
    if kind == "type" and isinstance(obj, UtcDateTime):
        autogen.imports.add("from myha_gateway.db.types import UtcDateTime")
        return "UtcDateTime()"
    return False


def _migrate(connection: Connection) -> None:
    # render_as_batch: SQLite alters most columns only by rebuilding the table.
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _migrate_url(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


_connection = context.config.attributes.get("connection")
if _connection is not None:
    _migrate(_connection)
else:
    _url = os.environ.get("MYHA_DB_URL") or context.config.get_main_option("sqlalchemy.url")
    assert _url, "set MYHA_DB_URL"
    asyncio.run(_migrate_url(_url))
