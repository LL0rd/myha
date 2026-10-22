"""``scripts/install-on-ha.py``: message shaping, app lookup and token masking, offline."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("websockets")
pytest.importorskip("httpx")

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "install-on-ha.py"
_spec = importlib.util.spec_from_file_location("install_on_ha", SCRIPT)
assert _spec is not None and _spec.loader is not None
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


class FakeWebSocket:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies = [json.dumps(reply) for reply in replies]

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return self.replies.pop(0)


def result(i: int, value: Any = None) -> dict[str, Any]:
    return {"id": i, "type": "result", "success": True, "result": value}


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
    assert script.mask("rejected Bearer abc.def.ghi", "abc.def.ghi") == "rejected Bearer <token>"
    assert script.mask("nothing to hide", "") == "nothing to hide"
