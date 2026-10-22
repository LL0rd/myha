"""E24: the admin CSRF proof, bound to user, ingress path and UTC day, plus Sec-Fetch-Site."""

import hashlib
import hmac
from datetime import date, timedelta

import pytest
from starlette.datastructures import Headers

from myha_gateway.auth.admin import csrf_ok, csrf_token

SECRET = "instance-server-secret"
USER = "owner1"
PATH = "/api/hassio_ingress/abc"
TODAY = date(2026, 10, 22)


def token(day: date = TODAY, user: str = USER, path: str = PATH, secret: str = SECRET) -> str:
    return csrf_token(secret, user, path, day)


def ok(sent: str | None, **headers: str) -> bool:
    """Whether a request from USER through PATH passes on TODAY."""
    raw = {"x-ingress-path": PATH, **headers}
    if sent is not None:
        raw["x-myha-csrf"] = sent
    return csrf_ok(Headers(raw), SECRET, USER, TODAY)


def test_token_is_the_hex_hmac_of_user_path_and_day() -> None:
    message = b"owner1|/api/hassio_ingress/abc|2026-10-22"
    assert token() == hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("day", [TODAY, TODAY - timedelta(days=1)])
def test_todays_and_yesterdays_token_pass(day: date) -> None:
    assert ok(token(day))


@pytest.mark.parametrize(
    "sent",
    [
        None,
        "",
        "0" * 64,
        token(TODAY - timedelta(days=2)),
        token(TODAY + timedelta(days=1)),
        token(user="user1"),
        token(path="/api/hassio_ingress/other"),
        token(secret="another-secret"),
        token().upper(),
        "ä",  # non-ASCII must fail, not raise
    ],
)
def test_other_tokens_fail(sent: str | None) -> None:
    assert not ok(sent)


def test_missing_ingress_path_counts_as_empty() -> None:
    assert csrf_ok(Headers({"x-myha-csrf": token(path="")}), SECRET, USER, TODAY)
    assert not csrf_ok(Headers({"x-myha-csrf": token()}), SECRET, USER, TODAY)


@pytest.mark.parametrize("site", ["cross-site", "same-site", "none", ""])
def test_sec_fetch_site_other_than_same_origin_fails(site: str) -> None:
    assert not ok(token(), **{"sec-fetch-site": site})


def test_same_origin_or_no_sec_fetch_site_passes() -> None:
    assert ok(token(), **{"sec-fetch-site": "same-origin"})
    assert ok(token())
