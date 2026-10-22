"""``POST /api/v1/actions``: one product action as an idempotent command (design §4.5, A31)."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Request
from pydantic import BaseModel

from myha_gateway.api.public.validation import idempotency_key, validate_action_request
from myha_gateway.auth.public import SessionContext, app_context, require_csrf_public
from myha_gateway.services.commands import submit

router = APIRouter()


class ActionAccepted(BaseModel):
    command_id: str
    status: str
    policy_version: int


@router.post("/api/v1/actions", status_code=202, response_model=ActionAccepted)
async def actions(
    request: Request,
    current: Annotated[SessionContext, Depends(require_csrf_public)],
    body: Annotated[dict[str, Any], Body()],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ActionAccepted:
    """``accepted`` means taken by the gateway; ``command.updated`` reports what followed."""
    checked_key = idempotency_key(key)
    resource_id, action, parameters = validate_action_request(body)
    row = await submit(app_context(request), current, checked_key, resource_id, action, parameters)
    return ActionAccepted(command_id=row.id, status=row.status, policy_version=row.policy_version)
