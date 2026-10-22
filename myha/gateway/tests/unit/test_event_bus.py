"""The event bus never blocks a publisher and turns a full queue into one overflow marker."""

import asyncio
from datetime import UTC, datetime

from myha_gateway.events import bus as events
from myha_gateway.events.bus import (
    ConnectionStatus,
    EventBus,
    Overflow,
    PolicyChanged,
    StateChanged,
)
from myha_gateway.ha import messages as ha


def drain(queue: asyncio.Queue[events.Event]) -> list[events.Event]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def change(entity_id: str) -> ha.StateChanged:
    return ha.StateChanged(entity_id, None, None, datetime.now(UTC), None)


async def test_publish_reaches_every_subscriber_until_it_leaves() -> None:
    bus = EventBus()
    first, second = bus.subscribe(), bus.subscribe()
    bus.publish(PolicyChanged(3))
    bus.unsubscribe(second)
    bus.publish(PolicyChanged(4))
    assert drain(first) == [PolicyChanged(3), PolicyChanged(4)]
    assert drain(second) == [PolicyChanged(3)]


async def test_a_full_queue_is_replaced_by_one_overflow_marker() -> None:
    bus = EventBus()
    slow, fast = bus.subscribe(), bus.subscribe()
    for version in range(events.QUEUE_SIZE):
        bus.publish(PolicyChanged(version))
    drain(fast)
    bus.publish(PolicyChanged(-1))
    assert drain(slow) == [Overflow()]
    assert drain(fast) == [PolicyChanged(-1)]


async def test_ha_events_count_only_while_connected() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    await bus.on_event(change("light.a"))  # during _load, before connected=True
    await bus.on_status(ha.ConnectionStatus(True, 2, None))
    await bus.on_event(change("light.b"))
    await bus.on_status(ha.ConnectionStatus(False, 2, "connection lost"))
    await bus.on_event(change("light.c"))
    assert drain(queue) == [
        ConnectionStatus("online", 2),
        StateChanged("light.b"),
        ConnectionStatus("stale", 2),
    ]
    assert (bus.connected, bus.generation) == (False, 2)
