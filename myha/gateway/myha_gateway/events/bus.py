"""In-process events for the open client connections (design §3, §4.3).

``publish`` never blocks and never awaits: the HA callbacks run in the HA client's reader and
runner tasks, which must not stall (websockets stops reading after 16 unread messages, and
then pongs and ``call_service`` answers stay out). Each connection has its own bounded queue.
One that falls ``QUEUE_SIZE`` events behind loses them all for an ``Overflow`` marker and is
closed with 1013, so its client reconnects to a fresh snapshot: dropping only the oldest
events would lose a revocation or an update without anyone noticing.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from myha_gateway.ha import messages as ha

QUEUE_SIZE = 256


@dataclass(frozen=True)
class StateChanged:
    entity_id: str  # stays in the gateway: connections map it to a bound resource


@dataclass(frozen=True)
class PolicyChanged:
    policy_version: int | None = None  # None: connections read it from the database anyway


@dataclass(frozen=True)
class SessionRevoked:
    reason: str  # sent to the client, such as "revoked", "checkout" or "expired"
    session_id: str | None = None
    stay_id: str | None = None  # every session of the stay


@dataclass(frozen=True)
class ConnectionStatus:
    status: Literal["online", "offline"]  # of the Home Assistant connection
    generation: int


@dataclass(frozen=True)
class CommandUpdated:
    session_id: str
    command: Mapping[str, Any]  # the public fields of the wire contract's command


@dataclass(frozen=True)
class Overflow:
    """Takes the place of the events a connection fell too far behind on."""


Event = StateChanged | PolicyChanged | SessionRevoked | ConnectionStatus | CommandUpdated | Overflow


class EventBus:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[Event]] = set()
        self.connected = False
        self.generation = 0  # of the HA connection whose snapshot the connections project

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._queues.discard(queue)

    def publish(self, event: Event) -> None:
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(Overflow())

    async def on_event(self, change: ha.StateChanged) -> None:
        """Ignored while disconnected: ``_load`` sends events before ``connected=True``."""
        if self.connected:
            self.publish(StateChanged(change.entity_id))

    async def on_status(self, status: ha.ConnectionStatus) -> None:
        """Connections answer ``online`` with a fresh snapshot: Core sends nothing for the gap."""
        self.connected, self.generation = status.connected, status.generation
        state: Literal["online", "offline"] = "online" if status.connected else "offline"
        self.publish(ConnectionStatus(state, status.generation))
