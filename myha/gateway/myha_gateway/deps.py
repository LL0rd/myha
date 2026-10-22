"""Process-wide resources, created once and shared by the admin and the public app."""

from dataclasses import dataclass
from typing import Self

from myha_gateway.ha.supervisor import SupervisorClient
from myha_gateway.settings import Settings


@dataclass
class AppContext:
    """Shared backend context; engine, HA client, event bus and policy join in later tasks."""

    settings: Settings
    supervisor: SupervisorClient

    @classmethod
    async def create(cls, settings: Settings | None = None) -> Self:
        """Create the context once per process; async for the resources that follow."""
        settings = Settings() if settings is None else settings
        return cls(settings, SupervisorClient(settings.supervisor_url, settings.supervisor_token))

    @property
    def public_ready(self) -> bool:
        """Whether the public listener may run; WP4 adds the instance configuration."""
        return self.settings.public_enabled
