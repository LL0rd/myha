"""Wall-clock plausibility (E26): decides ``instance.time_trusted``.

The wall clock is untrusted when it is behind the high-water mark ``instance.last_seen_clock``,
when it moved more than 60 s differently from the monotonic clock since the previous check, or
when the Supervisor reports the host clock as not synchronized. A later plausible check trusts
it again: E26 only needs the flag to be current. Only a trusted check advances the mark, so a
forward jump cannot lock the instance out once the clock is corrected.

A host clock that the Supervisor reports as synchronized is authoritative: that check is trusted
and resets the mark to the wall clock, backwards if need be. A rollback that the host reports as
NTP-synchronised is a correction, not an attack; without host info an operator reset is needed
(Plan 3 diagnostics).

The mark is exact in memory; the database gets it at most every 5 minutes of advance, and at
once on a trust transition or a reset. A restart therefore checks against a mark up to 5
minutes old.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from myha_gateway.db import session_factory, write_session
from myha_gateway.db.models import Instance
from myha_gateway.ha.supervisor import HostInfo, SupervisorUnavailable

MAX_JUMP = timedelta(seconds=60)
PERSIST_EVERY = timedelta(minutes=5)

HostInfoSource = Callable[[], Awaitable[HostInfo | None]]
AuditWriter = Callable[[AsyncSession, str], Awaitable[None]]  # (session, operation)


class TimeGuard:
    def __init__(
        self, engine: AsyncEngine, host_info: HostInfoSource | None, audit: AuditWriter
    ) -> None:
        self._engine = engine
        self._host_info = host_info
        self._audit = audit
        self._last: tuple[datetime, float] | None = None  # (wall, monotonic) of the last check
        # shortcut: mark and flag are loaded once; a later out-of-band reset (Plan 3) must
        # reach this object, not only the database.
        self._stored: tuple[datetime, bool] | None = None  # (mark, trusted) in the database
        self._mark: datetime | None = None  # exact high-water mark

    async def check(self, now_wall: datetime, now_mono: float) -> bool:
        """Evaluate the clocks, persist mark and flag when due, audit a transition; the flag."""
        synchronized = await self._synchronized()  # network I/O before taking the write lock
        if self._stored is None or self._mark is None:
            async with session_factory(self._engine)() as session:
                instance = (await session.execute(select(Instance))).scalar_one()
            self._stored = (instance.last_seen_clock, instance.time_trusted)
            self._mark = instance.last_seen_clock
        jumped = (
            self._last is not None
            and abs((now_wall - self._last[0]) - timedelta(seconds=now_mono - self._last[1]))
            > MAX_JUMP
        )
        self._last = (now_wall, now_mono)

        stored_mark, stored_trusted = self._stored
        reset = synchronized is True and now_wall < self._mark
        trusted = synchronized is True or (
            synchronized is None and not jumped and now_wall >= self._mark
        )
        if trusted and (now_wall > self._mark or reset):
            self._mark = now_wall
        if trusted == stored_trusted and not reset and self._mark - stored_mark < PERSIST_EVERY:
            return trusted
        async with write_session(self._engine) as session:
            instance = (await session.execute(select(Instance))).scalar_one()
            instance.last_seen_clock = self._mark
            if trusted != instance.time_trusted:
                instance.time_trusted = trusted
                await self._audit(session, "time.trusted" if trusted else "time.untrusted")
        self._stored = (self._mark, trusted)
        return trusted

    async def _synchronized(self) -> bool | None:
        """``dt_synchronized`` from the Supervisor; None when unknown or unreachable."""
        if self._host_info is None:
            return None
        try:
            host = await self._host_info()
        except SupervisorUnavailable:
            return None
        return None if host is None else host.dt_synchronized
