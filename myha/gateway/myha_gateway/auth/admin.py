"""Admin identity behind the Supervisor ingress (design E4, E5, E24).

``PeerGuardMiddleware`` turns away every other peer first. Each admin API route then depends on
``require_admin``: the peer again, exactly one ``X-Remote-User-Id``, a Supervisor of at least
the minimum version, and the user's role as Core reports it. Mutations and sensitive reads
depend on ``require_csrf`` instead, which asks Core afresh and checks the CSRF proof. Only the
S0 probe has neither: probe mode only, behind the peer guard, deleted with task 1.6.
"""

import functools
import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from starlette.datastructures import Headers

from myha_gateway.db.models import Instance
from myha_gateway.deps import AppContext
from myha_gateway.errors import ApiError
from myha_gateway.ha.client import HAError, utcnow
from myha_gateway.ha.supervisor import SupervisorInfo, SupervisorUnavailable
from myha_gateway.ids import constant_time_equal

logger = logging.getLogger(__name__)

SUPERVISOR_TTL = 60.0  # seconds a Supervisor of the minimum version is reused
SUPERVISOR_RETRY = 5.0  # seconds a too old or unreachable Supervisor is reused
ROLE_TTL = 5.0  # seconds a role Core confirmed is reused by checks that need not be fresh


@dataclass(frozen=True)
class AdminIdentity:
    user_id: str
    name: str | None  # X-Remote-User-Name
    display_name: str | None  # X-Remote-User-Display-Name
    groups_header: str | None  # X-Remote-User-Groups: logged, never used for the role


def _denied() -> ApiError:
    """The one answer to every failed check, so that it tells nothing about which one failed."""
    return ApiError(403, "UNAUTHENTICATED", "Admin access denied")


def csrf_token(secret: str, user_id: str, ingress_path: str, day: date) -> str:
    """Hex HMAC-SHA256 of ``admin-csrf|user_id|ingress_path|YYYY-MM-DD``, instance secret.

    The prefix keeps it apart from every other HMAC under the same secret.
    """
    message = f"admin-csrf|{user_id}|{ingress_path}|{day.isoformat()}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def csrf_ok(headers: Headers, secret: str, user_id: str, today: date) -> bool:
    """``X-MyHA-CSRF`` of today or yesterday, and ``Sec-Fetch-Site`` same-origin if present.

    A missing ``Sec-Fetch-Site`` passes: the custom header forces a CORS preflight, which the
    admin listener never answers.
    """
    if headers.get("sec-fetch-site", "same-origin") != "same-origin":
        return False
    sent = headers.get("x-myha-csrf", "")
    path = headers.get("x-ingress-path", "")
    days = (today, today - timedelta(days=1))
    return any(constant_time_equal(sent, csrf_token(secret, user_id, path, day)) for day in days)


def _utf8(value: str | None) -> str | None:
    """A header the Supervisor sends as UTF-8 and the ASGI server decodes as latin-1."""
    return None if value is None else value.encode("latin-1").decode("utf-8", "replace")


class AdminGuard:
    """The checks and their caches; one per admin app, kept in ``app.state.admin_guard``."""

    def __init__(
        self,
        ctx: AppContext,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.ctx = ctx
        self.clock = clock  # UTC: the day of a CSRF token
        self._monotonic = monotonic
        # Expiry and outcome of the last check: the Supervisor, or reason and message of the 503.
        self._supervisor: tuple[float, SupervisorInfo | tuple[str, str]] | None = None
        self._roles: dict[str, float] = {}  # user id -> when Core last confirmed the role
        self._denials: dict[str, float] = {}  # user id -> when Core last said no, for 5 s

    @property
    def instance(self) -> Instance:
        """The instance row; missing only while a restore awaits review (design 4.6)."""
        if self.ctx.instance is None:
            details = {"reason": "restore_pending"}
            raise ApiError(503, "UPSTREAM_UNAVAILABLE", "Restore pending", details)
        return self.ctx.instance

    async def supervisor_info(self) -> SupervisorInfo:
        """``GET /info``; 503 unless the Supervisor meets the minimum version (E4).

        A current Supervisor is reused for 60 s, a failed check for 5 s: during an outage that
        is one request and one warning per 5 s instead of one per admin request.
        """
        now = self._monotonic()
        if self._supervisor is None or now >= self._supervisor[0]:
            outcome = await self._check_supervisor()
            ttl = SUPERVISOR_TTL if isinstance(outcome, SupervisorInfo) else SUPERVISOR_RETRY
            self._supervisor = (self._monotonic() + ttl, outcome)  # from the answer
        outcome = self._supervisor[1]
        if isinstance(outcome, SupervisorInfo):
            return outcome
        reason, message = outcome
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", message, {"reason": reason})

    async def _check_supervisor(self) -> SupervisorInfo | tuple[str, str]:
        """The Supervisor if it meets the minimum version, else reason and message of the 503."""
        minimum = self.ctx.settings.min_supervisor_version
        try:
            info = await self.ctx.supervisor.info()
            if info.is_at_least(minimum):
                return info
        except SupervisorUnavailable as exc:  # its message never contains the token
            logger.warning("admin access blocked: %s", exc)
            return "supervisor_unreachable", "Supervisor unreachable"
        return "supervisor_version", f"Supervisor {info.version} is older than {minimum}"

    async def identity(self, request: Request, *, fresh: bool) -> AdminIdentity:
        """The admin behind ``request``; ``fresh`` asks Core even within the reuse window."""
        client = request.client
        if client is None or client.host != self.ctx.settings.trusted_ingress_peer:
            raise _denied()
        user_ids = request.headers.getlist("x-remote-user-id")
        if len(user_ids) != 1:
            raise _denied()
        await self.supervisor_info()
        headers = request.headers
        identity = AdminIdentity(
            user_ids[0],  # as received: it must match Core's id exactly, never re-decoded
            _utf8(headers.get("x-remote-user-name")),  # display only, like the next one
            _utf8(headers.get("x-remote-user-display-name")),
            headers.get("x-remote-user-groups"),
        )
        now = self._monotonic()
        confirmed = self._roles.get(identity.user_id)
        if fresh or confirmed is None or now - confirmed >= ROLE_TTL:
            self._roles.pop(identity.user_id, None)  # never a fallback when Core says no (A05)
            problem = await self._role_problem(identity.user_id)
            if problem is not None:
                self._deny(identity.user_id)
                logger.warning(
                    "admin access denied for user %r: %s; X-Remote-User-Groups %r",
                    identity.user_id,
                    problem,
                    identity.groups_header,
                )
                raise _denied()
            # A yes Core gave before a newer no, still in flight here, must not undo that no.
            if now > self._denials.get(identity.user_id, float("-inf")):
                self._roles[identity.user_id] = now
        return identity

    def _deny(self, user_id: str) -> None:
        """Forget the role, also a yes that came in meanwhile, and remember when Core said no.

        A no older than 5 s no longer matters: a yes from before it would have expired anyway.
        """
        now = self._monotonic()
        self._roles.pop(user_id, None)
        self._denials = {user: at for user, at in self._denials.items() if now - at < ROLE_TTL}
        self._denials[user_id] = now

    async def _role_problem(self, user_id: str) -> str | None:
        """Why Core does not list the user as active and owner or system-admin, if it does not."""
        try:
            users = await self.ctx.ha.auth_list()
        except HAError as exc:
            return f"role check failed: {exc}"
        user = next((user for user in users if user.id == user_id), None)
        if user is None:
            return "unknown to Home Assistant"
        if user.system_generated:
            return "a system-generated user"
        if not (user.is_active and (user.is_owner or "system-admin" in user.group_ids)):
            return "neither an active owner nor an active system-admin"
        return None

    def issue_csrf_token(self, request: Request, user_id: str) -> str:
        """Today's token for this user and ingress path, for the ``X-MyHA-CSRF`` header."""
        path = request.headers.get("x-ingress-path", "")
        return csrf_token(self.instance.server_secret, user_id, path, self.clock().date())


def admin_guard(request: Request) -> AdminGuard:
    guard: AdminGuard = request.app.state.admin_guard
    return guard


@functools.cache  # one callable per flag: FastAPI then runs it at most once per request
def require_admin(*, fresh: bool) -> Callable[[Request], Awaitable[AdminIdentity]]:
    """Dependency: the admin behind the request; ``fresh`` for mutations and sensitive reads."""

    async def admin(request: Request) -> AdminIdentity:
        return await admin_guard(request).identity(request, fresh=fresh)

    return admin


async def require_csrf(
    request: Request, admin: Annotated[AdminIdentity, Depends(require_admin(fresh=True))]
) -> AdminIdentity:
    """Dependency for mutations and sensitive reads: a fresh admin check plus the CSRF proof."""
    guard = admin_guard(request)
    secret = guard.instance.server_secret
    if not csrf_ok(request.headers, secret, admin.user_id, guard.clock().date()):
        logger.warning("admin request of user %r without a valid CSRF proof", admin.user_id)
        raise _denied()
    return admin
