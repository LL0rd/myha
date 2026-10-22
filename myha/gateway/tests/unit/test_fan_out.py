"""The app context hands each HA update to every handler, also after one of them failed."""

import logging

import pytest

from myha_gateway.deps import _fan_out


async def test_a_failing_handler_skips_none_of_the_others(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    async def broken(update: str) -> None:
        raise RuntimeError("handler bug")

    async def working(update: str) -> None:
        seen.append(update)

    with caplog.at_level(logging.ERROR):
        await _fan_out([broken, working], "update")
    assert seen == ["update"]
    assert [record.getMessage() for record in caplog.records] == [
        "a Home Assistant str handler failed"
    ]
