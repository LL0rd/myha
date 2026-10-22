from pathlib import Path

import pytest

from myha_gateway.settings import Settings


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MYHA_DATA_DIR", str(tmp_path))
    return tmp_path


def test_defaults(data_dir: Path) -> None:
    s = Settings()
    assert s.data_dir == data_dir
    assert s.state_dir == data_dir / "state" and s.backup_dir == data_dir / "backup"
    assert s.web_dist == Path("/opt/myha/web")
    assert s.admin_port == 8099 and s.public_port == 8443
    assert s.admin_bind == s.public_bind == "0.0.0.0"  # noqa: S104
    assert s.trusted_ingress_peer == "172.30.32.2"
    assert s.supervisor_url == "http://supervisor" and s.supervisor_token is None
    assert s.ha_ws_url == "ws://supervisor/core/websocket"
    assert s.min_supervisor_version == "2026.10.3"
    assert s.log_level == "info"
    assert not s.probe_mode and not s.public_enabled and s.public_origin is None


def test_env_uses_prefix_but_supervisor_token_is_unprefixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYHA_ADMIN_PORT", "9000")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-secret")
    s = Settings()
    assert s.admin_port == 9000
    assert s.supervisor_token is not None
    assert s.supervisor_token.get_secret_value() == "sup-secret"
    assert "sup-secret" not in repr(s)


def test_options_json_sets_only_log_level_and_probe_mode(data_dir: Path) -> None:
    (data_dir / "options.json").write_text(
        '{"log_level": "debug", "probe_mode": true, "admin_port": 1, "public_origin": "http://x"}'
    )
    s = Settings()
    assert s.log_level == "debug" and s.probe_mode
    assert s.admin_port == 8099
    assert s.public_enabled and s.public_origin == "http://probe.invalid:8443"


def test_options_json_follows_data_dir_kwarg(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "options.json").write_text('{"log_level": "error"}')
    assert Settings(data_dir=other).log_level == "error"


def test_environment_beats_options_json(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (data_dir / "options.json").write_text('{"log_level": "debug"}')
    monkeypatch.setenv("MYHA_LOG_LEVEL", "WARNING")
    assert Settings().log_level == "warning"


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYHA_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="log_level"):
        Settings()


def test_public_enabled_requires_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    with pytest.raises(ValueError, match="MYHA_PUBLIC_ORIGIN required with MYHA_PUBLIC_ENABLED"):
        Settings()
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    assert Settings().public_origin == "http://testserver"


def test_probe_mode_allows_public_enabled_without_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYHA_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("MYHA_PROBE_MODE", "1")
    monkeypatch.setenv("MYHA_PUBLIC_PORT", "9443")
    s = Settings()
    assert s.public_enabled and s.public_origin == "http://probe.invalid:9443"
    monkeypatch.setenv("MYHA_PUBLIC_ORIGIN", "http://testserver")
    assert Settings().public_origin == "http://testserver"
