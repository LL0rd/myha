"""A14-A16 at the loader: Home Assistant changes under a binding mark it, never widen it.

Every change comes from the simulator over the real WebSocket connection: state_changed for
capabilities, registry events for area, unique_id, existence and renames.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db import models as db
from myha_gateway.db import write_session
from myha_gateway.deps import AppContext
from myha_gateway.policy.codes import DecisionCode as Code
from myha_gateway.policy.decide import Decision, decide
from tests.helpers import SimHandle
from tests.policy_world import GUEST_SESSION, NOW, bindings, policy_version, world

Audited = tuple[str, str | None, int, dict[str, Any]]


class Recorder:
    """Audit stand-in: what the loader audits, in order."""

    def __init__(self) -> None:
        self.events: list[Audited] = []

    async def __call__(
        self,
        session: AsyncSession,
        operation: str,
        resource_id: str | None,
        version: int,
        params: dict[str, Any],
    ) -> None:
        assert session.in_transaction()  # the change and its audit commit together
        self.events.append((operation, resource_id, version, params))


@pytest.fixture
async def ctx(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    async with world(sim, tmp_path) as ctx:
        ctx.policy.audit = Recorder()
        await ctx.policy.index(NOW)  # the loader knows the bindings from here on
        yield ctx


async def eventually(check: Callable[[], Awaitable[bool]], seconds: float) -> None:
    async with asyncio.timeout(seconds):
        while not await check():  # noqa: ASYNC110 (polls the database)
            await asyncio.sleep(0.02)


def status_is(ctx: AppContext, resource_id: str, status: str) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return (await bindings(ctx.engine))[resource_id].status == status

    return check


async def guest_decides(ctx: AppContext, resource_id: str, action: str) -> Decision:
    found = await ctx.policy.context_for_session(GUEST_SESSION, NOW)
    assert found is not None
    guest, index = found
    view = ctx.policy.state_view(resource_id)
    return decide(guest, index, resource_id, action, {}, NOW, view, execute=True)


def audited(ctx: AppContext) -> list[Audited]:
    recorder = ctx.policy.audit
    assert isinstance(recorder, Recorder)
    return recorder.events


async def registry_reloaded(ctx: AppContext, before: int) -> None:
    async def check() -> bool:
        return ctx.ha.registry_version > before

    await eventually(check, 3)


async def test_a14_new_entity_in_area_not_granted(ctx: AppContext, sim: SimHandle) -> None:
    before_index, version = await ctx.policy.index(NOW), await policy_version(ctx.engine)
    registry = ctx.ha.registry_version
    new = {"entity_id": "light.room_203_new", "area_id": "area_203", "platform": "shelly"}
    new |= {"state": "on", "attributes": {"supported_color_modes": ["onoff"]}}
    await sim.control.post("entity", new)
    await registry_reloaded(ctx, registry)
    await asyncio.sleep(0.6)  # the loader's registry poll; nothing may follow from it
    index = await ctx.policy.index(NOW)
    assert set(index.resources) == set(before_index.resources)
    assert index.units["unit_203"] == before_index.units["unit_203"]
    for resource in ("light.room_203_new", "res_203_new"):
        decision = await guest_decides(ctx, resource, "light.on")
        assert decision.public_code == "NOT_FOUND"
    assert {row.status for row in (await bindings(ctx.engine)).values()} == {
        "confirmed",
        "blocked",
    }
    assert await policy_version(ctx.engine) == version and audited(ctx) == []


async def test_a16_capability_change_marks_needs_review(ctx: AppContext, sim: SimHandle) -> None:
    version = await policy_version(ctx.engine)
    assert (await guest_decides(ctx, "res_203_blind", "cover.open")).code is Code.GRANTED
    change = {"entity_id": "cover.room_203_blind", "attributes": {"supported_features": 3}}
    await sim.control.post("entity/attributes", change)  # reaches the loader as state_changed
    await eventually(status_is(ctx, "res_203_blind", "needs_review"), 1)

    write = await guest_decides(ctx, "res_203_blind", "cover.open")
    assert (write.code, write.public_code) == (Code.RESOURCE_NEEDS_REVIEW, "UPSTREAM_UNAVAILABLE")
    assert (await guest_decides(ctx, "res_203_blind", "state.read")).code is Code.GRANTED
    assert await policy_version(ctx.engine) == version + 1
    assert audited(ctx) == [
        ("binding.needs_review", "res_203_blind", version + 1, {"reason": "capabilities"}),
        ("policy.changed", None, version + 1, {}),
    ]


async def test_a16_area_change_marks_needs_review(ctx: AppContext, sim: SimHandle) -> None:
    """The floor lamp has no area of its own: moving its device moves it (Core semantics)."""
    registry = ctx.ha.registry_version
    await sim.control.post(
        "registry/area", {"entity_id": "switch.living_floor_lamp", "area_id": "area_kitchen"}
    )
    await registry_reloaded(ctx, registry)
    await eventually(status_is(ctx, "res_lr_lamp", "needs_review"), 2)
    assert ("binding.needs_review", "res_lr_lamp") in [event[:2] for event in audited(ctx)]
    assert audited(ctx)[0][3] == {"reason": "area"}


async def test_a15_replaced_device_marks_needs_review(ctx: AppContext, sim: SimHandle) -> None:
    registry = ctx.ha.registry_version
    replace = {"entity_id": "light.room_203_ceiling", "new_unique_id": "govee-new"}
    await sim.control.post("registry/replace", replace)
    await registry_reloaded(ctx, registry)
    await eventually(status_is(ctx, "res_203_light", "needs_review"), 2)
    assert audited(ctx)[0][:2] == ("binding.needs_review", "res_203_light")
    assert audited(ctx)[0][3] == {"reason": "unique_id"}
    write = await guest_decides(ctx, "res_203_light", "light.on")
    assert write.code is Code.RESOURCE_NEEDS_REVIEW


async def test_rename_keeps_binding(ctx: AppContext, sim: SimHandle) -> None:
    version = await policy_version(ctx.engine)
    rename = {"entity_id": "light.room_203_ceiling", "new_entity_id": "light.room_203_main"}
    await sim.control.post("registry/rename", rename)

    async def renamed() -> bool:
        return (await bindings(ctx.engine))["res_203_light"].entity_id == "light.room_203_main"

    await eventually(renamed, 3)
    row = (await bindings(ctx.engine))["res_203_light"]
    assert row.status == "confirmed"
    assert await policy_version(ctx.engine) == version + 1
    old, new = "light.room_203_ceiling", "light.room_203_main"
    assert audited(ctx) == [
        ("binding.renamed", "res_203_light", version + 1, {"from": old, "to": new}),
        ("policy.changed", None, version + 1, {}),
    ]
    assert (await guest_decides(ctx, "res_203_light", "light.on")).code is Code.GRANTED
    view = ctx.policy.state_view("res_203_light")
    assert view is not None and view.power == "on"  # read from the new entity id


async def test_removed_entity_marks_needs_review(ctx: AppContext, sim: SimHandle) -> None:
    registry = ctx.ha.registry_version
    await sim.control.post("entity/remove", {"entity_id": "light.room_203_bedside"})
    await registry_reloaded(ctx, registry)
    await eventually(status_is(ctx, "res_203_bedside", "needs_review"), 2)
    assert audited(ctx)[0][3] == {"reason": "missing"}


async def test_blocked_never_becomes_needs_review(ctx: AppContext, sim: SimHandle) -> None:
    """needs_review would allow reads of an entity that must stay hidden (A15)."""
    change = {"entity_id": "light.group_all", "attributes": {"supported_color_modes": ["rgb"]}}
    await sim.control.post("entity/attributes", change)
    registry = ctx.ha.registry_version
    await sim.control.post("registry/area", {"entity_id": "light.group_all", "area_id": "area_203"})
    await registry_reloaded(ctx, registry)
    await asyncio.sleep(0.6)
    assert (await bindings(ctx.engine))["res_203_group"].status == "blocked"
    assert audited(ctx) == []


async def test_offline_states_are_no_drift(ctx: AppContext, sim: SimHandle) -> None:
    """An unavailable device reports fewer attributes; that is no change of its abilities."""
    await sim.control.post("offline/climate.room_203_trv")
    await sim.control.post("offline/light.room_203_ceiling")
    await asyncio.sleep(0.6)
    ctx.policy.invalidate()
    index = await ctx.policy.index(NOW)  # a build checks too
    for resource in ("res_203_trv", "res_203_light"):
        ref = index.resource(resource)
        assert ref is not None and ref.status == "confirmed"
    assert audited(ctx) == []


async def test_drift_while_disconnected_is_found_on_reconnect(
    ctx: AppContext, sim: SimHandle
) -> None:
    """Core sends no events for what changed while the connection was down."""
    await ctx.ha.stop()
    change = {"entity_id": "cover.room_203_blind", "attributes": {"supported_features": 3}}
    await sim.control.post("entity/attributes", change)
    await ctx.ha.start()
    await eventually(status_is(ctx, "res_203_blind", "needs_review"), 2)


async def test_binding_without_registry_entry_needs_its_state(
    ctx: AppContext, sim: SimHandle
) -> None:
    """An entity bound without a unique_id has no registry entry to follow: only its state."""
    async with write_session(ctx.engine) as session:
        await session.execute(
            update(db.ResourceBinding)
            .where(db.ResourceBinding.id == "res_203_hum")
            .values(unique_id=None)
        )
    ctx.policy.invalidate()
    ref = (await ctx.policy.index(NOW)).resource("res_203_hum")
    assert ref is not None and ref.status == "confirmed"
    await sim.control.post("entity/remove", {"entity_id": "sensor.room_203_humidity"})
    await eventually(status_is(ctx, "res_203_hum", "needs_review"), 2)
    assert audited(ctx)[0] == (
        "binding.needs_review",
        "res_203_hum",
        audited(ctx)[0][2],
        {"reason": "missing"},
    )


async def test_failed_check_fails_closed_and_is_retried(
    ctx: AppContext, sim: SimHandle, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = ctx.policy.audit
    assert isinstance(recorder, Recorder)
    calls = 0

    async def fails_once(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("audit down")
        await recorder(*args)

    ctx.policy.audit = fails_once
    change = {"entity_id": "cover.room_203_blind", "attributes": {"supported_features": 3}}
    await sim.control.post("entity/attributes", change)

    async def logged() -> bool:
        return any("drift failed" in record.getMessage() for record in caplog.records)

    await eventually(logged, 2)
    assert (await bindings(ctx.engine))["res_203_blind"].status == "confirmed"  # rolled back
    ref = (await ctx.policy.index(NOW)).resource("res_203_blind")
    assert ref is not None and ref.status == "needs_review"  # the index marks it meanwhile
    await eventually(status_is(ctx, "res_203_blind", "needs_review"), 2)  # and asks again
    assert [event[0] for event in recorder.events] == ["binding.needs_review", "policy.changed"]
