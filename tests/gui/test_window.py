"""Render the window in representative states. Opt-in: SHOKZ_GUI=1, needs a display.

Set SHOKZ_GUI_SHOTS=DIR to also save each state as a PNG for a visual check. Uses a stub
controller; no D-Bus and no headset are involved.
"""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fakes import OWNER, headset_tree
from sim_headset import PHONE, THIS_COMPUTER

from shokzer.capabilities import PROPRIETARY_FEATURES, capabilities
from shokzer.device import Selector, build_report
from shokzer.protocol.commands import EqPreset
from shokzer.protocol.responses import (
    Equalizer,
    MultipointDevices,
    MultipointPeer,
    MultipointStatus,
)
from shokzer.service import Multipoint
from shokzer.state import Support
from shokzer.transport.bluez import BluezTree, Snapshot
from shokzer.ui.controller import ViewState

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(os.environ.get("SHOKZ_GUI") != "1", reason="GUI test: set SHOKZ_GUI=1"),
]

FIRMWARE = "BOS_HU_R_47_20251023"


class StubController:
    def __init__(self, state: ViewState) -> None:
        self.state = state
        self.on_change: Callable[[ViewState], None] = lambda _: None
        self.spawned: list[Any] = []

    def spawn(self, action: Any) -> None:
        self.spawned.append(action)
        action.close()  # never run: rendering only


@pytest.fixture(scope="module")
def gtk() -> Any:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    Adw.init()
    return (Adw, GLib, Gtk)


def base_state(**changes: Any) -> ViewState:
    tree = BluezTree()
    tree.load(Snapshot(headset_tree(battery=60), OWNER, 1))
    report = build_report(tree, Selector())
    state = ViewState(
        report=report,
        firmware=FIRMWARE,
        headset_battery=60,
        eq=Equalizer(1, EqPreset.STANDARD, None, False),
        capabilities=all_supported(),
    )
    return dataclasses.replace(state, **changes)


def all_supported() -> dict[str, Support]:
    return dict.fromkeys(PROPRIETARY_FEATURES, Support.SUPPORTED)


def multipoint() -> Multipoint:
    peers = (
        MultipointPeer(0, THIS_COMPUTER, True, "fedora"),
        MultipointPeer(1, PHONE, False, "Pixel 10"),
    )
    return Multipoint(MultipointStatus(True, 0x00010100), MultipointDevices(8, peers, 0))


def states() -> dict[str, ViewState]:
    return {
        "untested-firmware": base_state(
            firmware="BOS_HU_R_99_20260101",
            capabilities=dict.fromkeys(PROPRIETARY_FEATURES, Support.UNTESTED),
            multipoint=multipoint(),
        ),
        "all-verified": base_state(
            capabilities=all_supported(),
            eq=Equalizer(5, EqPreset.CUSTOM, (-2, 0, 1, 3, -5), False),
            multipoint=multipoint(),
        ),
        "busy": base_state(capabilities=all_supported(), busy="Setting Vocal", pending="eq:vocal"),
        "classic": base_state(
            capabilities=capabilities(FIRMWARE),
            eq=Equalizer(8, EqPreset.CLASSIC, None, False),
        ),
        "no-headset": ViewState(bluez_error="BlueZ isn't running."),
    }


@pytest.mark.parametrize(
    "name", ["untested-firmware", "all-verified", "busy", "no-headset", "classic"]
)
def test_window_renders(gtk: Any, name: str, tmp_path: Path) -> None:
    _adw, glib, gtk_mod = gtk
    from shokzer.ui.window import MainWindow

    state = states()[name]
    window = MainWindow(None, StubController(state))  # type: ignore[arg-type]
    window.present()
    _pump(glib, 0.6)

    custom_visible = window._custom_group.get_visible()
    connections_visible = window._connections.get_visible()
    if name == "untested-firmware":
        # Everything works on untested firmware; a banner asks for a report.
        assert window._banner.get_revealed()
        assert custom_visible
        assert connections_visible
        assert window._preset_rows[EqPreset.VOCAL].get_sensitive()
        assert window._preset_checks[EqPreset.STANDARD].get_active()
        assert "Not hardware-verified" in window._preset_rows[EqPreset.CLASSIC].get_subtitle()
    if name == "all-verified":
        assert not window._banner.get_revealed()
        assert custom_visible
        assert connections_visible
        assert [int(b.get_value()) for b in window._bands] == [-2, 0, 1, 3, -5]
        assert window._preset_checks[EqPreset.CUSTOM].get_active()
    if name == "busy":
        assert not window._refresh.get_sensitive()
        assert window._preset_spinners[EqPreset.VOCAL].get_visible()
        assert not window._preset_rows[EqPreset.CLASSIC].get_sensitive()
    if name == "classic":
        assert not window._banner.get_revealed()
        assert window._preset_checks[EqPreset.CLASSIC].get_active()
        assert not window._preset_checks[EqPreset.STANDARD].get_active()
        assert window._preset_rows[EqPreset.CLASSIC].get_sensitive()
        assert window._preset_rows[EqPreset.CLASSIC].get_subtitle() == "Air speaker off"
        assert window._preset_rows[EqPreset.STANDARD].get_title() == "Standard (Hybrid)"
    if name == "no-headset":
        assert window._stack.get_visible_child_name() == "missing"

    shots = os.environ.get("SHOKZ_GUI_SHOTS")
    if shots:
        _save_png(window, gtk_mod, Path(shots) / f"{name}.png")
    window.destroy()
    _pump(glib, 0.1)


def _pump(glib: Any, seconds: float) -> None:
    context = glib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.01)


def _save_png(window: Any, gtk_mod: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = window.get_width(), window.get_height()
    paintable = gtk_mod.WidgetPaintable.new(window)
    snapshot = gtk_mod.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    assert node is not None, "nothing was drawn"
    texture = window.get_renderer().render_texture(node, None)
    texture.save_to_png(str(path))
