"""PolicyLoader against a seeded database and the simulator: index, cache key, contexts."""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select, update

from myha_gateway.db import create_engine, write_session
from myha_gateway.db import models as db
from myha_gateway.deps import AppContext
from myha_gateway.policy.codes import DecisionCode as Code
from myha_gateway.policy.decide import decide
from tests.helpers import SimHandle
from tests.policy_world import (
    GUEST_SESSION,
    NOW,
    RESIDENT_SESSION,
    set_instance,
    world,
)


@pytest.fixture
async def ctx(sim: SimHandle, tmp_path: Path) -> AsyncIterator[AppContext]:
    async with world(sim, tmp_path) as ctx:
        yield ctx


async def test_index_holds_every_binding_with_its_status(ctx: AppContext) -> None:
    index = await ctx.policy.index(NOW)
    light = index.resource("res_203_light")
    assert light is not None and light.status == "confirmed" and light.kind == "light"
    assert light.capabilities.actions  # stored capabilities, not a fresh guess
    group = index.resource("res_203_group")
    assert group is not None and group.status == "blocked"
    assert index.units["unit_empty"] == frozenset()
    assert index.units["unit_203"] >= {"res_203_light", "res_203_group"}
    assert set(index.role_versions) == {("room_guest", 1)}


async def test_blocked_binding_is_never_offered(ctx: AppContext) -> None:
    found = await ctx.policy.context_for_session(GUEST_SESSION, NOW)
    assert found is not None
    guest, index = found
    for action in ("metadata.read", "light.on"):
        view = ctx.policy.state_view("res_203_group")
        decision = decide(guest, index, "res_203_group", action, {}, NOW, view, execute=True)
        assert decision.code is Code.RESOURCE_UNCONFIRMED


async def test_same_key_same_index_new_version_new_index(ctx: AppContext) -> None:
    first = await ctx.policy.index(NOW)
    assert await ctx.policy.index(NOW) is first
    ctx.policy.invalidate()
    rebuilt = await ctx.policy.index(NOW)
    assert rebuilt is not first and rebuilt.policy_version == first.policy_version
    await set_instance(ctx.engine, policy_version=first.policy_version + 1)
    newer = await ctx.policy.index(NOW)
    assert newer.policy_version == first.policy_version + 1


async def test_another_process_changing_the_version_is_seen(
    ctx: AppContext, tmp_path: Path
) -> None:
    first = await ctx.policy.index(NOW)
    other = create_engine(ctx.settings.db_path)  # e.g. dev-seed in its own process
    try:
        async with write_session(other) as session:
            await session.execute(update(db.Instance).values(policy_version=41))
    finally:
        await other.dispose()
    index = await ctx.policy.index(NOW)
    assert index is not first and index.policy_version == 41


async def test_adapter_lock_follows_the_connection(ctx: AppContext) -> None:
    unlocked = await ctx.policy.index(NOW)
    view = ctx.policy.state_view("res_203_light")
    assert view is not None and not view.adapter_locked and view.power == "on"
    assert not ctx.policy.adapter_locked
    found = await ctx.policy.context_for_session(GUEST_SESSION, NOW)
    assert found is not None
    guest, index = found
    allowed = decide(guest, index, "res_203_light", "light.on", {}, NOW, view, execute=True)
    assert allowed.code is Code.GRANTED

    await ctx.ha.stop()  # not connected: the snapshot is stale, writes stay locked
    assert ctx.policy.adapter_locked
    locked = await ctx.policy.index(NOW)
    assert locked is not unlocked  # adapter_locked is part of the cache key
    view = ctx.policy.state_view("res_203_light")
    assert view is not None and view.adapter_locked
    denied = decide(guest, locked, "res_203_light", "light.on", {}, NOW, view, execute=True)
    assert denied.code is Code.ADAPTER_LOCKED
    assert ctx.policy.state_view("res_unknown") is None


async def test_context_and_index_share_the_version_read_with_time_trusted(
    ctx: AppContext,
) -> None:
    await set_instance(ctx.engine, policy_version=7, time_trusted=False)
    assert ctx.instance is None  # the start snapshot is never read
    found = await ctx.policy.context_for_session(GUEST_SESSION, NOW)
    assert found is not None
    guest, index = found
    assert guest.policy_version == index.policy_version == 7
    assert not guest.time_trusted and guest.stay_id == "stay_203"
    view = ctx.policy.state_view("res_203_light")
    decision = decide(guest, index, "res_203_light", "state.read", {}, NOW, view, execute=True)
    assert decision.code is Code.TIME_UNTRUSTED


async def test_resident_context_loads_group_assignments_and_own_fences(ctx: AppContext) -> None:
    parameters = {"target_c": {"min": "18", "max": "22"}}
    async with write_session(ctx.engine) as session:
        for fence_id, scope_type, scope_id in (
            ("fen_b_instance", "instance", None),
            ("fen_a_mine", "principal", "prn_res"),
            ("fen_c_other", "principal", "prn_other"),  # someone else's: never loaded
            ("fen_d_session", "session", "ses_unknown"),
        ):
            session.add(
                db.Fence(
                    id=fence_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    resource_ids=None,
                    action="climate.set_temperature",
                    parameters=parameters,
                )
            )
    found = await ctx.policy.context_for_session(RESIDENT_SESSION, NOW)
    assert found is not None
    resident, index = found
    assert [(b.role_template_id, b.unit_ids) for b in resident.role_bindings] == [
        ("room_guest", frozenset({"unit_lr"}))
    ]
    assert [(f.scope_type, f.scope_id) for f in resident.fences] == [
        ("principal", "prn_res"),  # sorted by id
        ("instance", None),
    ]
    assert index.in_scope(resident, "res_lr_ac") and not index.in_scope(resident, "res_203_trv")


async def test_unknown_session_has_no_context(ctx: AppContext) -> None:
    assert await ctx.policy.context_for_session("ses_unknown", NOW) is None


async def test_broken_rows_fail_closed_for_their_own_resource_or_role(
    ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    async with write_session(ctx.engine) as session:
        await session.execute(
            update(db.ResourceBinding)
            .where(db.ResourceBinding.id == "res_203_bedside")
            .values(confirmed_capabilities={"kind": "light"})
        )
        await session.execute(  # a kind that its capabilities contradict
            update(db.ResourceBinding)
            .where(db.ResourceBinding.id == "res_203_hum")
            .values(kind="light")
        )
        await session.execute(update(db.RoleVersion).values(grants=[{"resources": []}]))
    with caplog.at_level(logging.ERROR):
        index = await ctx.policy.index(NOW)
    assert index.resource("res_203_bedside") is None and index.resource("res_203_hum") is None
    assert index.resource("res_203_light") is not None
    assert index.role_versions == {}  # rules_for() now raises for its bindings: fail closed
    assert sum("is broken" in record.getMessage() for record in caplog.records) == 3


async def test_drift_is_audited_as_the_system(ctx: AppContext, sim: SimHandle) -> None:
    """The real audit writer of the app context, in the drift worker's transaction."""
    await ctx.policy.index(NOW)
    change = {"entity_id": "cover.room_203_blind", "attributes": {"supported_features": 3}}
    await sim.control.post("entity/attributes", change)
    query = select(
        db.AuditEvent.actor,
        db.AuditEvent.operation,
        db.AuditEvent.resource_id,
        db.AuditEvent.policy_version,
        db.AuditEvent.normalized_params,
    ).order_by(db.AuditEvent.id)
    async with asyncio.timeout(3):
        while True:
            async with ctx.engine.connect() as connection:
                rows = [tuple(row) for row in (await connection.execute(query)).all()]
            if rows:
                break
            await asyncio.sleep(0.02)
    assert rows == [
        ("system", "binding.needs_review", "res_203_blind", 2, {"reason": "capabilities"}),
        ("system", "policy.changed", None, 2, {}),
    ]
