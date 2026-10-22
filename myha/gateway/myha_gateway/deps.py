"""Process-wide resources, created once and shared by the admin and the public app."""

import ipaddress
import logging
import re
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from myha_gateway.auth.ratelimit import SlidingWindowLimiter
from myha_gateway.db import create_engine, session_factory
from myha_gateway.db.models import Instance
from myha_gateway.events.bus import EventBus, PolicyChanged
from myha_gateway.ha.client import HAClient
from myha_gateway.ha.messages import ConnectionStatus, StateChanged
from myha_gateway.ha.supervisor import SupervisorClient
from myha_gateway.policy.loader import PolicyLoader
from myha_gateway.services.audit import AuditEvent, audit
from myha_gateway.services.timeguard import TimeGuard
from myha_gateway.settings import Settings

logger = logging.getLogger(__name__)
_DNS_NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


async def _fan_out[T](handlers: list[Callable[[T], Awaitable[None]]], update: T) -> None:
    """Hand an HA event or status change to each handler in order: loader, then event bus.

    A failing handler is logged and skips none of the others.
    """
    for handler in handlers:
        try:
            await handler(update)
        except Exception:
            logger.exception("a Home Assistant %s handler failed", type(update).__name__)


async def _audit_policy(
    session: AsyncSession,
    operation: str,
    resource_id: str | None,
    policy_version: int,
    params: dict[str, Any],
) -> None:
    """Audit row of a binding or policy change that the system made, not an admin."""
    event = AuditEvent(
        actor="system",
        operation=operation,
        result="ok",
        resource_id=resource_id,
        policy_version=policy_version,
        normalized_params=params,
    )
    await audit(session, event)


def _is_origin(origin: str, *, allow_http: bool) -> bool:
    """``https://host[:port]`` exactly as browsers serialize ``Origin``; ``http`` only if allowed.

    Origin checks compare it byte for byte and join URLs start with it, so only the canonical
    form passes: lowercase ASCII host, compressed IPv6, no default port, nothing after it.
    """
    try:
        parts = urlsplit(origin)
        port = parts.port
        host = parts.hostname or ""
        if ":" in host:  # IPv6: zones and IPv4-mapped forms never equal what browsers send
            address = ipaddress.IPv6Address(host)
            host = "" if address.scope_id or address.ipv4_mapped else f"[{address.compressed}]"
    except ValueError:
        return False
    default = {"https": 443, "http": 80 if allow_http else None}.get(parts.scheme)
    named = bool(_DNS_NAME.fullmatch(host)) or host.startswith("[")
    canonical = f"{parts.scheme}://{host}" + ("" if port in (None, default) else f":{port}")
    return default is not None and named and origin == canonical


def _origin_problem(origin: str, *, allow_http: bool) -> str | None:
    """Why ``origin`` cannot be the public origin, or ``None``."""
    if "@" in origin:  # user info may carry a password: never echo such an origin
        return "public origin contains '@' (user info is not allowed)"
    if not _is_origin(origin, allow_http=allow_http):
        form = "http(s)" if allow_http else "https"
        return f"public origin {origin!r} is not {form}://host[:port] as browsers send it"
    return None


def _limiters() -> dict[str, SlidingWindowLimiter]:
    """Per client IP: ``config`` 60/min for ``public/config``, ``redeem`` 10/min (spec §12.1)."""
    # shortcut: a flood of addresses past max_keys evicts keys and so resets their windows;
    # harmless while redeem guards 256-bit secrets, revisit for guessable ones (login, Plan 3)
    return {"config": SlidingWindowLimiter(60, 60), "redeem": SlidingWindowLimiter(10, 60)}


def _no_password() -> bytes:
    """Asked for an encrypted key's passphrase instead of OpenSSL's terminal prompt."""
    raise ValueError("the private key is encrypted; myHA needs an unencrypted key")


@dataclass
class AppContext:
    """Shared backend context."""

    settings: Settings
    supervisor: SupervisorClient
    ha: HAClient  # started and stopped by the admin app's lifespan
    engine: AsyncEngine  # connects on first use, after the admin lifespan has migrated
    sessions: async_sessionmaker[AsyncSession]  # reads; writes go through db.write_session
    policy: PolicyLoader  # index, contexts and drift; its worker starts with the HA connection
    bus: EventBus  # live events for the client connections
    instance: Instance | None = None  # loaded at startup; services that change it replace it
    restore_mode: bool = False  # backup_dir/restore.sqlite was there at startup (design 4.6)
    # Set by refresh_public(). public_origin is the only source for origin checks and join
    # URLs; it is None while the public listener stays off.
    public_ready: bool = False
    public_origin: str | None = None
    public_tls: tuple[str, str] | None = None  # certificate and key file when serving HTTPS
    timeguard: TimeGuard | None = None  # set by the admin lifespan once the instance row exists
    limiters: dict[str, SlidingWindowLimiter] = field(default_factory=_limiters)  # tests swap

    @classmethod
    async def create(cls, settings: Settings | None = None) -> Self:
        """Create the context once per process; async for the resources that follow."""
        settings = Settings() if settings is None else settings
        token = settings.supervisor_token
        # The Supervisor token is also the Core token behind ws://supervisor/core/websocket.
        ha_token = "" if token is None else token.get_secret_value()
        on_event: list[Callable[[StateChanged], Awaitable[None]]] = []
        on_status: list[Callable[[ConnectionStatus], Awaitable[None]]] = []
        bus = EventBus()
        ha = HAClient(
            settings.ha_ws_url,
            ha_token,
            on_event=lambda change: _fan_out(on_event, change),
            on_status=lambda status: _fan_out(on_status, status),
        )
        engine = create_engine(settings.db_path)
        policy = PolicyLoader(engine, ha, audit=_audit_policy)
        on_event.append(policy.on_event)
        on_status.append(policy.on_status)
        policy.on_invalidate = lambda: bus.publish(PolicyChanged())  # after each policy commit
        on_event.append(bus.on_event)  # after the loader: connections use the index it updated
        on_status.append(bus.on_status)
        supervisor = SupervisorClient(settings.supervisor_url, token)
        ctx = cls(settings, supervisor, ha, engine, session_factory(engine), policy, bus)
        ctx.refresh_public()  # the settings override only; the admin lifespan decides again
        return ctx

    async def close(self) -> None:
        """Stop the drift worker and close the database connections."""
        await self.policy.close()
        await self.engine.dispose()

    def refresh_public(self) -> str | None:
        """Decide the public listener from settings and instance; returns why it stays off.

        The admin lifespan decides once at startup. The setup assistant (Plan 3) calls this
        again after changing origin or TLS; starting or stopping the running public listener
        on such a change does not exist yet.
        """
        self.public_ready, self.public_origin, self.public_tls = False, None, None
        settings, instance, tls = self.settings, self.instance, None
        if self.restore_mode:
            return "restore mode, the restore awaits review in the admin UI"
        if settings.public_enabled:  # dev/test override: plain HTTP
            origin = settings.public_origin or ""
            if problem := _origin_problem(origin, allow_http=True):
                return problem
            if instance is not None:  # the startup decision; create() has no instance yet
                message = "settings override: public listener on plain HTTP for %s; %s"
                logger.info(message, origin, "the instance's public settings do not apply")
        else:
            if instance is None or not instance.public_origin:
                return "no public origin configured"
            origin = instance.public_origin
            if problem := _origin_problem(origin, allow_http=False):
                return problem
            if instance.tls_mode == "tls":
                cert, key = instance.tls_cert_path, instance.tls_key_path
                if not (cert and key):
                    return "certificate or key path missing"
                try:  # what uvicorn does with ssl_certfile and ssl_keyfile, minus the prompt
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(cert, key, password=_no_password)
                except (OSError, ValueError) as exc:  # SSLError is an OSError; no file content
                    return f"certificate or key not usable: {exc}"
                tls = (cert, key)
            elif instance.tls_mode != "proxy":
                return f"TLS mode {instance.tls_mode!r} is neither tls nor proxy"
            else:
                # shortcut: without the CIDR peer guard a proxy-mode listener would answer
                # everyone, so it stays closed; Plan 3 Task 11.2 adds the guard and opens it
                return "proxy mode is not available yet"
        self.public_ready, self.public_origin, self.public_tls = True, origin, tls
        return None
