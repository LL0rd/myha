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

import websockets
from websockets.asyncio.client import ClientConnection

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

MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # get_states of a large installation; check in S0
COMMAND_TIMEOUT = 30.0
SERVICE_TIMEOUT = 15.0
START_WAIT = 10.0  # start() waits at most this long for the first attempt
BACKOFF_MAX = 30.0
REGISTRY_DEBOUNCE = 0.5
REGISTRY_REFRESH = 300.0
CELSIUS = "°C"
REGISTRY_EVENTS = ("entity_registry_updated", "device_registry_updated", "area_registry_updated")


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
        yield delay * random.uniform(0.8, 1.2)  # noqa: S311 (jitter, not a secret)
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
    ) -> None:
        self._ws_url, self._token = ws_url, token
        self._on_event, self._on_status, self._clock = on_event, on_status, clock
        self.generation = 0  # +1 per successful (re)connect
        self.adapter_locked = True  # until get_config confirms °C after each (re)connect
        self.temperature_unit: str | None = None
        self._connected = False
        self._status: ConnectionStatus | None = None
        self._states: dict[str, HAState] = {}
        self._registry = Registry({}, {}, {})
        self._skew: float | None = None
        self._ws: ClientConnection | None = None
        self._next_id = 0
        self._pending: dict[int, tuple[str, asyncio.Future[dict[str, Any] | None]]] = {}
        self._registry_dirty = asyncio.Event()
        self._first_attempt = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

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
            self._runner = asyncio.create_task(self._run(), name="ha-client")
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(START_WAIT):
                await self._first_attempt.wait()

    async def stop(self) -> None:
        """Cancel every task and close the socket; the snapshot stays readable."""
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner

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
        """``config/auth/list``, never cached; any failure or missing field: HAProtocolError."""
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
                except (OSError, websockets.WebSocketException, HAError) as exc:
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
        async with websockets.connect(
            self._ws_url,
            max_size=MAX_MESSAGE_BYTES,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            proxy=None,  # the token goes straight to Core, never through a proxy from the env
        ) as ws:
            await self._authenticate(ws)
            self._ws = ws
            tasks = [asyncio.create_task(self._read(ws), name="ha-client-reader")]
            try:
                await self._load()
                refresh = asyncio.create_task(self._refresh_registry(), name="ha-client-registry")
                tasks.append(refresh)
                self.generation += 1
                await self._set_status(True, None)
                await tasks[0]  # until the connection closes
            finally:
                self._ws = None
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        return f"connection closed ({ws.close_code})"

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
        for event_type in ("state_changed", *REGISTRY_EVENTS):
            await self._result({"type": "subscribe_events", "event_type": event_type})
        if not isinstance(await self._result({"type": "get_states"}), list):
            raise HAProtocolError("get_states: result is not a list")
        await self._load_registry()
        config = await self._result({"type": "get_config"})
        try:
            self.temperature_unit = parse_temperature_unit(config)
        except MALFORMED as exc:
            raise HAProtocolError(f"get_config: no temperature unit: {exc!r}") from exc
        self.adapter_locked = self.temperature_unit != CELSIUS
        if self.adapter_locked:
            unit = self.temperature_unit
            logger.warning("Home Assistant uses %s instead of °C: device writes stay locked", unit)

    async def _load_registry(self) -> None:
        self._registry_dirty.clear()  # changes from now on trigger another reload
        entities = await self._result({"type": "config/entity_registry/list"})
        devices = await self._result({"type": "config/device_registry/list"})
        areas = await self._result({"type": "config/area_registry/list"})
        try:
            self._registry = parse_registry(entities, devices, areas)
        except MALFORMED as exc:
            raise HAProtocolError(f"registry: {exc!r}") from exc

    async def _refresh_registry(self) -> None:
        """Reload 0.5 s after registry events (a burst reloads once) and every 5 minutes."""
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(REGISTRY_REFRESH):
                    await self._registry_dirty.wait()
                await asyncio.sleep(REGISTRY_DEBOUNCE)
            try:
                await self._load_registry()
            except HAError as exc:
                logger.warning("reloading the Home Assistant registries failed: %s", exc)

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
            try:
                await self._on_status(status)
            except Exception:
                logger.exception("connection status handler failed")
        self._first_attempt.set()  # start() returns once the first status is reported

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
            await ws.send(json.dumps({"id": msg_id, **message}))
            async with asyncio.timeout(seconds):
                reply = await future
        except websockets.ConnectionClosed as exc:
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
            if message["type"] == "result":
                self._resolve(message)
                return
            if message["type"] != "event":
                return
            event = message["event"]
            self._skew = (parse_time(event["time_fired"]) - self._clock()).total_seconds()
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
        try:
            await self._on_event(change)
        except Exception:
            logger.exception("state_changed handler failed")

    def _resolve(self, message: dict[str, Any]) -> None:
        pending = self._pending.get(message["id"])
        if pending is None:
            return  # answered after its timeout
        command, future = pending
        result = message.get("result")
        if command == "get_states" and message.get("success") is True and isinstance(result, list):
            # Here and not in _load: events read after this answer must apply on top of it.
            self._states = parse_states(result)
        if not future.done():
            future.set_result(message)
