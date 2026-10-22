from pathlib import Path

import httpx
import pytest

from myha_gateway.__main__ import main


def test_probe_prints_status_names_and_paths_but_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-secret")
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
        calls.append((url, headers))
        return httpx.Response(200, json={"result": "ok", "data": {"supervisor": "2026.10.3"}})

    monkeypatch.setattr(httpx, "get", fake_get)
    main(["probe"])
    out = capsys.readouterr().out
    assert calls == [("http://supervisor/info", {"Authorization": "Bearer sup-secret"})]
    assert "HTTP 200" in out and "2026.10.3" in out
    assert "SUPERVISOR_TOKEN" in out and "sup-secret" not in out
    assert f"data_dir: {tmp_path} (exists)" in out
    assert f"state_dir: {tmp_path / 'state'} (missing)" in out
