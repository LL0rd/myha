import re

from myha_gateway.ids import constant_time_equal, hash_secret, new_id, new_secret


def test_new_id_is_prefix_and_12_hex() -> None:
    assert re.fullmatch(r"res_[0-9a-f]{12}", new_id("res"))
    assert new_id("res") != new_id("res")


def test_new_secret_is_base64url_of_at_least_43_chars() -> None:
    secret = new_secret()
    assert re.fullmatch(r"[A-Za-z0-9_-]{43,}", secret)
    assert secret != new_secret()


def test_hash_secret_is_hex_sha256() -> None:
    sha256_abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hash_secret("abc") == sha256_abc
    assert re.fullmatch(r"[0-9a-f]{64}", hash_secret(new_secret()))


def test_constant_time_equal() -> None:
    s = new_secret()
    assert constant_time_equal(hash_secret(s), hash_secret(s))
    assert not constant_time_equal(hash_secret(s), hash_secret(new_secret()))
    assert not constant_time_equal("é", "e")  # non-ASCII input must not raise
