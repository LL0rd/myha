"""No read session and no network I/O while a write session holds the SQLite write lock."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.db import (
    assert_no_write_session,
    create_engine,
    run_migrations,
    session_factory,
    write_session,
)
from myha_gateway.ha.client import HAClient
from myha_gateway.ha.supervisor import SupervisorClient


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    yield engine
    await engine.dispose()


class FakeSocket:
    """Records what is sent and answers every command at once."""

    def __init__(self, client: HAClient) -> None:
        self.client, self.sent = client, list[dict[str, Any]]()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        _, future = self.client._pending[message["id"]]
        future.set_result({"id": message["id"], "type": "result", "success": True})


async def ignore(_: Any) -> None:
    pass


def ha_client() -> tuple[HAClient, FakeSocket]:
    client = HAClient("ws://unused", "token", on_event=ignore, on_status=ignore)
    socket = FakeSocket(client)
    client._ws = socket  # type: ignore[assignment]
    return client, socket


def supervisor() -> tuple[SupervisorClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": "ok", "data": {"supervisor": "2026.10.3"}})

    client = SupervisorClient("http://supervisor", SecretStr("t"), httpx.MockTransport(handler))
    return client, requests


async def test_read_session_inside_a_write_session_is_refused(engine: AsyncEngine) -> None:
    sessions = session_factory(engine)
    async with write_session(engine):
        with pytest.raises(RuntimeError, match="read session inside a write session"):
            sessions()
    async with sessions() as session:  # fine again once the write session has ended
        assert (await session.execute(text("SELECT 1"))).scalar() == 1


async def test_ha_command_inside_a_write_session_sends_nothing(engine: AsyncEngine) -> None:
    client, socket = ha_client()
    async with write_session(engine):
        with pytest.raises(RuntimeError, match="network I/O inside a write session"):
            await client._command({"type": "ping"}, 1)
    assert socket.sent == []
    assert (await client._command({"type": "ping"}, 1))["success"] is True
    assert [message["type"] for message in socket.sent] == ["ping"]


async def test_ha_connect_inside_a_write_session_is_refused(engine: AsyncEngine) -> None:
    client, _ = ha_client()
    async with write_session(engine):
        with pytest.raises(RuntimeError, match="network I/O inside a write session"):
            await client._connection()


async def test_supervisor_call_inside_a_write_session_sends_nothing(engine: AsyncEngine) -> None:
    client, requests = supervisor()
    async with write_session(engine):
        with pytest.raises(RuntimeError, match="network I/O inside a write session"):
            await client.info()
    assert requests == []
    assert (await client.info()).version == "2026.10.3"
    assert len(requests) == 1


async def test_tasks_inherit_the_flag_only_from_where_they_were_created(
    engine: AsyncEngine,
) -> None:
    opened = asyncio.Event()

    async def check() -> None:
        assert_no_write_session("network I/O")

    async def check_once_open() -> None:
        await opened.wait()
        await check()

    outside = asyncio.create_task(check_once_open())  # created before: a context without it
    async with write_session(engine):
        opened.set()
        await outside  # runs while the write session is open, does not raise
        inside = asyncio.create_task(check())
        with pytest.raises(RuntimeError, match="network I/O inside a write session"):
            await inside
    await check()
