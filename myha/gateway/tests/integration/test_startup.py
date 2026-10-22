"""Startup order, the public listener switch and the listener supervision of ``serve``."""

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

import myha_gateway.__main__ as gateway
from myha_gateway.app import create_admin_app
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db.models import Instance
from myha_gateway.deps import AppContext
from myha_gateway.settings import Settings
from tests.helpers import fake_supervisor

OVERRIDE = {"public_enabled": True, "public_origin": "http://guest.test:8443"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def closed(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def port_of(server: uvicorn.Server) -> int:
    return int(server.servers[0].sockets[0].getsockname()[1])


def local(data_dir: Path, **fields: Any) -> Settings:
    """Both listeners on 127.0.0.1, the public one on a free port; the Supervisor refuses."""
    return Settings(
        data_dir=data_dir,
        admin_bind="127.0.0.1",
        admin_port=0,
        public_bind="127.0.0.1",
        public_port=free_port(),
        supervisor_url="http://127.0.0.1:1",
        **fields,
    )


def self_signed(directory: Path, name: str = "guest") -> tuple[Path, Path]:
    """A throwaway certificate and key from the openssl command line tool."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl not installed")
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    subprocess.run(  # noqa: S603 (fixed arguments)
        [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256"]
        + ["-nodes", "-subj", "/CN=guest.test", "-days", "1"]
        + ["-keyout", str(key), "-out", str(cert)],
        check=True,
        capture_output=True,
    )
    return cert, key


async def configure(settings: Settings, **fields: Any) -> None:
    """The instance row as the setup assistant leaves it."""
    ctx = await AppContext.create(settings)
    settings.state_dir.mkdir(parents=True)
    await run_migrations(ctx.engine)
    async with write_session(ctx.engine) as session:
        instance = await ensure_instance(session)
        for name, value in fields.items():
            setattr(instance, name, value)
    await ctx.close()


@asynccontextmanager
async def started(settings: Settings) -> AsyncIterator[AppContext]:
    """The admin lifespan alone, as uvicorn runs it before the admin listener opens."""
    ctx = await AppContext.create(settings)
    ctx.supervisor = fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        yield ctx


async def until(condition: Callable[[], object], seconds: float = 10) -> None:
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def answers(admin: uvicorn.Server) -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"http://127.0.0.1:{port_of(admin)}/")
    return r.status_code == 403  # the peer guard: listening, for the ingress peer only


async def healthz(url: str) -> httpx.Response:
    async with httpx.AsyncClient(verify=False) as client:  # noqa: S501 (self-signed test cert)
        return await client.get(f"{url}/healthz")


@asynccontextmanager
async def serving(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> AsyncIterator[list[uvicorn.Server]]:
    """``_serve`` with its admin listener up; yields its servers, the admin one first.

    The servers leave the signal handlers to pytest; leaving stops the admin server.
    """
    servers: list[uvicorn.Server] = []
    real = gateway._server

    def recording(*args: Any, **kw: Any) -> uvicorn.Server:
        server = real(*args, **kw)
        server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
        servers.append(server)
        return server

    monkeypatch.setattr(gateway, "_server", recording)
    task = asyncio.create_task(gateway._serve(settings, **kwargs))
    try:
        await until(lambda: task.done() or (servers and servers[0].started))
        assert not task.done(), task
        yield servers
    finally:
        if servers:
            servers[0].should_exit = True
        async with asyncio.timeout(10):
            await task


def ctx_of(admin: uvicorn.Server) -> AppContext:
    ctx: AppContext = admin.config.app.state.ctx
    return ctx


async def test_without_public_origin_only_the_admin_listener_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = local(tmp_path)
    settings.state_dir.mkdir()
    settings.state_dir.chmod(0o755)  # as builds with the S0 probe left it
    async with serving(settings, monkeypatch) as servers:
        assert stat.S_IMODE(settings.state_dir.stat().st_mode) == 0o700
        # Logged right before the public decision, in the same step.
        await until(lambda: "Supervisor unreachable" in caplog.text)
        assert len(servers) == 1 and closed(settings.public_port)
        ctx = ctx_of(servers[0])
        assert not ctx.public_ready and ctx.public_origin is None and ctx.instance is not None
    off = [r for r in caplog.records if r.getMessage().startswith("public listener off")]
    assert len(off) == 1 and off[0].levelno == logging.WARNING
    assert off[0].getMessage() == "public listener off: no public origin configured"


async def test_settings_override_opens_plain_http_with_its_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = local(tmp_path, **OVERRIDE)
    async with serving(settings, monkeypatch) as servers:
        await until(lambda: len(servers) == 2 and servers[1].started)
        ctx = ctx_of(servers[0])
        assert ctx.public_ready and ctx.public_tls is None
        assert ctx.public_origin == "http://guest.test:8443"  # the only origin source from now on
        r = await healthz(f"http://127.0.0.1:{settings.public_port}")
        assert r.status_code == 200 and r.json()["client_ip"] == "127.0.0.1"


async def test_restore_export_keeps_public_closed_and_creates_no_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = local(tmp_path, **OVERRIDE)  # would open the public listener without the export
    settings.backup_dir.mkdir()
    (settings.backup_dir / "restore.sqlite").write_bytes(b"")
    async with serving(settings, monkeypatch) as servers:
        await until(lambda: "Supervisor unreachable" in caplog.text)
        ctx = ctx_of(servers[0])
        assert ctx.restore_mode and not ctx.public_ready and ctx.public_origin is None
        assert ctx.instance is None and len(servers) == 1 and closed(settings.public_port)
    with closing(sqlite3.connect(settings.db_path)) as db:
        assert db.execute("SELECT count(*) FROM instance").fetchone() == (0,)
    assert "public listener off: restore mode" in caplog.text


async def test_restore_mode_loads_an_existing_instance(tmp_path: Path) -> None:
    settings = local(tmp_path)
    async with started(settings) as first:
        assert first.instance is not None and not first.restore_mode
    settings.backup_dir.mkdir()
    (settings.backup_dir / "restore.sqlite").write_bytes(b"")
    async with started(settings) as second:
        assert second.restore_mode and second.instance is not None
        assert second.instance.id == first.instance.id


async def test_tls_mode_serves_https_with_the_configured_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert, key = self_signed(tmp_path)
    settings = local(tmp_path)
    await configure(
        settings,
        public_origin="https://guest.test:8443",
        tls_mode="tls",
        tls_cert_path=str(cert),
        tls_key_path=str(key),
    )
    async with serving(settings, monkeypatch) as servers:
        ctx = ctx_of(servers[0])
        assert ctx.public_ready and ctx.public_origin == "https://guest.test:8443"
        assert ctx.public_tls == (str(cert), str(key))
        await until(lambda: len(servers) == 2 and servers[1].started)
        r = await healthz(f"https://127.0.0.1:{settings.public_port}")
        assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("broken", ["missing key", "key of another certificate"])
async def test_tls_mode_with_unusable_key_keeps_public_closed(
    broken: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cert, key = self_signed(tmp_path)
    if broken == "missing key":
        key.unlink()
    else:
        key = self_signed(tmp_path, "other")[1]
    settings = local(tmp_path)
    await configure(
        settings,
        public_origin="https://guest.test",
        tls_mode="tls",
        tls_cert_path=str(cert),
        tls_key_path=str(key),
    )
    async with started(settings) as ctx:
        assert not ctx.public_ready and ctx.public_origin is None and ctx.public_tls is None
    off = [r for r in caplog.records if r.getMessage().startswith("public listener off")]
    assert len(off) == 1 and off[0].levelno == logging.WARNING
    assert "certificate or key not usable" in off[0].getMessage()
    assert "PRIVATE KEY" not in caplog.text and "CERTIFICATE" not in caplog.text


CIDRS = ["10.0.0.0/8"]


@pytest.mark.parametrize(
    ("origin", "mode", "cidrs", "reason"),
    [
        ("https://guest.example", "proxy", CIDRS, None),
        ("https://guest.example:8443", "proxy", CIDRS, None),
        ("https://[2001:db8::1]:8443", "proxy", CIDRS, None),
        (None, "proxy", CIDRS, "no public origin configured"),
        ("https://guest.example", "proxy", [], "proxy mode without proxy CIDRs"),
        ("https://guest.example", None, CIDRS, "TLS mode None is neither tls nor proxy"),
        ("http://guest.example", "proxy", CIDRS, "is not https://host[:port]"),
        ("https://guest.example/", "proxy", CIDRS, "is not https://host[:port]"),
        ("https://guest.example/join", "proxy", CIDRS, "is not https://host[:port]"),
        ("https://admin@guest.example", "proxy", CIDRS, "is not https://host[:port]"),
        ("https://guest.example:99999", "proxy", CIDRS, "is not https://host[:port]"),
        ("guest.example", "proxy", CIDRS, "is not https://host[:port]"),
    ],
)
async def test_public_decision_from_the_instance(
    origin: str | None, mode: str | None, cidrs: list[str], reason: str | None, tmp_path: Path
) -> None:
    ctx = await AppContext.create(Settings(data_dir=tmp_path))
    ctx.instance = Instance(public_origin=origin, tls_mode=mode, proxy_cidrs=cidrs)
    got = ctx.refresh_public()
    assert got is None if reason is None else got is not None and reason in got
    assert ctx.public_ready is (reason is None) and ctx.public_tls is None
    assert ctx.public_origin == (origin if reason is None else None)


@pytest.mark.parametrize(
    ("origin", "ready"), [("http://guest.test", True), ("http://guest.test/join", False)]
)
async def test_override_allows_plain_http_but_needs_an_origin(
    origin: str, ready: bool, tmp_path: Path
) -> None:
    settings = Settings(data_dir=tmp_path, public_enabled=True, public_origin=origin)
    ctx = await AppContext.create(settings)  # decided without the instance, before the lifespan
    assert ctx.public_ready is ready and ctx.public_origin == (origin if ready else None)


async def test_public_listener_restarts_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gateway, "PUBLIC_RESTART", (0.3, 1.0))
    settings = local(tmp_path, **OVERRIDE)
    attempts: list[float] = []

    def flaky(ctx: AppContext) -> uvicorn.Server:
        attempts.append(asyncio.get_running_loop().time())
        if len(attempts) == 1:
            raise RuntimeError("first attempt fails")
        return gateway._public_server(ctx)

    async with serving(settings, monkeypatch, public_server=flaky) as servers:
        admin = servers[0]
        await until(lambda: "public listener failed" in caplog.text)
        assert await answers(admin)  # up while the public listener waits for its restart
        await until(lambda: len(servers) == 2 and servers[1].started)
        assert (await healthz(f"http://127.0.0.1:{settings.public_port}")).status_code == 200
        assert await answers(admin) and not admin.should_exit and servers[0] is admin
    assert len(attempts) == 2 and attempts[1] - attempts[0] >= 0.3
    failed = [r for r in caplog.records if r.getMessage().startswith("public listener failed")]
    assert len(failed) == 1 and failed[0].levelno == logging.ERROR
    assert "RuntimeError('first attempt fails')" in failed[0].getMessage()


async def test_public_bind_conflict_keeps_the_admin_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gateway, "PUBLIC_RESTART", (0.05, 0.1))
    settings = local(tmp_path, **OVERRIDE)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", settings.public_port))
        blocker.listen()
        async with serving(settings, monkeypatch) as servers:
            # uvicorn exits its startup with SystemExit(3); the attempts go on.
            await until(lambda: caplog.text.count("public listener failed: SystemExit(3)") >= 2)
            assert await answers(servers[0]) and not servers[0].should_exit
            blocker.close()  # the port frees up: a later attempt takes it
            await until(lambda: len(servers) > 2 and servers[-1].started)
            assert (await healthz(f"http://127.0.0.1:{settings.public_port}")).status_code == 200
    assert "never retrieved" not in caplog.text


def test_admin_startup_failure_leaves_main_with_its_exit_code(tmp_path: Path) -> None:
    (tmp_path / "state" / "myha.sqlite").mkdir(parents=True)  # unusable: uvicorn exits with 3
    env = {
        **os.environ,
        "MYHA_DATA_DIR": str(tmp_path),
        "MYHA_ADMIN_BIND": "127.0.0.1",
        "MYHA_ADMIN_PORT": "0",
        "MYHA_PUBLIC_PORT": str(free_port()),
    }
    run = subprocess.run(
        [sys.executable, "-W", "error", "-m", "myha_gateway", "serve"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = run.stdout + run.stderr
    assert run.returncode == 3, output
    assert "startup aborted" in output
    assert "never retrieved" not in output and "Exception ignored" not in output


@pytest.mark.parametrize(
    ("minimum", "reachable", "level", "message"),
    [
        ("2026.10.3", True, logging.INFO, "Supervisor 2026.10.3 (minimum 2026.10.3)"),
        ("2026.10.4", True, logging.WARNING, "Supervisor 2026.10.3 (minimum 2026.10.4): "),
        ("2026.10.3", False, logging.WARNING, "Supervisor unreachable (minimum 2026.10.3): "),
    ],
)
async def test_startup_logs_the_supervisor_version(
    minimum: str,
    reachable: bool,
    level: int,
    message: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    ctx = await AppContext.create(Settings(data_dir=tmp_path, min_supervisor_version=minimum))
    ctx.supervisor = fake_supervisor(reachable=reachable)
    await gateway._log_supervisor(ctx)
    [record] = [r for r in caplog.records if r.name == gateway.__name__]
    assert record.levelno == level and record.getMessage().startswith(message)
    assert "sup-secret" not in caplog.text
