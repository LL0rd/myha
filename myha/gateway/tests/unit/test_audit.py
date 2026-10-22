"""The audit writer: one row per event, nothing but the audit fields."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from myha_gateway.db import create_engine, run_migrations, session_factory, write_session
from myha_gateway.db.models import AuditEvent as AuditRow
from myha_gateway.services.audit import AuditEvent, audit


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(tmp_path / "myha.sqlite")
    await run_migrations(engine)
    yield engine
    await engine.dispose()


async def rows(engine: AsyncEngine) -> list[AuditRow]:
    async with session_factory(engine)() as session:
        return list((await session.execute(select(AuditRow))).scalars())


async def test_audit_writes_one_row_with_the_audit_fields(engine: AsyncEngine) -> None:
    event = AuditEvent(
        actor="prn_guest",
        effective_principal="prn_guest",
        operation="action.execute",
        result="denied",
        reason_code="OUT_OF_SCOPE",
        resource_id="res_203_light",
        session_id="ses_1",
        policy_version=3,
        normalized_params={"level": 40, "transition": {"seconds": 2}},
        anon_key="auth.redeem|UNAUTHENTICATED|2026-10-22T12:00|203.0.113.9",
    )
    before = datetime.now(UTC)
    async with write_session(engine) as session:
        await audit(session, event)
    [row] = await rows(engine)
    assert before - timedelta(seconds=1) <= row.ts <= datetime.now(UTC)
    assert (row.actor, row.effective_principal, row.session_id) == ("prn_guest",) * 2 + ("ses_1",)
    assert (row.operation, row.result, row.reason_code) == (
        "action.execute",
        "denied",
        "OUT_OF_SCOPE",
    )
    assert (row.resource_id, row.policy_version) == ("res_203_light", 3)
    assert row.normalized_params == {"level": 40, "transition": {"seconds": 2}}
    assert row.anon_key == "auth.redeem|UNAUTHENTICATED|2026-10-22T12:00|203.0.113.9"
    assert row.ha_context_id is None
    assert row.aggregate_count == 1


async def test_anonymous_event_defaults(engine: AsyncEngine) -> None:
    async with write_session(engine) as session:
        await audit(session, AuditEvent(operation="auth.redeem", result="failed"))
    [row] = await rows(engine)
    assert row.actor == "anonymous"
    assert row.effective_principal is row.session_id is row.reason_code is None
    assert row.resource_id is row.policy_version is row.normalized_params is row.anon_key is None


async def test_audit_does_not_commit(engine: AsyncEngine) -> None:
    class Abort(Exception):
        pass

    with pytest.raises(Abort):
        async with write_session(engine) as session:
            await audit(session, AuditEvent(operation="stay.create", result="ok"))
            raise Abort  # rolls back the caller's transaction, the audit row with it
    assert await rows(engine) == []


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "invite_secret",
        "Password",
        "pin",
        "pairing_code",
        "X-MyHA-CSRF",
        "ws_ticket",
        "cookie",
        "auth",
        "Authorization",
        "credentials",
        "apiKey",
        "api_key",
        "API-KEY",
        "myApikeyValue",
        "bearer",
        "passphrase",
        "private_key",
        "accesstoken",
    ],
)
def test_secret_looking_keys_are_refused(key: str) -> None:
    for params in ({key: "x"}, {"nested": {key: "x"}}, {"items": [{key: "x"}]}):
        with pytest.raises(ValueError, match="normalized_params"):
            AuditEvent(operation="auth.redeem", result="ok", normalized_params=params)


@pytest.mark.parametrize(
    "key",
    ["reason_code", "language_code", "mapping", "keeping", "ping", "spin", "hvac_mode"]
    + ["target_c", "position", "brightness_pct", "author", "key", "private", "api"],
)
def test_ordinary_keys_pass(key: str) -> None:
    AuditEvent(operation="action.execute", result="ok", normalized_params={key: 1})


async def test_audit_checks_the_params_again_on_write(engine: AsyncEngine) -> None:
    params: dict[str, Any] = {"level": 40}
    event = AuditEvent(operation="action.execute", result="ok", normalized_params=params)
    params["token"] = "added after the check"
    async with write_session(engine) as session:
        with pytest.raises(ValueError, match="normalized_params"):
            await audit(session, event)
    assert await rows(engine) == []


def test_event_has_no_payload_fields_beyond_the_audit_columns() -> None:
    with pytest.raises(TypeError):
        AuditEvent(operation="auth.redeem", result="ok", token="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AuditEvent(operation="auth.redeem", result="ok", secret="x")  # type: ignore[call-arg]


def test_event_is_frozen() -> None:
    event = AuditEvent(operation="auth.redeem", result="ok")
    with pytest.raises(AttributeError):
        event.actor = "prn_x"  # type: ignore[misc]


def test_anon_key_is_at_most_128_characters() -> None:
    AuditEvent(operation="auth.redeem", result="failed", anon_key="k" * 128)
    with pytest.raises(ValueError, match="anon_key"):
        AuditEvent(operation="auth.redeem", result="failed", anon_key="k" * 129)


async def test_fold_index_is_partial_on_anon_key(engine: AsyncEngine) -> None:
    query = text("SELECT sql FROM sqlite_master WHERE name = 'ix_audit_event_fold'")
    async with session_factory(engine)() as session:
        sql = (await session.execute(query)).scalar_one()
    assert "(anon_key)" in sql and sql.endswith("WHERE anon_key IS NOT NULL")
