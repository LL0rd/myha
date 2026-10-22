"""One client's live events (design §4.3): first-frame auth, snapshot, filtered updates, close.

Independent of the ASGI layer: ``Transport`` is the part of Starlette's ``WebSocket`` used here,
so the state machine runs against a fake in tests. Every event is projected with the
connection's own context right before it is sent; entity ids never leave this module.
Protocol pings (20 s) and the inbound frame limit of the server come with the endpoint; the
frame check here covers transports without one.
"""

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, Protocol

from myha_gateway.deps import AppContext
from myha_gateway.events.bus import (
    CommandUpdated,
    ConnectionStatus,
    Event,
    Overflow,
    PolicyChanged,
    SessionRevoked,
    StateChanged,
)
from myha_gateway.ha.client import utcnow
from myha_gateway.policy.context import EffectiveContext
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.projection.dashboard import (
    Card,
    dashboard_status,
    load_layout,
    project_dashboard,
    project_resource,
)

logger = logging.getLogger(__name__)

CLOSE_UNAUTHENTICATED = 4401
CLOSE_TOO_BIG = 1009
CLOSE_TRY_AGAIN = 1013  # fell behind: the client reconnects to a fresh snapshot
AUTH_SECONDS = 5.0
RECHECK_SECONDS = 30.0  # revocations without a bus event, such as by another process
MAX_FRAME = 64 * 1024

# Checks and uses up a WS ticket: the id of its session, or ``None``.
Authenticate = Callable[[str], Awaitable[str | None]]


class Transport(Protocol):
    async def receive_text(self) -> str: ...

    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class _Closed(Exception):
    """The connection closed itself; nothing more to send."""


class ClientConnection:
    def __init__(self, app: AppContext, transport: Transport, authenticate: Authenticate) -> None:
        self._app, self._transport, self._authenticate = app, transport, authenticate
        self._session_id = ""
        self._ctx: EffectiveContext
        self._index: PolicyIndex
        self._cards: dict[str, tuple[str, Card]] = {}  # resource id -> room id and card
        self._shown: set[str] = set()  # resource ids the client has
        self._status = ""
        self._sequence = 0

    async def run(self) -> None:
        """Serve until the client leaves, the session ends or the connection falls behind."""
        try:
            self._session_id = await self._auth()
            queue = self._app.bus.subscribe()  # before the snapshot: no event slips between
            try:
                await self._load()
                await self._snapshot()
                await self._until_first(self._read(), self._serve(queue))
            finally:
                self._app.bus.unsubscribe(queue)
        except _Closed:
            pass

    @staticmethod
    async def _until_first(*work: Awaitable[None]) -> None:
        tasks = [asyncio.ensure_future(item) for item in work]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()  # _Closed or a real error

    async def _auth(self) -> str:
        """The first frame within ``AUTH_SECONDS``: ``{"type": "auth", "ticket": ...}``."""
        ticket = None
        try:
            async with asyncio.timeout(AUTH_SECONDS):
                text = await self._transport.receive_text()
            message = json.loads(text) if len(text.encode()) <= MAX_FRAME else None
            if isinstance(message, dict) and message.get("type") == "auth":
                ticket = message.get("ticket")
        except (TimeoutError, KeyError, ValueError):  # KeyError: a binary frame (Starlette)
            pass
        session_id = await self._authenticate(ticket) if isinstance(ticket, str) else None
        if session_id is None:
            await self._transport.close(CLOSE_UNAUTHENTICATED)
            raise _Closed
        return session_id

    async def _load(self) -> None:
        """Context and index anew; a revoked or ended session closes the connection."""
        now = utcnow()
        try:
            found = await self._app.policy.context_for_session(self._session_id, now)
        except ValueError:  # a session without a live view, such as a preview
            found = None
        if found is None or not found[0].session_active:
            await self._end("revoked")
        if dashboard_status(found[0], now) == "expired":
            await self._end("expired")
        self._ctx, self._index = found

    async def _snapshot(self) -> None:
        async with self._app.sessions() as session:
            layout = await load_layout(session)
        policy = self._app.policy
        view = project_dashboard(self._ctx, self._index, policy.state_view, layout, utcnow())
        self._cards = {card.resource_id: (unit.id, card) for unit in layout for card in unit.cards}
        self._shown = {resource.id for room in view.rooms for resource in room.resources}
        self._status, self._sequence = view.status, 0
        await self._send(
            {
                "type": "snapshot",
                "generation": self._app.bus.generation,
                "sequence": 0,
                "policy_version": self._index.policy_version,
                "dashboard": view.model_dump(mode="json", exclude_unset=True),
            }
        )
        # After every snapshot: a client that (re)connects learns whether controls work now.
        status = "online" if self._app.bus.connected else "offline"
        await self._send({"type": "connection.status", "status": status})

    async def _serve(self, queue: asyncio.Queue[Event]) -> None:
        loop = asyncio.get_running_loop()
        recheck = loop.time() + RECHECK_SECONDS
        while True:
            try:
                async with asyncio.timeout_at(min(recheck, self._window_edge(loop.time()))):
                    event = await queue.get()
            except TimeoutError:
                await self._refresh()
                recheck = loop.time() + RECHECK_SECONDS
                continue
            await self._handle(event)

    def _window_edge(self, loop_now: float) -> float:
        """Loop time of the next start or end of the session window, so it closes on time."""
        now = utcnow()
        edges = [t for t in (self._ctx.not_before, self._ctx.not_after) if t and t > now]
        return loop_now + min((t - now).total_seconds() for t in edges) if edges else math.inf

    async def _refresh(self) -> None:
        """What no event announced: a revocation, a newer policy, a window opening or ending."""
        version, status = self._index.policy_version, self._status
        await self._load()
        if self._index.policy_version != version:
            changed = {"type": "policy.changed", "policy_version": self._index.policy_version}
            await self._send(changed)
            await self._snapshot()
        elif dashboard_status(self._ctx, utcnow()) != status:
            await self._snapshot()

    async def _handle(self, event: Event) -> None:
        match event:
            case StateChanged(entity_id=entity_id):
                await self._resource(entity_id)
            case PolicyChanged():
                await self._refresh()
            case SessionRevoked(reason=reason, session_id=session_id, stay_id=stay_id):
                if session_id == self._session_id or (
                    stay_id is not None and stay_id == self._ctx.stay_id
                ):
                    await self._end(reason)
            case ConnectionStatus(status="online"):  # Core sends no events for the gap
                await self._load()
                await self._snapshot()
            case ConnectionStatus(status=status):
                await self._send({"type": "connection.status", "status": status})
            case CommandUpdated(session_id=session_id, command=command):
                if session_id == self._session_id:
                    await self._send({"type": "command.updated", "command": dict(command)})
            case Overflow():
                await self._transport.close(CLOSE_TRY_AGAIN)
                raise _Closed

    async def _resource(self, entity_id: str) -> None:
        """``resource.updated`` while the context sees the resource, then ``resource.removed``."""
        policy = self._app.policy
        resource_id = policy.resource_for_entity(entity_id)
        placed = None if resource_id is None else self._cards.get(resource_id)
        if resource_id is None or placed is None:
            return
        room_id, card = placed
        view = policy.state_view(resource_id)
        resource = project_resource(self._ctx, self._index, view, card, utcnow())
        message: dict[str, Any]
        if resource is not None:
            self._shown.add(resource_id)
            body = resource.model_dump(mode="json", exclude_unset=True)
            message = {"type": "resource.updated", "room_id": room_id, "resource": body}
        elif resource_id in self._shown:
            self._shown.discard(resource_id)
            message = {"type": "resource.removed", "resource_id": resource_id}
        else:
            return
        self._sequence += 1
        await self._send({**message, "sequence": self._sequence})

    async def _read(self) -> None:
        """Clients send nothing after auth; a frame over ``MAX_FRAME`` closes with 1009."""
        try:
            while True:
                if len((await self._transport.receive_text()).encode()) > MAX_FRAME:
                    await self._transport.close(CLOSE_TOO_BIG)
                    return
        except Exception:  # the client is gone, however the transport says it
            logger.debug("event connection closed by the client")

    async def _end(self, reason: str) -> NoReturn:
        await self._send({"type": "session.revoked", "reason": reason})
        await self._transport.close(CLOSE_UNAUTHENTICATED)
        raise _Closed

    async def _send(self, message: dict[str, Any]) -> None:
        await self._transport.send_text(json.dumps(message, separators=(",", ":")))
