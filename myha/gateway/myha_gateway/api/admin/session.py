"""The admin UI's session: who is signed in, the CSRF token, versions and setup state."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.admin import AdminIdentity, admin_guard, require_admin, require_csrf

router = APIRouter()


@router.get("/api/v1/admin/session")
async def session(
    request: Request, admin: Annotated[AdminIdentity, Depends(require_admin(fresh=False))]
) -> dict[str, Any]:
    """What the admin UI loads first; its CSRF token stays valid until the next UTC day ends."""
    guard = admin_guard(request)
    supervisor = await guard.supervisor_info()  # the answer require_admin has just cached
    instance = guard.instance
    return {
        "user": {"id": admin.user_id, "name": admin.display_name or admin.name},
        "csrf_token": guard.issue_csrf_token(request, admin.user_id),
        "supervisor_version": supervisor.version,
        "core_version": supervisor.homeassistant,
        "setup_state": instance.setup_state,
        "time_trusted": instance.time_trusted,
    }


@router.post("/api/v1/admin/session/check", status_code=204, dependencies=[Depends(require_csrf)])
async def check() -> None:
    """Asks Core afresh whether the caller is still an admin; the UI calls it before it acts."""
