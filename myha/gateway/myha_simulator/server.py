"""Fake Home Assistant: Core WebSocket API, Supervisor REST API and the ``/_sim`` control API.

A test and dev double, not a security boundary. One ``SimState`` per app holds the world in
memory and behaves like Home Assistant where a client can tell: ``state_changed`` only on real
changes, carrying the context of the call that caused it; calls for unknown or unavailable
entities succeed without effect; errors with the codes and texts Core and Supervisor send.
"""

import contextlib
import copy
import itertools
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Response, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

from myha_simulator import fixtures
from myha_simulator.fixtures import Fixture

logger = logging.getLogger(__name__)

JSON = dict[str, Any]
MAX_MESSAGE_BYTES = 4 * 1024 * 1024  # per incoming WebSocket message
REGISTRY_KEYS = ("entity_id", "unique_id", "device_id", "area_id", "platform")
HVAC_MODES = frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"})
# What Core still reports for an unavailable entity: capability and static attributes.
KEPT_WHEN_UNAVAILABLE = frozenset(
    {
        "friendly_name",
        "icon",
        "entity_picture",
        "device_class",
        "unit_of_measurement",
        "supported_features",
        "supported_color_modes",
        "hvac_modes",
        "min_temp",
        "max_temp",
        "target_temp_step",
    }
)
AuthHeader = Annotated[str | None, Header()]


def _error(msg_id: Any, code: str, message: str, translation: JSON | None = None) -> JSON:
    error = {"code": code, "message": message, **(translation or {})}
    return {"id": msg_id, "type": "result", "success": False, "error": error}


class CommandError(Exception):
    """A command that fails with ``{"success": false, "error": {"code", "message", ...}}``."""

    def __init__(self, code: str, message: str, translation: JSON | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.translation = code, message, translation


# Core's English texts of the ServiceValidationErrors raised here, by translation key.
VALIDATION_TEXTS = {
    "temp_out_of_range": "Provided temperature {check_temp} is not valid. "
    "Accepted range is {min_temp} to {max_temp}.",
    "not_valid_hvac_mode": "HVAC mode {mode} is not valid. Valid HVAC modes are: {modes}.",
    "service_not_supported": "Entity {entity_id} does not support action {domain}.{service}.",
}


def _invalid(domain: str, key: str, /, **placeholders: str) -> CommandError:
    """A ``ServiceValidationError`` as Core's ``call_service`` answers it."""
    message = "Validation error: " + VALIDATION_TEXTS[key].format(**placeholders)
    translation = {
        "translation_key": key,
        "translation_placeholders": placeholders,
        "translation_domain": domain,
    }
    return CommandError("service_validation_error", message, translation)


def _number(data: JSON, key: str) -> float:
    """``data[key]`` if it is a number, else a schema error."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError("invalid_format", f"expected a number for data['{key}']")
    return value


def _percent(data: JSON, key: str) -> float:
    """``data[key]`` if it is a number from 0 to 100, else a schema error."""
    if not 0 <= (value := _number(data, key)) <= 100:
        raise CommandError("invalid_format", f"value must be from 0 to 100 for data['{key}']")
    return value


# Service handlers get the old state, a copy of its attributes to change and the service data;
# they return the new state.
def _light_on(state: str, attrs: JSON, data: JSON) -> str:
    pct = _percent(data, "brightness_pct") if "brightness_pct" in data else None
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
    temperature = _number(data, "temperature")
    low, high = attrs.get("min_temp", 7), attrs.get("max_temp", 35)
    if not low <= temperature <= high:
        raise _invalid(
            "climate",
            "temp_out_of_range",
            check_temp=str(float(temperature)),
            min_temp=str(low),
            max_temp=str(high),
        )
    attrs["temperature"] = temperature
    return state


def _set_hvac_mode(state: str, attrs: JSON, data: JSON) -> str:
    mode = data.get("hvac_mode")
    if not isinstance(mode, str) or mode not in HVAC_MODES:
        raise CommandError("invalid_format", "expected a valid HVAC mode for data['hvac_mode']")
    if mode not in attrs["hvac_modes"]:  # KeyError: a climate entity without modes is broken
        modes = ", ".join(attrs["hvac_modes"])
        raise _invalid("climate", "not_valid_hvac_mode", mode=mode, modes=modes)
    return mode


def _set_position(state: str, attrs: JSON, data: JSON) -> str:
    attrs["current_position"] = position = int(_percent(data, "position"))
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
# CoverEntityFeature bit each cover service needs; Core refuses the call without it.
REQUIRED_FEATURES = {
    "cover.open_cover": 1,
    "cover.close_cover": 2,
    "cover.set_cover_position": 4,
    "cover.stop_cover": 8,
}


class Connection:
    """One WebSocket client; sending to or closing a socket that is gone does nothing."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.user_id: str | None = None
        self.is_admin = False
        self.subscriptions: dict[int, str] = {}  # subscribe command id -> event type or "*"

    async def receive(self) -> Any:
        """The next JSON message; bad data closes with 1007, a binary frame with 1003."""
        try:
            return await self.ws.receive_json()
        except ValueError:
            code = 1007
        except (KeyError, TypeError):  # a binary frame has no text
            code = 1003
        await self.close(code)
        raise WebSocketDisconnect(code)

    async def send(self, message: JSON) -> None:
        # RuntimeError: uvicorn refuses to send once its keepalive has closed the connection
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await self.ws.send_json(message)

    async def close(self, code: int = 1000) -> None:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await self.ws.close(code)


class SimState:
    """The simulated world; every change reaches subscribers as Home Assistant sends it."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.connections: set[Connection] = set()
        self.states: dict[str, JSON] = {}
        self._context_ids = itertools.count(1)  # never reset, so ids stay unique
        self._load()
        for spec in fixture.entities:
            attributes = copy.deepcopy(spec["attributes"])
            self._write(spec["entity_id"], spec["state"], attributes, self.context())

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
        self.offline: dict[str, JSON] = {}  # entity id -> state before /_sim/offline

    def now(self) -> datetime:
        """Home Assistant's clock, wrong by ``clock_skew`` seconds."""
        return datetime.now(UTC) + timedelta(seconds=self.clock_skew)

    def context(self, user_id: str | None = None) -> JSON:
        """A new context; the id has the length of Core's ULIDs and counts up."""
        return {"id": f"{next(self._context_ids):026d}", "parent_id": None, "user_id": user_id}

    async def fire(
        self,
        event_type: str,
        data: JSON,
        context: JSON | None = None,
        time_fired: str | None = None,
    ) -> None:
        """Send an event to every matching subscription."""
        event = {
            "event_type": event_type,
            "data": data,
            "origin": "LOCAL",
            "time_fired": time_fired or self.now().isoformat(),
            "context": context or self.context(),
        }
        # shortcut: a subscriber that stops reading blocks fan-out, upgrade to per-connection
        # queues once E2E runs many clients
        for conn in list(self.connections):
            for sub_id, event_filter in list(conn.subscriptions.items()):
                if event_filter in ("*", event_type):
                    await conn.send({"id": sub_id, "type": "event", "event": event})

    def _write(self, entity_id: str, state: str, attributes: JSON, context: JSON) -> JSON | None:
        """Like ``hass.states.async_set``: the ``state_changed`` data, None if nothing changed."""
        now = self.now().isoformat()
        old = self.states.get(entity_id)
        if old is not None and old["state"] == state and old["attributes"] == attributes:
            old["last_reported"] = now
            return None
        self.states[entity_id] = new = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": old["last_changed"]
            if old is not None and old["state"] == state
            else now,
            "last_reported": now,
            "last_updated": now,
            "context": context,
        }
        return {"entity_id": entity_id, "old_state": old, "new_state": new}

    async def set_state(
        self, entity_id: str, state: str, attributes: JSON, context: JSON | None = None
    ) -> JSON:
        """Write a state, fire ``state_changed`` if it changed and return the current state."""
        context = context or self.context()
        if (data := self._write(entity_id, state, attributes, context)) is not None:
            # Core stamps the event with the time of the new state.
            await self.fire("state_changed", data, context, data["new_state"]["last_updated"])
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
                    conn.subscriptions[msg_id] = msg.get("event_type", "*")
                case "unsubscribe_events":
                    subscription = msg.get("subscription")
                    if not isinstance(subscription, int) or subscription not in conn.subscriptions:
                        raise CommandError("not_found", "Subscription not found.")
                    del conn.subscriptions[subscription]
                case "call_service":
                    result = await self.call_service(conn, msg)
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
            return _error(msg_id, exc.code, exc.message, exc.translation)
        except Exception as exc:  # like Core: answer, log and keep the connection
            logger.exception("command %s failed", msg["type"])
            return _error(msg_id, "unknown_error", str(exc))
        return {"id": msg_id, "type": "result", "success": True, "result": result}

    def auth_list(self, conn: Connection) -> list[JSON]:
        if not conn.is_admin:
            raise CommandError("unauthorized", "Unauthorized")
        if self.auth_list_mode == "error":
            raise CommandError("unknown_error", "Simulated failure of config/auth/list")
        if self.auth_list_mode == "schema_missing_groups":
            return [{k: v for k, v in user.items() if k != "group_ids"} for user in self.users]
        return self.users

    async def call_service(self, conn: Connection, msg: JSON) -> JSON:
        """Run a call like Core; every attempt lands in the service-call log."""
        target, data = msg.get("target"), msg.get("service_data", {})
        entity_id = target.get("entity_id") if isinstance(target, dict) else None
        domain, service = msg.get("domain"), msg.get("service")
        record = {
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "data": data,
            "context_id": None,
            "error": "unknown_error",  # until the call ends otherwise
            "ts": self.now().isoformat(),
        }
        self.service_calls.append(record)
        try:
            context = await self._run_service(conn, f"{domain}.{service}", entity_id, data)
        except CommandError as exc:
            record["error"] = exc.code
            raise
        record.update(context_id=context["id"], error=None)
        return {"context": context}

    async def _run_service(self, conn: Connection, name: str, entity_id: Any, data: Any) -> JSON:
        if self.fail_next is not None:
            code, self.fail_next = self.fail_next, None
            raise CommandError(code, "Simulated service failure")
        if (handler := SERVICES.get(name)) is None:
            raise CommandError("not_found", f"Service {name} not found.")
        if not isinstance(data, dict):
            raise CommandError("invalid_format", "expected a dictionary for data['service_data']")
        context = self.context(conn.user_id)
        known = isinstance(entity_id, str) and entity_id.startswith(f"{name.split('.')[0]}.")
        old = self.states.get(entity_id) if known else None
        if old is None:
            logger.warning("%s: no entity %s", name, entity_id)  # Core only warns as well
        elif old["state"] != "unavailable":  # Core skips it as well; the call still succeeds
            required = REQUIRED_FEATURES.get(name, 0)
            if (old["attributes"].get("supported_features", 0) & required) != required:
                domain, service = name.split(".")
                placeholders = {"domain": domain, "service": service, "entity_id": entity_id}
                raise _invalid("homeassistant", "service_not_supported", **placeholders)
            # shortcut: Core checks the service schema before it resolves entities, the handlers
            # only for existing, available ones; split schema from handlers when a test needs it
            attrs = dict(old["attributes"])
            state = handler(old["state"], attrs, data)
            jump = self.mode_setpoints.get((entity_id, state))
            if name == "climate.set_hvac_mode" and state != old["state"] and jump is not None:
                attrs["temperature"] = jump
            await self.set_state(entity_id, state, attrs, context)
        return context

    async def set_offline(self, entity_id: str) -> JSON:
        """``unavailable``; like Core, only capability and static attributes remain."""
        old = self.offline.setdefault(entity_id, self.state(entity_id))
        attributes = {k: v for k, v in old["attributes"].items() if k in KEPT_WHEN_UNAVAILABLE}
        return await self.set_state(entity_id, "unavailable", attributes)

    async def set_online(self, entity_id: str) -> JSON:
        """Back to the state and attributes from before ``set_offline``."""
        if (old := self.offline.pop(entity_id, None)) is None:
            return self.state(entity_id)
        return await self.set_state(entity_id, old["state"], dict(old["attributes"]))

    async def rename(self, entity_id: str, new_entity_id: str) -> None:
        """Same registry entry and ``unique_id`` under a new entity id of the same domain."""
        entry = self.entry(entity_id)
        if new_entity_id.split(".")[0] != entity_id.split(".")[0]:
            raise HTTPException(422, "the domain must stay the same")
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
                before, after = old.get(item_id), new.get(item_id)
                data: JSON
                if before == after:
                    continue
                if before is None:
                    data = {"action": "create", key: item_id}
                elif after is None:
                    data = {"action": "remove", key: item_id}
                else:
                    changes = {k: v for k, v in before.items() if after.get(k) != v}
                    data = {"action": "update", key: item_id, "changes": changes}
                await self.fire(event_type, data)
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
    attributes: JSON = {}  # merged into the current ones


class EntityAttributes(_Body):
    entity_id: str
    attributes: JSON  # merged into the current ones; null removes an attribute


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
    new_entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")


class Replace(_Body):
    entity_id: str
    new_unique_id: str


class AreaChange(_Body):
    entity_id: str
    area_id: str | None


def _supervisor(authorization: str | None, data: JSON, *, forbidden: bool = False) -> Response:
    """A Supervisor answer; errors in aiohttp's plain-text form."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not token:
        return PlainTextResponse("401: Unauthorized", status_code=401)
    if token != fixtures.ADMIN_TOKEN or forbidden:
        return PlainTextResponse("403: Forbidden", status_code=403)
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
            auth = await conn.receive()
            is_auth = isinstance(auth, dict) and auth.get("type") == "auth"
            token = auth.get("access_token") if is_auth else None
            if not isinstance(token, str) or token not in fixtures.TOKEN_USERS:
                message = "Invalid access token or password"
                await conn.send({"type": "auth_invalid", "message": message})
                await conn.close()
                return
            conn.user_id, conn.is_admin = fixtures.TOKEN_USERS[token], token == fixtures.ADMIN_TOKEN
            await conn.send({"type": "auth_ok", "ha_version": fixtures.HA_VERSION})
            last_id = 0
            while True:
                msg = await conn.receive()
                msg_id = msg.get("id") if isinstance(msg, dict) else 0
                kind = msg.get("type") if isinstance(msg, dict) else None
                if type(msg_id) is not int or msg_id <= 0 or not isinstance(kind, str) or not kind:
                    reply = _error(msg_id, "invalid_format", "Message incorrectly formatted.")
                elif msg_id <= last_id:
                    reply = _error(msg_id, "id_reuse", "Identifier values have to increase.")
                else:
                    reply = await sim.handle(conn, msg)
                    if reply.get("error", {}).get("code") != "unknown_command":  # like Core
                        last_id = msg_id
                await conn.send(reply)
        except (WebSocketDisconnect, WebSocketDisconnected):
            pass  # closed by the client, after bad data or by /_sim/drop-connections
        finally:
            sim.connections.discard(conn)

    @app.get("/info")
    async def info(authorization: AuthHeader = None) -> Response:
        data = {
            "supervisor": sim.supervisor_version,
            "homeassistant": fixtures.HA_VERSION,
            "channel": "stable",
            "arch": "amd64",
            "supported": True,
        }
        return _supervisor(authorization, data)

    @app.get("/host/info")
    async def host_info(authorization: AuthHeader = None) -> Response:
        data = {"dt_synchronized": True, "operating_system": "Home Assistant OS 18.3"}
        return _supervisor(authorization, data, forbidden=sim.host_info_mode == "forbidden")

    @app.get("/addons/self/info")
    async def addon_info(authorization: AuthHeader = None) -> Response:
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
        old = sim.state(entity_id)
        state = old["state"] if body.state is None else body.state
        return await sim.set_state(entity_id, state, {**old["attributes"], **body.attributes})

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
        """A wrong Home Assistant clock: every timestamp it sends moves by ``seconds``."""
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
        old, new = sim.state(body.entity_id), body.attributes
        merged = {**old["attributes"], **new}
        attributes = {k: v for k, v in merged.items() if k not in new or v is not None}
        return await sim.set_state(body.entity_id, old["state"], attributes)

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
