"""Process-wide resources, created once and shared by the admin and the public app."""

import ssl
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from myha_gateway.db import create_engine, session_factory
from myha_gateway.db.models import Instance
from myha_gateway.ha.client import HAClient
from myha_gateway.ha.supervisor import SupervisorClient
from myha_gateway.settings import Settings


async def _unused(update: object) -> None:
    """HA events and status changes go nowhere until the event bus subscribes (task 6.3)."""


def _is_origin(origin: str, *, allow_http: bool) -> bool:
    """``https://host[:port]`` exactly as browsers send ``Origin``; ``http`` only if allowed."""
    try:
        parts = urlsplit(origin)
        parts.port  # noqa: B018 (raises ValueError when malformed or out of range)
    except ValueError:
        return False
    schemes = ("https", "http") if allow_http else ("https",)
    # Equal to its own scheme://netloc: no path, query, fragment, trailing slash or user.
    exact = origin == f"{parts.scheme}://{parts.netloc}" and "@" not in parts.netloc
    return exact and parts.scheme in schemes and bool(parts.hostname)


@dataclass
class AppContext:
    """Shared backend context; event bus and policy join in later tasks."""

    settings: Settings
    supervisor: SupervisorClient
    ha: HAClient  # started and stopped by the admin app's lifespan
    engine: AsyncEngine  # connects on first use, after the admin lifespan has migrated
    sessions: async_sessionmaker[AsyncSession]  # reads; writes go through db.write_session
    instance: Instance | None = None  # loaded at startup; services that change it replace it
    restore_mode: bool = False  # backup_dir/restore.sqlite was there at startup (design 4.6)
    # Set by refresh_public(). public_origin is the only source for origin checks and join
    # URLs; it is None while the public listener stays off.
    public_ready: bool = False
    public_origin: str | None = None
    public_tls: tuple[str, str] | None = None  # certificate and key file when serving HTTPS

    @classmethod
    async def create(cls, settings: Settings | None = None) -> Self:
        """Create the context once per process; async for the resources that follow."""
        settings = Settings() if settings is None else settings
        token = settings.supervisor_token
        # The Supervisor token is also the Core token behind ws://supervisor/core/websocket.
        ha_token = "" if token is None else token.get_secret_value()
        ha = HAClient(settings.ha_ws_url, ha_token, on_event=_unused, on_status=_unused)
        engine = create_engine(settings.db_path)
        supervisor = SupervisorClient(settings.supervisor_url, token)
        ctx = cls(settings, supervisor, ha, engine, session_factory(engine))
        ctx.refresh_public()  # the settings override only; the admin lifespan decides again
        return ctx

    async def close(self) -> None:
        """Close the database connections; the engine would reopen them on use."""
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
        if settings.public_enabled:  # dev/test override: plain HTTP, the instance is ignored
            origin = settings.public_origin or ""
            if not _is_origin(origin, allow_http=True):
                return f"MYHA_PUBLIC_ORIGIN {origin!r} is not http(s)://host[:port]"
        else:
            if instance is None or not instance.public_origin:
                return "no public origin configured"
            origin = instance.public_origin
            if not _is_origin(origin, allow_http=False):
                return f"public origin {origin!r} is not https://host[:port]"
            if instance.tls_mode == "tls":
                cert, key = instance.tls_cert_path, instance.tls_key_path
                if not (cert and key):
                    return "certificate or key path missing"
                try:  # what uvicorn does with ssl_certfile and ssl_keyfile
                    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
                except (OSError, ValueError) as exc:  # SSLError is an OSError; no file content
                    return f"certificate or key not usable: {exc}"
                tls = (cert, key)
            elif instance.tls_mode != "proxy":
                return f"TLS mode {instance.tls_mode!r} is neither tls nor proxy"
            elif not instance.proxy_cidrs:  # design 3: proxy mode needs its trusted CIDRs
                return "proxy mode without proxy CIDRs"
        self.public_ready, self.public_origin, self.public_tls = True, origin, tls
        return None
