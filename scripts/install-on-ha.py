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
install returns only when the Supervisor has built and installed the app, which takes minutes.
"""

import argparse
import asyncio
import itertools
import json
import os
import re
import sys
import threading
import urllib.parse
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import WebSocketException
from websockets.uri import parse_uri

DEFAULT_REPO = "https://github.com/LL0rd/myha"
HA_DEFAULT = object()  # sentinel: omit the field, HA Core then applies its 10 s default
USAGE = "error: set HA_URL (http:// or https://) and HA_TOKEN (a long-lived access token)"


class Failure(Exception):
    """Printed as ``error: <message>``, exit code 1."""


def mask(text: str, token: str) -> str:
    """Remove the token from text about to be printed; exception messages may echo input."""
    return text.replace(token, "<token>") if token else text


def environment() -> tuple[str, str]:
    """HA_URL without a trailing slash and HA_TOKEN, checked before either is used."""
    base = os.environ.get("HA_URL", "").strip().rstrip("/")
    token = os.environ.get("HA_TOKEN", "")
    # The token is not stripped: with a control character in it, httpx's header error would
    # quote it in a form mask() cannot find, so anything but a plain token stops here.
    if not base.startswith(("http://", "https://")) or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        sys.exit(USAGE)
    return base, token


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
    try:
        # Our own bound on top: an hour where Core waits without limit, else a minute.
        async with asyncio.timeout(3600 if timeout is None else 60):
            await ws.send(json.dumps(msg))
            while True:
                reply: dict[str, Any] = json.loads(await ws.recv())
                if reply.get("id") == i:
                    return reply
    except TimeoutError:
        hint = "no answer; the Supervisor may still be working, run status"
        raise Failure(f"{method.upper()} {endpoint}: {hint}") from None


def read_line() -> str:
    """One line from stdin, or "" at its end. os.read, unlike input(), holds no stdio lock
    while it waits, so a Ctrl+C exit is not stalled by the thread still waiting here."""
    try:
        return os.read(0, 1024).decode(errors="replace")
    except OSError:
        return ""


async def ask(prompt: str) -> str:
    """The answer, read in a daemon thread so that the connection keeps answering pings."""
    print(prompt, end="", file=sys.stderr, flush=True)
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[str] = loop.create_future()

    def read() -> None:
        text = read_line()

        def deliver() -> None:
            if not answer.done():  # Ctrl+C may have cancelled the wait meanwhile
                answer.set_result(text)

        loop.call_soon_threadsafe(deliver)

    threading.Thread(target=read, daemon=True).start()
    return await answer


class Supervisor:
    """Supervisor API calls over one authenticated Core WebSocket connection."""

    def __init__(self, ws: ClientConnection, args: argparse.Namespace, host: str) -> None:
        self.ws, self.args, self.host, self.ids = ws, args, host, itertools.count(1)

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
            reason = (reply.get("error") or {}).get("message")
            reason = reason or "no reason given (often HA Core's 10 s timeout)"
            raise Failure(f"{method.upper()} {endpoint}: {reason}")
        return reply.get("result")

    async def change(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        """A mutating POST, after confirmation unless --yes."""
        if not self.args.yes:
            shown = f"POST {endpoint}" + (f" {json.dumps(data)}" if data else "")
            answer = await ask(f"{shown} on {self.host}\nContinue? [y/N] ")
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
            await ha.change(f"/store/addons/{slug}/install")  # blocks until the build is done
            app = await ha.call(f"/addons/{slug}/info")
            return f"{slug}: state {app['state']}, version {app['version']}"
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
            path = f"/api/hassio/addons/{await ha.slug()}/logs?lines={args.lines}"
            response = await rest_get(base, path, bearer)
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
    url = re.sub("^http", "ws", base) + "/api/websocket"
    async with websockets.connect(url) as ws:
        # connect() follows HTTP redirects; the token goes to HA_URL and nowhere else.
        if ws.protocol.uri != parse_uri(url):
            moved = ws.protocol.uri
            raise Failure(f"redirected to {moved.host}:{moved.port}; the token is not sent there")
        if json.loads(await ws.recv()).get("type") != "auth_required":
            raise Failure("HA_URL does not answer like the Home Assistant WebSocket API")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise Failure(f"authentication failed: {auth.get('message', auth.get('type'))}")
        host = urllib.parse.urlsplit(base).netloc
        return await command(Supervisor(ws, args, host), base, token)


def download_backup(base: str, token: str, backup: str, out: str) -> str:
    """Stream a backup archive through the Core proxy; ``out`` appears only once complete."""
    url = f"{base}/api/hassio/backups/{backup}/download"
    part = f"{out}.part"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.stream("GET", url, headers=headers, timeout=60) as response:
        if response.status_code != 200:
            raise Failure(f"GET {url}: HTTP {response.status_code}")
        with open(part, "wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)
    os.replace(part, out)
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
    commands.add_parser("logs").add_argument("--lines", type=int, default=100, help="default 100")
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
    base, token = environment()
    try:
        if args.command == "backup-download":
            output = download_backup(base, token, args.backup_slug, args.out)
        else:
            output = asyncio.run(run(args, base, token))
    except Failure as exc:
        sys.exit(mask(f"error: {exc}", token))
    except (OSError, httpx.HTTPError, httpx.InvalidURL, WebSocketException) as exc:
        sys.exit(mask(f"error: {type(exc).__name__}: {exc}", token))
    except KeyboardInterrupt:
        sys.exit("\nerror: aborted; a request already sent keeps running, check with status")
    print(mask(output, token))


if __name__ == "__main__":
    main()
