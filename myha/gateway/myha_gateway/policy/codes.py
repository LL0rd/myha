"""Decision codes in evaluation order and the public error codes they map to (design §4.1, §6)."""

from collections.abc import Mapping
from enum import StrEnum


class DecisionCode(StrEnum):
    """Admin-visible reasons, declared in evaluation order: the first failing check wins."""

    PREVIEW_CONTEXT = "PREVIEW_CONTEXT"
    SESSION_INACTIVE = "SESSION_INACTIVE"
    TIME_UNTRUSTED = "TIME_UNTRUSTED"
    STAY_NOT_STARTED = "STAY_NOT_STARTED"
    STAY_ENDED = "STAY_ENDED"
    RESOURCE_NOT_IN_SCOPE = "RESOURCE_NOT_IN_SCOPE"
    RESOURCE_UNCONFIRMED = "RESOURCE_UNCONFIRMED"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    PARAM_SCHEMA = "PARAM_SCHEMA"
    DENIED_BY_RULE = "DENIED_BY_RULE"
    NO_GRANT = "NO_GRANT"
    PARAM_OUT_OF_RANGE = "PARAM_OUT_OF_RANGE"
    PARAM_STEP = "PARAM_STEP"
    FENCE_VIOLATION = "FENCE_VIOLATION"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    RESOURCE_NEEDS_REVIEW = "RESOURCE_NEEDS_REVIEW"
    MODE_WOULD_BYPASS_FENCE = "MODE_WOULD_BYPASS_FENCE"
    ADAPTER_LOCKED = "ADAPTER_LOCKED"
    GRANTED = "GRANTED"


EVALUATION_ORDER: tuple[DecisionCode, ...] = tuple(DecisionCode)


class PublicError(StrEnum):
    """The only error codes public clients ever see (spec §12.1)."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    NOT_FOUND = "NOT_FOUND"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    ACCESS_EXPIRED = "ACCESS_EXPIRED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"


# Every denial; GRANTED has no public error.
PUBLIC_CODE: Mapping[DecisionCode, PublicError] = {
    DecisionCode.RESOURCE_NOT_IN_SCOPE: PublicError.NOT_FOUND,
    DecisionCode.RESOURCE_UNCONFIRMED: PublicError.NOT_FOUND,
    DecisionCode.UNKNOWN_ACTION: PublicError.NOT_FOUND,
    DecisionCode.NO_GRANT: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.DENIED_BY_RULE: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.FENCE_VIOLATION: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.CAPABILITY_MISSING: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.MODE_WOULD_BYPASS_FENCE: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.PREVIEW_CONTEXT: PublicError.ACTION_NOT_ALLOWED,
    DecisionCode.PARAM_SCHEMA: PublicError.INVALID_PARAMETERS,
    DecisionCode.PARAM_OUT_OF_RANGE: PublicError.INVALID_PARAMETERS,
    DecisionCode.PARAM_STEP: PublicError.INVALID_PARAMETERS,
    DecisionCode.SESSION_INACTIVE: PublicError.ACCESS_EXPIRED,
    DecisionCode.STAY_NOT_STARTED: PublicError.ACCESS_EXPIRED,
    DecisionCode.STAY_ENDED: PublicError.ACCESS_EXPIRED,
    DecisionCode.ADAPTER_LOCKED: PublicError.UPSTREAM_UNAVAILABLE,
    DecisionCode.RESOURCE_NEEDS_REVIEW: PublicError.UPSTREAM_UNAVAILABLE,
    DecisionCode.TIME_UNTRUSTED: PublicError.UPSTREAM_UNAVAILABLE,
}

HTTP_STATUS: Mapping[PublicError, int] = {
    PublicError.UNAUTHENTICATED: 401,
    PublicError.NOT_FOUND: 404,
    PublicError.ACTION_NOT_ALLOWED: 403,
    PublicError.INVALID_PARAMETERS: 400,
    PublicError.ACCESS_EXPIRED: 403,
    PublicError.UPSTREAM_UNAVAILABLE: 503,
    PublicError.RATE_LIMITED: 429,
    PublicError.CONFLICT: 409,
}


def public_error(code: DecisionCode) -> PublicError | None:
    """The public error for a denial; ``None`` for ``GRANTED``."""
    return PUBLIC_CODE.get(code)
