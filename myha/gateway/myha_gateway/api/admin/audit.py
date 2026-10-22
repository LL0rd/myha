"""Admin API: the audit log, newest first, by keyset pages (design §6)."""

import base64
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, or_, select

from myha_gateway.auth.admin import require_admin
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError

router = APIRouter()

_COLUMNS = {
    "operation": AuditRow.operation,
    "principal": AuditRow.effective_principal,
    "session": AuditRow.session_id,
    "result": AuditRow.result,
}


def _invalid(message: str) -> ApiError:
    return ApiError(400, "INVALID_PARAMETERS", message)


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp")
    return parsed


def _conditions(text: str) -> tuple[list[Any], set[str]]:
    """``key=value`` pairs separated by commas; keys from a closed set, each at most once."""
    conditions: list[Any] = []
    seen: set[str] = set()
    for clause in filter(None, text.split(",")):
        key, _, value = clause.partition("=")
        if key in seen or not value or (key not in _COLUMNS and key not in ("since", "until")):
            raise _invalid("Invalid filter")
        seen.add(key)
        if key in _COLUMNS:
            conditions.append(_COLUMNS[key] == value)
            continue
        try:
            at = _aware(value)
        except ValueError:
            raise _invalid("Invalid filter") from None
        conditions.append(AuditRow.ts >= at if key == "since" else AuditRow.ts < at)
    return conditions, seen


def _cursor(row: AuditRow) -> str:
    return base64.urlsafe_b64encode(f"{row.ts.isoformat()}|{row.id}".encode()).decode()


def _after(cursor: str) -> Any:
    """The keyset condition for the rows older than the cursor's ``(ts, id)``."""
    try:
        ts, _, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        at, before = _aware(ts), int(row_id)
    except ValueError:
        raise _invalid("Invalid cursor") from None
    return or_(AuditRow.ts < at, and_(AuditRow.ts == at, AuditRow.id < before))


def _row_json(row: AuditRow) -> dict[str, Any]:
    """The audit fields; never ``anon_key``, which holds the client IP of anonymous events."""
    return {
        "id": row.id,
        "ts": row.ts,
        "actor": row.actor,
        "effective_principal": row.effective_principal,  # NULL: anonymous
        "operation": row.operation,
        "result": row.result,
        "reason_code": row.reason_code,
        "resource_id": row.resource_id,
        "session_id": row.session_id,
        "policy_version": row.policy_version,
        "aggregate_count": row.aggregate_count,
        "normalized_params": row.normalized_params,
    }


@router.get("/api/v1/admin/audit", dependencies=[Depends(require_admin(fresh=True))])
async def list_audit(
    request: Request,
    filter: str = "",  # noqa: A002 - the documented query parameter name
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """One page of audit events; ``principal=`` leaves out anonymous events and says so."""
    ctx: AppContext = request.app.state.ctx
    conditions, keys = _conditions(filter)
    if cursor is not None:
        conditions.append(_after(cursor))
    query = (
        select(AuditRow)
        .where(*conditions)
        .order_by(AuditRow.ts.desc(), AuditRow.id.desc())
        .limit(limit + 1)
    )
    async with ctx.sessions() as session:
        rows = list((await session.execute(query)).scalars())
    more = len(rows) > limit
    return {
        "events": [_row_json(row) for row in rows[:limit]],
        "next_cursor": _cursor(rows[limit - 1]) if more else None,
        "anonymous_excluded": "principal" in keys,
    }
