import pytest

from myha_gateway.ha.messages import (
    MALFORMED,
    parse_registry,
    parse_states,
    parse_time,
    parse_user,
)

STATE = {
    "entity_id": "light.a",
    "state": "on",
    "attributes": {"brightness": 3},
    "last_changed": "2026-10-22T10:00:00+00:00",
    "last_updated": "2026-10-22T10:00:01+00:00",
    "context": {"id": "c1", "parent_id": None, "user_id": None},
}
USER = {
    "id": "u1",
    "name": "U",
    "is_owner": False,
    "is_active": True,
    "system_generated": False,
    "group_ids": ["system-admin"],
}


def test_malformed_states_are_skipped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    naive = STATE | {"entity_id": "light.b", "last_updated": "2026-10-22T10:00:01"}
    states = parse_states([STATE, naive, STATE | {"entity_id": "light.c", "state": 1}, None])
    assert list(states) == ["light.a"] and states["light.a"].context_id == "c1"
    assert caplog.text.count("ignoring a malformed state") == 3
    with pytest.raises(TypeError):
        parse_states({"light.a": STATE})


@pytest.mark.parametrize(
    "user",
    [
        {k: v for k, v in USER.items() if k != "system_generated"},
        USER | {"is_owner": "false"},  # the role check needs real booleans
        USER | {"group_ids": "system-admin"},
        USER | {"id": None},
    ],
)
def test_users_missing_role_fields_are_rejected(user: dict[str, object]) -> None:
    with pytest.raises(MALFORMED):
        parse_user(user)


def test_timestamps_need_an_offset() -> None:
    assert parse_time("2026-10-22T10:00:00Z").utcoffset() is not None
    with pytest.raises(ValueError, match="without offset"):
        parse_time("2026-10-22T10:00:00")


def test_registry_names_and_areas() -> None:
    entities = [
        {
            "entity_id": "light.a",
            "unique_id": "u-a",
            "device_id": "d1",
            "area_id": None,
            "platform": "zha",
            "disabled_by": "user",
        },
        {
            "entity_id": "light.b",
            "unique_id": "u-b",
            "device_id": None,
            "area_id": None,
            "platform": "zha",
        },
        {"entity_id": "light.c", "device_id": "d1", "platform": "zha"},  # no unique_id
    ]
    devices = [{"id": "d1", "area_id": "kitchen", "name": "Bulb", "name_by_user": "Lamp"}]
    registry = parse_registry(entities, devices, [{"area_id": "kitchen", "name": "Kitchen"}])
    assert list(registry.entities) == ["light.a", "light.b"]
    assert registry.entities["light.a"].disabled and registry.devices["d1"].name == "Lamp"
    assert registry.effective_area("light.a") == "kitchen"  # from the device
    assert registry.effective_area("light.b") is None  # neither own area nor device
