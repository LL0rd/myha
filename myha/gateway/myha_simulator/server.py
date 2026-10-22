"""Fake Home Assistant: Core WebSocket API, Supervisor REST API and the ``/_sim`` control API.

A test and dev double, not a security boundary. One ``SimState`` per app holds the world in
memory and behaves like Home Assistant where a client can tell: ``state_changed`` only on real
changes, carrying the context of the service call that caused it; unavailable entities ignore
service calls.
"""

import contextlib
import copy
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

from myha_simulator import fixtures
from myha_simulator.fixtures import Fixture

JSON = dict[str, Any]
MAX_MESSAGE_BYTES = 4 * 1024 * 1024  # per incoming WebSocket message
REGISTRY_KEYS = ("entity_id", "unique_id", "device_id", "area_id", "platform")
TOKENS = (fixtures.ADMIN_TOKEN, fixtures.USER_TOKEN)
AuthHeader = Annotated[str | None, Header()]


def _now() -> datetime:
    return datetime.now(UTC)


def _context() -> JSON:
    return {"id": uuid.uuid4().hex, "parent_id": None, "user_id": None}


def _error(msg_id: Any, code: str, message: str) -> JSON:
    error = {"code": code, "message": message}
    return {"id": msg_id, "type": "result", "success": False, "error": error}


class CommandError(Exception):
    """A command that fails with ``{"success": false, "error": {"code", "message"}}``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _number(data: JSON, key: str, low: float, high: float) -> float:
    """``data[key]`` if it is a number within ``[low, high]``, else a schema error."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise CommandError("invalid_format", f"{key} must be a number from {low} to {high}")
    return value


# Service handlers get the old state, a copy of its attributes to change and the service data;
# they return the new state.
def _light_on(state: str, attrs: JSON, data: JSON) -> str:
    pct = _number(data, "brightness_pct", 0, 100) if "brightness_pct" in data else None
    if pct == 0:  # Home Assistant turns the light off
        return _light_off(state, attrs, data)
    if "brightness" in attrs:  # dimmable; a light that was off comes on at full brightness
        old = attrs["brightness"] or 255
        attrs["brightness"] = old if pct is None else round(pct * 255 / 100)
    if "color_mode" in attrs:
        attrs["color_mode"] = attrs["color_mode"] or attrs["supported_color_modes"][0]
    return "on"


def _light_off(state: str, attrs: JSON, data: JSON) -> str:
    for key in ("brightness", "color_mode"):  # None while the light is off
        if key in attrs:
            attrs[key] = None
    return "off"


def _set_temperature(state: str, attrs: JSON, data: JSON) -> str:
    low, high = attrs.get("min_temp", 7), attrs.get("max_temp", 35)
    attrs["temperature"] = _number(data, "temperature", low, high)
    return state


def _set_hvac_mode(state: str, attrs: JSON, data: JSON) -> str:
    modes = attrs.get("hvac_modes", [])
    if data.get("hvac_mode") not in modes:
        raise CommandError("invalid_format", f"hvac_mode must be one of {modes}")
    return str(data["hvac_mode"])


def _set_position(state: str, attrs: JSON, data: JSON) -> str:
    attrs["current_position"] = position = int(_number(data, "position", 0, 100))
    return "open" if position else "closed"


SERVICES: dict[str, Callable[[str, JSON, JSON], str]] = {
    "light.turn_on": _light_on,
    "light.turn_off": _light_off,
    "switch.turn_on": lambda state, attrs, data: "on",
    "switch.turn_off": lambda state, attrs, data: "off",
    "climate.set_temperature": _set_temperature,
    "climate.set_hvac_mode": _set_hvac_mode,
    "cover.open_cover": lambda state, attrs, data: _set_position(state, attrs, {"position": 100}),
    "cover.close_cover": lambda state, attrs, data: _set_position(state, attrs, {"position": 0}),
    "cover.stop_cover": lambda state, attrs, data: state,
    "cover.set_cover_position": _set_position,
}


class Connection:
    """One WebSocket client; sending to or closing a socket that is gone does nothing."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.is_admin = False
        self.subscriptions: dict[int, str | None] = {}  # subscribe command id -> event type

    async def send(self, message: JSON) -> None:
        with contextlib.suppress(WebSocketDisconnect, WebSocketDisconnected):
            await self.ws.send_json(message)

    async def close(self, code: int = 1000) -> None:
        with contextlib.suppress(WebSocketDisconnect, WebSocketDisconnected):
            await self.ws.close(code)


class SimState:
    """The simulated world; every change reaches subscribers as Home Assistant sends it."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.connections: set[Connection] = set()
        self.states: dict[str, JSON] = {}
        self._load()
        for spec in fixture.entities:
            attributes = copy.deepcopy(spec["attributes"])
            self._write(spec["entity_id"], spec["state"], attributes, _context())

    def _load(self) -> None:
        """Registries, users, config and every mode from the fixture; states change separately."""
        fixture = copy.deepcopy(self.fixture)
        self.entities: dict[str, JSON] = {
            e["entity_id"]: {key: e[key] for key in REGISTRY_KEYS} for e in fixture.entities
        }
        self.devices: dict[str, JSON] = {d["id"]: d for d in fixture.devices}
        self.areas: dict[str, JSON] = {a["area_id"]: a for a in fixture.areas}
        self.users: list[JSON] = fixture.users
        self.config: JSON = fixture.config
        self.service_calls: list[JSON] = []
        self.fail_next: str | None = None
        self.clock_skew = 0.0
        self.auth_list_mode = "ok"
        self.supervisor_version = fixtures.SUPERVISOR_VERSION
        self.host_info_mode = "forbidden"
        self.mode_setpoints: dict[tuple[str, str], float] = {}
        self.offline: dict[str, str] = {}  # entity id -> state before /_sim/offline

    async def fire(self, event_type: str, data: JSON, context: JSON | None = None) -> None:
        """Send an event to every matching subscription; ``time_fired`` includes the skew."""
        event = {
            "event_type": event_type,
            "data": data,
            "origin": "LOCAL",
            "time_fired": (_now() + timedelta(seconds=self.clock_skew)).isoformat(),
            "context": context or _context(),
        }
        for conn in list(self.connections):
            for sub_id, event_filter in list(conn.subscriptions.items()):
                if event_filter in (None, event_type):
                    await conn.send({"id": sub_id, "type": "event", "event": event})

    def _write(self, entity_id: str, state: str, attributes: JSON, context: JSON) -> JSON | None:
        """Like ``hass.states.async_set``: the ``state_changed`` data, None if nothing changed."""
        old = self.states.get(entity_id)
        if old is not None and old["state"] == state and old["attributes"] == attributes:
            return None
        now = _now().isoformat()
        self.states[entity_id] = new = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": old["last_changed"]
            if old is not None and old["state"] == state
            else now,
            "last_updated": now,
            "context": context,
        }
        return {"entity_id": entity_id, "old_state": old, "new_state": new}

    async def set_state(
        self, entity_id: str, state: str, attributes: JSON, context: JSON | None = None
    ) -> JSON:
        """Write a state, fire ``state_changed`` if it changed and return the current state."""
        context = context or _context()
        if (data := self._write(entity_id, state, attributes, context)) is not None:
            await self.fire("state_changed", data, context)
        return self.states[entity_id]

    async def remove_state(self, entity_id: str) -> None:
        old = self.states.pop(entity_id)
        data = {"entity_id": entity_id, "old_state": old, "new_state": None}
        await self.fire("state_changed", data)

    def state(self, entity_id: str) -> JSON:
        """The current state of a known entity, else HTTP 404 (control API)."""
        if (state := self.states.get(entity_id)) is None:
            raise HTTPException(404, f"unknown entity {entity_id}")
        return state

    def entry(self, entity_id: str) -> JSON:
        """The entity registry entry, else HTTP 404 (control API)."""
        if (entry := self.entities.get(entity_id)) is None:
            raise HTTPException(404, f"unknown registry entry {entity_id}")
        return entry

    async def merge(self, entity_id: str, state: str | None, attributes: JSON) -> JSON:
        """New state (None keeps it) and attributes merged into the current ones."""
        old = self.state(entity_id)
        new_state = old["state"] if state is None else state
        return await self.set_state(entity_id, new_state, {**old["attributes"], **attributes})

    async def handle(self, conn: Connection, msg: JSON) -> JSON:
        """Answer one command whose ``id`` and ``type`` are already checked."""
        msg_id = msg["id"]
        result: Any = None
        try:
            match msg["type"]:
                case "ping":
                    return {"id": msg_id, "type": "pong"}
                case "get_states":
                    result = list(self.states.values())
                case "get_config":
                    result = self.config
                case "subscribe_events":
                    conn.subscriptions[msg_id] = msg.get("event_type")
                case "unsubscribe_events":
                    subscription = msg.get("subscription")
                    if not isinstance(subscription, int) or subscription not in conn.subscriptions:
                        raise CommandError("not_found", "Subscription not found.")
                    del conn.subscriptions[subscription]
                case "call_service":
                    result = await self.call_service(msg)
                case "config/entity_registry/list":
                    result = list(self.entities.values())
                case "config/device_registry/list":
                    result = list(self.devices.values())
                case "config/area_registry/list":
                    result = list(self.areas.values())
                case "config/auth/list":
                    result = self.auth_list(conn)
                case _:
                    raise CommandError("unknown_command", "Unknown command.")
        except CommandError as exc:
            return _error(msg_id, exc.code, exc.message)
        return {"id": msg_id, "type": "result", "success": True, "result": result}

    def auth_list(self, conn: Connection) -> list[JSON]:
        if not conn.is_admin:
            raise CommandError("unauthorized", "Unauthorized")
        if self.auth_list_mode == "error":
            raise CommandError("unknown_error", "Simulated failure of config/auth/list")
        if self.auth_list_mode == "schema_missing_groups":
            return [{k: v for k, v in user.items() if k != "group_ids"} for user in self.users]
        return self.users

    async def call_service(self, msg: JSON) -> JSON:
        if self.fail_next is not None:
            code, self.fail_next = self.fail_next, None
            raise CommandError(code, "Simulated service failure")
        domain, service = msg.get("domain"), msg.get("service")
        name = f"{domain}.{service}"
        if (handler := SERVICES.get(name)) is None:
            raise CommandError("service_not_found", f"Service {name} not found.")
        target, data = msg.get("target"), msg.get("service_data", {})
        entity_id = target.get("entity_id") if isinstance(target, dict) else None
        if (
            not isinstance(entity_id, str)
            or not entity_id.startswith(f"{domain}.")
            or entity_id not in self.states
        ):
            raise CommandError("not_found", f"Entity {entity_id} not found.")
        if not isinstance(data, dict):
            raise CommandError("invalid_format", "service_data must be an object")
        old, context = self.states[entity_id], _context()
        if old["state"] != "unavailable":  # Home Assistant skips it, the call still succeeds
            attrs = dict(old["attributes"])
            state = handler(old["state"], attrs, data)
            if name == "climate.set_hvac_mode" and (entity_id, state) in self.mode_setpoints:
                attrs["temperature"] = self.mode_setpoints[(entity_id, state)]
            await self.set_state(entity_id, state, attrs, context)
        call = {"domain": domain, "service": service, "entity_id": entity_id, "data": data}
        self.service_calls.append(call | {"context_id": context["id"], "ts": _now().isoformat()})
        return {"context": context}

    async def set_offline(self, entity_id: str) -> JSON:
        self.offline.setdefault(entity_id, self.state(entity_id)["state"])
        return await self.merge(entity_id, "unavailable", {})

    async def set_online(self, entity_id: str) -> JSON:
        return await self.merge(entity_id, self.offline.pop(entity_id, None), {})

    async def rename(self, entity_id: str, new_entity_id: str) -> None:
        """Same registry entry and ``unique_id`` under a new entity id."""
        entry = self.entry(entity_id)
        if new_entity_id in self.entities or new_entity_id in self.states:
            raise HTTPException(409, f"{new_entity_id} exists")
        # shortcut: offline/mode-setpoint entries keep the old id, move them when a test needs it
        del self.entities[entity_id]
        self.entities[new_entity_id] = entry
        entry["entity_id"] = new_entity_id
        changes = {"entity_id": entity_id}
        data = {"action": "update", "entity_id": new_entity_id, "changes": changes}
        await self.fire("entity_registry_updated", data | {"old_entity_id": entity_id})
        if (old := self.states.get(entity_id)) is not None:
            await self.remove_state(entity_id)
            await self.set_state(new_entity_id, old["state"], dict(old["attributes"]))

    async def replace(self, entity_id: str, new_unique_id: str) -> None:
        """Device replacement: new ``unique_id`` on a new device in the old device's area."""
        entry = self.entry(entity_id)
        device = self.devices.get(entry["device_id"], {}) | {"id": f"dev_{new_unique_id}"}
        self.devices[device["id"]] = device
        changes = {"unique_id": entry["unique_id"], "device_id": entry["device_id"]}
        entry.update(unique_id=new_unique_id, device_id=device["id"])
        await self.fire("device_registry_updated", {"action": "create", "device_id": device["id"]})
        data = {"action": "update", "entity_id": entity_id, "changes": changes}
        await self.fire("entity_registry_updated", data)

    async def move(self, entity_id: str, area_id: str | None) -> None:
        """New area for the entity, or for its device if the entity has none of its own."""
        entry = self.entry(entity_id)
        if area_id is not None and area_id not in self.areas:
            raise HTTPException(404, f"unknown area {area_id}")
        device = self.devices.get(entry["device_id"])
        if entry["area_id"] is None and device is not None:
            changes = {"area_id": device["area_id"]}
            device["area_id"] = area_id
            data = {"action": "update", "device_id": device["id"], "changes": changes}
            await self.fire("device_registry_updated", data)
        else:
            changes = {"area_id": entry["area_id"]}
            entry["area_id"] = area_id
            data = {"action": "update", "entity_id": entity_id, "changes": changes}
            await self.fire("entity_registry_updated", data)

    async def reset(self) -> None:
        """Back to the fixture without dropping connections; subscribers see what changed."""
        old_entities, old_devices = self.entities, self.devices
        self._load()
        for event_type, key, old, new in (
            ("entity_registry_updated", "entity_id", old_entities, self.entities),
            ("device_registry_updated", "device_id", old_devices, self.devices),
        ):
            for item_id in [*old, *(item_id for item_id in new if item_id not in old)]:
                if old.get(item_id) == new.get(item_id):
                    continue
                action = (
                    "remove" if item_id not in new else "update" if item_id in old else "create"
                )
                await self.fire(event_type, {"action": action, key: item_id})
        specs = {spec["entity_id"]: spec for spec in self.fixture.entities}
        for entity_id in [entity_id for entity_id in self.states if entity_id not in specs]:
            await self.remove_state(entity_id)
        for entity_id, spec in specs.items():
            await self.set_state(entity_id, spec["state"], copy.deepcopy(spec["attributes"]))
        self.states = {entity_id: self.states[entity_id] for entity_id in specs}  # fixture order


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in test requests fail loudly


class StateBody(_Body):
    state: str | None = None
    attributes: JSON = {}


class EntityAttributes(_Body):
    entity_id: str
    attributes: JSON


class ServiceFailure(_Body):
    error: str


class ClockSkew(_Body):
    seconds: float


class AuthListMode(_Body):
    mode: Literal["ok", "error", "schema_missing_groups"]


class HostInfoMode(_Body):
    mode: Literal["ok", "forbidden"]


class SupervisorVersion(_Body):
    version: str


class ModeSetpoint(_Body):
    entity_id: str
    mode: str
    temperature: float


class Rename(_Body):
    entity_id: str
    new_entity_id: str


class Replace(_Body):
    entity_id: str
    new_unique_id: str


class AreaChange(_Body):
    entity_id: str
    area_id: str | None


def _supervisor(authorization: str | None, data: JSON, *, forbidden: bool = False) -> JSONResponse:
    """A Supervisor answer: 401 without the token, 403 if forbidden, else ``data``."""
    if authorization != f"Bearer {fixtures.ADMIN_TOKEN}":
        return JSONResponse({"result": "error", "message": "Unauthorized"}, status_code=401)
    if forbidden:
        return JSONResponse({"result": "error", "message": "Forbidden"}, status_code=403)
    return JSONResponse({"result": "ok", "data": data})


def create_app(fixture: Fixture = fixtures.DEFAULT) -> FastAPI:
    """Core WebSocket API, Supervisor REST API and the ``/_sim`` control API in one app."""
    sim = SimState(fixture)
    app = FastAPI(title="myHA simulator")

    @app.websocket("/api/websocket")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        conn = Connection(ws)
        sim.connections.add(conn)
        try:
            await conn.send({"type": "auth_required", "ha_version": fixtures.HA_VERSION})
            auth = await ws.receive_json()
            if (
                not isinstance(auth, dict)
                or auth.get("type") != "auth"
                or auth.get("access_token") not in TOKENS
            ):
                await conn.send({"type": "auth_invalid", "message": "Invalid access token"})
                await conn.close()
                return
            conn.is_admin = auth["access_token"] == fixtures.ADMIN_TOKEN
            await conn.send({"type": "auth_ok", "ha_version": fixtures.HA_VERSION})
            last_id = 0
            while True:
                msg = await ws.receive_json()
                msg_id = msg.get("id") if isinstance(msg, dict) else None
                if type(msg_id) is not int or not isinstance(msg.get("type"), str):
                    await conn.send(
                        _error(msg_id, "invalid_format", "Message incorrectly formatted.")
                    )
                elif msg_id <= last_id:
                    await conn.send(
                        _error(msg_id, "id_reuse", "Identifier values have to increase.")
                    )
                else:
                    last_id = msg_id
                    await conn.send(await sim.handle(conn, msg))
        except (WebSocketDisconnect, WebSocketDisconnected):
            pass  # closed by the client or by /_sim/drop-connections
        finally:
            sim.connections.discard(conn)

    @app.get("/info")
    async def info(authorization: AuthHeader = None) -> JSONResponse:
        data = {
            "supervisor": sim.supervisor_version,
            "channel": "stable",
            "arch": "amd64",
            "healthy": True,
            "supported": True,
        }
        return _supervisor(authorization, data)

    @app.get("/host/info")
    async def host_info(authorization: AuthHeader = None) -> JSONResponse:
        data = {"dt_synchronized": True, "operating_system": "Home Assistant OS 18.3"}
        return _supervisor(authorization, data, forbidden=sim.host_info_mode == "forbidden")

    @app.get("/addons/self/info")
    async def addon_info(authorization: AuthHeader = None) -> JSONResponse:
        data = {
            "ingress_entry": "/api/hassio_ingress/simtoken",
            "ingress_url": "/api/hassio_ingress/simtoken/",
            "name": "myHA",
            "version": "0.1.0",
        }
        return _supervisor(authorization, data)

    @app.post("/_sim/reset")
    async def reset() -> None:
        await sim.reset()

    @app.post("/_sim/state/{entity_id}")
    async def set_state(entity_id: str, body: StateBody) -> JSON:
        return await sim.merge(entity_id, body.state, body.attributes)

    @app.post("/_sim/offline/{entity_id}")
    async def offline(entity_id: str) -> JSON:
        return await sim.set_offline(entity_id)

    @app.post("/_sim/online/{entity_id}")
    async def online(entity_id: str) -> JSON:
        return await sim.set_online(entity_id)

    @app.post("/_sim/fail-next-service")
    async def fail_next_service(body: ServiceFailure) -> None:
        sim.fail_next = body.error

    @app.post("/_sim/drop-connections")
    async def drop_connections() -> dict[str, int]:
        connections, sim.connections = sim.connections, set()
        for conn in connections:
            await conn.close(1012)
        return {"closed": len(connections)}

    @app.post("/_sim/clock-skew")
    async def clock_skew(body: ClockSkew) -> None:
        sim.clock_skew = body.seconds

    @app.get("/_sim/service-calls")
    async def service_calls() -> list[JSON]:
        return sim.service_calls

    @app.post("/_sim/service-calls/reset")
    async def reset_service_calls() -> None:
        sim.service_calls.clear()

    @app.post("/_sim/auth/users")
    async def auth_users(users: Annotated[list[JSON], Body()]) -> None:
        sim.users = users

    @app.post("/_sim/auth/list-mode")
    async def auth_list_mode(body: AuthListMode) -> None:
        sim.auth_list_mode = body.mode

    @app.post("/_sim/supervisor/version")
    async def supervisor_version(body: SupervisorVersion) -> None:
        sim.supervisor_version = body.version

    @app.post("/_sim/host-info-mode")
    async def host_info_mode(body: HostInfoMode) -> None:
        sim.host_info_mode = body.mode

    @app.post("/_sim/climate/mode-setpoint")
    async def mode_setpoint(body: ModeSetpoint) -> None:
        sim.state(body.entity_id)  # 404 for unknown entities
        sim.mode_setpoints[(body.entity_id, body.mode)] = body.temperature

    @app.post("/_sim/registry/rename")
    async def registry_rename(body: Rename) -> None:
        await sim.rename(body.entity_id, body.new_entity_id)

    @app.post("/_sim/registry/replace")
    async def registry_replace(body: Replace) -> None:
        await sim.replace(body.entity_id, body.new_unique_id)

    @app.post("/_sim/registry/area")
    async def registry_area(body: AreaChange) -> None:
        await sim.move(body.entity_id, body.area_id)

    @app.post("/_sim/entity/attributes")
    async def entity_attributes(body: EntityAttributes) -> JSON:
        return await sim.merge(body.entity_id, None, body.attributes)

    @app.get("/_sim/connections")
    async def connections() -> dict[str, int]:
        return {"connections": len(sim.connections)}

    return app


def make_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """uvicorn server with the 4 MiB message limit; logging is left to the caller."""
    config = uvicorn.Config(
        app, host=host, port=port, ws_max_size=MAX_MESSAGE_BYTES, log_config=None
    )
    return uvicorn.Server(config)
