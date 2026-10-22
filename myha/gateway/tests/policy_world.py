"""A gateway wired to the simulator with the room 203 world in its database, for loader tests.

Bindings are made from the simulator fixture through the adapters, as the admin would confirm
them: every product mode the device offers, blocked where the adapter blocks.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db import models as db
from myha_gateway.deps import AppContext
from myha_gateway.ha.messages import parse_registry, parse_temperature_unit
from myha_gateway.settings import Settings
from myha_simulator import fixtures
from tests.helpers import (
    ROOM_GUEST_DENIES,
    ROOM_GUEST_GRANTS,
    SIM_TOKEN,
    UNITS,
    SimHandle,
    fixture_state,
)

NOW = datetime.now(UTC)
GUEST_SESSION = "ses_guest"
RESIDENT_SESSION = "ses_resident"
_REGISTRY = parse_registry(fixtures.ENTITIES, fixtures.DEVICES, fixtures.AREAS)
_UNIT = parse_temperature_unit(fixtures.CONFIG)


def binding(resource_id: str, unit_id: str, entity_id: str) -> db.ResourceBinding:
    kind = detect_kind(entity_id)
    assert kind is not None
    entry = _REGISTRY.entities[entity_id]
    capabilities = adapter_for(kind).capabilities(fixture_state(entity_id), entry, _UNIT)
    modes = capabilities.hvac_modes & {"off", "cool", "heat"}
    capabilities = replace(capabilities, confirmed_modes=modes)
    return db.ResourceBinding(
        id=resource_id,
        unit_id=unit_id,
        kind=kind,
        entity_id=entity_id,
        registry_entity_id=None,
        unique_id=entry.unique_id,
        device_id=entry.device_id,
        area_id=_REGISTRY.effective_area(entity_id),
        label=resource_id,
        icon=None,
        confirmed_capabilities=capabilities.to_json(),
        capability_fingerprint=capabilities.fingerprint,
        status="blocked" if capabilities.blocked_reason else "confirmed",
    )


def _session(session_id: str, principal_id: str, stay_id: str | None) -> db.Session:
    return db.Session(
        id=session_id,
        token_hash=f"hash-{session_id}",
        principal_id=principal_id,
        audience="browser",
        purpose="access",
        stay_id=stay_id,
        unit_ids=None,
        not_before=None,
        not_after=NOW + timedelta(days=2),
        last_seen_at=NOW,
        csrf_secret="csrf",
        created_at=NOW,
    )


async def seed(engine: AsyncEngine) -> None:
    """Units, bindings, role room_guest v1, a guest in room 203 and a resident with a group."""
    async with write_session(engine) as session:
        await ensure_instance(session)
        session.add_all(db.Unit(id=unit, name=unit, sort=n) for n, unit in enumerate(UNITS))
        session.add(db.Unit(id="unit_empty", name="Empty", sort=9))
        await session.flush()
        session.add_all(
            binding(resource, unit, entity)
            for unit, members in UNITS.items()
            for resource, entity in members.items()
        )
        session.add(db.RoleTemplate(id="room_guest", name="Room guest", description=None))
        await session.flush()
        session.add(
            db.RoleVersion(
                id="room_guest@1",
                role_template_id="room_guest",
                version_number=1,
                grants=list(ROOM_GUEST_GRANTS),
                denies=list(ROOM_GUEST_DENIES),
                published_at=NOW,
                based_on_version=None,
            )
        )
        session.add(
            db.Stay(
                id="stay_203",
                unit_id="unit_203",
                starts_at=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=2),
                status="confirmed",
                external_ref=None,
                role_template_id="room_guest",
                role_version=1,
                device_slots=2,
                checked_out_at=None,
            )
        )
        await session.flush()
        for principal_id, kind, stay_id in (
            ("prn_guest", "guest", "stay_203"),
            ("prn_res", "resident", None),
            ("prn_other", "resident", None),
        ):
            session.add(
                db.Principal(
                    id=principal_id,
                    type=kind,
                    status="active",
                    display_name=principal_id,
                    locale=None,
                    theme=None,
                    stay_id=stay_id,
                    invite_id=None,
                    created_at=NOW,
                )
            )
        session.add(db.Group(id="grp_family", name="Family", description=None))
        await session.flush()
        session.add(
            db.Membership(
                id="mem_1",
                principal_id="prn_res",
                group_id="grp_family",
                valid_from=NOW - timedelta(days=1),
                valid_to=None,
            )
        )
        session.add(
            db.Assignment(
                id="asg_family",
                subject_type="group",
                subject_id="grp_family",
                role_template_id="room_guest",
                role_version=1,
                unit_ids=["unit_lr"],
                status="active",
            )
        )
        session.add_all(
            [_session(GUEST_SESSION, "prn_guest", "stay_203")]
            + [_session(RESIDENT_SESSION, "prn_res", None)]
        )


async def policy_version(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(select(db.Instance.policy_version))).scalar_one()


async def set_instance(engine: AsyncEngine, **values: object) -> None:
    """A change by another process: straight into the database, past the loader."""
    async with write_session(engine) as session:
        await session.execute(update(db.Instance).values(**values))


async def bindings(engine: AsyncEngine) -> dict[str, Any]:
    """Each binding row by id, read past the loader."""
    async with engine.connect() as connection:
        rows = (await connection.execute(select(db.ResourceBinding))).all()
    return {row.id: row for row in rows}


async def audit_operations(engine: AsyncEngine) -> list[tuple[str, str | None]]:
    async with engine.connect() as connection:
        query = select(db.AuditEvent.operation, db.AuditEvent.resource_id)
        return [(op, res) for op, res in (await connection.execute(query)).all()]


@asynccontextmanager
async def world(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    """The gateway's context on a seeded database, its HA client connected to the simulator."""
    settings = Settings(
        data_dir=tmp_path,
        supervisor_url=sim.http_url,
        ha_ws_url=sim.ws_url,
        supervisor_token=SIM_TOKEN,
    )
    ctx = await AppContext.create(settings)
    try:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        await run_migrations(ctx.engine)
        await seed(ctx.engine)
        await ctx.ha.start()
        assert ctx.ha.connected
        yield ctx
    finally:
        await ctx.ha.stop()
        await ctx.close()
