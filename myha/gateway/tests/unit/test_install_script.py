"""``scripts/install-on-ha.py`` offline: message shaping, prompts, failures and the token."""

import argparse
import asyncio
import contextlib
import importlib.util
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from websockets.uri import parse_uri

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "install-on-ha.py"
_spec = importlib.util.spec_from_file_location("install_on_ha", SCRIPT)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

TOKEN = "s3cr3t.tok-en_1"
NO_REASON = "no reason given (often HA Core's 10 s timeout)"


class FakeWebSocket:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies = [json.dumps(reply) for reply in replies]

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.replies:
            await asyncio.Event().wait()  # a Supervisor that never answers
        return self.replies.pop(0)


def result(i: int, value: Any = None) -> dict[str, Any]:
    return {"id": i, "type": "result", "success": True, "result": value}


def supervisor(ws: FakeWebSocket, *, yes: bool = False) -> Any:
    return script.Supervisor(ws, argparse.Namespace(yes=yes, slug=None), "ha.local:8123")


async def test_sup_leaves_out_timeout_and_data_by_default() -> None:
    ws = FakeWebSocket(result(1, {"version": "2026.10.3"}))
    reply = await script.sup(ws, 1, "/supervisor/info")
    assert ws.sent == [
        {"id": 1, "type": "supervisor/api", "endpoint": "/supervisor/info", "method": "get"}
    ]
    assert reply["result"] == {"version": "2026.10.3"}


async def test_sup_sends_data_and_an_explicit_null_timeout() -> None:
    ws = FakeWebSocket(result(2))
    await script.sup(ws, 2, "/store/repositories", "post", {"repository": "x"}, timeout=None)
    assert ws.sent[0]["data"] == {"repository": "x"}
    assert "timeout" in ws.sent[0] and ws.sent[0]["timeout"] is None


async def test_sup_skips_messages_for_other_ids() -> None:
    ws = FakeWebSocket({"id": 7, "type": "event"}, {"type": "pong"}, result(3, "mine"))
    assert (await script.sup(ws, 3, "/addons"))["result"] == "mine"
    assert ws.replies == []


@pytest.mark.parametrize(("core_timeout", "limit"), [(script.HA_DEFAULT, 60), (None, 3600)])
async def test_sup_gives_up_when_no_answer_comes(
    core_timeout: object, limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[float] = []
    real_timeout = asyncio.timeout

    def short(delay: float) -> asyncio.Timeout:
        limits.append(delay)
        return real_timeout(0.01)

    monkeypatch.setattr(asyncio, "timeout", short)
    with pytest.raises(script.Failure, match="^POST /store/reload: no answer; .* run status$"):
        await script.sup(FakeWebSocket(), 1, "/store/reload", "post", timeout=core_timeout)
    assert limits == [limit]


@pytest.mark.parametrize(("message", "reason"), [("App is not installed", None), ("", NO_REASON)])
async def test_failed_call_names_the_endpoint(message: str, reason: str | None) -> None:
    error = {"code": "unknown_error", "message": message}
    ws = FakeWebSocket({"id": 1, "type": "result", "success": False, "error": error})
    with pytest.raises(script.Failure) as failed:
        await supervisor(ws).call("/addons/a0d7b954_myha/info")
    assert str(failed.value) == f"GET /addons/a0d7b954_myha/info: {reason or message}"


@pytest.mark.parametrize("answer", ["n\n", "\n", ""])  # no, empty line, end of input
async def test_change_sends_nothing_without_confirmation(
    answer: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(script, "read_line", lambda: answer)
    ws = FakeWebSocket()
    with pytest.raises(script.Failure, match="^aborted$"):
        await supervisor(ws).change("/addons/a0d7b954_myha/start")
    assert ws.sent == []
    prompt = "POST /addons/a0d7b954_myha/start on ha.local:8123\nContinue? [y/N] "
    assert capsys.readouterr().err == prompt


async def test_change_after_y_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "read_line", lambda: "y\n")
    ws = FakeWebSocket(result(1))
    await supervisor(ws).change("/addons/a0d7b954_myha/start")
    assert [(m["method"], m["endpoint"]) for m in ws.sent] == [
        ("post", "/addons/a0d7b954_myha/start")
    ]


async def test_change_with_yes_asks_nothing_and_waits_without_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(script, "read_line", lambda: "n\n")  # a prompt would abort
    ws = FakeWebSocket(result(1, {"slug": "abcd1234"}))
    body = {"name": "myha-probe", "addons": ["a0d7b954_myha"]}
    assert await supervisor(ws, yes=True).change("/backups/new/partial", body) == {
        "slug": "abcd1234"
    }
    assert ws.sent[0]["data"] == body
    assert "timeout" in ws.sent[0] and ws.sent[0]["timeout"] is None
    assert capsys.readouterr().err == ""


async def test_redirected_websocket_never_gets_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWebSocket({"type": "auth_required"})
    ws.protocol = SimpleNamespace(uri=parse_uri("ws://elsewhere.example/api/websocket"))

    @contextlib.asynccontextmanager
    async def connect(url: str) -> AsyncIterator[FakeWebSocket]:
        yield ws

    monkeypatch.setattr(script.websockets, "connect", connect)
    args = argparse.Namespace(command="status", yes=False, slug=None)
    with pytest.raises(script.Failure, match="redirected to elsewhere.example:80"):
        await script.run(args, "http://ha.local:8123", TOKEN)
    assert ws.sent == []


async def test_command_rejects_an_unknown_command() -> None:
    args = argparse.Namespace(command="frobnicate", yes=False, slug=None)
    ha = script.Supervisor(FakeWebSocket(), args, "ha.local:8123")
    with pytest.raises(script.Failure, match="^unknown command 'frobnicate'; see --help$"):
        await script.command(ha, "http://ha.local:8123", TOKEN)


def test_pick_slug_finds_the_one_myha_app() -> None:
    apps = [{"slug": "core_ssh"}, {"slug": "a0d7b954_myha"}, {"slug": "5c53de3b_notmyha"}]
    assert script.pick_slug(apps) == "a0d7b954_myha"
    assert script.pick_slug([{"slug": "local_myha"}]) == "local_myha"


@pytest.mark.parametrize(
    "apps", [[], [{"slug": "core_ssh"}], [{"slug": "a0d7b954_myha"}, {"slug": "1b2c3d4e_myha"}]]
)
def test_pick_slug_needs_exactly_one_match(apps: list[dict[str, str]]) -> None:
    with pytest.raises(script.Failure, match="--slug"):
        script.pick_slug(apps)


def test_mask_removes_the_token() -> None:
    assert script.mask(f"rejected Bearer {TOKEN}", TOKEN) == "rejected Bearer <token>"
    assert script.mask("nothing to hide", "") == "nothing to hide"


@pytest.fixture
def ha_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HA_URL", " http://ha.local:8123/ ")
    monkeypatch.setenv("HA_TOKEN", TOKEN)


@pytest.mark.parametrize(
    ("url", "token", "cause"),
    [
        ("", "a.b.c", "HA_URL is not set"),
        (" / ", "a.b.c", "HA_URL is not set"),
        ("ha-test.example:8123", "a.b.c", "HA_URL must start with http:// or https://"),
        ("http://ha-test.example:8123", "", "HA_TOKEN is not set"),
        ("http://ha-test.example:8123", "a.b.c\r", "HA_TOKEN: only letters, digits, . _ -"),
        ("http://ha-test.example:8123", "a.b.c d", "HA_TOKEN: only letters, digits, . _ -"),
    ],
)
def test_main_names_unusable_settings_without_echoing_them(
    url: str,
    token: str,
    cause: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HA_URL", url)
    monkeypatch.setenv("HA_TOKEN", token)
    with pytest.raises(SystemExit) as stopped:
        script.main(["status"])
    message = str(stopped.value.code)  # a string code exits with status 1
    assert message.startswith(f"error: {cause}")
    out, err = capsys.readouterr()
    assert "a.b.c" not in out + err + message and "ha-test" not in message


def test_main_passes_cleaned_settings_and_masks_output(
    ha_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, str]] = []

    async def fake_run(args: argparse.Namespace, base: str, token: str) -> str:
        seen.append((base, token))
        return f"echoed {token}"

    monkeypatch.setattr(script, "run", fake_run)
    script.main(["status"])
    assert seen == [("http://ha.local:8123", TOKEN)]
    assert capsys.readouterr().out == "echoed <token>\n"


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (script.Failure(f"GET /x: {TOKEN}"), "error: GET /x: <token>"),
        (OSError(f"no route for {TOKEN}"), "error: OSError: no route for <token>"),
        (
            KeyboardInterrupt(),
            "\nerror: aborted; a request already sent keeps running, check with status",
        ),
    ],
)
def test_main_errors_exit_1_without_the_token(
    raised: BaseException, code: str, ha_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(args: argparse.Namespace, base: str, token: str) -> str:
        raise raised

    monkeypatch.setattr(script, "run", failing)
    with pytest.raises(SystemExit) as stopped:
        script.main(["status"])
    assert stopped.value.code == code  # a string code exits with status 1


@pytest.mark.parametrize("started", [True, False])
def test_ctrl_c_during_download_names_the_partial_file(
    started: bool, ha_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "probe.tar"

    def interrupted(base: str, token: str, backup: str, target: str) -> str:
        if started:
            Path(f"{target}.part").write_bytes(b"first chunks")
        raise KeyboardInterrupt

    monkeypatch.setattr(script, "download_backup", interrupted)
    with pytest.raises(SystemExit) as stopped:
        script.main(["backup-download", "abcd1234", str(out)])
    left = f"; {out}.part was left" if started else ""
    assert stopped.value.code == f"\nerror: download aborted{left}"
