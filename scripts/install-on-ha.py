# /// script
# requires-python = ">=3.12"
# dependencies = ["websockets>=15", "httpx>=0.28"]
# ///
"""Install and drive the myHA app on a Home Assistant instance through the Supervisor API.

Run from the repository root:

    uv run scripts/install-on-ha.py [--yes] [--slug SLUG] COMMAND [ARGS]

HA_URL (for example http://homeassistant.local:8123) and HA_TOKEN (long-lived access token of a
Home Assistant administrator) come from the environment; the token is never printed. Supervisor
calls go through the Core WebSocket command supervisor/api; logs, backup downloads and ingress
requests through the Core HTTP proxy. Mutating commands ask before they run unless --yes is given.
"""

import argparse
import asyncio
import itertools
import json
import os
import re
import sys
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import WebSocketException

DEFAULT_REPO = "https://github.com/LL0rd/myha"
HA_DEFAULT = object()  # sentinel: omit the field, HA Core then applies its 10 s default


class Failure(Exception):
    """Printed as ``error: <message>``, exit code 1."""


def mask(text: str, token: str) -> str:
    """Remove the token from text about to be printed; exception messages may echo input."""
    return text.replace(token, "<token>") if token else text


def is_myha(slug: str) -> bool:
    """Store slugs are ``<repository hash>_<app slug>`` or ``local_<app slug>``."""
    return slug.partition("_")[2] == "myha"


def pick_slug(apps: list[dict[str, Any]]) -> str:
    """The one myHA app in an ``/addons`` or ``/store/addons`` list."""
    slugs: list[str] = sorted(app["slug"] for app in apps if is_myha(app["slug"]))
    if len(slugs) != 1:
        raise Failure(f"expected exactly one app *_myha, found {slugs or 'none'}; use --slug")
    return slugs[0]


async def sup(
    ws: ClientConnection,
    i: int,
    endpoint: str,
    method: str = "get",
    data: dict[str, Any] | None = None,
    timeout: object = HA_DEFAULT,  # noqa: ASYNC109 (a field for HA Core, not a local timeout)
) -> dict[str, Any]:
    """Send one supervisor/api command; skip every message until the reply with id ``i``."""
    msg: dict[str, Any] = {
        "id": i,
        "type": "supervisor/api",
        "endpoint": endpoint,
        "method": method,
    }
    if data is not None:
        msg["data"] = data
    if timeout is not HA_DEFAULT:
        msg["timeout"] = timeout  # explicit null: no timeout at all
    await ws.send(json.dumps(msg))
    while True:
        reply: dict[str, Any] = json.loads(await ws.recv())
        if reply.get("id") == i:
            return reply


class Supervisor:
    """Supervisor API calls over one authenticated Core WebSocket connection."""

    def __init__(self, ws: ClientConnection, args: argparse.Namespace) -> None:
        self.ws, self.args, self.ids = ws, args, itertools.count(1)

    async def call(
        self,
        endpoint: str,
        method: str = "get",
        data: dict[str, Any] | None = None,
        *,
        unlimited: bool = False,
    ) -> Any:
        """The call's result; ``unlimited`` lifts HA Core's 10 s timeout."""
        timeout = None if unlimited else HA_DEFAULT
        reply = await sup(self.ws, next(self.ids), endpoint, method, data, timeout)
        if not reply.get("success"):
            message = (reply.get("error") or {}).get("message", "failed")
            raise Failure(f"{method.upper()} {endpoint}: {message}")
        return reply.get("result")

    async def change(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        """A mutating POST, after confirmation unless --yes."""
        if not self.args.yes:
            shown = f"POST {endpoint}" + (f" {json.dumps(data)}" if data else "")
            prompt = f"{shown}\nContinue? [y/N] "
            try:  # in a thread, so the connection keeps answering pings while we wait
                answer = await asyncio.to_thread(input, prompt)
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                raise Failure("aborted")
        # No timeout: builds and backups take minutes, and a Core timeout would report an
        # error for a change that the Supervisor still completes.
        return await self.call(endpoint, "post", data, unlimited=True)

    async def slug(self, *, store: bool = False) -> str:
        """--slug, or the myHA app among the installed apps (the store apps for install)."""
        if self.args.slug:
            return str(self.args.slug)
        return pick_slug((await self.call("/store/addons" if store else "/addons"))["addons"])

    async def wait_installed(self, slug: str) -> str:
        """Poll the app info every 5 s, at most 15 minutes, until it has a state."""
        for _ in range(180):
            reply = await sup(self.ws, next(self.ids), f"/addons/{slug}/info")
            if reply.get("success") and reply["result"].get("state"):
                return str(reply["result"]["state"])
            await asyncio.sleep(5)
        raise Failure(f"{slug} has no state after 15 minutes")


async def status(ha: Supervisor) -> str:
    info = await ha.call("/supervisor/info")
    lines = [f"Supervisor {info['version']}, channel {info['channel']}"]
    for repo in await ha.call("/store/repositories"):
        lines.append(f"repository {repo['slug']}: {repo['name']} ({repo['source']})")
    installed = (await ha.call("/addons"))["addons"]
    slugs = [ha.args.slug] if ha.args.slug else [a["slug"] for a in installed if is_myha(a["slug"])]
    if not slugs:
        lines.append("myHA app: not installed")
    for slug in slugs:
        app = await ha.call(f"/addons/{slug}/info")
        fields = ("state", "version", "ingress_entry", "network")
        lines.append(f"app {slug}: " + ", ".join(f"{name} {app.get(name)}" for name in fields))
    return "\n".join(lines)


async def rest_get(base: str, path: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as http:
        return await http.get(f"{base}{path}", headers=headers)


async def command(ha: Supervisor, base: str, token: str) -> str:
    """Run the command; returns what to print."""
    args, bearer = ha.args, {"Authorization": f"Bearer {token}"}
    match args.command:
        case "status":
            return await status(ha)
        case "add-repo":
            result = await ha.change("/store/repositories", {"repository": args.repo})
        case "reload":
            result = await ha.change("/store/reload")
        case "install":
            slug = await ha.slug(store=True)
            await ha.change(f"/store/addons/{slug}/install")
            return f"{slug}: {await ha.wait_installed(slug)}"
        case "rebuild" | "start" | "stop" | "restart":
            result = await ha.change(f"/addons/{await ha.slug()}/{args.command}")
        case "update":
            result = await ha.change(f"/store/addons/{await ha.slug()}/update")
        case "options":
            if not isinstance(args.options, dict):
                raise Failure("options must be a JSON object")
            result = await ha.change(
                f"/addons/{await ha.slug()}/options", {"options": args.options}
            )
        case "network":
            network = {"network": {"8443/tcp": args.hostport}}
            result = await ha.change(f"/addons/{await ha.slug()}/options", network)
        case "backup-new":
            body = {"name": "myha-probe", "addons": [await ha.slug()]}
            return str((await ha.change("/backups/new/partial", body))["slug"])
        case "logs":
            response = await rest_get(base, f"/api/hassio/addons/{await ha.slug()}/logs", bearer)
            if response.status_code in (401, 403):
                raise Failure(f"logs: HTTP {response.status_code}; read the app's Log tab in HA")
            return response.raise_for_status().text
        case "ingress-get":
            slug = await ha.slug()
            session = (await ha.call("/ingress/session", "post"))["session"]
            entry = (await ha.call(f"/addons/{slug}/info"))["ingress_entry"]
            cookie = {"Cookie": f"ingress_session={session}"}
            response = await rest_get(base, f"{entry}/{args.path.lstrip('/')}", cookie)
            try:
                text = json.dumps(response.json(), indent=2)
            except ValueError:
                text = response.text
            return f"HTTP {response.status_code}\n{text}"
    return json.dumps(result, indent=2) if result else "ok"


async def run(args: argparse.Namespace, base: str, token: str) -> str:
    async with websockets.connect(re.sub("^http", "ws", base) + "/api/websocket") as ws:
        if json.loads(await ws.recv()).get("type") != "auth_required":
            raise Failure("HA_URL does not answer like the Home Assistant WebSocket API")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise Failure(f"authentication failed: {auth.get('message', auth.get('type'))}")
        return await command(Supervisor(ws, args), base, token)


def download_backup(base: str, token: str, backup: str, out: str) -> str:
    """Stream a backup archive to ``out`` through the Core proxy."""
    url = f"{base}/api/hassio/backups/{backup}/download"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.stream("GET", url, headers=headers, timeout=60) as response:
        if response.status_code != 200:
            raise Failure(f"GET {url}: HTTP {response.status_code}")
        with open(out, "wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)
    return f"{out}: {os.path.getsize(out)} bytes"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--yes", action="store_true", help="do not ask before mutating commands")
    parser.add_argument("--slug", help="app slug, instead of looking up the one ending in _myha")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "reload", "install", "rebuild", "update", "start", "stop", "restart"):
        commands.add_parser(name)
    commands.add_parser("logs")
    commands.add_parser("backup-new")
    commands.add_parser("add-repo").add_argument("--repo", default=DEFAULT_REPO)
    commands.add_parser("options").add_argument("options", type=json.loads, help="JSON object")
    commands.add_parser("network").add_argument("hostport", type=int, help="host port for 8443")
    download = commands.add_parser("backup-download")
    download.add_argument("backup_slug")
    download.add_argument("out")
    commands.add_parser("ingress-get").add_argument("path", help="for example api/v1/admin/probe")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    base, token = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
    if not base.startswith(("http://", "https://")) or not token:
        sys.exit("error: set HA_URL (http:// or https://) and HA_TOKEN")
    try:
        if args.command == "backup-download":
            print(download_backup(base, token, args.backup_slug, args.out))
        else:
            print(asyncio.run(run(args, base, token)))
    except Failure as exc:
        sys.exit(mask(f"error: {exc}", token))
    except (OSError, httpx.HTTPError, httpx.InvalidURL, WebSocketException) as exc:
        sys.exit(mask(f"error: {type(exc).__name__}: {exc}", token))


if __name__ == "__main__":
    main()
