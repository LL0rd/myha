"""``GET /api/v1/commands/{id}``: a command of the caller's own session (design §4.5)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.public import SessionContext, app_context, require_session
from myha_gateway.services.commands import command_json, get_command

router = APIRouter()


@router.get("/api/v1/commands/{command_id}")
async def command(
    request: Request,
    command_id: str,
    current: Annotated[SessionContext, Depends(require_session(purpose="access"))],
) -> dict[str, Any]:
    row = await get_command(app_context(request), current.session.id, command_id)
    return command_json(row)
