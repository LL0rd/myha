import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import myha_gateway.__main__ as gateway
from myha_gateway.app import create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.logsetup import setup_logging
from myha_gateway.settings import Settings


@pytest.fixture
def root_logging() -> Iterator[None]:
    """Let the test reconfigure the root logger; restore it afterwards."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


async def crash() -> None:
    raise RuntimeError("Bearer sup-secret")


async def test_real_server_config_and_redacted_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_logging: None,
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    app = create_public_app(await AppContext.create())
    app.get("/crash")(crash)
    setup_logging("info")
    server = gateway._server(app, "127.0.0.1", 0)
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:  # noqa: ASYNC110 (uvicorn has no startup event)
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        health = await client.get(
            "/healthz?token=SECRETVALUE123", headers={"X-Forwarded-For": "203.0.113.7"}
        )
        crashed = await client.get("/crash")
    server.should_exit = True
    await task

    config = server.config
    assert config.proxy_headers is False and config.server_header is False
    assert config.access_log is True and config.log_config is None
    assert config.ws_max_size == 65536 and config.timeout_graceful_shutdown == 3
    for name in ("uvicorn.access", "uvicorn.error"):  # both reach the redacting root handler
        assert logging.getLogger(name).handlers == [] and logging.getLogger(name).propagate
    assert health.json()["client_ip"] == "127.0.0.1" and "server" not in health.headers
    assert crashed.status_code == 500
    assert crashed.json() == {
        "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "Internal error"}
    }
    captured = capsys.readouterr()
    output = captured.out + captured.err  # uvicorn's own default logging would write to stdout
    access = [line for line in output.splitlines() if "uvicorn.access" in line]
    assert any("/healthz?token=<redacted>" in line for line in access)
    assert "Exception in ASGI application" in output and "RuntimeError: Bearer <redacted>" in output
    assert "SECRETVALUE123" not in output and "sup-secret" not in output


def test_gateway_crash_is_logged_redacted_and_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_logging: None,
) -> None:
    async def failing_serve(settings: Settings) -> None:
        raise RuntimeError("token=crash-secret")

    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gateway, "_serve", failing_serve)
    with pytest.raises(SystemExit) as stopped:
        gateway.main(["serve"])
    err = capsys.readouterr().err
    assert stopped.value.code == 1
    assert "gateway stopped" in err and "token=<redacted>" in err and "crash-secret" not in err
