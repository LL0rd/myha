"""``myha-gateway serve`` runs both listeners; ``myha-gateway probe`` checks the environment."""

import argparse
import asyncio
import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI

from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.deps import AppContext
from myha_gateway.logsetup import setup_logging
from myha_gateway.settings import Settings

logger = logging.getLogger(__name__)


def _server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """uvicorn server that trusts no proxy header and logs through the redacting root handler."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
        ws_max_size=65536,
        log_config=None,
        access_log=True,
        # Public, then admin stop in turn; 2 x 4 s fits the Supervisor's 10 s stop timeout.
        timeout_graceful_shutdown=4,
    )
    return uvicorn.Server(config)


async def _serve(settings: Settings) -> None:
    """Run the admin listener, and the public listener as soon as ``ctx.public_ready``."""
    ctx = await AppContext.create(settings)
    admin = _server(create_admin_app(ctx), settings.admin_bind, settings.admin_port)

    async def public_when_ready() -> None:
        # Polled because public_ready can turn true at runtime (instance configuration, WP4).
        while not admin.should_exit:
            if ctx.public_ready:
                try:
                    app = create_public_app(ctx)
                    await _server(app, settings.public_bind, settings.public_port).serve()
                except (Exception, SystemExit):  # SystemExit: uvicorn exits on startup errors
                    # The admin listener stays up: it is where the public setup gets fixed.
                    # shortcut: no restart after a failure; restart on config changes in WP4
                    logger.exception("public listener stopped")
                return
            await asyncio.sleep(2)

    # Each Server.serve() installs uvicorn's SIGINT/SIGTERM handlers and on exit re-raises the
    # signal to the handler it replaced, so a signal stops the public, then the admin server.
    await asyncio.gather(admin.serve(), public_when_ready())


def _probe(settings: Settings) -> None:
    """Print Supervisor reachability, environment variable names and data paths (S0 only)."""
    url = f"{settings.supervisor_url}/info"
    token = settings.supervisor_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token is not None else {}
    try:
        response = httpx.get(url, headers=headers, timeout=5)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        print(f"GET {url}: {type(exc).__name__}: {exc}")
    else:
        try:
            version = response.json()["data"]["supervisor"]
        except (ValueError, LookupError, TypeError):  # not JSON or without data.supervisor
            version = None
        print(f"GET {url}: HTTP {response.status_code}, supervisor {version}")
    print("environment variable names:", " ".join(sorted(os.environ)))
    for name in ("data_dir", "state_dir", "backup_dir", "web_dist"):
        path = getattr(settings, name)
        print(f"{name}: {path} ({'exists' if path.exists() else 'missing'})")


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
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:  # Ctrl+C after graceful shutdown, like uvicorn
        pass
    except Exception:  # through the redacting handler instead of sys.excepthook
        logger.exception("gateway stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
