"""Development seed: the room 203 world of the simulator fixture, for dev and E2E runs only.

The tests share these definitions through ``tests.helpers``.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.db import write_session
from myha_gateway.db.models import Instance, ResourceBinding, RoleTemplate, RoleVersion, Unit
from myha_gateway.ha.client import HAClient, utcnow
from myha_gateway.services.stays import MAX_REDEEM_MINUTES, create_stay, issue_invites

# Product resource id -> entity id of the simulator fixture, per unit.
ROOM_203 = {
    "res_203_light": "light.room_203_ceiling",
    "res_203_bedside": "light.room_203_bedside",
    "res_203_trv": "climate.room_203_trv",
    "res_203_blind": "cover.room_203_blind",
    "res_203_hum": "sensor.room_203_humidity",
    "res_203_group": "light.group_all",  # a group entity: its binding is blocked (A15)
}
LIVING_ROOM = {
    "res_lr_ceiling": "light.living_ceiling",
    "res_lr_lamp": "switch.living_floor_lamp",
    "res_lr_ac": "climate.living_ac",
}
UNITS = {"unit_203": ROOM_203, "unit_lr": LIVING_ROOM}

# Role room_guest v1 of spec §7 on these resources, plus set_mode, set_position and set_level.
ROOM_GUEST_GRANTS: tuple[dict[str, Any], ...] = (
    {
        "resources": ["res_203_light", "res_203_bedside", "res_203_trv", "res_203_blind"]
        + ["res_203_hum"],
        "actions": ["metadata.read", "state.read"],
    },
    {"resources": ["res_203_light"], "actions": ["light.on", "light.off", "light.set_level"]},
    {
        "resources": ["res_203_trv"],
        "actions": ["climate.set_temperature"],
        "parameters": {"target_c": {"intervals": [{"min": "19", "max": "24", "step": "0.5"}]}},
    },
    {
        "resources": ["res_203_trv"],
        "actions": ["climate.set_mode"],
        "parameters": {"mode": {"allowed": ["off", "cool", "heat"]}},
    },
    {
        "resources": ["res_203_blind"],
        "actions": ["cover.open", "cover.close", "cover.stop", "cover.set_position"],
    },
)
ROOM_GUEST_DENIES: tuple[dict[str, Any], ...] = (
    {"resources": ["res_203_light", "res_203_trv", "res_203_blind"], "actions": ["history.read"]},
)

# What the seed binds, with the modes the admin confirms; the rest of UNITS stays unbound.
_SEEDED = {
    "unit_203": ("res_203_light", "res_203_bedside", "res_203_trv", "res_203_blind")
    + ("res_203_hum",),
    "unit_lr": ("res_lr_ceiling", "res_lr_ac"),
}
_UNIT_NAMES = {"unit_203": "Room 203", "unit_lr": "Living room"}
_CONFIRMED_MODES = {"res_203_trv": {"off", "heat"}, "res_lr_ac": {"off", "cool", "heat"}}


def _binding(resource_id: str, unit_id: str, ha: HAClient) -> ResourceBinding:
    """The confirmed binding of a fixture entity, capabilities as Home Assistant reports them."""
    entity_id = UNITS[unit_id][resource_id]
    kind, state = detect_kind(entity_id), ha.state(entity_id)
    if kind is None or state is None:
        raise RuntimeError(f"{entity_id} missing in Home Assistant: is it the simulator?")
    registry = ha.registry()
    entry = registry.entities.get(entity_id)
    capabilities = adapter_for(kind).capabilities(state, entry, ha.temperature_unit)
    modes = frozenset(_CONFIRMED_MODES.get(resource_id, ()))
    capabilities = replace(capabilities, confirmed_modes=modes)
    return ResourceBinding(
        id=resource_id,
        unit_id=unit_id,
        kind=kind,
        entity_id=entity_id,
        registry_entity_id=None,
        unique_id=None if entry is None else entry.unique_id,
        device_id=None if entry is None else entry.device_id,
        area_id=registry.effective_area(entity_id),
        label=str(state.attributes.get("friendly_name", entity_id)),
        icon=None,
        confirmed_capabilities=capabilities.to_json(),
        capability_fingerprint=capabilities.fingerprint,
        status="blocked" if capabilities.blocked_reason else "confirmed",
    )


async def seed_room203(engine: AsyncEngine, ha: HAClient, origin: str) -> list[str]:
    """Units, bindings, role ``room_guest`` v1 and a stay with two invitations; their join URLs.

    The stay runs from today 00:00 to tomorrow 23:59 UTC. A second run changes nothing and
    returns no URL: the secrets of the first run exist nowhere any more.
    """
    async with engine.connect() as connection:
        if (await connection.execute(select(Unit.id).where(Unit.id == "unit_203"))).first():
            return []
    if not ha.connected:
        raise RuntimeError("Home Assistant unreachable: no states to bind")
    now = utcnow()
    bindings = [_binding(res, unit, ha) for unit, members in _SEEDED.items() for res in members]
    async with write_session(engine) as session:  # a concurrent seed fails on the unit's key
        instance = (await session.execute(select(Instance))).scalar_one()
        session.add_all(
            Unit(id=unit, name=name, sort=n) for n, (unit, name) in enumerate(_UNIT_NAMES.items())
        )
        session.add(RoleTemplate(id="room_guest", name="Room guest", description=None))
        await session.flush()
        session.add_all(bindings)
        session.add(
            RoleVersion(
                id="room_guest@1",
                role_template_id="room_guest",
                version_number=1,
                grants=list(ROOM_GUEST_GRANTS),
                denies=list(ROOM_GUEST_DENIES),
                published_at=now,
                based_on_version=None,
            )
        )
        await session.flush()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stay = await create_stay(
            session,
            unit_id="unit_203",
            role_template_id="room_guest",
            role_version=1,
            starts_at=today,
            ends_at=today + timedelta(days=1, hours=23, minutes=59),
            device_slots=2,
            actor="dev-seed",
        )
        issued = await issue_invites(
            session,
            stay,
            count=2,
            redeem_minutes=MAX_REDEEM_MINUTES,
            origin=origin,
            instance=instance,
            now=now,
            actor="dev-seed",
        )
    return [item.join_url for item in issued]
