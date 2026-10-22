from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from myha_gateway.ha.supervisor import (
    AddonSelfInfo,
    HostInfo,
    SupervisorClient,
    SupervisorInfo,
    SupervisorUnavailable,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, *, with_token: bool = True) -> SupervisorClient:
    token = SecretStr("sup-secret") if with_token else None
    return SupervisorClient("http://supervisor", token, transport=httpx.MockTransport(handler))


def ok(data: object) -> Handler:
    return lambda request: httpx.Response(200, json={"result": "ok", "data": data})


def status(code: int) -> Handler:
    return lambda request: httpx.Response(code, json={"result": "error", "message": "nope"})


async def test_info_sends_the_token_and_reads_the_versions() -> None:
    seen: list[httpx.Request] = []
    data = {
        "supervisor": "2026.10.3",
        "homeassistant": "2026.10.2",
        "channel": "stable",
        "arch": "amd64",
        "supported": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(data)(request)

    info = await client(handler).info()
    assert info == SupervisorInfo("2026.10.3", "stable", "amd64", True, "2026.10.2")
    assert str(seen[0].url) == "http://supervisor/info"
    assert seen[0].headers["authorization"] == "Bearer sup-secret"


async def test_without_token_no_authorization_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok({"supervisor": "2026.10.3"})(request)

    await client(handler, with_token=False).info()
    assert "authorization" not in seen[0].headers


def refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Name or service not known", request=request)


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (status(401), "GET /info: HTTP 401"),
        (status(500), "GET /info: HTTP 500"),
        (refuse_connection, "ConnectError: Name or service not known"),
        (lambda request: httpx.Response(200, text="<html>"), "JSONDecodeError"),
        (ok({"channel": "stable"}), "no Supervisor version"),
        (ok(["not", "an", "object"]), "data is not an object"),
    ],
)
async def test_info_failures_are_unavailable(handler: Handler, message: str) -> None:
    with pytest.raises(SupervisorUnavailable, match=message) as raised:
        await client(handler).info()
    assert "sup-secret" not in str(raised.value)


async def test_host_info_reads_time_sync_and_os() -> None:
    data = {"dt_synchronized": True, "operating_system": "Home Assistant OS 16.2"}
    assert await client(ok(data)).host_info() == HostInfo(True, "Home Assistant OS 16.2")


@pytest.mark.parametrize("code", [401, 403])
async def test_host_info_not_permitted_is_none(code: int) -> None:
    assert await client(status(code)).host_info() is None


async def test_host_info_other_errors_raise() -> None:
    with pytest.raises(SupervisorUnavailable) as raised:
        await client(status(500)).host_info()
    assert raised.value.status == 500


async def test_self_info_reads_ingress_entry() -> None:
    data = {
        "name": "myHA",
        "version": "0.1.0",
        "ingress_entry": "/api/hassio_ingress/abc",
        "ingress_url": "/api/hassio_ingress/abc/",
        "options": {"log_level": "info"},
    }
    assert await client(ok(data)).self_info() == AddonSelfInfo(
        "myHA", "0.1.0", "/api/hassio_ingress/abc", "/api/hassio_ingress/abc/"
    )


@pytest.mark.parametrize(
    ("version", "minimum", "expected"),
    [
        ("2026.10.3", "2026.10.3", True),
        ("2026.10.2", "2026.10.3", False),
        ("2026.9.9", "2026.10.0", False),
        ("2026.11.0", "2026.10.3", True),
        ("2026.10", "2026.10.0", True),
        ("2026.10", "2026.10.1", False),
        ("2026.10.", "2026.10.0", True),
        ("2026.10.4.dev2108", "2026.10.3", True),  # dev channel, after the minimum
        ("2026.10.3.dev2108", "2026.10.3", False),  # dev build before its release
        ("2026.10.3.dev2108", "2026.10.2", True),
    ],
)
def test_is_at_least_compares_numerically(version: str, minimum: str, expected: bool) -> None:
    assert SupervisorInfo(version).is_at_least(minimum) is expected


@pytest.mark.parametrize("version", ["garbage", "2026", "2026.x.1", "", "dev.2026.10"])
def test_unparsable_version_is_unavailable(version: str) -> None:
    with pytest.raises(SupervisorUnavailable, match="unparsable"):
        SupervisorInfo(version).is_at_least("2026.10.3")
