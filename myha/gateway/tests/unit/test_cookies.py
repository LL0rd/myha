"""The host-bound session cookie."""

from datetime import UTC, datetime, timedelta

from fastapi import Response

from myha_gateway.auth.cookies import (
    clear_session_cookie,
    session_cookie_name,
    set_session_cookie,
)


def attributes(response: Response) -> tuple[str, dict[str, str]]:
    """The one Set-Cookie header: ``name=value`` and its attributes, keys lower-cased."""
    [header] = response.headers.getlist("set-cookie")
    pair, *rest = (part.strip() for part in header.split(";"))
    attrs = {}
    for item in rest:
        key, _, value = item.partition("=")
        attrs[key.lower()] = value
    return pair, attrs


def test_cookie_name_is_host_bound_per_instance() -> None:
    assert session_cookie_name("a1b2c3d4") == "__Host-myha_a1b2c3d4_session"


def test_set_session_cookie_attributes() -> None:
    response = Response()
    name = session_cookie_name("a1b2c3d4")
    set_session_cookie(response, name, "tok", datetime.now(UTC) + timedelta(hours=1))
    pair, attrs = attributes(response)
    assert pair == "__Host-myha_a1b2c3d4_session=tok"
    assert attrs.keys() == {"max-age", "path", "samesite", "secure", "httponly"}
    assert 3590 <= int(attrs["max-age"]) <= 3600
    assert attrs["path"] == "/"
    assert attrs["samesite"].lower() == "lax"


def test_expired_session_gets_max_age_zero() -> None:
    response = Response()
    set_session_cookie(response, "n", "tok", datetime.now(UTC) - timedelta(minutes=1))
    assert attributes(response)[1]["max-age"] == "0"


def test_clear_session_cookie_keeps_the_host_prefix_rules() -> None:
    response = Response()
    clear_session_cookie(response, session_cookie_name("a1b2c3d4"))
    pair, attrs = attributes(response)
    assert pair == '__Host-myha_a1b2c3d4_session=""'
    assert attrs["max-age"] == "0"
    assert {"secure", "httponly"} <= attrs.keys()
    assert attrs["path"] == "/"
    assert "domain" not in attrs
