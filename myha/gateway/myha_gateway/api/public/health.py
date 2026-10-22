"""Public liveness check that also shows which client address reaches the public port."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str | None]:
    """Forwarding headers are echoed for display only; ``client_ip`` is the TCP peer."""
    return {
        "status": "ok",
        "client_ip": request.client.host if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
    }
