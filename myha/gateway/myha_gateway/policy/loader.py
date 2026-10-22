"""The policy index of the current policy version, cached, and contexts of the same version.

The cache key is ``(policy_version, ha.generation, adapter_locked, ha.registry_version)``;
``policy_version`` comes from one small select per call, so changes by other processes
(``dev-seed``) count at once.
Capabilities come from the bindings as the admin confirmed them; Home Assistant only shows
drift (``drift.py``), which turns a binding to ``needs_review`` in the database. A build that
finds drift marks the binding in its index already and wakes the drift worker.

``on_event`` and ``on_status`` run in the HA client's tasks: they never wait for the database
or the client, they only set a flag for the worker.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from myha_gateway.adapters.base import DeviceCapabilities, StateView
from myha_gateway.adapters.registry import adapter_for, detect_kind
from myha_gateway.db import models as db
from myha_gateway.db import session_factory, write_session
from myha_gateway.ha.client import HAClient
from myha_gateway.ha.messages import ConnectionStatus, StateChanged
from myha_gateway.policy.context import EffectiveContext, build_context_for_session
from myha_gateway.policy.drift import Audit, capabilities_changed, drift_reason, reconcile
from myha_gateway.policy.index import PolicyIndex, ResourceRef
from myha_gateway.policy.model import RoleRules

logger = logging.getLogger(__name__)
REGISTRY_POLL = 0.5  # the HA client reloads registries 0.5 s after an event, without a hook
Status = Literal["confirmed", "needs_review", "blocked"]
_Key = tuple[int, int, bool, int]


class PolicyLoader:
    def __init__(self, engine: AsyncEngine, ha: HAClient, *, audit: Audit) -> None:
        self.audit = audit  # wired by the app context; tests may record instead
        self._engine, self._ha = engine, ha
        self._sessions = session_factory(engine)
        self._cache: tuple[_Key, PolicyIndex] | None = None
        self._by_id: dict[str, db.ResourceBinding] = {}
        self._known_version = -1  # policy version of the maps: an older read never wins
        self._by_entity: dict[str, db.ResourceBinding] = {}
        self._gone: set[str] = set()  # bindings without unique_id whose state an event removed
        self._dirty = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self.passes_started = self.passes_done = 0  # worker passes; tests wait on them

    @property
    def adapter_locked(self) -> bool:
        """Writes stay locked while Core's unit is unchecked or the snapshot is stale."""
        return self._ha.adapter_locked or not self._ha.connected

    async def index(self, now: datetime) -> PolicyIndex:
        """The index of the policy version in the database now."""
        async with self._sessions() as session:
            version, _ = await _instance(session)
            return await self._index(session, version)

    async def context_for_session(
        self, session_id: str, now: datetime
    ) -> tuple[EffectiveContext, PolicyIndex] | None:
        """The session's context and the index of the same policy version; ``None`` if unknown.

        One read transaction, so both stem from one snapshot; ``policy_version`` and
        ``time_trusted`` come from the database, never from the start snapshot of the instance.
        Raises ``ValueError`` for rows that do not belong together (see ``context.py``).
        """
        async with self._sessions() as session:
            version, trusted = await _instance(session)
            row = await session.get(db.Session, session_id)
            if row is None:
                return None
            principal = None
            if row.principal_id is not None:
                principal = await session.get(db.Principal, row.principal_id)
            principal_id = None if principal is None else principal.id
            stay_id = None if principal is None else principal.stay_id
            stay = None if stay_id is None else await session.get(db.Stay, stay_id)
            memberships = await _all(
                session,
                select(db.Membership)
                .where(db.Membership.principal_id == principal_id)
                .order_by(db.Membership.id),
            )
            groups = [membership.group_id for membership in memberships]
            assignments = await _all(
                session,
                select(db.Assignment)
                .where(
                    or_(
                        and_(
                            db.Assignment.subject_type == "principal",
                            db.Assignment.subject_id == principal_id,
                        ),
                        and_(
                            db.Assignment.subject_type == "group",
                            db.Assignment.subject_id.in_(groups),
                        ),
                    )
                )
                .order_by(db.Assignment.id),
            )
            own = (("principal", principal_id), ("stay", stay_id), ("session", row.id))
            fences = await _all(
                session,
                select(db.Fence)
                .where(
                    or_(
                        db.Fence.scope_type.in_(("instance", "unit")),
                        *(
                            and_(db.Fence.scope_type == scope, db.Fence.scope_id == scope_id)
                            for scope, scope_id in own
                            if scope_id is not None
                        ),
                    )
                )
                .order_by(db.Fence.id),
            )
            instance = db.Instance(policy_version=version, time_trusted=trusted)
            ctx = build_context_for_session(
                row, principal, assignments, memberships, stay, fences, instance, now
            )
            return ctx, await self._index(session, version)

    def state_view(self, resource_id: str) -> StateView | None:
        """The adapter's view of a bound entity, locked as ``adapter_locked`` says."""
        binding = self._by_id.get(resource_id)
        kind = None if binding is None else detect_kind(binding.entity_id)
        if binding is None or kind is None or kind != binding.kind:
            return None
        return adapter_for(kind).state_view(self._ha.state(binding.entity_id), self.adapter_locked)

    def invalidate(self) -> None:
        """Build anew on the next call, also if the cache key looks the same."""
        self._cache = None

    async def on_event(self, change: StateChanged) -> None:
        """A confirmed binding's state: compare its abilities, cheap and without the database."""
        binding = self._by_entity.get(change.entity_id)
        if binding is None or binding.status != "confirmed" or not self._ha.connected:
            return  # while disconnected, the reconnect checks everything anyway
        state = change.new_state
        if state is not None:
            self._gone.discard(binding.id)
        elif binding.unique_id is None:
            self._gone.add(binding.id)
        if state is None or capabilities_changed(binding, state, self._ha):
            self.invalidate()  # the next build marks it before the database does
            self._dirty.set()

    async def on_status(self, status: ConnectionStatus) -> None:
        """After every (re)connect check all bindings: Core sends no events for the gap."""
        if not status.connected:
            return
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="policy-drift")
        self._dirty.set()

    async def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _index(self, session: AsyncSession, version: int) -> PolicyIndex:
        ha = self._ha
        key = (version, ha.generation, self.adapter_locked, ha.registry_version)
        cached = self._cache
        if cached is not None and cached[0] == key:
            return cached[1]
        units = await _all(session, select(db.Unit.id).order_by(db.Unit.id))
        bindings = await _all(session, select(db.ResourceBinding).order_by(db.ResourceBinding.id))
        roles = await _all(session, select(db.RoleVersion).order_by(db.RoleVersion.id))
        # No await from here on: the drift check sees the HA snapshot the index is cached with.
        self._remember(bindings, version)
        resources, members = self._resources(units, bindings)
        units_map = {unit: frozenset(ids) for unit, ids in members.items()}
        index = PolicyIndex(_rules(roles), resources, units_map, policy_version=version)
        self._cache = (key, index)
        return index

    def _resources(
        self, units: Iterable[str], bindings: Iterable[db.ResourceBinding]
    ) -> tuple[dict[str, ResourceRef], dict[str, set[str]]]:
        resources: dict[str, ResourceRef] = {}
        members: dict[str, set[str]] = {unit: set() for unit in units}
        connected = self._ha.connected
        for binding in bindings:
            try:
                ref = _resource(binding)
            except (ValueError, TypeError, KeyError) as exc:
                logger.error("binding %s is broken and stays hidden: %s", binding.id, exc)
                continue
            drifted = drift_reason(binding, self._ha, self._gone)
            if connected and ref.status == "confirmed" and drifted:
                ref = replace(ref, status="needs_review")
                self._dirty.set()
            resources[ref.id] = ref
            members.setdefault(ref.unit_id, set()).add(ref.id)
        return resources, members

    def _remember(self, bindings: Iterable[db.ResourceBinding], version: int) -> None:
        if version < self._known_version:
            return  # a build that read before the worker's commit
        self._known_version = version
        self._by_id = {binding.id: binding for binding in bindings}
        self._by_entity = {binding.entity_id: binding for binding in self._by_id.values()}

    async def _run(self) -> None:
        """Reconcile on a flag or a newer registry; a failed pass is retried at the next poll."""
        seen = -1
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(REGISTRY_POLL):
                    await self._dirty.wait()
            registry = self._ha.registry_version
            if not self._dirty.is_set() and registry == seen:
                continue
            self._dirty.clear()
            seen = registry
            self.passes_started += 1
            try:
                await self._reconcile()
            except Exception:
                seen = -1
                logger.exception("checking the bindings for drift failed")
            self.passes_done += 1

    async def _reconcile(self) -> None:
        if not self._ha.connected:
            return  # the reconnect sets the flag again
        gone = frozenset(self._gone)
        async with write_session(self._engine) as session:
            bindings, version = await reconcile(session, self._ha, self.audit, gone)
        self._gone -= gone
        self._remember(bindings, version)
        self.invalidate()  # also unchanged: an index may still mark drift that is gone again


async def _instance(session: AsyncSession) -> tuple[int, bool]:
    """``policy_version`` and ``time_trusted`` in one select: the cache key and the context."""
    query = select(db.Instance.policy_version, db.Instance.time_trusted)
    version, trusted = (await session.execute(query)).one()
    return version, trusted


async def _all[T](session: AsyncSession, query: Select[T]) -> Sequence[T]:
    return (await session.scalars(query)).all()


def _resource(binding: db.ResourceBinding) -> ResourceRef:
    capabilities = DeviceCapabilities.from_json(binding.confirmed_capabilities)
    if capabilities.kind != binding.kind:
        raise ValueError(f"kind {binding.kind!r} but capabilities of {capabilities.kind!r}")
    status = cast(Status, binding.status)  # an unknown status is refused by decide()
    return ResourceRef(binding.id, binding.unit_id, binding.kind, status, capabilities)


def _rules(roles: Iterable[db.RoleVersion]) -> dict[tuple[str, int], RoleRules]:
    """Every published role version; a broken one is left out, so ``rules_for()`` raises."""
    rules = {}
    for role in roles:
        try:
            parsed = RoleRules.model_validate({"grants": role.grants, "denies": role.denies})
        except ValueError as exc:
            logger.error("role version %s is broken and decides nothing: %s", role.id, exc)
            continue
        rules[(role.role_template_id, role.version_number)] = parsed
    return rules
