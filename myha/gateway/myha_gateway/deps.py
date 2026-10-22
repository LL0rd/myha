"""Process-wide resources, created once and shared by the admin and the public app."""

from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from myha_gateway.db import create_engine, session_factory
from myha_gateway.db.models import Instance
from myha_gateway.ha.client import HAClient
from myha_gateway.ha.supervisor import SupervisorClient
from myha_gateway.settings import Settings


async def _unused(update: object) -> None:
    """HA events and status changes go nowhere until the event bus subscribes (task 6.3)."""


@dataclass
class AppContext:
    """Shared backend context; event bus and policy join in later tasks."""

    settings: Settings
    supervisor: SupervisorClient
    ha: HAClient  # started and stopped by the admin app's lifespan
    engine: AsyncEngine  # connects on first use, after the admin lifespan has migrated
    sessions: async_sessionmaker[AsyncSession]  # reads; writes go through db.write_session
    instance: Instance | None = None  # loaded at startup; services that change it replace it

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
        return cls(settings, supervisor, ha, engine, session_factory(engine))

    async def close(self) -> None:
        """Close the database connections; the engine would reopen them on use."""
        await self.engine.dispose()

    @property
    def public_ready(self) -> bool:
        """Whether the public listener may run; WP4 adds the instance configuration."""
        return self.settings.public_enabled
