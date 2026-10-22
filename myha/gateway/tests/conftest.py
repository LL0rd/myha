import os

import pytest


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore MYHA_* and SUPERVISOR_TOKEN from the developer's shell."""
    for key in list(os.environ):
        if key.upper().startswith("MYHA_") or key.upper() == "SUPERVISOR_TOKEN":
            monkeypatch.delenv(key)
