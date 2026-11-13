from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fake_headset import TEST_FIRMWARE, FakeConnector
from fakes import HEADSET, FakeBluez, device_path, headset_tree, settle
from sim_headset import SimHeadset, SimState

from shokzctl import capabilities as caps
from shokzctl.capabilities import PROPRIETARY_FEATURES
from shokzctl.lock import SessionLock
from shokzctl.protocol.commands import EqPreset
from shokzctl.state import Connection
from shokzctl.transport.bluez import DEVICE
from shokzctl.ui.controller import Controller, ViewState

HEADSET_PATH = device_path(HEADSET)


@pytest.fixture
def all_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(caps, "VERIFIED", {TEST_FIRMWARE: frozenset(PROPRIETARY_FEATURES)})


def make(
    tmp_path: Path, bluez: FakeBluez, sim: SimHeadset | None = None
) -> tuple[Controller, list[ViewState], SimHeadset]:
    sim = sim or SimHeadset()
    states: list[ViewState] = []
    controller = Controller(
        connect_bus=bluez,
        open_connector=FakeConnector(sim),
        lock_factory=lambda: SessionLock(tmp_path / "lock"),
        on_change=states.append,
    )
    return controller, states, sim


@pytest.mark.usefixtures("all_verified")
async def test_start_reads_the_connected_headset(tmp_path: Path) -> None:
    controller, states, _ = make(tmp_path, FakeBluez(headset_tree(battery=60)))
    await controller.start()
    state = controller.state
    assert state.connected
    assert (state.firmware, state.headset_battery) == (TEST_FIRMWARE, 60)
    assert state.eq is not None
    assert state.eq.preset is EqPreset.STANDARD
    assert state.multipoint is not None
    assert state.multipoint.status.enabled is True
    assert state.busy is None
    assert "Reading" in {s.busy for s in states}
    await controller.close()


async def test_unverified_firmware_reads_nothing_but_firmware(tmp_path: Path) -> None:
    controller, _, sim = make(tmp_path, FakeBluez(headset_tree()))
    await controller.start()
    assert controller.state.firmware == TEST_FIRMWARE
    assert controller.state.eq is None
    assert controller.state.multipoint is None
    assert [m.code for m in sim.sent] == [0x0002]
    assert not controller.state.can("eq_write_vocal")
    await controller.close()


async def test_bluez_missing(tmp_path: Path) -> None:
    controller, _, _ = make(tmp_path, FakeBluez(owner=None))
    await controller.start()
    assert controller.state.report is None
    assert "BlueZ isn't running" in (controller.state.bluez_error or "")
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_set_preset_updates_the_confirmed_eq(tmp_path: Path) -> None:
    controller, states, sim = make(tmp_path, FakeBluez(headset_tree()))
    await controller.start()
    await controller.set_preset(EqPreset.VOCAL)
    assert controller.state.eq is not None
    assert controller.state.eq.preset is EqPreset.VOCAL
    assert controller.state.message == "EQ set to Vocal"
    assert "eq:vocal" in {s.pending for s in states}
    assert controller.state.pending is None
    assert len(sim.writes()) == 1
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_change_that_doesnt_land_is_reported(tmp_path: Path) -> None:
    sim = SimHeadset(SimState(apply_writes=False))
    controller, _, _ = make(tmp_path, FakeBluez(headset_tree()), sim)
    await controller.start()
    await controller.set_preset(EqPreset.BASS)
    assert controller.state.message == "EQ didn't change to Bass Boost"
    assert controller.state.eq is not None
    assert controller.state.eq.preset is EqPreset.STANDARD
    await controller.close()


async def test_unverified_change_shows_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(caps, "VERIFIED", {TEST_FIRMWARE: frozenset({"eq_read"})})
    controller, _, sim = make(tmp_path, FakeBluez(headset_tree()))
    await controller.start()
    await controller.set_preset(EqPreset.VOCAL)
    assert "isn't verified" in (controller.state.message or "")
    assert sim.writes() == []
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_disconnect_forgets_headset_values_and_reconnect_rereads(tmp_path: Path) -> None:
    bluez = FakeBluez(headset_tree())
    controller, _, sim = make(tmp_path, bluez)
    await controller.start()
    bluez.change(HEADSET_PATH, DEVICE, {"Connected": False})
    await settle()
    assert controller.state.report is not None
    assert controller.state.report.device.connection is Connection.DISCONNECTED
    assert (controller.state.firmware, controller.state.eq) == (None, None)
    reads_before = len(sim.sent)
    bluez.change(HEADSET_PATH, DEVICE, {"Connected": True})
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    assert controller.state.firmware == TEST_FIRMWARE
    assert len(sim.sent) > reads_before
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_second_action_while_busy_is_ignored(tmp_path: Path) -> None:
    controller, _, sim = make(tmp_path, FakeBluez(headset_tree()))
    await controller.start()
    first = asyncio.create_task(controller.set_preset(EqPreset.VOCAL))
    await asyncio.sleep(0)
    await controller.set_preset(EqPreset.TREBLE)
    await first
    assert [m.args[0] for m in sim.writes()] == [0x02]
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_multipoint_toggle(tmp_path: Path) -> None:
    controller, _, sim = make(tmp_path, FakeBluez(headset_tree()))
    await controller.start()
    await controller.set_multipoint(False)
    assert controller.state.message == "Multipoint set to off"
    assert controller.state.multipoint is not None
    assert [(m.group, m.code) for m in sim.writes()] == [(0x01, 0x0010)]
    await controller.close()


@pytest.mark.usefixtures("all_verified")
async def test_close_waits_for_running_session(tmp_path: Path) -> None:
    sim = SimHeadset()
    controller, _, _ = make(tmp_path, FakeBluez(headset_tree()), sim)
    await controller.start()
    controller.spawn(controller.refresh())
    await asyncio.sleep(0)
    await controller.close()
    assert sim.closed
    with SessionLock(tmp_path / "lock"):
        pass  # released
