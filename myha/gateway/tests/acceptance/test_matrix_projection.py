"""A06, A21, A24 and A27 at the projection: what one context sees on its dashboard (design §4.4).

Most cases run the projection on the simulator's fixture world as a policy index; the A06 case
and the layout run it on the real loader against the simulator.
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from myha_gateway.adapters.base import WRITE_ACTIONS, Action, StateView
from myha_gateway.db import models as db
from myha_gateway.db import session_factory, write_session
from myha_gateway.deps import AppContext
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.decide import decide
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.policy.model import parameter_name
from myha_gateway.projection.dashboard import (
    Card,
    UnitLayout,
    load_layout,
    project_dashboard,
    project_resource,
)
from myha_gateway.projection.schema import DashboardView
from myha_simulator import fixtures
from tests import policy_world, public_helpers
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
from tests.policy_world import GUEST_SESSION, NOW
from tests.public_helpers import World, browser_guest, new_stay, rows

ctx, world = public_helpers.ctx, public_helpers.world  # the public app fixtures
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
    async with policy_world.world(sim, tmp_path) as ctx:
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
        "state": {"availability": "unknown"},  # no write controls without a view
    }


def test_locked_adapter_offers_no_write_controls() -> None:
    locked = partial(fixture_view, adapter_locked=True)
    view = project_dashboard(guest_context(), room203_index(), locked, LAYOUT, DURING_STAY)
    found = resources(view)
    assert set(found) == set(resources(project()))  # still visible
    for res in found.values():
        assert "capabilities" not in res
        assert "state" in res  # state.read still shows the state
    assert found["res_203_trv"]["state"]["target_c"] == "21.5"


LEVEL = {"intervals": [{"min": "0", "max": "100", "step": "1", "origin": "0"}]}


def assert_offers_are_allowed(
    view: DashboardView, ctx: EffectiveContext, index: PolicyIndex
) -> int:
    """Every offered interval edge and mode is one decide allows; returns how many it checked."""
    checked = 0
    for resource_id, res in resources(view).items():
        for action, capability in res.get("capabilities", {}).items():
            name = parameter_name(Action(action))
            values: list[Any] = list(capability.get("allowed", []))
            for edge in capability.get("intervals", []):
                values += [edge["min"], edge["max"]]
            if name in ("level_pct", "position_pct"):
                values = [int(value) for value in values]
            params_list = [{name: value} for value in values] if name else [{}]
            for params in params_list:
                state = fixture_view(resource_id)
                found = decide(
                    ctx, index, resource_id, action, params, DURING_STAY, state, execute=False
                )
                assert found.allowed, (resource_id, action, params, found.code)
                checked += 1
    return checked


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
    assert assert_offers_are_allowed(plain, guest_context(), room203_index()) > 10

    def unit_fence(action: str, **parameters: Any) -> dict[str, Any]:
        scope = {"scope_type": "unit", "scope_id": "unit_203"}
        return scope | {"action": action, "parameters": parameters}

    fenced_ctx = guest_context(
        unit_fence("climate.set_temperature", target_c={"min": "20", "max": "22.2"}),
        unit_fence("climate.set_mode", mode={"allowed": ["off"]}),
        unit_fence("cover.set_position", position_pct={"min": "0", "max": "50"}),
        unit_fence("light.set_level", level_pct={"min": "10", "max": "40"}),
    )
    fenced = project(fenced_ctx)
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
    assert assert_offers_are_allowed(fenced, fenced_ctx, room203_index()) > 5
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


def test_status_waiting_and_expired_show_no_rooms() -> None:
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


def test_state_decimals_never_use_exponents() -> None:
    def views(resource_id: str) -> StateView:
        if resource_id != "res_203_trv":
            return fixture_view(resource_id)
        return fixture_view(resource_id, target_c=Decimal("1E+2"), current_c=Decimal("1E-7"))

    view = project_dashboard(guest_context(), room203_index(), views, LAYOUT, DURING_STAY)
    state = resources(view)["res_203_trv"]["state"]
    assert (state["target_c"], state["current_c"]) == ("100", "0.0000001")


# --- GET /api/v1/dashboard on the public app, as a redeemed guest ---


async def get_dashboard(world: World, secret: str) -> httpx.Response:
    async with world.client() as client:
        guest = await browser_guest(world, client, secret)
        return await client.get("/api/v1/dashboard", headers={"Cookie": guest.cookie})


def nulls(value: Any, key: str = "") -> set[str]:
    """The keys anywhere in ``value`` whose value is null."""
    if isinstance(value, dict):
        return {k for k, v in value.items() if v is None} | {
            found for k, v in value.items() for found in nulls(v, k)
        }
    if isinstance(value, list):
        return {found for item in value for found in nulls(item, key)}
    return set()


async def test_dashboard_endpoint_projects_the_guest_session(world: World) -> None:
    r = await get_dashboard(world, world.secrets[0])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"status", "rooms", "policy_version"}
    assert body["status"] == "online"
    assert isinstance(body["policy_version"], int)
    assert [room["id"] for room in body["rooms"]] == ["unit_203"]
    found = {res["id"]: res for res in body["rooms"][0]["resources"]}
    assert set(found["res_203_trv"]["state"]) == {
        "availability",
        "observed_at",
        "current_c",
        "target_c",
        "hvac_mode",
    }
    assert found["res_203_trv"]["capabilities"]["climate.set_mode"] == {"allowed": ["heat", "off"]}
    assert found["res_203_light"]["capabilities"]["light.on"] == {}
    # no "state": null, "intervals": null and the like: unset fields stay away
    assert not nulls(body) & {"state", "capabilities", "intervals", "allowed", "rooms"}
    assert '"intervals":null' not in r.text and '"state":null' not in r.text
    assert_no_ha_detail(r.text)


async def test_a24_waiting_dashboard_is_empty(world: World) -> None:
    starts = public_helpers.now() + timedelta(days=2)
    _, secrets = await new_stay(world.ctx, starts_at=starts, ends_at=starts + timedelta(days=3))
    r = await get_dashboard(world, secrets[0])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["rooms"]) == ("waiting", [])
    assert isinstance(body["policy_version"], int)


async def test_dashboard_needs_a_session(world: World) -> None:
    async with world.client() as client:
        r = await client.get("/api/v1/dashboard")
        forged = await client.get("/api/v1/dashboard", headers={"Cookie": f"{world.cookie_name}=x"})
    for answer in (r, forged):
        assert (answer.status_code, answer.json()["error"]["code"]) == (401, "UNAUTHENTICATED")


async def test_decide_error_is_upstream_unavailable_and_audited(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> DashboardView:
        raise ValueError("unknown role version room_guest@9")

    monkeypatch.setattr("myha_gateway.api.public.dashboard.project_dashboard", broken)
    r = await get_dashboard(world, world.secrets[0])
    assert (r.status_code, r.json()["error"]["code"]) == (500, "UPSTREAM_UNAVAILABLE")
    assert "room_guest" not in r.text
    events = [e for e in await rows(world.ctx, db.AuditEvent) if e.operation == "decide_error"]
    assert [(e.result, e.policy_version is not None) for e in events] == [("error", True)]
    assert events[0].session_id is not None


async def test_session_gone_since_the_check_is_unauthenticated(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def gone(*args: Any) -> None:
        return None

    monkeypatch.setattr(world.ctx.policy, "context_for_session", gone)
    r = await get_dashboard(world, world.secrets[0])
    assert (r.status_code, r.json()["error"]["code"]) == (401, "UNAUTHENTICATED")


def test_layout_unit_differs_from_index_unit_stays_hidden() -> None:
    # the layout was read apart from the index: the light moved to the living room in it
    moved = Card("res_203_light", "Label res_203_light", None)
    room_203 = UnitLayout("unit_203", "Name unit_203", LAYOUT[0].cards[1:])
    living = UnitLayout("unit_lr", "Name unit_lr", (moved, *LAYOUT[1].cards))
    body = public(project(layout=(room_203, living)))
    assert [room["id"] for room in body["rooms"]] == ["unit_203"]
    assert "Name unit_lr" not in json.dumps(body)
    assert "res_203_light" not in resources(project(layout=(room_203, living)))


@pytest.mark.parametrize(
    ("resource_id", "actions", "denied", "expected"),
    [
        ("res_203_light", ["light.set_level", "light.on"], [], [("1", "100")]),
        ("res_203_light", ["light.set_level", "light.off"], [], [("0", "0")]),
        (
            "res_203_light",
            ["light.set_level", "light.on", "light.off"],
            ["light.off"],
            [("1", "100")],
        ),
        ("res_203_blind", ["cover.set_position", "cover.open"], [], [("100", "100")]),
        ("res_203_blind", ["cover.set_position", "cover.close"], [], [("0", "0")]),
        ("res_203_blind", ["cover.set_position"], [], []),
    ],
)
def test_offered_intervals_respect_companion_grants(
    resource_id: str, actions: list[str], denied: list[str], expected: list[tuple[str, str]]
) -> None:
    grants = [{"resources": [resource_id], "actions": ["state.read", *actions]}]
    denies = [{"resources": [resource_id], "actions": denied}] if denied else []
    index = room203_index(grants=grants, denies=denies)
    view = project(index=index)
    action = actions[0]
    offered = capabilities(view, resource_id).get(action, {}).get("intervals", [])
    assert [(edge["min"], edge["max"]) for edge in offered] == expected
    assert_offers_are_allowed(view, guest_context(), index)
