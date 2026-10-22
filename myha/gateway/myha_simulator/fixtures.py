"""Deterministic Home Assistant world of the simulator: areas, devices, entities, users, config."""

from dataclasses import dataclass
from typing import Any

HA_VERSION = "2026.10.2"
SUPERVISOR_VERSION = "2026.10.3"
ADMIN_TOKEN = "sim-token"  # noqa: S105 (fake credential, also the Supervisor token)
USER_TOKEN = "sim-user-token"  # noqa: S105 (fake credential of a non-admin user)
# Whose calls a token makes (context.user_id). The Supervisor reaches Core as its own user.
TOKEN_USERS = {ADMIN_TOKEN: "sup", USER_TOKEN: "user1"}

AREAS: list[dict[str, Any]] = [
    {"area_id": "area_203", "name": "Room 203"},
    {"area_id": "area_204", "name": "Room 204"},
    {"area_id": "area_living", "name": "Living room"},
    {"area_id": "area_kitchen", "name": "Kitchen"},
    {"area_id": "area_hall", "name": "Hallway"},
]


def _entity(
    entity_id: str,
    area_id: str | None,
    platform: str,
    state: str,
    attributes: dict[str, Any],
    device_id: str | None = None,
) -> dict[str, Any]:
    """Registry fields plus state; ids follow from the object id unless given."""
    object_id = entity_id.split(".", 1)[1]
    return {
        "entity_id": entity_id,
        "unique_id": f"{platform}-{object_id}",
        "device_id": device_id or f"dev_{object_id}",
        "area_id": area_id,
        "platform": platform,
        "state": state,
        "attributes": attributes,
    }


ENTITIES: list[dict[str, Any]] = [
    _entity(
        "light.room_203_ceiling",
        "area_203",
        "govee",
        "on",
        {
            "friendly_name": "Room 203 ceiling",
            "supported_color_modes": ["color_temp", "rgb"],
            "color_mode": "color_temp",
            "brightness": 178,
        },
    ),
    _entity(
        "light.room_203_bedside",
        "area_203",
        "shelly",
        "on",
        {"friendly_name": "Room 203 bedside", "supported_color_modes": ["onoff"]},
    ),
    _entity(
        "climate.room_203_trv",
        "area_203",
        "zha",
        "heat",
        {
            "friendly_name": "Room 203 radiator",
            "hvac_modes": ["heat", "off"],
            "min_temp": 7,
            "max_temp": 30,
            "target_temp_step": 0.5,
            "temperature": 21.5,
            "current_temperature": 21.0,
        },
    ),
    _entity(
        "cover.room_203_blind",
        "area_203",
        "zha",
        "open",
        {"friendly_name": "Room 203 blind", "supported_features": 15, "current_position": 100},
    ),
    _entity(
        "sensor.room_203_humidity",
        "area_203",
        "zha",
        "46",
        {
            "friendly_name": "Room 203 humidity",
            "unit_of_measurement": "%",
            "device_class": "humidity",
        },
    ),
    _entity(
        "light.living_ceiling",
        "area_living",
        "govee",
        "on",
        {
            "friendly_name": "Living room ceiling",
            "supported_color_modes": ["color_temp", "rgb"],
            "brightness": 140,
        },
    ),
    # No area of its own: Home Assistant falls back to the device's area (see DEVICES).
    _entity(
        "switch.living_floor_lamp",
        None,
        "shelly",
        "off",
        {"friendly_name": "Living room floor lamp"},
        device_id="dev_living_lamp",
    ),
    _entity(
        "climate.living_ac",
        "area_living",
        "midea",
        "cool",
        {
            "friendly_name": "Living room AC",
            "hvac_modes": ["auto", "cool", "dry", "fan_only", "heat", "off"],
            "min_temp": 8,
            "max_temp": 30,
            "target_temp_step": 1,
            "temperature": 22,
            "current_temperature": 24,
        },
    ),
    _entity(
        "cover.kitchen_blind",
        "area_kitchen",
        "zha",
        "closed",
        {"friendly_name": "Kitchen blind", "supported_features": 11, "current_position": 0},
    ),
    _entity(
        "light.hallway",
        "area_hall",
        "shelly",
        "on",
        {"friendly_name": "Hallway", "supported_color_modes": ["onoff"]},
    ),
    _entity(
        "light.group_all",
        None,
        "group",
        "on",
        {"friendly_name": "All lights", "supported_color_modes": ["onoff"]},
    ),
    _entity(
        "climate.room_204_dual",
        "area_204",
        "generic_thermostat",
        "heat_cool",
        {
            "friendly_name": "Room 204 thermostat",
            "hvac_modes": ["heat_cool", "off"],
            "target_temp_high": 24,
            "target_temp_low": 20,
            "min_temp": 7,
            "max_temp": 35,
        },
    ),
    _entity(
        "sensor.outdoor_temp",
        None,
        "openweathermap",
        "12.3",
        {
            "friendly_name": "Outdoor temperature",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
        },
    ),
    _entity(
        "camera.lobby",
        "area_hall",
        "unifiprotect",
        "idle",
        {
            "friendly_name": "Lobby",
            "access_token": "sim-cam-token",
            "entity_picture": "/api/camera_proxy/camera.lobby?token=sim-cam-token",
        },
    ),
    _entity(
        "person.owner",
        None,
        "person",
        "home",
        {"friendly_name": "Owner", "latitude": 42.6977, "longitude": 23.3219},
    ),
]

# One device per entity, in the entity's area; the floor lamp's area is set on its device only.
DEVICES: list[dict[str, Any]] = [
    {
        "id": entity["device_id"],
        "name": entity["attributes"]["friendly_name"],
        "area_id": "area_living" if entity["device_id"] == "dev_living_lamp" else entity["area_id"],
    }
    for entity in ENTITIES
]

USERS: list[dict[str, Any]] = [
    {
        "id": "owner1",
        "username": "owner",
        "name": "Owner",
        "is_owner": True,
        "is_active": True,
        "system_generated": False,
        "local_only": False,
        "group_ids": ["system-admin"],
        "credentials": [{"type": "homeassistant"}],
    },
    {
        "id": "user1",
        "username": "user",
        "name": "Regular user",
        "is_owner": False,
        "is_active": True,
        "system_generated": False,
        "local_only": False,
        "group_ids": ["system-users"],
        "credentials": [{"type": "homeassistant"}],
    },
    {
        "id": "sup",
        "username": None,
        "name": "Supervisor",
        "is_owner": False,
        "is_active": True,
        "system_generated": True,
        "local_only": False,
        "group_ids": ["system-admin"],
        "credentials": [],
    },
]

CONFIG: dict[str, Any] = {
    "location_name": "Home",
    "time_zone": "Europe/Sofia",
    "unit_system": {"temperature": "°C", "length": "km", "mass": "g", "volume": "L"},
    "version": HA_VERSION,
    "state": "RUNNING",
    "components": [
        "auth",
        "camera",
        "climate",
        "config",
        "cover",
        "generic_thermostat",
        "govee",
        "group",
        "light",
        "midea",
        "openweathermap",
        "person",
        "sensor",
        "shelly",
        "switch",
        "unifiprotect",
        "websocket_api",
        "zha",
    ],
}


@dataclass(frozen=True)
class Fixture:
    """What a simulator starts from and ``/_sim/reset`` returns to; the server copies it."""

    areas: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    entities: list[dict[str, Any]]  # registry fields plus "state" and "attributes"
    users: list[dict[str, Any]]
    config: dict[str, Any]


DEFAULT = Fixture(AREAS, DEVICES, ENTITIES, USERS, CONFIG)
