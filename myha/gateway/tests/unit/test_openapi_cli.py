import json
from pathlib import Path
from typing import Any

import pytest

from myha_gateway.__main__ import main


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    return tmp_path


def _schema(capsys: pytest.CaptureFixture[str], *flags: str) -> dict[str, Any]:
    main(["openapi", *flags])
    schema: dict[str, Any] = json.loads(capsys.readouterr().out)  # stdout: the schema only
    return schema


def test_public_schema_has_no_admin_route(capsys: pytest.CaptureFixture[str]) -> None:
    paths = _schema(capsys, "--public")["paths"]
    assert "/healthz" in paths
    assert not [p for p in paths if p.startswith("/api/v1/admin")]


def test_admin_schema_has_admin_routes(capsys: pytest.CaptureFixture[str]) -> None:
    assert "/api/v1/admin/session" in _schema(capsys, "--admin")["paths"]


def test_openapi_touches_no_database(capsys: pytest.CaptureFixture[str], _data_dir: Path) -> None:
    _schema(capsys, "--public")
    assert list(_data_dir.rglob("*.sqlite*")) == []


def test_openapi_needs_one_audience() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["openapi"])
    assert exited.value.code == 2  # argparse: usage error
