import logging

import pytest

from myha_gateway.logsetup import RedactionFilter, setup_logging

LONG = "A" * 43  # base64url run as long as a 256-bit secret


def redact(msg: str, *args: object) -> str:
    record = logging.LogRecord("x", logging.INFO, "", 0, msg, args, None)
    assert RedactionFilter().filter(record)
    return record.getMessage()


def test_redacts_bearer_and_secrets() -> None:
    out = redact("Authorization: Bearer abc.def invite_secret=QmFzZTY0 token=xyz")
    assert out == "Authorization: Bearer <redacted> invite_secret=<redacted> token=<redacted>"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("bearer abc.def", "bearer <redacted>"),
        ("password: hunter2", "password: <redacted>"),
        ("PASSWORD=hunter2", "PASSWORD=<redacted>"),
        ("{'Authorization': 'Bearer abc.def'}", "{'Authorization': 'Bearer <redacted>"),
        ("authorization=[Bearer abc.def]", "authorization=<redacted> <redacted>"),
        ('{"refresh_token": "xyz"}', '{"refresh_token": "<redacted>'),
        (f"/invite/{LONG}", "/invite/<redacted>"),
        (f"id {LONG[:-1]}", f"id {LONG[:-1]}"),
    ],
)
def test_redaction_rules(message: str, expected: str) -> None:
    assert redact(message) == expected


def test_redacts_format_args() -> None:
    assert redact("token=%s", "xyz") == "token=<redacted>"


def test_malformed_log_call_does_not_raise() -> None:
    assert "xyz" not in redact("token=%s %s", "xyz")


def test_setup_logging_redacts_records_from_child_loggers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        setup_logging("info")
        logging.getLogger("myha_gateway.test").info("token=%s", "xyz")
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    assert "token=<redacted>" in capsys.readouterr().err
