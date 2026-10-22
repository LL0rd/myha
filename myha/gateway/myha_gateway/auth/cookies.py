"""The browser session cookie (design E25).

``__Host-`` makes the browser insist on ``Secure``, ``Path=/`` and no ``Domain``, so the cookie
is bound to the exact host; the instance's short id in the name separates several instances on
one host, because browsers do not separate cookies by port.
"""

import math
from datetime import UTC, datetime

from starlette.responses import Response


def session_cookie_name(short_id: str) -> str:
    """``__Host-myha_<short_id>_session`` for the instance's 8-character short id."""
    return f"__Host-myha_{short_id}_session"


def set_session_cookie(response: Response, name: str, token: str, expires_at: datetime) -> None:
    """Set the session cookie so that the browser drops it at ``expires_at``."""
    max_age = max(0, math.ceil((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        name, token, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )


def clear_session_cookie(response: Response, name: str) -> None:
    """Expire the session cookie; the attributes must match, or ``__Host-`` rejects it."""
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")
