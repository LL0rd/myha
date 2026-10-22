import functools
import json
from pathlib import Path

import httpx
import pytest

from myha_gateway import deps
from myha_gateway.__main__ import main
from myha_gateway.ha.supervisor import SupervisorClient


def test_probe_prints_supervisor_names_and_paths_but_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-secret")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/host/info":
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json={"result": "ok", "data": {"supervisor": "2026.10.3"}})

    client = functools.partial(SupervisorClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(deps, "SupervisorClient", client)
    main(["probe"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert str(calls[0].url) == "http://supervisor/info"
    assert calls[0].headers["authorization"] == "Bearer sup-secret"
    assert report["supervisor_info"]["version"] == "2026.10.3" and report["host_info"] is None
    assert "SUPERVISOR_TOKEN" in report["env_names"] and "sup-secret" not in out
    assert report["paths"]["data_dir"] is True and report["paths"]["state_dir"] is False
