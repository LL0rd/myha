"""Validation of POST /api/v1/actions before decide(): shape and size, never echoing values."""

import json
from typing import Any

import pytest

from myha_gateway.adapters.base import Action
from myha_gateway.api.public.validation import idempotency_key, validate_action_request
from myha_gateway.errors import ApiError

VALID = {"resource_id": "res_203_light", "action": "light.set_level", "params": {"level_pct": 40}}


def refused(body: Any) -> ApiError:
    with pytest.raises(ApiError) as caught:
        validate_action_request(body)
    assert (caught.value.status, caught.value.code) == (400, "INVALID_PARAMETERS")
    return caught.value


def test_valid_request_returns_its_parts() -> None:
    assert validate_action_request(VALID) == (
        "res_203_light",
        Action.LIGHT_SET_LEVEL,
        {"level_pct": 40},
    )


def test_params_default_to_empty() -> None:
    body = {"resource_id": "res_203_light", "action": "light.on"}
    assert validate_action_request(body) == ("res_203_light", Action.LIGHT_ON, {})


def test_scalar_values_pass() -> None:
    params = {"a": "x", "b": 1, "c": 1.5, "d": True, "e": None}
    assert validate_action_request(VALID | {"params": params})[2] == params


@pytest.mark.parametrize(
    "body",
    [
        [],
        "light.on",
        {"action": "light.on"},
        VALID | {"resource_id": 7},
        VALID | {"resource_id": "res 203"},
        VALID | {"resource_id": "r" * 65},
        VALID | {"resource_id": ""},
        VALID | {"action": "light.explode"},
        VALID | {"action": "a" * 65},
        VALID | {"action": None},
        VALID | {"params": [1]},
        VALID | {"params": "level=40"},
        VALID | {"params": {"level_pct": {"nested": 1}}},
        VALID | {"params": {"level_pct": [40]}},
        VALID | {"params": {f"k{i}": i for i in range(17)}},
        VALID | {"params": {"k" * 65: 1}},
        VALID | {"params": {"note": "x" * 4096}},
        VALID | {"extra": 1},
    ],
)
def test_malformed_requests_are_refused(body: Any) -> None:
    refused(body)


def test_refusal_never_echoes_values() -> None:
    secret = "do not echo 0123456789"
    for body in (
        VALID | {"resource_id": secret},
        VALID | {"action": secret},
        VALID | {"params": {secret: [secret]}},
    ):
        error = refused(body)
        assert secret not in json.dumps([error.message, error.details])


def test_idempotency_key_is_a_canonical_uuid() -> None:
    upper = "6F9619FF-8B86-D011-B42D-00C04FC964FF"
    assert idempotency_key(upper) == upper.lower()
    assert idempotency_key("{6f9619ff-8b86-d011-b42d-00c04fc964ff}") == upper.lower()


@pytest.mark.parametrize("header", [None, "", "not-a-uuid", "6f9619ff8b86d011b42d00c04fc964ff0"])
def test_idempotency_key_missing_or_malformed_is_refused(header: str | None) -> None:
    with pytest.raises(ApiError) as caught:
        idempotency_key(header)
    assert (caught.value.status, caught.value.code) == (400, "INVALID_PARAMETERS")
