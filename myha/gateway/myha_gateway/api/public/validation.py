"""Shape and size checks for ``POST /api/v1/actions``, before ``decide`` sees the request.

Refusals never echo a value: the body comes from the guest and may carry anything.
"""

import json
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from myha_gateway.adapters.base import Action
from myha_gateway.errors import ApiError
from myha_gateway.policy.model import ProductId

MAX_PARAMS = 16
MAX_KEY = 64
MAX_PARAMS_JSON = 4096
_PRODUCT_ID: TypeAdapter[str] = TypeAdapter(ProductId)
_FIELDS = frozenset({"resource_id", "action", "parameters"})
# The canonical 8-4-4-4-12 form only: no braces, URN or bare hex, so one key has one spelling.
_UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _invalid(message: str) -> ApiError:
    return ApiError(400, "INVALID_PARAMETERS", message)


def validate_action_request(body: Any) -> tuple[str, Action, dict[str, Any]]:
    """``(resource_id, action, parameters)`` of a well-formed body, else 400."""
    if not isinstance(body, dict) or not _FIELDS.issuperset(body):
        raise _invalid("Invalid request body")
    try:
        resource_id = _PRODUCT_ID.validate_python(body.get("resource_id"), strict=True)
    except ValidationError:
        raise _invalid("Invalid resource_id") from None
    raw_action = body.get("action")
    if not isinstance(raw_action, str) or raw_action not in Action:
        raise _invalid("Unknown action")
    params = body.get("parameters", {})
    if (
        not isinstance(params, dict)
        or len(params) > MAX_PARAMS
        or not all(0 < len(key) <= MAX_KEY and key.isprintable() for key in params)
        or not all(
            value is None or isinstance(value, str | int | float) for value in params.values()
        )
        or _size(params) > MAX_PARAMS_JSON
    ):
        raise _invalid("Invalid parameters")
    return resource_id, Action(raw_action), params


def _size(params: dict[str, Any]) -> float:
    """The UTF-8 size of ``params`` as compact JSON; infinite for NaN and infinities."""
    try:
        text = json.dumps(params, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        return float("inf")
    return len(text.encode())


def idempotency_key(header: str | None) -> str:
    """The ``Idempotency-Key`` header, a UUID in its 8-4-4-4-12 form, lowercased; else 400."""
    if header is None or not _UUID.fullmatch(header):
        raise _invalid("Idempotency-Key must be a UUID")
    return header.lower()
