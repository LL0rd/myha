"""A06, A21, A24 and A27 at the projection: what one context sees on its dashboard (design §4.4).

Most cases run the projection on the simulator's fixture world as a policy index; the A06 case
and the layout run it on the real loader against the simulator.
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from myha_gateway.adapters.base import WRITE_ACTIONS, Action
from myha_gateway.db import models as db
from myha_gateway.db import session_factory, write_session
from myha_gateway.deps import AppContext
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.projection.dashboard import (
    Card,
    UnitLayout,
    load_layout,
    project_dashboard,
    project_resource,
)
from myha_gateway.projection.schema import DashboardView
from myha_simulator import fixtures
from tests.helpers import (
    DURING_STAY,
    ROOM_GUEST_GRANTS,
    STAY_END,
    STAY_START,
    UNITS,
    SimHandle,
    fixture_view,
    guest_context,
    resident_context,
    room203_index,
)
from tests.policy_world import GUEST_SESSION, NOW, world

LAYOUT = tuple(
    UnitLayout(unit, f"Name {unit}", tuple(Card(res, f"Label {res}", None) for res in members))
    for unit, members in UNITS.items()
)
# The living room readable too, so a resident sees more than the guest of room 203.
LR_READ = {"resources": ["res_lr_ceiling", "res_lr_ac"], "actions": ["state.read"]}
WIDE = room203_index(grants=[*ROOM_GUEST_GRANTS, LR_READ])
OUTSIDER = resident_context(
    principal_ref="prn_other", role_bindings=(RoleBinding("room_guest", 1, frozenset({"unit_lr"})),)
)


def project(
    ctx: EffectiveContext | None = None,
    index: PolicyIndex | None = None,
    now: Any = DURING_STAY,
    layout: Sequence[UnitLayout] = LAYOUT,
) -> DashboardView:
    context = guest_context() if ctx is None else ctx
    return project_dashboard(context, index or room203_index(), fixture_view, layout, now)


def public(view: DashboardView) -> dict[str, Any]:
    """The JSON the client receives."""
    return view.model_dump(mode="json", exclude_unset=True)


def resources(view: DashboardView) -> dict[str, dict[str, Any]]:
    return {res["id"]: res for room in public(view)["rooms"] for res in room["resources"]}


def capabilities(view: DashboardView, resource_id: str) -> Mapping[str, Any]:
    caps: Mapping[str, Any] = resources(view)[resource_id].get("capabilities", {})
    return caps


def assert_no_ha_detail(text: str) -> None:
    """No entity id, HA attribute or context id; action names such as ``light.on`` may appear."""
    for action in Action:
        text = text.replace(f'"{action}"', '""')
    for forbidden in ("light.", "climate.", "cover.", "sensor.", "switch.", "camera", "context"):
        assert forbidden not in text
    for entity in fixtures.ENTITIES:
        assert entity["entity_id"] not in text
    for attribute in ("friendly_name", "brightness", "current_position", "entity_picture"):
        assert attribute not in text


@pytest.fixture
async def app_ctx(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    async with world(sim, tmp_path) as ctx:
        yield ctx


async def project_session(ctx: AppContext, session_id: str) -> DashboardView:
    found = await ctx.policy.context_for_session(session_id, NOW)
    assert found is not None
    context, index = found
    async with session_factory(ctx.engine)() as session:
        layout = await load_layout(session)
    return project_dashboard(context, index, ctx.policy.state_view, layout, NOW)


async def test_a06_projection_hides_foreign_rooms_and_entity_ids(app_ctx: AppContext) -> None:
    view = await project_session(app_ctx, GUEST_SESSION)
    body = public(view)
    assert body["status"] == "online"
    assert [room["id"] for room in body["rooms"]] == ["unit_203"]  # not the living room
    # the group light is blocked (A15), so hidden
    assert set(resources(view)) == {
        "res_203_light",
        "res_203_bedside",
        "res_203_trv",
        "res_203_blind",
        "res_203_hum",
    }
    assert resources(view)["res_203_trv"]["state"]["target_c"] == "21.5"
    assert_no_ha_detail(json.dumps(body))
    # a principal outside the unit sees no room at all
    assert public(project(OUTSIDER))["rooms"] == []


async def test_layout_order_from_dashboard_else_binding_order(app_ctx: AppContext) -> None:
    async with write_session(app_ctx.engine) as session:
        order = ["res_203_trv", "res_unknown", 7, "res_203_hum"]  # junk entries are skipped
        session.add(db.Dashboard(unit_id="unit_203", layout={"order": order}))
        session.add(db.Dashboard(unit_id="unit_lr", layout=["not", "a", "mapping"]))
    view = await project_session(app_ctx, GUEST_SESSION)
    ids = [res["id"] for res in public(view)["rooms"][0]["resources"]]
    assert ids == [
        "res_203_trv",
        "res_203_hum",
        "res_203_bedside",
        "res_203_blind",
        "res_203_light",
    ]
    async with session_factory(app_ctx.engine)() as session:
        layout = await load_layout(session)
    assert [unit.id for unit in layout] == ["unit_203", "unit_lr", "unit_empty"]  # by sort
    assert [card.resource_id for card in layout[1].cards] == [
        "res_lr_ac",
        "res_lr_ceiling",
        "res_lr_lamp",
    ]
    assert layout[0].name == "unit_203"
    assert {card.label for card in layout[0].cards} >= {"res_203_light"}


def test_a21_cache_keys_separate_principals() -> None:
    resident = resident_context()
    first = project(resident, WIDE)
    guest = project(guest_context(), WIDE)
    again = project(resident, WIDE)
    assert [room.id for room in first.rooms] == ["unit_203", "unit_lr"]
    assert [room.id for room in guest.rooms] == ["unit_203"]
    assert public(project(guest_context(), WIDE)) == public(guest)  # same as on its own
    assert public(again) == public(first)  # the guest's view never replaced the resident's
    outsider = project(OUTSIDER, WIDE)
    assert [room.id for room in outsider.rooms] == ["unit_lr"]
    assert set(resources(outsider)) == {"res_lr_ceiling", "res_lr_ac"}
    assert project_dashboard(resident, WIDE, fixture_view, LAYOUT, DURING_STAY) is not first


def test_state_fields_are_allow_listed_per_kind() -> None:
    found = resources(project())
    expected = {
        "res_203_light": {"power", "level_pct"},
        "res_203_bedside": {"power", "level_pct"},
        "res_203_trv": {"current_c", "target_c", "hvac_mode"},
        "res_203_blind": {"position_pct", "cover_state"},
        "res_203_hum": {"value", "unit"},
    }
    for resource_id, fields in expected.items():
        assert set(found[resource_id]["state"]) == fields | {"availability", "observed_at"}
        assert set(found[resource_id]) <= {"id", "kind", "label", "icon", "state", "capabilities"}
    assert found["res_203_trv"]["state"] | {"observed_at": None} == {
        "availability": "online",
        "observed_at": None,
        "current_c": "21.0",
        "target_c": "21.5",
        "hvac_mode": "heat",
    }
    assert found["res_203_hum"]["state"]["unit"] == "%"


def test_state_hidden_without_state_read() -> None:
    index = room203_index(
        grants=[{"resources": ["res_203_light"], "actions": ["metadata.read", "light.on"]}]
    )
    found = resources(project(index=index))
    assert set(found) == {"res_203_light"}
    assert "state" not in found["res_203_light"]
    assert found["res_203_light"]["capabilities"] == {"light.on": {}}
    # a write the device can do implies metadata.read (design §4.1), still without state
    implied = room203_index(grants=[{"resources": ["res_203_blind"], "actions": ["cover.stop"]}])
    assert resources(project(index=implied)) == {
        "res_203_blind": {
            "id": "res_203_blind",
            "kind": "cover",
            "label": "Label res_203_blind",
            "icon": None,
            "capabilities": {"cover.stop": {}},
        }
    }


def test_state_without_a_view_is_unknown() -> None:
    ref = room203_index().resources["res_203_light"]
    card = Card("res_203_light", "Ceiling", "bulb")
    found = project_resource(guest_context(), room203_index(), None, card, DURING_STAY)
    assert found is not None
    assert found.model_dump(mode="json", exclude_unset=True) == {
        "id": ref.id,
        "kind": "light",
        "label": "Ceiling",
        "icon": "bulb",
        "state": {"availability": "unknown"},
        "capabilities": {"light.on": {}, "light.off": {}, "light.set_level": LEVEL},
    }


LEVEL = {"intervals": [{"min": "0", "max": "100", "step": "1", "origin": "0"}]}


def test_capabilities_reflect_fence() -> None:
    plain = project()
    assert capabilities(plain, "res_203_trv") == {
        "climate.set_temperature": {
            "intervals": [{"min": "19.0", "max": "24.0", "step": "0.5", "origin": "0.0"}]
        },
        "climate.set_mode": {"allowed": ["heat", "off"]},  # cool: the device has no such mode
    }
    assert capabilities(plain, "res_203_light")["light.set_level"] == LEVEL
    assert set(capabilities(plain, "res_203_blind")) == {
        "cover.open",
        "cover.close",
        "cover.stop",
        "cover.set_position",
    }
    assert "capabilities" not in resources(plain)["res_203_hum"]  # read only
    assert "capabilities" not in resources(plain)["res_203_bedside"]  # no write granted

    def unit_fence(action: str, **parameters: Any) -> dict[str, Any]:
        scope = {"scope_type": "unit", "scope_id": "unit_203"}
        return scope | {"action": action, "parameters": parameters}

    fenced = project(
        guest_context(
            unit_fence("climate.set_temperature", target_c={"min": "20", "max": "22.2"}),
            unit_fence("climate.set_mode", mode={"allowed": ["off"]}),
            unit_fence("cover.set_position", position_pct={"min": "0", "max": "50"}),
            unit_fence("light.set_level", level_pct={"min": "10", "max": "40"}),
        )
    )
    assert capabilities(fenced, "res_203_trv") == {
        "climate.set_temperature": {
            "intervals": [{"min": "20.0", "max": "22.0", "step": "0.5", "origin": "0.0"}]
        },
        "climate.set_mode": {"allowed": ["off"]},
    }
    assert set(capabilities(fenced, "res_203_blind")) == {
        "cover.close",
        "cover.stop",
        "cover.set_position",  # cover.open would run past 50 %
    }
    assert capabilities(fenced, "res_203_light")["light.set_level"] == {
        "intervals": [{"min": "10", "max": "40", "step": "1", "origin": "0"}]
    }
    # a fence that leaves no value and no mode removes the control
    closed = project(
        guest_context(
            unit_fence("climate.set_temperature", target_c={"min": "25", "max": "26"}),
            unit_fence("climate.set_mode", mode={"allowed": ["cool"]}),
        )
    )
    assert "capabilities" not in resources(closed)["res_203_trv"]


def test_capability_keeps_finer_origin_digits() -> None:
    grants = [
        {"resources": ["res_203_trv"], "actions": ["metadata.read"]},
        {
            "resources": ["res_203_trv"],
            "actions": ["climate.set_temperature"],
            "parameters": {
                "target_c": {
                    "intervals": [{"min": "19", "max": "21", "step": "1", "origin": "0.5"}]
                }
            },
        },
    ]
    found = capabilities(project(index=room203_index(grants=grants)), "res_203_trv")
    assert found["climate.set_temperature"]["intervals"] == [
        {"min": "19.5", "max": "20.5", "step": "1.0", "origin": "0.5"}
    ]


def test_needs_review_is_read_only_and_blocked_is_hidden() -> None:
    index = room203_index(status={"res_203_trv": "needs_review", "res_203_light": "blocked"})
    found = resources(project(index=index))
    assert "res_203_light" not in found
    assert set(found["res_203_trv"]) == {"id", "kind", "label", "icon", "state"}


def test_a24_waiting_dashboard_is_empty() -> None:
    before = project(now=STAY_START - timedelta(minutes=1))
    assert public(before) == {"status": "waiting", "rooms": []}
    assert public(project(now=STAY_START))["status"] == "online"
    assert public(project(now=STAY_END)) == {"status": "expired", "rooms": []}
    revoked = project(guest_context(session_active=False))
    assert public(revoked) == {"status": "expired", "rooms": []}
    resident = project(resident_context(), now=STAY_END + timedelta(days=99))
    assert resident.status == "online"  # no window, no end


def test_a27_no_history_fields() -> None:
    everything = [
        {"resources": sorted(UNITS["unit_203"]), "actions": [str(action) for action in Action]}
    ]
    view = project(index=room203_index(grants=everything, denies=[]))
    text = json.dumps(public(view))
    assert "history" not in text
    for res in resources(view).values():
        assert set(res.get("capabilities", {})) <= {str(action) for action in WRITE_ACTIONS}


def test_project_resource_matches_the_dashboard_entry() -> None:
    ctx, index = guest_context(), room203_index()
    card = LAYOUT[0].cards[0]
    single = project_resource(ctx, index, fixture_view(card.resource_id), card, DURING_STAY)
    assert single is not None
    assert single == project(ctx, index).rooms[0].resources[0]
    foreign = LAYOUT[1].cards[0]
    view = fixture_view(foreign.resource_id)
    assert project_resource(ctx, index, view, foreign, DURING_STAY) is None


def test_projection_refuses_another_policy_version() -> None:
    with pytest.raises(ValueError, match="policy version"):
        project(replace(guest_context(), policy_version=2))
