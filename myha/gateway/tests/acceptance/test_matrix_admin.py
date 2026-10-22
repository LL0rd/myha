"""A01–A05: the admin identity behind the Supervisor ingress (design E4, E5, E24)."""

import copy
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from myha_gateway.api.admin import session
from myha_gateway.app import create_admin_app, create_public_app
from myha_gateway.auth.admin import csrf_token, require_admin, require_csrf
from myha_gateway.deps import AppContext
from myha_gateway.errors import add_error_handlers
from myha_gateway.ha.supervisor import SupervisorClient
from myha_simulator.fixtures import HA_VERSION, SUPERVISOR_VERSION, USERS
from tests.acceptance.conftest import OWNER, Clocks
from tests.helpers import SimHandle, call

SESSION = "/api/v1/admin/session"
CHECK = "/api/v1/admin/session/check"
PROBE = "/api/v1/admin/probe"


def denied(r: httpx.Response) -> bool:
    return r.status_code == 403 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def unavailable(r: httpx.Response, reason: str) -> bool:
    error = r.json()["error"]
    return (r.status_code, error["code"], error.get("details")) == (
        503,
        "UPSTREAM_UNAVAILABLE",
        {"reason": reason},
    )


async def csrf(client: httpx.AsyncClient) -> str:
    """The token the admin UI gets from the session endpoint."""
    r = await client.get(SESSION)
    assert r.status_code == 200
    return str(r.json()["csrf_token"])


def owner_changed(**changes: Any) -> list[dict[str, Any]]:
    """The simulator's users with the owner's fields changed."""
    users = copy.deepcopy(USERS)
    users[0].update(changes)
    return users


async def test_a01_public_listener_ignores_identity_headers(ctx: AppContext) -> None:
    ctx.settings.probe_mode = True  # the mode in which the admin app has the S0 probe
    public = create_public_app(ctx)
    identity = {
        "X-Remote-User-Id": OWNER,
        "X-Remote-User-Name": "owner",
        "X-Ingress-Path": "/api/hassio_ingress/simtoken",
    }
    for method, path in [("GET", SESSION), ("POST", CHECK), ("GET", PROBE)]:
        r = await call(public, method, path, headers=identity)  # even from the ingress peer
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_a02_wrong_peer_rejected(
    admin_app: FastAPI, admin_client: httpx.AsyncClient, ctx: AppContext
) -> None:
    identity = {"X-Remote-User-Id": OWNER, "X-MyHA-CSRF": await csrf(admin_client)}
    for method, path in [("GET", SESSION), ("POST", CHECK), ("GET", "/")]:
        assert denied(await call(admin_app, method, path, peer="10.0.0.5", headers=identity))

    ctx.settings.probe_mode = True  # the S0 probe route exists in probe mode only
    probe_on = create_admin_app(ctx)
    assert denied(await call(probe_on, "GET", PROBE, peer="10.0.0.5", headers=identity))

    # require_admin checks the peer itself, not only the middleware around the admin app.
    bare = FastAPI()
    bare.state.admin_guard = admin_app.state.admin_guard
    add_error_handlers(bare, validation_details=True)
    bare.include_router(session.router)
    assert denied(await call(bare, "GET", SESSION, peer="10.0.0.5", headers=identity))
    assert (await call(bare, "GET", SESSION, headers=identity)).status_code == 200


async def test_a03_missing_duplicate_headers_and_old_supervisor(
    admin_app: FastAPI,
    admin_client: httpx.AsyncClient,
    ctx: AppContext,
    sim: SimHandle,
    clocks: Clocks,
) -> None:
    assert denied(await call(admin_app, "GET", SESSION))  # no X-Remote-User-Id
    for second in (OWNER, "user1"):
        twice = [("X-Remote-User-Id", OWNER), ("X-Remote-User-Id", second)]
        assert denied(await admin_client.get(SESSION, headers=twice))

    assert ctx.instance is not None
    token = csrf_token(ctx.instance.server_secret, OWNER, "", clocks.now.date())
    await sim.control.post("supervisor/version", {"version": "2026.10.2"})
    mutation = await admin_client.post(CHECK, headers={"X-MyHA-CSRF": token})
    assert unavailable(mutation, "supervisor_version")
    assert unavailable(await admin_client.get(SESSION), "supervisor_version")

    ctx.supervisor = SupervisorClient("http://127.0.0.1:1", None)  # connection refused
    clocks.advance(5)  # a failed check is reused for 5 s
    assert unavailable(await admin_client.get(SESSION), "supervisor_unreachable")


async def test_a04_demoted_admin_blocked_on_next_mutation(
    admin_client: httpx.AsyncClient, sim: SimHandle, clocks: Clocks
) -> None:
    await sim.control.post("auth/users", owner_changed(is_owner=False))  # admin by group only
    token = await csrf(admin_client)
    assert (await admin_client.post(CHECK, headers={"X-MyHA-CSRF": token})).status_code == 204

    demoted = owner_changed(is_owner=False, group_ids=["system-users"])
    await sim.control.post("auth/users", demoted)
    clocks.advance(4)
    assert (await admin_client.get(SESSION)).status_code == 200  # reads reuse the role for 5 s
    assert denied(await admin_client.post(CHECK, headers={"X-MyHA-CSRF": token}))
    assert denied(await admin_client.get(SESSION))  # the denial also ended the reuse


async def test_cached_role_expires_after_five_seconds(
    admin_client: httpx.AsyncClient, sim: SimHandle, clocks: Clocks
) -> None:
    assert (await admin_client.get(SESSION)).status_code == 200
    await sim.control.post("auth/users", owner_changed(is_active=False))
    clocks.advance(4)
    assert (await admin_client.get(SESSION)).status_code == 200
    clocks.advance(1)
    assert denied(await admin_client.get(SESSION))


async def auth_list_error(sim: SimHandle, ctx: AppContext) -> None:
    await sim.control.post("auth/list-mode", {"mode": "error"})


async def auth_list_without_groups(sim: SimHandle, ctx: AppContext) -> None:
    await sim.control.post("auth/list-mode", {"mode": "schema_missing_groups"})


async def core_unreachable(sim: SimHandle, ctx: AppContext) -> None:
    await ctx.ha.stop()


@pytest.mark.parametrize("fail", [auth_list_error, auth_list_without_groups, core_unreachable])
async def test_a05_auth_list_failure_no_fallback(
    fail: Callable[[SimHandle, AppContext], Awaitable[None]],
    admin_client: httpx.AsyncClient,
    ctx: AppContext,
    sim: SimHandle,
) -> None:
    token = await csrf(admin_client)  # a success the guard could fall back to
    await fail(sim, ctx)
    assert denied(await admin_client.post(CHECK, headers={"X-MyHA-CSRF": token}))
    assert denied(await admin_client.get(SESSION))  # within the 5 s reuse of that success


@pytest.mark.parametrize(
    ("changes", "allowed"),
    [
        ({}, True),
        ({"group_ids": []}, True),  # the owner needs no group
        ({"is_owner": False}, True),  # system-admin
        ({"is_owner": False, "group_ids": ["system-users"]}, False),
        ({"is_active": False}, False),  # an inactive owner
        ({"id": "someone-else"}, False),  # Core does not know the user of the header
    ],
)
async def test_admin_role_comes_from_core_only(
    changes: dict[str, Any], allowed: bool, admin_client: httpx.AsyncClient, sim: SimHandle
) -> None:
    await sim.control.post("auth/users", owner_changed(**changes))
    groups = {"X-Remote-User-Groups": "system-admin"}  # logged, never trusted (E5)
    r = await admin_client.get(SESSION, headers=groups)
    assert r.status_code == 200 if allowed else denied(r)


async def test_supervisor_answer_is_reused_for_60_seconds(
    admin_client: httpx.AsyncClient, sim: SimHandle, clocks: Clocks
) -> None:
    assert (await admin_client.get(SESSION)).status_code == 200
    await sim.control.post("supervisor/version", {"version": "2026.10.2"})
    clocks.advance(59)
    assert (await admin_client.get(SESSION)).status_code == 200  # no second GET /info
    clocks.advance(1)
    assert unavailable(await admin_client.get(SESSION), "supervisor_version")

    await sim.control.post("supervisor/version", {"version": SUPERVISOR_VERSION})
    clocks.advance(4)
    assert unavailable(await admin_client.get(SESSION), "supervisor_version")  # reused 5 s
    clocks.advance(1)
    assert (await admin_client.get(SESSION)).status_code == 200


async def test_supervisor_outage_costs_one_request_per_5_seconds(
    admin_client: httpx.AsyncClient, ctx: AppContext, clocks: Clocks
) -> None:
    requests: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise httpx.ConnectError("Connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    ctx.supervisor = SupervisorClient("http://supervisor", None, transport=transport)
    assert unavailable(await admin_client.get(SESSION), "supervisor_unreachable")
    clocks.advance(4)
    assert unavailable(await admin_client.get(SESSION), "supervisor_unreachable")
    assert requests == ["/info"]
    clocks.advance(1)
    assert unavailable(await admin_client.get(SESSION), "supervisor_unreachable")
    assert requests == ["/info", "/info"]


async def test_names_are_decoded_as_utf8_but_the_user_id_is_not(
    admin_client: httpx.AsyncClient, sim: SimHandle
) -> None:
    # ASGI servers decode header bytes as latin-1; the Supervisor sends UTF-8.
    await sim.control.post("auth/users", owner_changed(id="owner\u00e9"))
    user_id = "owner\u00e9".encode("latin-1")  # never re-decoded: it must match Core exactly
    named = {"X-Remote-User-Id": user_id, "X-Remote-User-Name": "jürgen".encode()}
    r = await admin_client.get(SESSION, headers=named)
    assert r.json()["user"] == {"id": "owner\u00e9", "name": "jürgen"}
    displayed = {**named, "X-Remote-User-Display-Name": "Jürgen Müller".encode()}
    r = await admin_client.get(SESSION, headers=displayed)
    assert r.json()["user"] == {"id": "owner\u00e9", "name": "Jürgen Müller"}


async def test_session_reports_identity_csrf_token_and_versions(
    admin_client: httpx.AsyncClient,
    ctx: AppContext,
    clocks: Clocks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    headers = {
        "X-Remote-User-Name": "owner",
        "X-Remote-User-Display-Name": "Owner",
        "X-Ingress-Path": "/api/hassio_ingress/simtoken",
    }
    r = await admin_client.get(SESSION, headers=headers)
    assert ctx.instance is not None
    secret = ctx.instance.server_secret
    token = csrf_token(secret, OWNER, "/api/hassio_ingress/simtoken", clocks.now.date())
    assert r.json() == {
        "user": {"id": OWNER, "name": "Owner"},
        "csrf_token": token,
        "supervisor_version": SUPERVISOR_VERSION,
        "core_version": HA_VERSION,
        "setup_state": "fresh",
        "time_trusted": True,
    }
    assert r.headers["cache-control"] == "no-store"
    assert token not in caplog.text and secret not in caplog.text


async def test_mutation_needs_a_csrf_token_of_today_or_yesterday(
    admin_client: httpx.AsyncClient, clocks: Clocks
) -> None:
    token = await csrf(admin_client)
    assert denied(await admin_client.post(CHECK))
    other_path = {"X-MyHA-CSRF": token, "X-Ingress-Path": "/api/hassio_ingress/other"}
    assert denied(await admin_client.post(CHECK, headers=other_path))
    cross_site = {"X-MyHA-CSRF": token, "Sec-Fetch-Site": "cross-site"}
    assert denied(await admin_client.post(CHECK, headers=cross_site))
    same_origin = {"X-MyHA-CSRF": token, "Sec-Fetch-Site": "same-origin"}
    assert (await admin_client.post(CHECK, headers=same_origin)).status_code == 204
    clocks.advance(24 * 3600)
    assert (await admin_client.post(CHECK, headers=same_origin)).status_code == 204
    clocks.advance(24 * 3600)
    assert denied(await admin_client.post(CHECK, headers=same_origin))


async def test_cross_origin_preflight_gets_no_cors_answer(admin_client: httpx.AsyncClient) -> None:
    # Why a missing Sec-Fetch-Site may pass: X-MyHA-CSRF forces this preflight, and it fails.
    preflight = {
        "Origin": "https://attacker.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-myha-csrf",
    }
    r = await admin_client.options(CHECK, headers=preflight)
    assert r.status_code == 405 and "access-control-allow-origin" not in r.headers


def _calls(dependant: Any) -> set[Any]:
    return {c for d in dependant.dependencies for c in (d.call, *_calls(d))}


async def test_every_admin_api_route_requires_the_guard(ctx: AppContext) -> None:
    guards = {require_admin(fresh=False), require_admin(fresh=True), require_csrf}
    ctx.settings.probe_mode = True  # every route the admin app can have
    checked = set()
    for context in iter_route_contexts(create_admin_app(ctx).routes):
        assert isinstance(context.original_route, APIRoute), context.path  # WS, Mount: own check
        if context.path == PROBE:  # S0 tool, probe mode only, peer guard only (task 1.6)
            continue
        calls = _calls(context.dependant)  # the route plus router-level dependencies
        assert calls & guards, context.path
        if context.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert require_csrf in calls, context.path
        checked.add(context.path)
    assert {SESSION, CHECK} <= checked  # never passes on an empty loop again
