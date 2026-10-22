"""The ``quiet_timeguard`` fixture stops the guard of the running admin app."""

import asyncio

import pytest


@pytest.mark.usefixtures("quiet_timeguard")
async def test_quiet_timeguard_stops_the_guard() -> None:
    await asyncio.sleep(0)  # let the cancellation land
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "timeguard" and not t.done()]
