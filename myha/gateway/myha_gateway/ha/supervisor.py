"""Supervisor REST client: version gate, host time source and the app's own ingress data."""

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr


class SupervisorUnavailable(Exception):
    """Unreachable, refused (``status``), or an answer without the expected data."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _version(text: str) -> tuple[int, ...]:
    """``YYYY.MM.P`` as a tuple of three ints; a missing patch counts as 0."""
    parts = text.split(".")
    if len(parts) not in (2, 3) or not all(part.isdecimal() for part in parts):
        raise SupervisorUnavailable(f"unparsable Supervisor version {text!r}")
    return tuple(int(part) for part in parts) + (0,) * (3 - len(parts))


@dataclass(frozen=True)
class SupervisorInfo:
    version: str
    channel: str | None
    arch: str | None
    healthy: bool | None  # only /supervisor/info has it, which needs hassio_api
    supported: bool | None

    def is_at_least(self, minimum: str) -> bool:
        """Numeric comparison, so 2026.9.9 is older than 2026.10.0."""
        return _version(self.version) >= _version(minimum)


@dataclass(frozen=True)
class HostInfo:
    dt_synchronized: bool | None
    operating_system: str | None


@dataclass(frozen=True)
class AddonSelfInfo:
    name: str | None
    version: str | None
    ingress_entry: str | None
    ingress_url: str | None


class SupervisorClient:
    """Read-only GETs with the app's own token; every failure is ``SupervisorUnavailable``."""

    def __init__(
        self,
        base_url: str,
        token: SecretStr | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._transport = transport  # tests pass an httpx.MockTransport

    async def _data(self, path: str) -> dict[str, Any]:
        """The ``data`` object of a Supervisor answer."""
        token = self._token
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        try:
            # One short-lived client per call: rare calls, and nothing to close on shutdown.
            async with httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=5, transport=self._transport
            ) as client:
                response = await client.get(path)
            response.raise_for_status()
            data = response.json()["data"]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise SupervisorUnavailable(f"GET {path}: HTTP {status}", status) from exc
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, LookupError, TypeError) as exc:
            raise SupervisorUnavailable(f"GET {path}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(data, dict):
            raise SupervisorUnavailable(f"GET {path}: data is not an object")
        return data

    async def info(self) -> SupervisorInfo:
        """``GET /info``: open to every app token, no ``hassio_api`` needed."""
        data = await self._data("/info")
        if not isinstance(data.get("supervisor"), str):
            raise SupervisorUnavailable("GET /info: no Supervisor version")
        return SupervisorInfo(
            data["supervisor"],
            data.get("channel"),
            data.get("arch"),
            data.get("healthy"),
            data.get("supported"),
        )

    async def host_info(self) -> HostInfo | None:
        """``GET /host/info``; ``None`` when the app may not read it (401/403)."""
        try:
            data = await self._data("/host/info")
        except SupervisorUnavailable as exc:
            if exc.status in (401, 403):
                return None
            raise
        return HostInfo(data.get("dt_synchronized"), data.get("operating_system"))

    async def self_info(self) -> AddonSelfInfo:
        """``GET /addons/self/info``: the app's own name, version and ingress paths."""
        data = await self._data("/addons/self/info")
        return AddonSelfInfo(
            data.get("name"),
            data.get("version"),
            data.get("ingress_entry"),
            data.get("ingress_url"),
        )
