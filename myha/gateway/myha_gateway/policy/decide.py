"""The decision function: one request against an effective context (design §4.1, E7, E26).

Pure: no I/O and no logging, every input is passed in. The checks run in ``EVALUATION_ORDER``
and the first that fails decides.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Any

from myha_gateway.adapters.base import (
    WRITE_ACTIONS,
    Action,
    DeviceCapabilities,
    Expectation,
    StateView,
)
from myha_gateway.adapters.registry import adapter_for
from myha_gateway.policy.codes import DecisionCode, PublicError, public_error
from myha_gateway.policy.context import EffectiveContext
from myha_gateway.policy.index import PolicyIndex
from myha_gateway.policy.model import (
    PARAM_SCHEMA,
    Bound,
    Fence,
    Grant,
    Interval,
    ParamSchemaError,
    parse_params,
)

# Public-safe on purpose: no policy detail, and one text for every NOT_FOUND (A06, A15).
_MESSAGES: Mapping[PublicError | None, str] = {
    None: "The action is allowed.",
    PublicError.NOT_FOUND: "The resource or action was not found.",
    PublicError.ACTION_NOT_ALLOWED: "The action is not allowed.",
    PublicError.INVALID_PARAMETERS: "The parameters are not valid for this action.",
    PublicError.ACCESS_EXPIRED: "The access is not active at this time.",
    PublicError.UPSTREAM_UNAVAILABLE: "The device cannot be used right now.",
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: DecisionCode
    public_code: str | None  # None exactly when allowed
    normalized_params: dict[str, Any]  # from the parsed model; empty before or without parsing
    matched: tuple[str, ...]  # the grants that allow the request; empty for every denial
    message: str  # public-safe, never client input
    check_range: tuple[Decimal, Decimal] | None = None  # set_mode cool/heat (E7), when allowed


def decide(
    ctx: EffectiveContext,
    index: PolicyIndex,
    resource_id: str,
    action: Action | str,
    params: Mapping[str, Any],
    now: datetime,
    state_view: StateView | None,
    *,
    execute: bool = False,
) -> Decision:
    """Whether ``ctx`` may do ``action`` with ``params`` on the resource at ``now`` (aware).

    ``state_view`` is the adapter's view of the device, ``None`` while unknown; ``execute`` marks
    the endpoints that act, where a preview context is refused.
    """
    code = _window_code(ctx, now, execute)
    if code is not None:
        return _denial(code)
    resource = index.resource(resource_id)
    if resource is None or not index.in_scope(ctx, resource_id):
        return _denial(DecisionCode.RESOURCE_NOT_IN_SCOPE)
    if resource.status == "blocked":  # hidden completely, reads included
        return _denial(DecisionCode.RESOURCE_UNCONFIRMED)
    try:
        act = Action(action)
    except ValueError:
        return _denial(DecisionCode.UNKNOWN_ACTION)
    try:
        parsed = parse_params(act, params)
    except ParamSchemaError:
        return _denial(DecisionCode.PARAM_SCHEMA)
    name = next(iter(PARAM_SCHEMA[act].model_fields), None)  # every schema has at most one
    value: Any = None if name is None else getattr(parsed, name)
    normalized = parsed.model_dump()
    if name == "target_c":
        normalized[name] = _canonical(Decimal(value))

    capabilities = resource.capabilities
    # set_level 0 switches off, set_position moves: each action they need must be allowed too
    needed = adapter_for(capabilities.kind).required_actions(act, normalized) | {act}
    if any(index.denied(ctx, resource_id, need) for need in needed):
        return _denial(DecisionCode.DENIED_BY_RULE, normalized)
    grants = index.grants(ctx, resource_id, act) or _implied(ctx, index, resource_id, act)
    others = [index.grants(ctx, resource_id, need) for need in sorted(needed - {act})]
    if not grants or not all(others):
        return _denial(DecisionCode.NO_GRANT, normalized)

    fences = index.fences(ctx, resource_id, act)
    if name == "mode":
        code, allowing = _mode_code(value, grants, fences, capabilities)
    elif name is not None:
        cuts = index.grant_intervals(ctx, resource_id, act)
        code, allowing = _number_code(name, Decimal(value), cuts, fences)
    else:
        code, allowing = None, tuple(grants)
    if code is not None:
        return _denial(code, normalized)

    write = act in WRITE_ACTIONS
    # No device offers history in the MVP (design §4.1), whatever a role grants.
    if act is Action.HISTORY_READ or (write and act not in capabilities.actions):
        return _denial(DecisionCode.CAPABILITY_MISSING, normalized)
    if write and resource.status == "needs_review":
        return _denial(DecisionCode.RESOURCE_NEEDS_REVIEW, normalized)
    check_range = None
    if act is Action.CLIMATE_SET_MODE and value != "off":
        check_range = index.check_range(ctx, resource_id)  # low > high: no target passes
        if check_range is not None and (
            state_view is None  # an unknown target counts as outside
            or Expectation(target_c_range=check_range).violated_range(state_view)
        ):
            return _denial(DecisionCode.MODE_WOULD_BYPASS_FENCE, normalized)
    if write and state_view is not None and state_view.adapter_locked:
        return _denial(DecisionCode.ADAPTER_LOCKED, normalized)
    matched = dict.fromkeys([*allowing, *(grant_id for more in others for grant_id in more)])
    return Decision(
        allowed=True,
        code=DecisionCode.GRANTED,
        public_code=None,
        normalized_params=normalized,
        matched=tuple(matched),
        message=_MESSAGES[None],
        check_range=check_range,
    )


def _denial(code: DecisionCode, normalized: Mapping[str, Any] | None = None) -> Decision:
    public = public_error(code)
    return Decision(False, code, public, dict(normalized or {}), (), _MESSAGES[public])


def _window_code(ctx: EffectiveContext, now: datetime, execute: bool) -> DecisionCode | None:
    """Purpose, session and window: a stay's window is its own, any other is the session's."""
    if execute and ctx.purpose == "preview":
        return DecisionCode.PREVIEW_CONTEXT
    started = ctx.not_before is None or ctx.not_before <= now
    ended = ctx.not_after is not None and now >= ctx.not_after  # the end is exclusive
    if not ctx.session_active or (ctx.stay_id is None and (ended or not started)):
        return DecisionCode.SESSION_INACTIVE
    if ctx.stay_id is None:
        return None  # residents and displays need no trusted time (E26)
    if not ctx.time_trusted:
        return DecisionCode.TIME_UNTRUSTED
    if not started:
        return DecisionCode.STAY_NOT_STARTED
    return DecisionCode.STAY_ENDED if ended else None


def _implied(
    ctx: EffectiveContext, index: PolicyIndex, resource_id: str, action: Action
) -> dict[str, Grant]:
    """``metadata.read`` follows from every other action granted and not denied."""
    if action is not Action.METADATA_READ:
        return {}
    return {
        grant_id: grant
        for other in Action
        if other is not action and not index.denied(ctx, resource_id, other)
        for grant_id, grant in index.grants(ctx, resource_id, other).items()
    }


def _mode_code(
    mode: str,
    grants: Mapping[str, Grant],
    fences: list[Fence],
    capabilities: DeviceCapabilities,
) -> tuple[DecisionCode | None, tuple[str, ...]]:
    """A mode inside a grant's set and the admin-confirmed modes, then inside every fence."""
    allowing = tuple(
        grant_id
        for grant_id, grant in grants.items()
        if grant.parameters.mode is None or mode in grant.parameters.mode.allowed
    )
    if not allowing or mode not in capabilities.confirmed_modes:
        return DecisionCode.PARAM_OUT_OF_RANGE, ()
    rules = [fence.parameters.mode for fence in fences]
    if any(rule is not None and mode not in rule.allowed for rule in rules):
        return DecisionCode.FENCE_VIOLATION, ()
    return None, allowing


def _number_code(
    name: str, value: Decimal, cuts: Mapping[str, tuple[Interval, ...]], fences: list[Fence]
) -> tuple[DecisionCode | None, tuple[str, ...]]:
    """A value in a grant's settable interval, on that interval's step, then inside every fence.

    ``cuts`` are the intervals the effective capability starts from, so decide refuses nothing
    it offers and allows nothing it does not.
    """
    inside = [
        (gid, interval) for gid, cut in cuts.items() for interval in cut if interval.contains(value)
    ]
    if not inside:  # also for a grant with nothing admissible left: granted, but out of range
        return DecisionCode.PARAM_OUT_OF_RANGE, ()
    allowing = tuple(dict.fromkeys(gid for gid, interval in inside if _on_step(interval, value)))
    if not allowing:
        return DecisionCode.PARAM_STEP, ()
    bounds = [getattr(fence.parameters, name) for fence in fences]
    if any(isinstance(bound, Bound) and not bound.contains(value) for bound in bounds):
        return DecisionCode.FENCE_VIOLATION, ()
    return None, allowing


def _on_step(interval: Interval, value: Decimal) -> bool:
    try:
        return interval.on_step(value)
    except DecimalException:  # a step the exact context cannot resolve is no step (PARAM_STEP)
        return False


def _canonical(value: Decimal) -> str:
    """One spelling per number: ``22`` for 22.0 and 2E+1, ``5`` for 05, ``0`` for -0.0."""
    return format(value.normalize(), "f") if value else "0"
