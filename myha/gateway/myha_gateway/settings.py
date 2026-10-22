"""Gateway settings from MYHA_* environment variables and the add-on options file."""

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class _AddonOptions(PydanticBaseSettingsSource):
    """``<data_dir>/options.json`` written by the Supervisor; only ``log_level`` is honoured."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError  # unused: __call__ reads the whole file

    def __call__(self) -> dict[str, Any]:
        # current_state already holds data_dir from init kwargs and environment.
        default = self.settings_cls.model_fields["data_dir"].default
        path = Path(self.current_state.get("data_dir", default)) / "options.json"
        if not path.is_file():
            return {}
        options = json.loads(path.read_text(encoding="utf-8"))
        # A whitelist: other keys, such as the probe option left over from S0 builds, would fail
        # startup with extra_forbidden.
        return {key: options[key] for key in ("log_level",) if key in options}


class Settings(BaseSettings):
    """Runtime configuration; precedence: init kwargs > environment > options.json > defaults."""

    # Unknown fields fail (default extra="forbid"); errors never echo inputs like SUPERVISOR_TOKEN.
    model_config = SettingsConfigDict(env_prefix="MYHA_", hide_input_in_errors=True)

    data_dir: Path = Path("/data")
    web_dist: Path = Path("/opt/myha/web")
    admin_port: int = 8099
    public_port: int = 8443
    # Both listeners always bind to all interfaces inside the container.
    admin_bind: str = "0.0.0.0"  # noqa: S104
    public_bind: str = "0.0.0.0"  # noqa: S104
    trusted_ingress_peer: str = "172.30.32.2"
    supervisor_url: str = "http://supervisor"
    supervisor_token: SecretStr | None = Field(default=None, validation_alias="SUPERVISOR_TOKEN")
    ha_ws_url: str = "ws://supervisor/core/websocket"
    min_supervisor_version: str = "2026.10.3"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    # Dev/test override: public listener without instance configuration, plain HTTP.
    public_enabled: bool = False
    public_origin: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_path(self) -> Path:
        # .sqlite like the backup files (design 4.6): .gitignore and .dockerignore exclude it.
        return self.state_dir / "myha.sqlite"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backup"

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        # The database and backup paths must not depend on the working directory.
        return value.absolute()

    @field_validator("log_level", mode="before")
    @classmethod
    def lowercase_log_level(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_public_override(self) -> Self:
        if self.public_enabled and not self.public_origin:
            raise ValueError("MYHA_PUBLIC_ORIGIN required with MYHA_PUBLIC_ENABLED")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, _AddonOptions(settings_cls)
