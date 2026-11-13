"""Keep ordinary tests offline; run hardware tests only on explicit opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

HARDWARE_DIR = Path(__file__).parent / "hardware"
OPT_IN_HELP = "hardware test: set SHOKZ_HARDWARE=1 and SHOKZ_DEVICE=<address> to run"


def hardware_enabled() -> bool:
    return os.environ.get("SHOKZ_HARDWARE") == "1" and bool(os.environ.get("SHOKZ_DEVICE"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if HARDWARE_DIR in item.path.parents:
            item.add_marker(pytest.mark.hardware)
        if "hardware" in item.keywords and not hardware_enabled():
            item.add_marker(pytest.mark.skip(reason=OPT_IN_HELP))


class _NoRealBus:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("an offline test tried to open a real D-Bus connection")


@pytest.fixture(autouse=True)
def _block_real_dbus(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if "hardware" not in request.keywords:
        monkeypatch.setattr("shokzer.transport.bluez.MessageBus", _NoRealBus)
