"""``myha-gateway serve`` runs both listeners; ``myha-gateway probe`` checks the environment."""

import argparse
import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import uvicorn
from fastapi import FastAPI

from myha_gateway.api.admin import probe
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.ha.supervisor import SupervisorUnavailable
from myha_gateway.logsetup import setup_logging
from myha_gateway.settings import Settings

logger = logging.getLogger(__name__)
PUBLIC_RESTART = (5.0, 60.0)  # seconds before a failed public listener restarts: first, max


def _server(
    app: FastAPI, host: str, port: int, tls: tuple[str, str] | None = None
) -> uvicorn.Server:
    """uvicorn server that trusts no proxy header and logs through the redacting root handler."""
    certfile, keyfile = tls or (None, None)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
        proxy_headers=False,
        server_header=False,
        ws_max_size=65536,
        log_config=None,
        access_log=True,
        # Public, then admin stop in turn; 2 x 4 s fits the Supervisor's 10 s stop timeout.
        timeout_graceful_shutdown=4,
    )
    return uvicorn.Server(config)


def _public_server(ctx: AppContext) -> uvicorn.Server:
    """The public listener as ``ctx`` decided it: HTTPS with the instance's files, else HTTP."""
    settings = ctx.settings
    return _server(
        create_public_app(ctx), settings.public_bind, settings.public_port, ctx.public_tls
    )


async def _log_supervisor(ctx: AppContext) -> None:
    """One startup line on the Supervisor version; ``require_admin`` enforces the minimum."""
    minimum = ctx.settings.min_supervisor_version
    try:
        info = await ctx.supervisor.info()
        current = info.is_at_least(minimum)
    except SupervisorUnavailable as exc:  # its message never contains the token
        logger.warning("Supervisor unreachable (minimum %s): %s", minimum, exc)
        return
    if current:
        logger.info("Supervisor %s (minimum %s)", info.version, minimum)
    else:
        message = "Supervisor %s (minimum %s): admin access stays blocked until it is updated"
        logger.warning(message, info.version, minimum)


async def _serve(
    settings: Settings, public_server: Callable[[AppContext], uvicorn.Server] = _public_server
) -> None:
    """Run the admin listener; once it is up, the public one beside it if ``public_ready``.

    The admin server runs in this task, so its ``SystemExit`` (uvicorn's startup failure)
    leaves ``main()`` with its code. A failing public server is restarted with backoff while
    the admin listener stays up. Each ``serve()`` installs uvicorn's signal handlers and on
    exit re-raises the signal to the handler it replaced, so a signal stops the public server
    (started last) first, then the admin server.
    """
    ctx = await AppContext.create(settings)
    admin = _server(create_admin_app(ctx), settings.admin_bind, settings.admin_port)
    stop = asyncio.Event()
    running: list[uvicorn.Server] = []  # the public server while it serves

    async def public_listener() -> None:
        while not admin.started:  # polled: uvicorn has no startup event
            if stop.is_set():  # the admin server never started
                return
            await asyncio.sleep(0.1)
        await _log_supervisor(ctx)
        delay, longest = PUBLIC_RESTART
        # public_ready is decided by the admin lifespan; no restart once the admin stops.
        while ctx.public_ready and not (stop.is_set() or admin.should_exit):
            try:
                running.append(public_server(ctx))
                await running[0].serve()
                return  # stopped by a signal or by the shutdown below
            except (Exception, SystemExit) as exc:  # SystemExit: uvicorn's bind or startup error
                logger.error(
                    "public listener failed: %r; next attempt in %g s",
                    exc,
                    delay,
                    exc_info=not isinstance(exc, SystemExit),  # uvicorn logged the cause
                )
            finally:
                running.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)
            delay = min(2 * delay, longest)

    task = asyncio.create_task(public_listener())
    try:
        await admin.serve()
    finally:
        stop.set()
        for server in running:
            server.should_exit = True
        await task


def _probe(settings: Settings) -> None:
    """Print what the admin probe endpoint reports, without starting a server (S0 only)."""

    async def gather() -> dict[str, Any]:
        return await probe.details(await AppContext.create(settings))

    print(json.dumps(asyncio.run(gather()), indent=2))


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``myha-gateway`` command."""
    parser = argparse.ArgumentParser(prog="myha-gateway")
    parser.add_argument("command", choices=["serve", "probe"])
    command = parser.parse_args(argv).command
    settings = Settings()
    if command == "probe":
        _probe(settings)
        return
    setup_logging(settings.log_level)
    try:
        asyncio.run(_serve(settings))  # SystemExit from the admin server keeps its exit code
    except KeyboardInterrupt:  # Ctrl+C after graceful shutdown, like uvicorn
        pass
    except Exception:  # through the redacting handler instead of sys.excepthook
        logger.exception("gateway stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
