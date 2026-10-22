"""Random identifiers and secrets; secrets are only ever stored as SHA-256 hashes."""

import hashlib
import hmac
import secrets


def new_id(prefix: str) -> str:
    """Opaque identifier such as ``res_1a2b3c4d5e6f`` (48 random bits)."""
    return f"{prefix}_{secrets.token_hex(6)}"


def new_secret() -> str:
    """256-bit random secret, base64url without padding (43 characters)."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    """Hex SHA-256 of a secret, the form in which it is stored."""
    return hashlib.sha256(secret.encode()).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    """Timing-safe comparison; compares bytes so non-ASCII input cannot raise."""
    return hmac.compare_digest(a.encode(), b.encode())
