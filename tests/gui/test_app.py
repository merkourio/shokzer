"""Run the real Application on the GLib loop with a simulated headset. Opt-in: SHOKZ_GUI=1.

Proves the asyncio-on-GLib wiring: the controller starts from do_activate, a management
session runs as a task, the window renders the result, and closing the window waits for the
session cleanup before the app exits.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fake_headset import TEST_FIRMWARE, FakeConnector
from fakes import FakeBluez, headset_tree
from sim_headset import SimHeadset

from shokzctl import capabilities as caps
from shokzctl.capabilities import PROPRIETARY_FEATURES
from shokzctl.lock import SessionLock

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(os.environ.get("SHOKZ_GUI") != "1", reason="GUI test: set SHOKZ_GUI=1"),
]


def test_app_reads_headset_and_shuts_down_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(caps, "VERIFIED", {TEST_FIRMWARE: frozenset(PROPRIETARY_FEATURES)})
    from gi.events import GLibEventLoopPolicy
    from gi.repository import GLib

    from shokzctl.ui.app import Application
    from shokzctl.ui.controller import Controller

    sim = SimHeadset()

    def controller() -> Controller:
        return Controller(
            connect_bus=FakeBluez(headset_tree(battery=60)),
            open_connector=FakeConnector(sim),
            lock_factory=lambda: SessionLock(tmp_path / "lock"),
        )

    seen: dict[str, Any] = {}
    app = Application(controller, application_id="org.example.ShokzctlTest")

    def check() -> bool:
        state = app.controller.state if app.controller else None
        if state is not None and state.firmware and state.busy is None:
            seen["firmware"] = state.firmware
            seen["eq"] = state.eq
            window = app.get_active_window()
            seen["window"] = window.get_title() if window else None
            window.close()  # goes through close-request -> controller.close()
            return False
        return True

    def give_up() -> bool:
        seen["timeout"] = True
        app.quit()
        return False

    GLib.timeout_add(50, check)
    GLib.timeout_add_seconds(10, give_up)
    with GLibEventLoopPolicy():
        status = app.run([])

    assert "timeout" not in seen
    assert status == 0
    assert seen["firmware"] == TEST_FIRMWARE
    assert seen["eq"] is not None
    assert seen["window"] == "OpenRun Pro 2"
    assert sim.closed
    with SessionLock(tmp_path / "lock"):
        pass
