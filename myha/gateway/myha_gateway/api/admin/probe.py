"""S0 probe: what the Supervisor ingress delivers and what the app sees of its environment."""

import asyncio
import logging
import os
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from myha_gateway.auth.passwords import format_table, measure_argon2
from myha_gateway.deps import AppContext
from myha_gateway.ha.supervisor import SupervisorUnavailable

logger = logging.getLogger(__name__)
router = APIRouter()
_SHOWN_NAMES = {"x-ingress-path", "x-real-ip"}
_SHOWN_PREFIXES = ("x-remote-user-", "x-forwarded-")
_PATHS = ("data_dir", "state_dir", "backup_dir", "web_dist")


async def _outcome(call: Awaitable[Any]) -> dict[str, Any] | None:
    """A Supervisor answer as a dict, ``None`` if not permitted, or the error text."""
    try:
        result = await call
    except SupervisorUnavailable as exc:  # its message never contains the token
        return {"error": f"{type(exc).__name__}: {exc}"}
    return None if result is None else asdict(result)


async def details(ctx: AppContext) -> dict[str, Any]:
    """Supervisor answers, HA connection, environment variable names (never values), paths."""
    supervisor = ctx.supervisor
    data_dir = ctx.settings.data_dir
    return {
        "supervisor_info": await _outcome(supervisor.info()),
        "host_info": await _outcome(supervisor.host_info()),
        "self_info": await _outcome(supervisor.self_info()),
        "ha_connected": ctx.ha.connected,
        "ha_generation": ctx.ha.generation,
        "env_names": sorted(os.environ),
        "paths": {name: getattr(ctx.settings, name).exists() for name in _PATHS},
        "data_entries": sorted(p.name for p in data_dir.iterdir()) if data_dir.is_dir() else None,
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/api/v1/admin/probe")
async def probe(request: Request) -> dict[str, Any]:
    """Identity and forwarding headers (repeated ones joined, not dropped) plus ``details``."""
    headers = {
        name: ", ".join(request.headers.getlist(name))
        for name in request.headers
        if name in _SHOWN_NAMES or name.startswith(_SHOWN_PREFIXES)
    }
    peer = request.client.host if request.client else None
    return {"peer": peer, "headers": headers, **await details(request.app.state.ctx)}


async def log_startup(ctx: AppContext) -> None:
    """S0 evidence in the add-on log: Supervisor answers, backup marker, Argon2 timing."""
    supervisor = ctx.supervisor
    logger.info("probe: supervisor_info %s", await _outcome(supervisor.info()))
    host_info = await _outcome(supervisor.host_info())
    logger.info("probe: host_info %s", "not permitted" if host_info is None else host_info)
    logger.info("probe: self_info %s", await _outcome(supervisor.self_info()))
    # Each step logs its own failure (read-only /data, too little memory); startup goes on.
    try:
        # Must be missing from backups: proves that backup_exclude keeps state/ out.
        marker = ctx.settings.state_dir / "probe-live.marker"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{datetime.now(UTC).isoformat()}\n", encoding="utf-8")
        logger.info("probe: wrote %s", marker)
    except Exception:
        logger.exception("probe: marker not written")
    try:
        # In a thread: about 27 hashes of up to 64 MiB would block the event loop for seconds.
        table = format_table(await asyncio.to_thread(measure_argon2))
        logger.info("probe: argon2id timing, median of 3 runs, parallelism 1\n%s", table)
    except Exception:
        logger.exception("probe: argon2id timing failed")
