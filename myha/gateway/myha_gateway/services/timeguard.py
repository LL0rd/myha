"""Wall-clock plausibility (E26): decides ``instance.time_trusted``.

The wall clock is untrusted when it is behind the persisted high-water mark
``instance.last_seen_clock``, when it moved more than 60 s differently from the monotonic clock
since the previous check, or when the Supervisor reports the host clock as not synchronized.
A later plausible check trusts it again: E26 only needs the flag to be current, and a lasting
backwards jump stays untrusted through the mark until the wall clock passes it again.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from myha_gateway.db import write_session
from myha_gateway.db.models import Instance
from myha_gateway.ha.supervisor import HostInfo, SupervisorUnavailable

MAX_JUMP = timedelta(seconds=60)

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

    async def check(self, now_wall: datetime, now_mono: float) -> bool:
        """Evaluate the clocks, persist mark and flag, audit a transition; the new flag."""
        synchronized = await self._synchronized()  # network I/O before taking the write lock
        jumped = (
            self._last is not None
            and abs((now_wall - self._last[0]) - timedelta(seconds=now_mono - self._last[1]))
            > MAX_JUMP
        )
        self._last = (now_wall, now_mono)
        async with write_session(self._engine) as session:
            instance = (await session.execute(select(Instance))).scalar_one()
            trusted = now_wall >= instance.last_seen_clock and not jumped and synchronized
            if now_wall > instance.last_seen_clock:
                instance.last_seen_clock = now_wall
            if trusted != instance.time_trusted:
                instance.time_trusted = trusted
                await self._audit(session, "time.trusted" if trusted else "time.untrusted")
        return trusted

    async def _synchronized(self) -> bool:
        """False only when the Supervisor answers ``dt_synchronized: false``."""
        if self._host_info is None:
            return True
        try:
            host = await self._host_info()
        except SupervisorUnavailable:
            return True
        return host is None or host.dt_synchronized is not False
