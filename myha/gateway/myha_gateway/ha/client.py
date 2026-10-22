"""The gateway's one WebSocket connection to Home Assistant Core (design §3, ``ha.client``).

``start`` connects and keeps reconnecting with backoff until ``stop``. While disconnected the
last snapshot stays readable but stale (``connected`` is false) and commands raise
``HAUnavailable``. Handlers run in the reader task: they must not wait for this client's
commands, or they wait for their own answer.
"""

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from websockets import ClientConnection, ConnectionClosed, WebSocketException, connect

from myha_gateway.ha.messages import (
    MALFORMED,
    ConnectionStatus,
    HAState,
    HAUser,
    Registry,
    ServiceResult,
    StateChanged,
    context_id,
    parse_registry,
    parse_state_changed,
    parse_states,
    parse_temperature_unit,
    parse_time,
    parse_user,
)

logger = logging.getLogger(__name__)
# websockets logs every frame at DEBUG, the auth frame with the token included.
_ws_logger = logging.getLogger(f"{__name__}.websocket")
_ws_logger.setLevel(logging.INFO)

MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # get_states of a large installation; check in S0
COMMAND_TIMEOUT = 30.0
SERVICE_TIMEOUT = 15.0
# Core's own ping: the Supervisor proxy answers WebSocket protocol pings without Core.
PING_INTERVAL = 20.0
PING_TIMEOUT = 15.0
START_WAIT = 10.0  # start() waits at most this long for the first attempt
BACKOFF_MAX = 30.0
CELSIUS = "°C"
REGISTRY_EVENTS = ("entity_registry_updated", "device_registry_updated", "area_registry_updated")
EVENTS = ("state_changed", "core_config_updated", *REGISTRY_EVENTS)


def utcnow() -> datetime:
    return datetime.now(UTC)


class HAError(Exception):
    """Base of every error this client raises."""


class HAUnavailable(HAError):
    """Not connected, connection lost, or no answer in time (a sent call may still run)."""


class HAProtocolError(HAError):
    """A failed command whose success the client relies on, or an answer Core never sends."""


class HAServiceError(HAError):
    """``call_service`` answered with ``success: false``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


class _AuthInvalid(HAError):
    """The token was rejected; retried, since it may become valid after a Supervisor restart."""


def _backoff() -> Iterator[float]:
    """Reconnect delays: 1, 2, 4, … 30 s, each ±20 % so that restarts do not align."""
    delay = 1.0
    while True:
        jittered = delay * random.uniform(0.8, 1.2)  # noqa: S311 (jitter, not a secret)
        yield min(jittered, BACKOFF_MAX)
        delay = min(delay * 2, BACKOFF_MAX)


def _error(reply: dict[str, Any]) -> tuple[str, str]:
    error = reply.get("error")
    if not isinstance(error, dict):
        return "unknown_error", "no error details"
    return str(error.get("code", "unknown_error")), str(error.get("message", ""))


def _type(raw: str | bytes) -> Any:
    """The ``type`` of a message, None if it has none."""
    with contextlib.suppress(ValueError):
        message = json.loads(raw)
        return message.get("type") if isinstance(message, dict) else None
    return None


class HAClient:
    """Snapshot, registries, ``call_service`` and ``config/auth/list`` over one connection."""

    def __init__(
        self,
        ws_url: str,
        token: str,
        *,
        on_event: Callable[[StateChanged], Awaitable[None]],
        on_status: Callable[[ConnectionStatus], Awaitable[None]],
        clock: Callable[[], datetime] = utcnow,
        registry_debounce: float = 0.5,
        registry_refresh: float = 300.0,
    ) -> None:
        self._ws_url, self._token = ws_url, token
        self._on_event, self._on_status, self._clock = on_event, on_status, clock
        self._registry_debounce, self._registry_refresh = registry_debounce, registry_refresh
        self.generation = 0  # +1 per successful (re)connect
        self.adapter_locked = True  # until get_config confirms °C, after each (re)connect too
        self.temperature_unit: str | None = None
        self._connected = False
        self._status: ConnectionStatus | None = None
        self._states: dict[str, HAState] = {}
        self._registry = Registry({}, {}, {})
        self._registry_version = 0
        self._skew: float | None = None
        self._ws: ClientConnection | None = None
        self._next_id = 0
        self._pending: dict[int, tuple[str, asyncio.Future[dict[str, Any] | None]]] = {}
        self._registry_dirty = asyncio.Event()
        self._config_dirty = asyncio.Event()
        self._first_attempt = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        """False as soon as the socket is gone, before the close handshake and the status."""
        return self._connected and self._ws is not None

    @property
    def registry_version(self) -> int:
        """+1 per applied registry load, so that callers can tell a newer registry."""
        return self._registry_version

    def state(self, entity_id: str) -> HAState | None:
        return self._states.get(entity_id)

    def all_states(self) -> dict[str, HAState]:
        return dict(self._states)

    def registry(self) -> Registry:
        return self._registry

    def last_event_skew_seconds(self) -> float | None:
        """``time_fired`` of the last event minus ``clock()``; diagnostics only (E26)."""
        return self._skew

    async def start(self) -> None:
        """Start connecting; waits for the first attempt but never fails when Core does."""
        if self._runner is None:
            self._first_attempt.clear()
            self._runner = asyncio.create_task(self._run(), name="ha-client")
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(START_WAIT):
                await self._first_attempt.wait()

    async def stop(self) -> None:
        """Cancel every task and close the socket; the snapshot stays readable."""
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> ServiceResult:
        """Call ``domain.service`` on one entity; ``HAServiceError`` when Core refuses."""
        if not self._connected:
            raise HAUnavailable("not connected to Home Assistant")
        message = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": data,
            "target": {"entity_id": entity_id},
        }
        reply = await self._command(message, SERVICE_TIMEOUT)
        if reply.get("success") is not True:
            raise HAServiceError(*_error(reply))
        result = reply.get("result")
        context = result.get("context") if isinstance(result, dict) else None
        return ServiceResult(context_id(context))

    async def auth_list(self) -> list[HAUser]:
        """``config/auth/list``, never cached.

        Not connected, connection lost or no answer: ``HAUnavailable``. A failed command or a
        user without the fields the role check needs: ``HAProtocolError``.
        """
        if not self._connected:
            raise HAUnavailable("not connected to Home Assistant")
        users = await self._result({"type": "config/auth/list"})
        try:
            return [parse_user(user) for user in users]
        except MALFORMED as exc:
            raise HAProtocolError(f"config/auth/list: {exc!r}") from exc

    async def _run(self) -> None:
        """Connect, serve until the connection ends, wait, repeat; ends only by ``stop``."""
        delays = _backoff()
        try:
            while True:
                try:
                    reason = await self._connection()
                except _AuthInvalid:
                    logger.warning("Home Assistant rejected the access token")
                    reason = "auth_invalid"
                except (OSError, WebSocketException, HAError) as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                except Exception as exc:  # a bug must not end the reconnect loop
                    logger.exception("Home Assistant connection failed")
                    reason = f"{type(exc).__name__}: {exc}"
                if self._connected:
                    delays = _backoff()  # a connection that worked starts again at 1 s
                self.adapter_locked = True
                await self._set_status(False, reason)
                delay = next(delays)
                logger.debug("reconnecting to Home Assistant in %.1f s", delay)
                await asyncio.sleep(delay)
        finally:
            self._connected, self.adapter_locked = False, True
            self._first_attempt.set()

    async def _connection(self) -> str:
        """One connection from handshake to close; returns why it closed."""
        ws = await connect(
            self._ws_url,
            max_size=MAX_MESSAGE_BYTES,
            open_timeout=10,
            ping_interval=None,  # one keepalive: Core's own ping in _keepalive
            close_timeout=2,  # stop() stays within the Supervisor's 10 s stop budget
            proxy=None,  # the token goes straight to Core, never through a proxy from the env
            logger=_ws_logger,
        )
        tasks: list[asyncio.Task[None]] = []
        try:
            await self._authenticate(ws)
            self._ws = ws
            reader = asyncio.create_task(self._read(ws), name="ha-client-reader")
            tasks.append(reader)
            await self._load()
            keepalive = asyncio.create_task(self._keepalive(), name="ha-client-keepalive")
            tasks += [
                keepalive,
                asyncio.create_task(self._refresh_registry(), name="ha-client-registry"),
                asyncio.create_task(self._refresh_config(), name="ha-client-config"),
            ]
            self.generation += 1
            await self._set_status(True, None)
            await asyncio.wait([reader, keepalive], return_when=asyncio.FIRST_COMPLETED)
            if not reader.done():
                return "ping_timeout"
            await reader  # raises how the connection broke, unless it closed normally
            return f"connection closed ({ws.close_code})"
        finally:
            self._ws = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ws.close()  # 1000, also on stop(): the context manager would send 1011

    async def _authenticate(self, ws: ClientConnection) -> None:
        async with asyncio.timeout(COMMAND_TIMEOUT):
            if _type(await ws.recv()) != "auth_required":
                raise HAProtocolError("expected auth_required")
            await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            answer = _type(await ws.recv())
        if answer == "auth_invalid":
            raise _AuthInvalid("auth_invalid")
        if answer != "auth_ok":
            raise HAProtocolError(f"unexpected answer to auth: {answer!r}")

    async def _load(self) -> None:
        """Subscribe first, then load: every event read after an answer is newer than it."""
        self.adapter_locked = True
        for event_type in EVENTS:
            await self._result({"type": "subscribe_events", "event_type": event_type})
        await self._result({"type": "get_states"})  # the reader applies the answer
        await self._load_registry()
        await self._load_config()

    async def _load_registry(self) -> None:
        self._registry_dirty.clear()  # changes from now on trigger another reload
        entities = await self._result({"type": "config/entity_registry/list"})
        devices = await self._result({"type": "config/device_registry/list"})
        areas = await self._result({"type": "config/area_registry/list"})
        try:
            self._registry = parse_registry(entities, devices, areas)
        except MALFORMED as exc:
            raise HAProtocolError(f"registry: {exc!r}") from exc
        self._registry_version += 1

    async def _load_config(self) -> None:
        """Only °C unlocks the write adapters."""
        self._config_dirty.clear()  # changes from now on trigger another reload
        config = await self._result({"type": "get_config"})
        try:
            unit = parse_temperature_unit(config)
        except MALFORMED as exc:
            raise HAProtocolError(f"get_config: no temperature unit: {exc!r}") from exc
        self.temperature_unit = unit
        # A change announced while this answer was underway keeps the lock until it is re-read.
        self.adapter_locked = unit != CELSIUS or self._config_dirty.is_set()
        if unit != CELSIUS:
            logger.warning("Home Assistant uses %s instead of °C: device writes stay locked", unit)

    async def _keepalive(self) -> None:
        """Ping Core; ends with HAUnavailable when a pong does not come in time."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await self._command({"type": "ping"}, PING_TIMEOUT)

    async def _refresh_registry(self) -> None:
        """Reload after registry events (a burst reloads once) and periodically."""
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._registry_refresh):
                    await self._registry_dirty.wait()
                await asyncio.sleep(self._registry_debounce)
            try:
                await self._load_registry()
            except HAError as exc:
                logger.warning("reloading the Home Assistant registries failed: %s", exc)

    async def _refresh_config(self) -> None:
        """Read get_config again after core_config_updated; adapters stay locked until then."""
        while True:
            await self._config_dirty.wait()
            try:
                await self._load_config()
            except HAError as exc:
                logger.warning("reloading the Home Assistant config failed: %s", exc)

    async def _set_status(self, connected: bool, reason: str | None) -> None:
        """Record the status; report and log it only when it changed."""
        self._connected = connected
        status = ConnectionStatus(connected, self.generation, reason)
        if status != self._status:
            self._status = status
            if connected:
                logger.info("connected to Home Assistant (generation %d)", self.generation)
            else:
                logger.info("not connected to Home Assistant: %s", reason)
            await self._notify(self._on_status, status, "connection status")
        self._first_attempt.set()  # start() returns once the first status is reported

    async def _notify[T](self, handler: Callable[[T], Awaitable[None]], arg: T, what: str) -> None:
        """Call a handler; whatever it raises is logged, never passed on to the client's tasks."""
        try:
            await handler(arg)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # stop() or the end of the connection
            logger.error("%s handler was cancelled", what)
        except Exception:
            logger.exception("%s handler failed", what)

    async def _command(self, message: dict[str, Any], seconds: float) -> dict[str, Any]:
        """Send ``message`` with the next id; Core's answer, or HAUnavailable after ``seconds``."""
        ws = self._ws
        if ws is None:
            raise HAUnavailable("not connected to Home Assistant")
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = (message["type"], future)
        try:
            async with asyncio.timeout(seconds):  # a full send buffer counts as no answer
                await ws.send(json.dumps({"id": msg_id, **message}))
                reply = await future
        except ConnectionClosed as exc:
            raise HAUnavailable("connection to Home Assistant lost") from exc
        except TimeoutError as exc:
            raise HAUnavailable(f"{message['type']}: no answer within {seconds:g} s") from exc
        finally:
            del self._pending[msg_id]
        if reply is None:
            raise HAUnavailable("connection to Home Assistant lost")
        return reply

    async def _result(self, message: dict[str, Any]) -> Any:
        """The ``result`` of a command that must succeed."""
        reply = await self._command(message, COMMAND_TIMEOUT)
        if reply.get("success") is not True:
            code, text = _error(reply)
            raise HAProtocolError(f"{message['type']} failed: {code}: {text}")
        return reply.get("result")

    async def _read(self, ws: ClientConnection) -> None:
        """Handle every message in order; when the connection ends, release waiting commands."""
        try:
            async for raw in ws:
                await self._dispatch(raw)
        finally:
            for _, future in self._pending.values():
                if not future.done():
                    future.set_result(None)

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
            if message["type"] in ("result", "pong"):
                self._resolve(message)
                return
            if message["type"] != "event":
                return
            event = message["event"]
            self._skew = (parse_time(event["time_fired"]) - self._clock()).total_seconds()
            if event["event_type"] == "core_config_updated":
                self.adapter_locked = True  # the unit may have changed: check before writes
                self._config_dirty.set()
                return
            if event["event_type"] in REGISTRY_EVENTS:
                self._registry_dirty.set()
                return
            if event["event_type"] != "state_changed":
                return
            change = parse_state_changed(event)
        except MALFORMED as exc:
            logger.warning("ignoring a malformed message from Home Assistant: %r", exc)
            return
        if change.new_state is None:
            self._states.pop(change.entity_id, None)
        else:
            self._states[change.entity_id] = change.new_state
        await self._notify(self._on_event, change, "state_changed")

    def _resolve(self, message: dict[str, Any]) -> None:
        pending = self._pending.get(message["id"])
        if pending is None:
            return  # answered after its timeout
        command, future = pending
        if command == "get_states" and message.get("success") is True:
            # Here and not in _load: events read after this answer must apply on top of it.
            try:
                self._states = parse_states(message.get("result"))
            except MALFORMED as exc:  # _load fails at once instead of waiting for a timeout
                error = {"code": "malformed", "message": repr(exc)}
                message = {"id": message["id"], "type": "result", "success": False, "error": error}
        if not future.done():
            future.set_result(message)
