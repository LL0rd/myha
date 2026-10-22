"""The audit writer: one ``audit_event`` row per event, never a secret (design §5, §9).

Folding repeated denials into ``aggregate_count`` comes later; for now every event is a row.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db.models import AuditEvent as AuditRow

# Substrings of parameter names that could carry a credential, at any depth, in any case.
_SECRET_KEY = re.compile(r"token|secret|password|pin|code|csrf|ticket|cookie", re.IGNORECASE)


def _check_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError(f"normalized_params must not contain {key!r}")
            _check_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_keys(item)


@dataclass(frozen=True, kw_only=True)
class AuditEvent:
    """What may be audited: these fields and nothing else, so no payload can slip in."""

    operation: str
    result: str
    actor: str = "anonymous"
    effective_principal: str | None = None
    session_id: str | None = None
    reason_code: str | None = None
    resource_id: str | None = None
    policy_version: int | None = None
    normalized_params: Mapping[str, Any] | None = None
    anon_key: str | None = None  # fold key of an anonymous event

    def __post_init__(self) -> None:
        _check_keys(self.normalized_params)
        if self.anon_key is not None and len(self.anon_key) > 128:
            raise ValueError("anon_key is longer than 128 characters")


async def audit(session: AsyncSession, event: AuditEvent) -> None:
    """Add the event to the caller's write session; the caller's transaction commits it."""
    params = None if event.normalized_params is None else dict(event.normalized_params)
    session.add(
        AuditRow(
            ts=datetime.now(UTC),
            actor=event.actor,
            effective_principal=event.effective_principal,
            session_id=event.session_id,
            operation=event.operation,
            result=event.result,
            reason_code=event.reason_code,
            resource_id=event.resource_id,
            policy_version=event.policy_version,
            normalized_params=params,
            anon_key=event.anon_key,
        )
    )
    await session.flush()
