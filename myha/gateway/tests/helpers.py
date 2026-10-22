"""Helpers shared by the integration tests."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import websockets
from fastapi import FastAPI
from pydantic import SecretStr

from myha_gateway.adapters.base import StateView
from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.ha.messages import HAState, parse_registry, parse_temperature_unit
from myha_gateway.ha.supervisor import SupervisorClient
from myha_gateway.policy.context import EffectiveContext, RoleBinding
from myha_gateway.policy.index import PolicyIndex, ResourceRef
from myha_gateway.policy.model import Fence, RoleRules
from myha_simulator import fixtures

TRUSTED = "172.30.32.2"
SIM_TOKEN = "sim-token"  # the simulator's admin and Supervisor token
SUPERVISOR_DATA = {
    "/info": {"supervisor": "2026.10.3", "channel": "stable", "arch": "amd64", "supported": True},
    "/addons/self/info": {
        "name": "myHA",
        "version": "0.1.0",
        "ingress_entry": "/api/hassio_ingress/abc",
        "ingress_url": "/api/hassio_ingress/abc/",
    },
}


def fake_supervisor(*, reachable: bool = True) -> SupervisorClient:
    """The Supervisor as an app without ``hassio_api`` sees it: ``/host/info`` answers 403."""

    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("Name or service not known", request=request)
        data = SUPERVISOR_DATA.get(request.url.path)
        if data is None:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json={"result": "ok", "data": data})

    transport = httpx.MockTransport(handler)
    return SupervisorClient("http://supervisor", SecretStr("sup-secret"), transport=transport)


async def call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    peer: str = TRUSTED,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    """Send one request to ``app`` from the TCP peer ``peer``."""
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=raise_app_exceptions, client=(peer, 1)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers)


@dataclass(frozen=True)
class SimControl:
    """Client for the simulator's ``/_sim`` control API; error answers raise."""

    client: httpx.AsyncClient  # base URL is the simulator, usable for the Supervisor API too

    async def get(self, path: str) -> Any:
        response = await self.client.get(f"/_sim/{path}")
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, body: Any = None) -> Any:
        response = await self.client.post(f"/_sim/{path}", json=body)
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True)
class SimHandle:
    http_url: str  # Supervisor base URL (MYHA_SUPERVISOR_URL)
    ws_url: str  # Core WebSocket URL (MYHA_HA_WS_URL)
    control: SimControl


async def ws_receive(ws: websockets.ClientConnection) -> Any:
    """The next message as JSON, at most 5 s away."""
    async with asyncio.timeout(5):
        return json.loads(await ws.recv())


async def ws_command(ws: websockets.ClientConnection, msg: dict[str, Any]) -> Any:
    """Send one command and return the next message (keep subscribers on other connections)."""
    await ws.send(json.dumps(msg))
    return await ws_receive(ws)


@asynccontextmanager
async def ws_connect(
    sim: SimHandle, token: str = SIM_TOKEN
) -> AsyncIterator[websockets.ClientConnection]:
    """A WebSocket connection to the simulator, authenticated with ``token``."""
    async with websockets.connect(sim.ws_url) as ws:
        assert (await ws_receive(ws))["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        assert (await ws_receive(ws))["type"] == "auth_ok"
        yield ws


# --- the simulator's world as a policy index, without a running simulator ---

FIXTURE_TIME = datetime(2026, 10, 22, 10, tzinfo=UTC)
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
Status = Literal["confirmed", "needs_review", "blocked"]
_REGISTRY = parse_registry(fixtures.ENTITIES, fixtures.DEVICES, fixtures.AREAS)
_TEMPERATURE_UNIT = parse_temperature_unit(fixtures.CONFIG)


def fixture_state(entity_id: str) -> HAState:
    """The simulator fixture's state of ``entity_id``."""
    entity = next(entity for entity in fixtures.ENTITIES if entity["entity_id"] == entity_id)
    attributes = dict(entity["attributes"])
    return HAState(entity_id, entity["state"], attributes, FIXTURE_TIME, FIXTURE_TIME, None)


def _entity_of(resource_id: str) -> str:
    return (ROOM_203 | LIVING_ROOM)[resource_id]


def fixture_view(resource_id: str, *, adapter_locked: bool = False, **changes: Any) -> StateView:
    """The adapter's view of the resource's fixture state, with ``changes`` on top."""
    entity_id = _entity_of(resource_id)
    kind = detect_kind(entity_id)
    assert kind is not None
    view = adapter_for(kind).state_view(fixture_state(entity_id), adapter_locked)
    return replace(view, **changes)


def _resource(
    resource_id: str, unit_id: str, status: Status | None, modes: Iterable[str] | None
) -> ResourceRef:
    """The binding of one fixture entity: capabilities through its adapter, as HA reports them.

    The admin confirms every product mode the device offers unless ``modes`` says otherwise; a
    blocked adapter result makes a blocked binding unless ``status`` says otherwise.
    """
    entity_id = _entity_of(resource_id)
    kind = detect_kind(entity_id)
    assert kind is not None
    capabilities = adapter_for(kind).capabilities(
        fixture_state(entity_id), _REGISTRY.entities[entity_id], _TEMPERATURE_UNIT
    )
    confirmed = capabilities.hvac_modes & {"off", "cool", "heat"} if modes is None else modes
    capabilities = replace(capabilities, confirmed_modes=frozenset(confirmed))
    if status is None:  # "" is a status too: an unknown one, which decide must refuse
        status = "blocked" if capabilities.blocked_reason else "confirmed"
    return ResourceRef(resource_id, unit_id, kind, status, capabilities)


def room203_index(
    *,
    grants: Sequence[Mapping[str, Any]] = ROOM_GUEST_GRANTS,
    denies: Sequence[Mapping[str, Any]] = ROOM_GUEST_DENIES,
    status: Mapping[str, Status] | None = None,
    modes: Mapping[str, Iterable[str]] | None = None,
) -> PolicyIndex:
    """Units ``unit_203`` and ``unit_lr`` of the simulator fixture and role ``room_guest`` v1.

    ``grants`` and ``denies`` replace the role's rules; ``status`` and ``modes`` set the binding
    status and the confirmed modes of single resources.
    """
    status, modes = status or {}, modes or {}
    rules = RoleRules.model_validate({"grants": grants, "denies": denies})
    resources = {
        res_id: _resource(res_id, unit_id, status.get(res_id), modes.get(res_id))
        for unit_id, members in UNITS.items()
        for res_id in members
    }
    units = {unit_id: frozenset(members) for unit_id, members in UNITS.items()}
    return PolicyIndex({("room_guest", 1): rules}, resources, units, policy_version=1)


# The stay of spec §7 in room 203.
STAY_START = datetime(2026, 11, 1, 14, tzinfo=UTC)
STAY_END = datetime(2026, 11, 4, 10, tzinfo=UTC)
DURING_STAY = datetime(2026, 11, 2, 12, tzinfo=UTC)


def guest_context(*fences: Mapping[str, Any], **fields: Any) -> EffectiveContext:
    """The guest of room 203 during the stay, bound by role room_guest v1; ``fields`` override."""
    ctx = EffectiveContext(
        principal_ref="prn_guest",
        role_bindings=(RoleBinding("room_guest", 1, frozenset({"unit_203"})),),
        fences=tuple(Fence.model_validate(fence) for fence in fences),
        not_before=STAY_START,
        not_after=STAY_END,
        stay_id="stay_203",
        purpose="access",
        policy_version=1,
    )
    return replace(ctx, **fields)


def resident_context(*fences: Mapping[str, Any], **fields: Any) -> EffectiveContext:
    """A resident without a window, bound to both units by role room_guest v1 as well."""
    ctx = EffectiveContext(
        principal_ref="prn_res",
        role_bindings=(RoleBinding("room_guest", 1, frozenset({"unit_203", "unit_lr"})),),
        fences=tuple(Fence.model_validate(fence) for fence in fences),
        not_before=None,
        not_after=None,
        stay_id=None,
        purpose="access",
        policy_version=1,
    )
    return replace(ctx, **fields)
