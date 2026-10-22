"""``myha-gateway serve`` runs both listeners."""

import argparse
import asyncio
import contextlib
import logging
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.limits import HeaderDeadlineProtocol
from myha_gateway.logsetup import setup_logging
from myha_gateway.settings import Settings

logger = logging.getLogger(__name__)
PUBLIC_RESTART = (5.0, 60.0)  # seconds before a failed public listener restarts: first, max


def _server(
    app: FastAPI,
    host: str,
    port: int,
    tls: tuple[str, str] | None = None,
    limit_concurrency: int | None = None,
    http: type[asyncio.Protocol] | str = "h11",
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
        # h11 caps an incomplete header block at 16 KiB: 400 and a close.
        # shortcut: a block that arrives complete in one read (up to 256 KiB) still passes;
        # cap it in the protocol if a large single read ever matters
        http=http,
        h11_max_incomplete_event_size=16 * 1024,
        limit_concurrency=limit_concurrency,  # beyond: 503 instead of unbounded tasks
        timeout_keep_alive=5,
        ws_max_size=65536,
        log_config=None,
        access_log=True,
        # Public, then admin stop in turn: 2 x 3 s plus up to 2 s for closing the HA socket
        # stays below the 10 s after which Docker kills the app (8.4 s at worst).
        timeout_graceful_shutdown=3,
    )
    return uvicorn.Server(config)


def _public_server(ctx: AppContext) -> uvicorn.Server:
    """The public listener as ``ctx`` decided it: HTTPS with the instance's files, else HTTP."""
    settings = ctx.settings
    app = create_public_app(ctx)
    # Public only: on the admin listener, foreign peers could fill the limit with half-open
    # connections and lock the ingress out with 503 before the peer guard runs. Here the
    # header deadline frees the slots that half-open connections take.
    return _server(
        app,
        settings.public_bind,
        settings.public_port,
        ctx.public_tls,
        limit_concurrency=200,
        http=HeaderDeadlineProtocol,
    )


async def _serve(
    settings: Settings, public_server: Callable[[AppContext], uvicorn.Server] = _public_server
) -> None:
    """Run the admin listener; once it is up, the public one beside it if ``public_ready``.

    The admin server runs in this task, so its ``SystemExit`` (uvicorn's startup failure)
    leaves ``main()`` with its code. A failing public server is restarted with backoff while
    the admin listener stays up. Each ``serve()`` installs uvicorn's signal handlers and on
    exit re-raises the signal to the handler it replaced, so a signal stops the public server
    (started last) first, then the admin server. Setting ``should_exit`` on the admin server
    instead (tests) stops the public server only after the admin lifespan has shut down;
    starting and stopping the public listener at runtime comes with Plan 3.
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
        delay, longest = PUBLIC_RESTART
        failed = False
        # public_ready is decided by the admin lifespan; no restart once the admin stops.
        while ctx.public_ready and not (stop.is_set() or admin.should_exit):
            try:
                running.append(public_server(ctx))
                await running[0].serve()
                return  # stopped by a signal or by the shutdown below
            except (Exception, SystemExit) as exc:  # SystemExit: uvicorn's bind or startup error
                if failed:  # the first failure of the series carried the details
                    message = "public listener failed again: %r; next attempt in %g s"
                    logger.warning(message, exc, delay)
                else:  # no traceback for SystemExit: uvicorn has logged its cause
                    message = "public listener failed: %r; next attempt in %g s"
                    logger.error(message, exc, delay, exc_info=not isinstance(exc, SystemExit))
                failed = True
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


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``myha-gateway`` command."""
    parser = argparse.ArgumentParser(prog="myha-gateway")
    parser.add_argument("command", choices=["serve"])
    parser.parse_args(argv)
    settings = Settings()
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
