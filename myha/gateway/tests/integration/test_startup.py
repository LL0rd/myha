"""Startup order, the public listener switch and the listener supervision of ``serve``."""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

import myha_gateway.__main__ as gateway
from myha_gateway.app import _log_supervisor, create_admin_app
from myha_gateway.db import ensure_instance, run_migrations, write_session
from myha_gateway.db.models import Instance
from myha_gateway.deps import AppContext, _is_origin
from myha_gateway.settings import Settings
from tests.helpers import fake_supervisor

OVERRIDE = {"public_enabled": True, "public_origin": "http://guest.test:8443"}
CIDRS = ["10.0.0.0/8"]
LISTENING = re.compile(r"Uvicorn running on http://127\.0\.0\.1:(\d+)")


def closed(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def port_of(server: uvicorn.Server) -> int:
    return int(server.servers[0].sockets[0].getsockname()[1])


def local(data_dir: Path, **fields: Any) -> Settings:
    """Both listeners on free ports of 127.0.0.1; the Supervisor refuses at once."""
    defaults = {
        "admin_bind": "127.0.0.1",
        "admin_port": 0,
        "public_bind": "127.0.0.1",
        "public_port": 0,
        "supervisor_url": "http://127.0.0.1:1",
    }
    return Settings(data_dir=data_dir, **(defaults | fields))


def gateway_env(data_dir: Path, **variables: str) -> dict[str, str]:
    """``local`` for a ``myha-gateway serve`` subprocess."""
    return {
        **os.environ,
        "MYHA_DATA_DIR": str(data_dir),
        "MYHA_ADMIN_BIND": "127.0.0.1",
        "MYHA_ADMIN_PORT": "0",
        "MYHA_PUBLIC_BIND": "127.0.0.1",
        "MYHA_PUBLIC_PORT": "0",
        "MYHA_SUPERVISOR_URL": "http://127.0.0.1:1",
        **variables,
    }


def self_signed(
    directory: Path, name: str = "guest", passphrase: str | None = None
) -> tuple[Path, Path]:
    """A throwaway certificate and key from the openssl command line tool."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl not installed")
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    protection = ["-nodes"] if passphrase is None else ["-passout", f"pass:{passphrase}"]
    subprocess.run(  # noqa: S603 (fixed arguments)
        [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256"]
        + [*protection, "-subj", "/CN=guest.test", "-days", "1"]
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
async def started(settings: Settings, supervisor: Any = None) -> AsyncIterator[AppContext]:
    """The admin lifespan alone, as uvicorn runs it before the admin listener opens."""
    ctx = await AppContext.create(settings)
    ctx.supervisor = supervisor or fake_supervisor()
    app = create_admin_app(ctx)
    async with app.router.lifespan_context(app):
        yield ctx


async def until(condition: Callable[[], object], seconds: float = 10) -> None:
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def wait_out_the_public_decision() -> None:
    """``_serve`` polls the admin start every 0.1 s and decides on the public server at once."""
    await asyncio.sleep(0.3)


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
        await wait_out_the_public_decision()
        assert len(servers) == 1  # no public server, not even a failed one
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
        r = await healthz(f"http://127.0.0.1:{port_of(servers[1])}")
        assert r.status_code == 200 and r.json()["client_ip"] == "127.0.0.1"


async def test_restore_export_keeps_public_closed_and_creates_no_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = local(tmp_path, **OVERRIDE)  # would open the public listener without the export
    settings.backup_dir.mkdir()
    (settings.backup_dir / "restore.sqlite").write_bytes(b"")
    async with serving(settings, monkeypatch) as servers:
        await wait_out_the_public_decision()
        ctx = ctx_of(servers[0])
        assert ctx.restore_mode and not ctx.public_ready and ctx.public_origin is None
        assert ctx.instance is None and len(servers) == 1
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
        r = await healthz(f"https://127.0.0.1:{port_of(servers[1])}")
        assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ("missing key", "No such file or directory"),
        ("key of another certificate", "KEY_VALUES_MISMATCH"),
        # OpenSSL would ask for the passphrase on the terminal and hold up the startup.
        ("encrypted key", "the private key is encrypted; myHA needs an unencrypted key"),
    ],
)
async def test_tls_mode_with_unusable_key_keeps_public_closed(
    broken: str, reason: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cert, key = self_signed(tmp_path)
    if broken == "missing key":
        key.unlink()
    elif broken == "key of another certificate":
        key = self_signed(tmp_path, "other")[1]
    else:
        cert, key = self_signed(tmp_path, "locked", passphrase="test-passphrase")
    settings = local(tmp_path)
    await configure(
        settings,
        public_origin="https://guest.test",
        tls_mode="tls",
        tls_cert_path=str(cert),
        tls_key_path=str(key),
    )
    async with asyncio.timeout(10), started(settings) as ctx:
        assert not ctx.public_ready and ctx.public_origin is None and ctx.public_tls is None
    off = [r for r in caplog.records if r.getMessage().startswith("public listener off")]
    assert len(off) == 1 and off[0].levelno == logging.WARNING
    assert "certificate or key not usable: " in off[0].getMessage()
    assert reason in off[0].getMessage()
    assert "PRIVATE KEY" not in caplog.text and "CERTIFICATE" not in caplog.text


@pytest.mark.parametrize(
    ("origin", "mode", "cidrs", "reason"),
    [
        ("https://guest.example", "proxy", CIDRS, None),
        ("https://guest.example:8443", "proxy", CIDRS, None),
        (None, "proxy", CIDRS, "no public origin configured"),
        ("https://guest.example", "proxy", [], "proxy mode without proxy CIDRs"),
        ("https://guest.example", None, CIDRS, "TLS mode None is neither tls nor proxy"),
        ("https://guest.example", "tls", CIDRS, "certificate or key path missing"),
        ("http://guest.example", "proxy", CIDRS, "is not https://host[:port] as browsers"),
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
    ("cert", "key"), [(None, None), ("/ssl/fullchain.pem", None), (None, "/ssl/privkey.pem")]
)
async def test_tls_mode_needs_both_paths(cert: str | None, key: str | None, tmp_path: Path) -> None:
    ctx = await AppContext.create(Settings(data_dir=tmp_path))
    ctx.instance = Instance(
        public_origin="https://guest.example",
        tls_mode="tls",
        tls_cert_path=cert,
        tls_key_path=key,
        proxy_cidrs=[],
    )
    assert ctx.refresh_public() == "certificate or key path missing" and not ctx.public_ready


async def test_user_info_in_the_origin_never_reaches_the_log(tmp_path: Path) -> None:
    ctx = await AppContext.create(Settings(data_dir=tmp_path))
    origin = "https://admin:s3cret@guest.example"
    ctx.instance = Instance(public_origin=origin, tls_mode="proxy", proxy_cidrs=CIDRS)
    reason = ctx.refresh_public()
    assert reason is not None and "s3cret" not in reason and not ctx.public_ready


async def test_settings_override_beats_a_tls_instance(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    ctx = await AppContext.create(Settings(data_dir=tmp_path, **OVERRIDE))
    ctx.instance = Instance(  # files that do not exist: the override never looks at them
        public_origin="https://guest.example",
        tls_mode="tls",
        tls_cert_path=str(tmp_path / "missing.crt"),
        tls_key_path=str(tmp_path / "missing.key"),
        proxy_cidrs=[],
    )
    assert ctx.refresh_public() is None
    assert ctx.public_ready and ctx.public_tls is None
    assert ctx.public_origin == "http://guest.test:8443"
    [line] = [r for r in caplog.records if "settings override" in r.getMessage()]
    assert line.levelno == logging.INFO and "http://guest.test:8443" in line.getMessage()


@pytest.mark.parametrize(
    ("origin", "ready"), [("http://guest.test", True), ("http://guest.test/join", False)]
)
async def test_override_allows_plain_http_but_needs_an_origin(
    origin: str, ready: bool, tmp_path: Path
) -> None:
    settings = Settings(data_dir=tmp_path, public_enabled=True, public_origin=origin)
    ctx = await AppContext.create(settings)  # decided without the instance, before the lifespan
    assert ctx.public_ready is ready and ctx.public_origin == (origin if ready else None)


@pytest.mark.parametrize(
    ("origin", "allow_http"),
    [
        ("https://guest.example", False),
        ("https://guest.example:8443", False),
        ("https://xn--gst-qla.example", False),  # punycode, as browsers send it
        ("https://[2001:db8::1]:8443", False),
        ("http://127.0.0.1:8443", True),
    ],
)
def test_browser_origins_pass(origin: str, allow_http: bool) -> None:
    assert _is_origin(origin, allow_http=allow_http)


@pytest.mark.parametrize(
    ("origin", "allow_http"),
    [
        ("https://Guest.Example", False),  # browsers send lowercase
        ("https://guest.example:443", False),  # and never the default port
        ("http://127.0.0.1:80", True),
        ("https://guest.example:0443", False),
        ("https://guest.example:", False),
        ("https://guest.example:99999", False),
        ("https://guest .example", False),
        (" https://guest.example", False),
        ("https://guest\t.example", False),  # urlsplit would drop the tab
        ("https://guest\x00.example", False),
        ("https://%67uest.example", False),
        ("https://*.example", False),
        ("https://gäst.example", False),  # browsers send punycode
        ("https://guest.example.", False),
        ("https://[2001:0db8:0:0:0:0:0:1]:8443", False),  # browsers compress IPv6
        ("https://[2001:DB8::1]", False),
        ("https://[fe80::1%25eth0]", False),  # no zone in a URL
        ("https://[::1", False),
        ("https://guest.example/", False),
        ("https://guest.example/join", False),
        ("https://guest.example?x=1", False),
        ("https://guest.example#x", False),
        ("https://user:pass@guest.example", False),
        ("http://127.0.0.1:8443", False),  # plain http only where allowed
        ("ftp://guest.example", False),
        ("guest.example", False),
        ("https://", False),
    ],
)
def test_other_spellings_fail(origin: str, allow_http: bool) -> None:
    assert not _is_origin(origin, allow_http=allow_http)


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
        assert (await healthz(f"http://127.0.0.1:{port_of(servers[1])}")).status_code == 200
        assert await answers(admin) and not admin.should_exit and servers[0] is admin
    assert len(attempts) == 2 and attempts[1] - attempts[0] >= 0.3
    failed = [r for r in caplog.records if r.getMessage().startswith("public listener failed")]
    assert len(failed) == 1 and failed[0].levelno == logging.ERROR and failed[0].exc_info
    assert "RuntimeError('first attempt fails')" in failed[0].getMessage()


async def test_public_bind_conflict_keeps_the_admin_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gateway, "PUBLIC_RESTART", (0.05, 0.1))

    def failures() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.getMessage().startswith("public listener failed")]

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        settings = local(tmp_path, public_port=blocker.getsockname()[1], **OVERRIDE)
        async with serving(settings, monkeypatch) as servers:
            # uvicorn ends its startup with SystemExit(3); the attempts go on.
            await until(lambda: len(failures()) >= 3)
            assert await answers(servers[0]) and not servers[0].should_exit
            blocker.close()  # the port frees up: a later attempt takes it
            await until(lambda: len(servers) > 2 and servers[-1].started)
            assert (await healthz(f"http://127.0.0.1:{settings.public_port}")).status_code == 200
    first, *again = failures()
    assert first.levelno == logging.ERROR and "SystemExit(3)" in first.getMessage()
    assert not first.exc_info  # uvicorn has logged the cause: no traceback of its sys.exit
    assert again and all(r.levelno == logging.WARNING and not r.exc_info for r in again)
    assert "never retrieved" not in caplog.text


def test_admin_startup_failure_leaves_main_with_its_exit_code(tmp_path: Path) -> None:
    (tmp_path / "state" / "myha.sqlite").mkdir(parents=True)  # unusable: uvicorn exits with 3
    run = subprocess.run(
        [sys.executable, "-W", "error", "-m", "myha_gateway", "serve"],
        env=gateway_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = run.stdout + run.stderr
    assert run.returncode == 3, output
    assert "startup aborted" in output
    assert "never retrieved" not in output and "Exception ignored" not in output


def test_sigterm_closes_the_public_port_first_and_ends_by_the_signal(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    env = gateway_env(tmp_path, MYHA_PUBLIC_ENABLED="1", MYHA_PUBLIC_ORIGIN="http://guest.test")
    with log.open("w") as out:
        process = subprocess.Popen(
            [sys.executable, "-m", "myha_gateway", "serve"],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while len(ports := LISTENING.findall(log.read_text())) < 2:  # admin first, then public
            assert process.poll() is None and time.monotonic() < deadline, log.read_text()
            time.sleep(0.05)
        listeners = {"admin": int(ports[0]), "public": int(ports[1])}
        process.send_signal(signal.SIGTERM)  # this process only, never by name
        closed_at: dict[str, float] = {}
        while len(closed_at) < 2 and time.monotonic() < deadline + 10:
            for name, port in listeners.items():
                if name not in closed_at and closed(port):
                    closed_at[name] = time.monotonic()
            time.sleep(0.005)
        assert process.wait(timeout=10) == -signal.SIGTERM, log.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert closed_at["public"] < closed_at["admin"], closed_at


class BrokenSupervisor:
    async def info(self) -> None:
        raise RuntimeError("unexpected answer")


async def test_a_failing_supervisor_check_never_stops_the_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with started(local(tmp_path, **OVERRIDE), BrokenSupervisor()) as ctx:
        assert ctx.public_ready
    [failed] = [r for r in caplog.records if r.getMessage() == "Supervisor version check failed"]
    assert failed.levelno == logging.ERROR and failed.exc_info


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
    await _log_supervisor(ctx)
    [record] = [r for r in caplog.records if r.name == "myha_gateway.app"]
    assert record.levelno == level and record.getMessage().startswith(message)
    assert "sup-secret" not in caplog.text
