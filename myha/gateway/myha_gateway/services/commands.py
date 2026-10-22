"""Commands (design §4.5): idempotent acceptance, the guarded send and observed confirmation.

``submit`` decides, stores the command as ``accepted`` and returns; a background task then checks
again right before the service call (session, policy version, HA connection and a fresh
``decide``), sends, and watches the device state until the adapter's expectation holds, the
check range is violated, the device or Home Assistant goes offline, or the kind's timeout ends.
Every status change is audited and published as ``CommandUpdated`` after its commit.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from myha_gateway.adapters.base import Action, Expectation, StateView
from myha_gateway.adapters.registry import adapter_for
from myha_gateway.auth.public import SessionContext
from myha_gateway.db import bump_policy_version, write_session
from myha_gateway.db.models import Command, ResourceBinding
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.events.bus import CommandUpdated, ConnectionStatus, Overflow, PolicyChanged
from myha_gateway.events.bus import StateChanged as BusStateChanged
from myha_gateway.ha.client import HAError, HAServiceError, utcnow
from myha_gateway.ids import new_id
from myha_gateway.policy.codes import DecisionCode, PublicError, public_error
from myha_gateway.policy.decide import Decision, decide
from myha_gateway.services.audit import AuditEvent, audit

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0  # seconds to wait for the state to show the command
TIMEOUTS = {"cover": 90.0}  # real blinds take their time to reach the end
IN_FLIGHT = ("accepted", "sent")
# Public status codes of denials at the API (spec §12.1).
HTTP_STATUS: Mapping[PublicError, int] = {
    PublicError.NOT_FOUND: 404,
    PublicError.ACTION_NOT_ALLOWED: 403,
    PublicError.INVALID_PARAMETERS: 400,
    PublicError.ACCESS_EXPIRED: 403,
    PublicError.UPSTREAM_UNAVAILABLE: 503,
}
_tasks: set[asyncio.Task[None]] = set()  # running sends; kept so they are not collected


def body_hash(resource_id: str, action: str, parameters: Mapping[str, Any]) -> str:
    """The same request always hashes the same, whatever the key order the client sent."""
    body = {"resource_id": resource_id, "action": action, "parameters": parameters}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def public_code(row: Command) -> str | None:
    """What a client may learn about a failed or unknown command; nothing for the others."""
    if row.status not in ("failed", "unknown"):
        return None
    code = DecisionCode.__members__.get(row.reason or "")  # the second decision's denial
    return PublicError.UPSTREAM_UNAVAILABLE if code is None else public_error(code)


def command_json(row: Command) -> dict[str, Any]:
    """The wire contract's command (``command.updated`` and ``GET /commands/{id}``)."""
    return {
        "id": row.id,
        "resource_id": row.resource_id,
        "action": row.action,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "public_code": public_code(row),
    }


async def submit(
    app: AppContext,
    current: SessionContext,
    key: str,
    resource_id: str,
    action: Action,
    parameters: dict[str, Any],
) -> Command:
    """Accept the action as a command, or return the command this key already made."""
    session_id, digest = current.session.id, body_hash(resource_id, action, parameters)
    async with app.sessions() as session:
        existing = await _by_key(session, session_id, key)
    if existing is not None:  # a retry: no second decision, no second send
        return _replay(existing, digest)
    now = utcnow()
    decision, policy_version = await _decide(app, current, resource_id, action, parameters, now)
    actor = current.principal.id
    if not decision.allowed:
        assert decision.public_code is not None
        async with write_session(app.engine) as session:
            event = AuditEvent(
                operation="action.execute",
                result="denied",
                actor=actor,
                effective_principal=actor,
                session_id=session_id,
                reason_code=decision.code,
                resource_id=resource_id,
                policy_version=policy_version,
                normalized_params=dict(decision.normalized_params),
            )
            await audit(session, event, aggregate=True)
        status = HTTP_STATUS[decision.public_code]
        raise ApiError(status, decision.public_code, decision.message)
    row = Command(
        id=new_id("cmd"),
        session_id=session_id,
        idempotency_key=key,
        body_hash=digest,
        resource_id=resource_id,
        action=action,
        normalized_params=dict(decision.normalized_params),
        policy_version=policy_version,
        status="accepted",
        created_at=now,
        updated_at=now,
    )
    async with write_session(app.engine) as session:
        existing = await _by_key(session, session_id, key)  # a concurrent twin got here first
        if existing is None:
            session.add(row)
            await _audit_status(session, row, actor)
    if existing is not None:
        return _replay(existing, digest)
    app.bus.publish(CommandUpdated(session_id, command_json(row)))
    task = asyncio.create_task(_execute(app, row, actor), name=f"command-{row.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return row


async def get_command(app: AppContext, session_id: str, command_id: str) -> Command:
    """The session's own command; any other is not found."""
    async with app.sessions() as session:
        row = await session.get(Command, command_id)
    if row is None or row.session_id != session_id:
        raise ApiError(404, "NOT_FOUND", "The command was not found.")
    return row


async def mark_in_flight_unknown(engine: AsyncEngine) -> int:
    """At startup: commands a previous process left accepted or sent become ``unknown`` (A32).

    Nothing is sent again: whether Home Assistant ran them is not known.
    """
    now = utcnow()
    async with write_session(engine) as session:
        query = (
            update(Command)
            .where(Command.status.in_(IN_FLIGHT))
            .values(status="unknown", reason="restart", updated_at=now)
            .returning(Command)
        )
        rows = list((await session.execute(query)).scalars())
        for row in rows:
            await _audit_status(session, row, "system")
    return len(rows)


async def settle() -> None:
    """Wait for every running send to finish; for tests and an orderly shutdown."""
    while _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)


async def stop_commands() -> None:
    """Cancel every running send; the next start marks their commands ``unknown``."""
    for task in list(_tasks):
        task.cancel()
    await settle()


async def _by_key(session: AsyncSession, session_id: str, key: str) -> Command | None:
    query = select(Command).where(Command.session_id == session_id)
    return (await session.execute(query.where(Command.idempotency_key == key))).scalar_one_or_none()


def _replay(existing: Command, digest: str) -> Command:
    if existing.body_hash != digest:
        raise ApiError(409, "CONFLICT", "The Idempotency-Key was used for another request.")
    return existing


async def _decide(
    app: AppContext,
    current: SessionContext,
    resource_id: str,
    action: Action,
    parameters: Mapping[str, Any],
    now: datetime,
) -> tuple[Decision, int]:
    """The decision on a fresh context and state; any exception is a 500 and an audit row."""
    session_id, actor = current.session.id, current.principal.id
    version = None
    try:
        found = await app.policy.context_for_session(session_id, now)
        if found is not None:
            ctx, index = found
            version = index.policy_version
            view = app.policy.state_view(resource_id)
            return decide(
                ctx, index, resource_id, action, parameters, now, view, execute=True
            ), version
    except Exception:
        logger.exception("deciding an action of session %s failed", session_id)
        async with write_session(app.engine) as session:
            event = AuditEvent(
                operation="decide_error",
                result="error",
                actor=actor,
                effective_principal=actor,
                session_id=session_id,
                resource_id=resource_id,
                policy_version=version,
            )
            await audit(session, event)
        raise ApiError(
            500, "UPSTREAM_UNAVAILABLE", "The device cannot be used right now."
        ) from None
    raise ApiError(401, "UNAUTHENTICATED", "Not authenticated")  # revoked and removed since


async def _execute(app: AppContext, row: Command, actor: str) -> None:
    """Check again, send, observe; ends with the command confirmed, failed or unknown."""
    try:
        await _run(app, row, actor)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("command %s failed unexpectedly", row.id)
        if row.status in IN_FLIGHT:
            await _set_status(app, row, actor, "unknown", "internal_error")


async def _run(app: AppContext, row: Command, actor: str) -> None:
    now = utcnow()
    async with app.sessions() as session:
        found = await app.policy.context_for_session(row.session_id, now, session)
        binding = await session.get(ResourceBinding, row.resource_id)
    if found is None or binding is None:
        await _set_status(app, row, actor, "failed", DecisionCode.SESSION_INACTIVE)
        return
    ctx, index = found
    if index.policy_version != row.policy_version:
        await _set_status(app, row, actor, "failed", "policy_changed")
        return
    if not app.ha.connected:
        await _set_status(app, row, actor, "failed", "ha_disconnected")
        return
    action = Action(row.action)
    try:  # the second decision, on the state right before the call
        view = app.policy.state_view(row.resource_id)
        decision = decide(
            ctx, index, row.resource_id, action, row.normalized_params, now, view, execute=True
        )
    except Exception:
        logger.exception("deciding command %s again failed", row.id)
        await _set_status(app, row, actor, "failed", "decide_error")
        return
    if not decision.allowed:
        await _set_status(app, row, actor, "failed", decision.code)
        return
    adapter = adapter_for(binding.kind)  # type: ignore[arg-type]  # the index checked the kind
    call = adapter.translate(action, decision.normalized_params, binding.entity_id)
    expectation = adapter.expectation(action, decision.normalized_params)
    if action is Action.CLIMATE_SET_MODE and decision.check_range is not None:
        expectation = replace(expectation, target_c_range=decision.check_range)
    queue = app.bus.subscribe()  # before the call: no state change may slip past
    try:
        try:
            result = await app.ha.call_service(call.domain, call.service, call.entity_id, call.data)
        except HAServiceError:
            await _set_status(app, row, actor, "failed", "service_error")
            return
        except HAError:  # the call may or may not have reached Core
            await _set_status(app, row, actor, "unknown", "ha_unavailable")
            return
        await _set_status(app, row, actor, "sent", context_id=result.context_id)
        await _observe(app, row, actor, call.entity_id, binding.kind, expectation, queue)
    finally:
        app.bus.unsubscribe(queue)


async def _observe(
    app: AppContext,
    row: Command,
    actor: str,
    entity_id: str,
    kind: str,
    expectation: Expectation,
    queue: asyncio.Queue[Any],
) -> None:
    """Wait for the outcome within the kind's timeout, then store it outside that timeout."""
    outcome: tuple[str, str | None] = ("unknown", "timeout")
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(TIMEOUTS.get(kind, DEFAULT_TIMEOUT)):
            outcome = await _wait(app, row.resource_id, entity_id, expectation, queue)
    status, reason = outcome
    await _set_status(app, row, actor, status, reason, review=reason == "range_violated")


async def _wait(
    app: AppContext,
    resource_id: str,
    entity_id: str,
    expectation: Expectation,
    queue: asyncio.Queue[Any],
) -> tuple[str, str | None]:
    """Check at once (the device may be there already), then on each change of the entity."""
    while True:
        outcome = _outcome(expectation, app.policy.state_view(resource_id))
        if outcome is not None:
            return outcome
        while True:
            event = await queue.get()
            if isinstance(event, ConnectionStatus) and event.status == "offline":
                return "unknown", "ha_disconnected"
            if isinstance(event, Overflow) or (
                isinstance(event, BusStateChanged) and event.entity_id == entity_id
            ):
                break


def _outcome(expectation: Expectation, view: StateView | None) -> tuple[str, str | None] | None:
    """The final status the view shows, or ``None`` to keep waiting."""
    if view is None or view.availability != "online":
        return "unknown", "offline"
    # Before the new mode shows, the old setpoint says nothing about the check range.
    applied = expectation.hvac_mode is None or view.hvac_mode == expectation.hvac_mode
    if applied and expectation.violated_range(view):
        return "failed", "range_violated"
    return ("confirmed", None) if expectation.matches(view) else None


async def _set_status(
    app: AppContext,
    row: Command,
    actor: str,
    status: str,
    reason: str | None = None,
    *,
    context_id: str | None = None,
    review: bool = False,
) -> None:
    """Store, audit and publish one status change; ``review`` also flags the binding (E7)."""
    row.status, row.reason, row.updated_at = status, reason, utcnow()
    if context_id is not None:
        row.ha_context_id = context_id
    version = None
    async with write_session(app.engine) as session:
        values = {"status": status, "reason": reason, "updated_at": row.updated_at}
        values["ha_context_id"] = row.ha_context_id
        await session.execute(update(Command).where(Command.id == row.id).values(**values))
        await _audit_status(session, row, actor)
        if review:
            flag = update(ResourceBinding).where(ResourceBinding.id == row.resource_id)
            await session.execute(flag.values(status="needs_review"))
            version = await bump_policy_version(session)
            event = AuditEvent(  # as drift.py audits its own needs_review
                operation="binding.needs_review",
                result="ok",
                actor="system",
                resource_id=row.resource_id,
                policy_version=version,
                normalized_params={"reason": reason},
            )
            await audit(session, event)
    if version is not None:
        app.policy.invalidate()
        app.bus.publish(PolicyChanged(version))
    app.bus.publish(CommandUpdated(row.session_id, command_json(row)))


async def _audit_status(session: AsyncSession, row: Command, actor: str) -> None:
    principal = None if actor == "system" else actor
    event = AuditEvent(
        operation="command.status",
        result=row.status,
        actor=actor,
        effective_principal=principal,
        session_id=row.session_id,
        reason_code=row.reason,
        resource_id=row.resource_id,
        policy_version=row.policy_version,
        normalized_params={"command_id": row.id, **row.normalized_params},
    )
    await audit(session, event)
