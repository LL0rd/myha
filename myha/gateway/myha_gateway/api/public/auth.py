"""Guest sign-in: redeem an invitation, renew a browser session, log out (design §4.3)."""

import math
import unicodedata
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from myha_gateway.auth.cookies import clear_session_cookie, set_session_cookie
from myha_gateway.auth.public import (
    SessionContext,
    app_context,
    cookie_name,
    rate_limit,
    redeem,
    renew,
    require_csrf_public,
    revoke,
    session_view,
)
from myha_gateway.ha.client import utcnow

router = APIRouter()


class RedeemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invite_secret: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")  # the form of new_secret()
    device_label: str | None = None
    client: Literal["browser", "native"]

    @field_validator("device_label")
    @classmethod
    def clean_label(cls, value: str | None) -> str | None:
        """Without control and format characters (no bidi tricks in the admin UI), 60 at most."""
        if value is None:
            return None
        label = "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf")).strip()
        if len(label) > 60:
            raise ValueError("device_label is longer than 60 characters")
        return label or None


@router.post("/api/v1/auth/redeem", dependencies=[Depends(rate_limit("redeem"))])
async def redeem_invite(request: Request, response: Response, body: RedeemIn) -> dict[str, Any]:
    """Browser: the session cookie and the CSRF token. Native: access and refresh token."""
    ctx = app_context(request)
    redeemed = await redeem(
        ctx,
        secret=body.invite_secret,
        device_label=body.device_label,
        client=body.client,
        client_ip=request.client.host if request.client else "",
    )
    session = redeemed.session
    if redeemed.refresh_token is not None:
        expires_in = math.floor((session.not_after - session.created_at).total_seconds())
        return {
            "access_token": redeemed.token,
            "refresh_token": redeemed.refresh_token,
            "expires_in": expires_in,
        }
    set_session_cookie(response, cookie_name(ctx), redeemed.token, session.not_after)
    return {"session": session_view(session, utcnow()), "csrf_token": session.csrf_secret}


@router.post("/api/v1/auth/session/renew")
async def renew_session(
    request: Request,
    response: Response,
    current: Annotated[SessionContext, Depends(require_csrf_public)],
) -> dict[str, Any]:
    """A fresh cookie up to what stay and limits allow now; never a wider scope."""
    ctx = app_context(request)
    session = await renew(ctx, current)
    token = request.cookies[cookie_name(ctx)]  # a browser session came as this cookie
    set_session_cookie(response, cookie_name(ctx), token, session.not_after)
    return {"session": session_view(session, utcnow()), "csrf_token": session.csrf_secret}


@router.post("/api/v1/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    current: Annotated[SessionContext, Depends(require_csrf_public)],
) -> None:
    ctx = app_context(request)
    await revoke(ctx, current, "logout")
    if current.session.audience == "browser":
        clear_session_cookie(response, cookie_name(ctx))
