"""``seed_room203`` and ``myha-gateway dev-seed room203`` against the simulator."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from myha_gateway import __main__ as gateway
from myha_gateway.adapters.base import DeviceCapabilities
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db import models as db
from myha_gateway.deps import AppContext
from myha_gateway.settings import Settings
from tests.helpers import SIM_TOKEN, SimHandle, seed_room203

ORIGIN = "http://guest.test"


@pytest.fixture
async def ctx(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    """A migrated database with its instance row and the HA client on the simulator."""
    settings = Settings(
        data_dir=tmp_path,
        supervisor_url=sim.http_url,
        ha_ws_url=sim.ws_url,
        supervisor_token=SIM_TOKEN,
    )
    ctx = await AppContext.create(settings)
    settings.state_dir.mkdir(parents=True)
    await run_migrations(ctx.engine)
    async with write_session(ctx.engine) as session:
        await ensure_instance(session)
    await ctx.ha.start()
    try:
        yield ctx
    finally:
        await ctx.ha.stop()
        await ctx.close()


async def dump(ctx: AppContext) -> dict[str, list[Any]]:
    """Every row of every table, to compare two database states."""
    async with ctx.engine.connect() as connection:
        query = text("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = [t for (t,) in await connection.execute(query)]
        return {
            table: (await connection.execute(text(f'SELECT * FROM "{table}"'))).all()  # noqa: S608
            for table in tables
            if not table.startswith("sqlite_")
        }


async def test_seed_room203_is_idempotent(ctx: AppContext) -> None:
    urls = await seed_room203(ctx.engine, ctx.ha, ORIGIN)
    assert len(urls) == 2 and len(set(urls)) == 2
    assert all(url.startswith(f"{ORIGIN}/join#i=") for url in urls)
    seeded = await dump(ctx)
    assert await seed_room203(ctx.engine, ctx.ha, ORIGIN) == []
    assert await dump(ctx) == seeded

    async with ctx.sessions() as session:
        bindings = {b.id: b for b in (await session.execute(select(db.ResourceBinding))).scalars()}
        stay = (await session.execute(select(db.Stay))).scalar_one()
        role = (await session.execute(select(db.RoleVersion))).scalar_one()
    assert set(bindings) == {
        "res_203_light",
        "res_203_bedside",
        "res_203_trv",
        "res_203_blind",
        "res_203_hum",
        "res_lr_ceiling",
        "res_lr_ac",
    }
    assert {b.status for b in bindings.values()} == {"confirmed"}
    modes = {
        res: DeviceCapabilities.from_json(b.confirmed_capabilities).confirmed_modes
        for res, b in bindings.items()
    }
    assert modes["res_203_trv"] == {"off", "heat"}
    assert modes["res_lr_ac"] == {"off", "cool", "heat"}
    assert bindings["res_203_trv"].capability_fingerprint
    assert (role.role_template_id, role.version_number) == ("room_guest", 1)
    assert any("light.set_level" in grant["actions"] for grant in role.grants)
    assert any("history.read" in deny["actions"] for deny in role.denies)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    assert (stay.unit_id, stay.status) == ("unit_203", "confirmed")
    assert stay.starts_at in (today, today - timedelta(days=1))  # seeded before midnight
    assert stay.ends_at == stay.starts_at + timedelta(days=1, hours=23, minutes=59)


async def test_seed_needs_home_assistant(ctx: AppContext) -> None:
    await ctx.ha.stop()
    with pytest.raises(RuntimeError, match="unreachable"):
        await seed_room203(ctx.engine, ctx.ha, ORIGIN)


async def test_seed_with_a_fixture_entity_missing_writes_nothing(
    ctx: AppContext, sim: SimHandle
) -> None:
    before = await dump(ctx)
    await sim.control.post("entity/remove", {"entity_id": "climate.living_ac"})
    async with asyncio.timeout(5):
        while ctx.ha.state("climate.living_ac") is not None:  # noqa: ASYNC110 (no HA event)
            await asyncio.sleep(0.01)
    with pytest.raises(RuntimeError, match=r"climate\.living_ac missing"):
        await seed_room203(ctx.engine, ctx.ha, ORIGIN)
    assert await dump(ctx) == before


async def run_cli(env: dict[str, str]) -> tuple[int, str, str]:
    """``myha-gateway dev-seed room203`` in its own process; the test loop serves the simulator."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-W",
        "error",
        "-m",
        "myha_gateway",
        "dev-seed",
        "room203",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(60):
        out, err = await process.communicate()
    assert process.returncode is not None
    return process.returncode, out.decode(), err.decode()


async def test_cli_prints_only_the_join_urls(sim: SimHandle, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MYHA_")} | {
        "MYHA_DATA_DIR": str(tmp_path),
        "MYHA_SUPERVISOR_URL": sim.http_url,
        "MYHA_HA_WS_URL": sim.ws_url,
        "SUPERVISOR_TOKEN": SIM_TOKEN,
    }
    code, out, err = await run_cli(env)
    assert (code, out) == (2, "")
    assert err.count("\n") == 1 and "MYHA_PUBLIC_ENABLED=1" in err

    env |= {"MYHA_PUBLIC_ENABLED": "1", "MYHA_PUBLIC_ORIGIN": ORIGIN}
    code, out, err = await run_cli(env)
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 2 and all(line.startswith(f"{ORIGIN}/join#i=") for line in lines)
    for line in lines:
        assert line.split("#i=")[1].split("&")[0] not in err  # no secret in the log
    code, out, _ = await run_cli(env)  # seeded already: nothing new, nothing printed
    assert (code, out) == (0, "")


def test_cli_dev_seed_needs_a_world() -> None:
    with pytest.raises(SystemExit) as exc:
        gateway.main(["dev-seed"])
    assert exc.value.code == 2
