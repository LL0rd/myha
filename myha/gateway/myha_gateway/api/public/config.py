"""Public instance facts for the sign-in page; never a device list (design §6)."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from myha_gateway.auth.public import app_context, instance_of, rate_limit

router = APIRouter()


@router.get("/api/v1/public/config", dependencies=[Depends(rate_limit("config"))])
async def public_config(request: Request) -> dict[str, Any]:
    instance = instance_of(app_context(request))
    return {
        "instance_name": instance.name,
        "languages": ["de", "en"],
        "theme_default": "system",
        "operator_contact": instance.operator_contact,
    }
