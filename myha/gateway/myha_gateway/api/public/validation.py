"""Shape and size checks for ``POST /api/v1/actions``, before ``decide`` sees the request.

Refusals never echo a value: the body comes from the guest and may carry anything.
"""

import json
import uuid
from typing import Any

from pydantic import TypeAdapter, ValidationError

from myha_gateway.adapters.base import Action
from myha_gateway.errors import ApiError
from myha_gateway.policy.model import ProductId

MAX_PARAMS = 16
MAX_KEY = 64
MAX_PARAMS_JSON = 4096
_PRODUCT_ID: TypeAdapter[str] = TypeAdapter(ProductId)
_FIELDS = frozenset({"resource_id", "action", "params"})


def _invalid(message: str) -> ApiError:
    return ApiError(400, "INVALID_PARAMETERS", message)


def validate_action_request(body: Any) -> tuple[str, Action, dict[str, Any]]:
    """``(resource_id, action, params)`` of a well-formed body, else 400 ``INVALID_PARAMETERS``."""
    if not isinstance(body, dict) or not _FIELDS.issuperset(body):
        raise _invalid("Invalid request body")
    try:
        resource_id = _PRODUCT_ID.validate_python(body.get("resource_id"), strict=True)
    except ValidationError:
        raise _invalid("Invalid resource_id") from None
    raw_action = body.get("action")
    if not isinstance(raw_action, str) or raw_action not in Action:
        raise _invalid("Unknown action")
    params = body.get("params", {})
    if (
        not isinstance(params, dict)
        or len(params) > MAX_PARAMS
        or any(len(key) > MAX_KEY for key in params)
        or not all(
            value is None or isinstance(value, str | int | float) for value in params.values()
        )
        or len(json.dumps(params)) > MAX_PARAMS_JSON
    ):
        raise _invalid("Invalid params")
    return resource_id, Action(raw_action), params


def idempotency_key(header: str | None) -> str:
    """The ``Idempotency-Key`` header as a canonical lowercase UUID, else 400."""
    try:
        return str(uuid.UUID(header or ""))
    except ValueError:
        raise _invalid("Idempotency-Key must be a UUID") from None
