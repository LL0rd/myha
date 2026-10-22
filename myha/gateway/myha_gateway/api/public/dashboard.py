"""The caller's dashboard: the projection of its session's effective context (design §4.4)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.public import SessionContext, app_context, require_session
from myha_gateway.db import write_session
from myha_gateway.errors import ApiError
from myha_gateway.ha.client import utcnow
from myha_gateway.projection.dashboard import load_layout, project_dashboard
from myha_gateway.projection.schema import DashboardView
from myha_gateway.services.audit import AuditEvent, audit

logger = logging.getLogger(__name__)
router = APIRouter()


class DashboardResponse(DashboardView):
    policy_version: int


@router.get(
    "/api/v1/dashboard",
    response_model=DashboardResponse,
    response_model_exclude_unset=True,  # no state or capabilities where the context sees none
)
async def dashboard(
    request: Request,
    current: Annotated[SessionContext, Depends(require_session(purpose="access"))],
) -> DashboardResponse:
    ctx, now, session_id = app_context(request), utcnow(), current.session.id
    async with ctx.sessions() as session:  # one snapshot for context, index and layout
        found = await ctx.policy.context_for_session(session_id, now, session)
        layout = await load_layout(session)
    if found is None:  # revoked and removed since the session check
        raise ApiError(401, "UNAUTHENTICATED", "Not authenticated")
    effective, index = found
    try:
        view = project_dashboard(effective, index, ctx.policy.state_view, layout, now)
    except Exception:
        logger.exception("projecting the dashboard of session %s failed", session_id)
        async with write_session(ctx.engine) as session:
            event = AuditEvent(
                operation="decide_error",
                result="error",
                actor=current.principal.id,
                effective_principal=current.principal.id,
                session_id=session_id,
                policy_version=index.policy_version,
            )
            await audit(session, event)
        raise ApiError(
            500, "UPSTREAM_UNAVAILABLE", "The device cannot be used right now."
        ) from None
    return DashboardResponse(
        status=view.status, rooms=view.rooms, policy_version=index.policy_version
    )
