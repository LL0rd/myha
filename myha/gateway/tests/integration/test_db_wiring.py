"""The admin lifespan opens the database before anything else and stops on failure."""

import asyncio
import logging
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

import myha_gateway.__main__ as gateway
from myha_gateway.app import create_admin_app
from myha_gateway.deps import AppContext
from myha_gateway.settings import Settings
from tests.helpers import fake_supervisor


async def start_and_stop_admin(data_dir: Path) -> AppContext:
    ctx = await AppContext.create(Settings(data_dir=data_dir))
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        pass
    return ctx


async def test_startup_creates_the_database_and_loads_the_instance(tmp_path: Path) -> None:
    first = await start_and_stop_admin(tmp_path)
    assert (tmp_path / "state" / "myha.sqlite").is_file()
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert first.instance is not None
    assert len(first.instance.short_id) == 8 and first.instance.policy_version == 1

    second = await start_and_stop_admin(tmp_path)
    assert second.instance is not None and second.instance.id == first.instance.id


def directory_instead_of_file(database: Path) -> None:
    database.mkdir(parents=True)


def unknown_revision(database: Path) -> None:
    """What an add-on downgrade leaves behind: a schema newer than this build knows."""
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as raw:
        raw.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        raw.execute("INSERT INTO alembic_version VALUES ('9999')")
        raw.commit()


@pytest.mark.parametrize("break_database", [directory_instead_of_file, unknown_revision])
def test_unusable_database_stops_the_gateway(
    break_database: Callable[[Path], None], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    break_database(tmp_path / "state" / "myha.sqlite")
    settings = Settings(data_dir=tmp_path, admin_bind="127.0.0.1", admin_port=0)
    with pytest.raises(SystemExit) as stopped:
        asyncio.run(gateway._serve(settings))
    assert stopped.value.code == 3  # uvicorn's startup failure; the s6 finish script halts
    aborted = [r for r in caplog.records if r.getMessage().startswith("startup aborted")]
    assert len(aborted) == 1 and aborted[0].levelno == logging.ERROR
    assert "myha.sqlite is not usable" in aborted[0].getMessage() and aborted[0].exc_info
