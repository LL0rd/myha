import logging

import aiosqlite
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
        ("{'authorization': 'Basic dXNlcjpwYXNz'}", "{'authorization': 'Basic <redacted>"),
        ("GET /ws?ticket=abc123", "GET /ws?ticket=<redacted>"),
        ("cookie: myha_session=abc", "cookie: <redacted>"),
        ("token = xyz", "token = <redacted>"),
        ("using basic auth", "using basic auth"),
        ('{"refresh_token": "xyz"}', '{"refresh_token": "<redacted>'),
        (f"/invite/{LONG}", "/invite/<redacted>"),
        (f"id {LONG[:-1]}", f"id {LONG[:-1]}"),
    ],
)
def test_redaction_rules(message: str, expected: str) -> None:
    assert redact(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('body {\\"poll_token\\": \\"abc\\"}', 'body {\\"poll_token\\": \\"<redacted>'),
        ('{\\"csrf\\":\\"abc\\",\\"x\\":1}', '{\\"csrf\\":\\"<redacted>'),
        ("GET /pair?code%3D482913", "GET /pair?code%3D<redacted>"),
        ("cookie%3Aabc", "cookie%3A<redacted>"),
        ("SESSION%3dabc", "SESSION%3d<redacted>"),
        (
            "pairing code=482913 pin: 1234 csrf=abc session: s1 poll=p1 secret=s",
            "pairing code=<redacted> pin: <redacted> csrf=<redacted> session: <redacted> "
            "poll=<redacted> secret=<redacted>",
        ),
        ("invite_code=482913", "invite_code=<redacted>"),
        ("pairing_code: 123456 code=482913", "pairing_code: <redacted> code=<redacted>"),
        ("barcode: 4006381333931 spin: 3", "barcode: 4006381333931 spin: 3"),
        (
            "status_code=200 error_code: E1 reason_code=supervisor_version public_code=CONFLICT",
            "status_code=200 error_code: E1 reason_code=supervisor_version public_code=CONFLICT",
        ),
        ("HTTP code=404, code: 503", "HTTP code=404, code: 503"),
    ],
)
def test_redaction_corpus(message: str, expected: str) -> None:
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


async def test_debug_logging_never_shows_bound_database_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, driver = logging.getLogger(), logging.getLogger("aiosqlite")
    handlers, level, driver_level = root.handlers[:], root.level, driver.level
    driver.setLevel(logging.NOTSET)
    try:
        setup_logging("debug")
        async with aiosqlite.connect(":memory:") as db:  # what SQLAlchemy drives below
            await db.execute("SELECT ?", ("hunter2",))  # too short for the redaction rules
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        driver.setLevel(driver_level)
    assert "hunter2" not in capsys.readouterr().err


def test_setup_logging_redacts_exception_text(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        setup_logging("info")
        try:
            raise RuntimeError("token=abc")
        except RuntimeError:
            logging.getLogger("myha_gateway.test").exception("request failed")
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    err = capsys.readouterr().err
    assert "RuntimeError: token=<redacted>" in err and "abc" not in err


def test_redacts_stack_info() -> None:
    record = logging.LogRecord("x", logging.INFO, "", 0, "m", (), None, sinfo="Stack:\n token=abc")
    assert RedactionFilter().filter(record)
    assert record.stack_info == "Stack:\n token=<redacted>"
