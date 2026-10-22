"""S0 probe: shows the peer and identity headers the Supervisor ingress delivers."""

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()
_SHOWN = ("x-remote-user-", "x-ingress-path", "x-forwarded-", "x-real-ip")


@router.get("/api/v1/admin/probe")
async def probe(request: Request) -> dict[str, Any]:
    """Echo only identity and forwarding headers; repeated headers are joined, not dropped."""
    headers = {
        name: ", ".join(request.headers.getlist(name))
        for name in request.headers
        if name.startswith(_SHOWN)
    }
    return {"peer": request.client.host if request.client else None, "headers": headers}
