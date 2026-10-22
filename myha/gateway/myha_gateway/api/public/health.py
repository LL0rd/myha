"""Public liveness check that also shows which client address reaches the public port."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str | None]:
    """``client_ip`` is the TCP peer; forwarding headers are ignored."""
    return {"status": "ok", "client_ip": request.client.host if request.client else None}
