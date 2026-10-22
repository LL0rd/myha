"""The audit writer: one ``audit_event`` row per event, never a secret (design §5, §9).

With ``aggregate=True`` a denial folds into the row of the same minute and fold key instead.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myha_gateway.db.models import AuditEvent as AuditRow

# Parameter names that could carry a credential, judged word by word (snake_case, kebab-case,
# camelCase), at any depth, in any case. Single words match exactly, so "mapping" and "ping"
# pass; compounds match inside a word ("accesstoken", "usercode"); pairs match as neighbouring
# words ("api_key"). "code" is a secret word: Home Assistant takes alarm and lock codes as
# "code", and a reason code has its own column, never a place in normalized_params.
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])")
_SECRET_WORDS = frozenset(
    {"token", "secret", "password", "pwd", "pin", "code", "otp", "csrf", "ticket", "cookie"}
    | {"auth", "authorization", "credential", "credentials", "apikey", "bearer", "passphrase"}
)
_SECRET_PARTS = (
    *("token", "secret", "password", "passwd", "csrf", "ticket", "cookie", "apikey"),
    *("passcode", "pincode", "usercode"),
)
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key")})


def _is_secret(key: str) -> bool:
    words = [word.lower() for word in _WORD_SPLIT.split(key) if word]
    return (
        any(word in _SECRET_WORDS for word in words)
        or any(part in word for word in words for part in _SECRET_PARTS)
        or any(pair in _SECRET_PAIRS for pair in zip(words, words[1:], strict=False))
    )


def _check_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_secret(str(key)):
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


async def audit(session: AsyncSession, event: AuditEvent, *, aggregate: bool = False) -> None:
    """Add the event to the caller's write session; the caller's transaction commits it.

    ``aggregate`` folds a denial (any result but ``ok``) into the row with the same fold key in
    the same UTC minute, counting it in ``aggregate_count``; that row keeps its first ``ts``.
    The fold key is ``(session_id, operation, result, reason_code)``, or ``anon_key`` for an
    anonymous event (no session): SQL ``=`` never matches NULL, so those need a NULL-free key.
    """
    _check_keys(event.normalized_params)  # the mapping may have changed since the event was made
    now = datetime.now(UTC)
    if aggregate and event.result != "ok":
        if event.session_id is None and event.anon_key is None:
            raise ValueError("an anonymous denial folds only with an anon_key")
        minute = now.replace(second=0, microsecond=0)
        same = [AuditRow.ts >= minute, AuditRow.ts < minute + timedelta(minutes=1)]
        if event.session_id is None:
            same.append(AuditRow.anon_key == event.anon_key)
        else:
            same += [
                AuditRow.session_id == event.session_id,
                AuditRow.operation == event.operation,
                AuditRow.result == event.result,
                AuditRow.reason_code.is_not_distinct_from(event.reason_code),
            ]
        first = select(AuditRow.id).where(*same).order_by(AuditRow.id).limit(1).scalar_subquery()
        folded = await session.execute(
            update(AuditRow)
            .where(AuditRow.id == first)
            .values(aggregate_count=AuditRow.aggregate_count + 1)
            .returning(AuditRow.id)
        )
        if folded.first() is not None:
            return
    params = None if event.normalized_params is None else dict(event.normalized_params)
    session.add(
        AuditRow(
            ts=now,
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
