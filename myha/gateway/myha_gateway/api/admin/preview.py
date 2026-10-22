"""Admin API: preview another context and simulate its decisions (design §4.5, §6, E5)."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from myha_gateway.auth.admin import AdminIdentity, require_admin, require_csrf
from myha_gateway.deps import AppContext
from myha_gateway.services.preview import create_preview, open_preview, preview_dashboard, simulate

router = APIRouter()
Id = Annotated[str, Field(min_length=1, max_length=64)]


def _utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:  # e.g. year 1 with a positive offset
        raise ValueError("not representable in UTC") from exc


Instant = Annotated[AwareDatetime, AfterValidator(_utc)]


class TargetIn(BaseModel):
    """Exactly one of the forms ``{principal_id, session_id?}``, ``{stay_id}``, ``{invite_id}``,
    ``{group_id}``; the service refuses any other combination."""

    model_config = ConfigDict(extra="forbid")

    principal_id: Id | None = None
    session_id: Id | None = None
    stay_id: Id | None = None
    invite_id: Id | None = None
    group_id: Id | None = None


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: TargetIn
    at: Instant | None = None


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: Id
    action: Annotated[str, Field(min_length=1, max_length=64)]
    parameters: dict[str, Any] = Field(default_factory=dict)
    at: Instant | None = None


def _ctx(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


@router.post("/api/v1/admin/preview", status_code=201)
async def create(
    request: Request, body: PreviewIn, admin: Annotated[AdminIdentity, Depends(require_csrf)]
) -> dict[str, Any]:
    target = body.target.model_dump(exclude_none=True)
    session, _ = await create_preview(_ctx(request), target, body.at, admin.user_id)
    return {
        "id": session.id,
        "purpose": session.purpose,
        "principal_id": session.principal_id,
        "unit_ids": session.unit_ids,
        "preview_target": session.preview_target,
        "expires_at": session.not_after,
    }


@router.get("/api/v1/admin/preview/{preview_id}/dashboard")
async def dashboard(
    request: Request,
    preview_id: str,
    admin: Annotated[AdminIdentity, Depends(require_admin(fresh=True))],  # sensitive read (E5)
    at: Annotated[Instant | None, Query()] = None,
) -> dict[str, Any]:
    ctx = _ctx(request)
    opened = await open_preview(ctx, preview_id, admin.user_id, at)
    return await preview_dashboard(ctx, opened)


@router.post("/api/v1/admin/preview/{preview_id}/simulate")
async def simulate_action(
    request: Request,
    preview_id: str,
    body: SimulateIn,
    admin: Annotated[AdminIdentity, Depends(require_csrf)],
) -> dict[str, Any]:
    ctx = _ctx(request)
    opened = await open_preview(ctx, preview_id, admin.user_id, body.at)
    decision = await simulate(
        ctx, opened, admin.user_id, body.resource_id, body.action, body.parameters
    )
    return {
        "would_allow": decision.allowed,
        "code": decision.code,
        "public_code": decision.public_code,
        "normalized_parameters": dict(decision.normalized_params),
        "matched": list(decision.matched),
        "message": decision.message,
    }
