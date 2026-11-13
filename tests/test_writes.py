from __future__ import annotations

from typing import Any

import pytest
from fake_headset import TEST_FIRMWARE
from fakes import OWNER, headset_tree
from sim_headset import (
    LAPTOP,
    MULTIPOINT_OFF_RAW,
    PHONE,
    THIS_COMPUTER,
    UNNAMED_MULTIPOINT_RAW,
    Peer,
    SimHeadset,
)

from shokzctl import capabilities as caps
from shokzctl.device import Selector, build_report
from shokzctl.errors import ErrorCategory, ShokzError
from shokzctl.protocol import responses
from shokzctl.protocol.commands import EqPreset
from shokzctl.protocol.packets import Message
from shokzctl.service import Headset, WriteOutcome, _with_firmware
from shokzctl.session import ManagementSession
from shokzctl.transport.bluez import BluezTree, Snapshot

VOCAL_ARGS = bytes.fromhex("02fafe0405010000")


@pytest.fixture
def sim() -> SimHeadset:
    return SimHeadset()


async def open_headset(sim: SimHeadset, **options: Any) -> Headset:
    tree = BluezTree()
    tree.load(Snapshot(headset_tree(), OWNER, 1))
    report = _with_firmware(build_report(tree, Selector()), TEST_FIRMWARE)
    session = ManagementSession(sim, reply_timeout=0.05)
    session.start()
    defaults: dict[str, Any] = {
        "readback_interval": 0,
        "peer_interval": 0.01,
        "peer_timeout": 0.05,
    }
    return Headset(session, report, **(defaults | options))


def eq_writes(sim: SimHeadset) -> list[Message]:
    return [m for m in sim.sent if (m.group, m.code) == (0x01, 0x000E)]


# --- presets ---------------------------------------------------------------------------------


async def test_preset_is_confirmed_by_readback(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL, allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert (result.previous, result.readback, result.acknowledged) == ("standard", "vocal", True)
    assert not result.verified
    assert [(m.group, m.code) for m in sim.sent] == [(2, 0x0008), (1, 0x000E), (2, 0x0008)]
    assert eq_writes(sim)[0].args == VOCAL_ARGS
    assert headset.report.active_eq.value == "vocal"


async def test_already_set_sends_nothing(sim: SimHeadset) -> None:
    sim.state.eq_code = 0x02
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL, allow_unverified=True)
    assert result.outcome is WriteOutcome.ALREADY_SET
    assert eq_writes(sim) == []


async def test_missing_acknowledgement_still_reads_back(sim: SimHeadset) -> None:
    sim.state.ack_writes = False
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.BASS, allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert result.acknowledged is False
    assert len(eq_writes(sim)) == 1


async def test_change_that_doesnt_land_is_not_applied_and_not_resent(sim: SimHeadset) -> None:
    sim.state.apply_writes = False
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.TREBLE, allow_unverified=True)
    assert result.outcome is WriteOutcome.NOT_APPLIED
    assert result.readback == "standard"
    assert len(eq_writes(sim)) == 1
    assert len([m for m in sim.sent if m.code == 0x0008]) == 4  # before + 3 readbacks


async def test_slow_change_is_confirmed_on_a_later_read(sim: SimHeadset) -> None:
    sim.state.apply_after_reads = 1
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL, allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert len(eq_writes(sim)) == 1


async def test_channel_lost_after_write_is_uncertain(sim: SimHeadset) -> None:
    sim.state.hang_up_after_write = True
    sim.state.ack_writes = False
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL, allow_unverified=True)
    assert result.outcome is WriteOutcome.UNCERTAIN
    assert result.readback is None
    assert len(eq_writes(sim)) == 1


async def test_unverified_write_is_refused_without_opt_in(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    with pytest.raises(ShokzError) as caught:
        await headset.set_preset(EqPreset.VOCAL)
    assert caught.value.category is ErrorCategory.UNSUPPORTED
    assert "--allow-unverified" in (caught.value.hint or "")
    assert sim.sent == []


async def test_verified_write_needs_no_opt_in(
    sim: SimHeadset, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = frozenset({"eq_read", "eq_write_vocal"})
    monkeypatch.setattr(caps, "VERIFIED", {TEST_FIRMWARE: features})
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert result.verified


@pytest.mark.parametrize("preset", [EqPreset.CLASSIC, EqPreset.VOLUME_BOOST, EqPreset.SWIMMING])
async def test_presets_without_pro2_bytes_are_refused(sim: SimHeadset, preset: EqPreset) -> None:
    headset = await open_headset(sim)
    with pytest.raises(ShokzError) as caught:
        await headset.set_preset(preset, allow_unverified=True)
    assert caught.value.category is ErrorCategory.UNSUPPORTED
    assert sim.sent == []


# --- custom EQ -------------------------------------------------------------------------------


async def test_custom_eq_is_confirmed_by_band_readback(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    result = await headset.set_custom([-5, -1, 0, 2, 5], allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert result.readback == "custom (-5, -1, 0, +2, +5)"
    assert eq_writes(sim)[0].args == bytes([0x05, 0xFB, 0xFF, 0x00, 0x02, 0x05, 0x00, 0x00])


async def test_custom_eq_without_band_readback_is_partly_confirmed(sim: SimHeadset) -> None:
    sim.state.report_bands = False
    headset = await open_headset(sim)
    result = await headset.set_custom([1, 1, 1, 1, 1], allow_unverified=True)
    assert result.outcome is WriteOutcome.PARTLY_CONFIRMED
    assert result.ok


async def test_custom_eq_already_set(sim: SimHeadset) -> None:
    sim.state.eq_code, sim.state.bands = 0x05, (1, 0, 0, 0, -1)
    headset = await open_headset(sim)
    result = await headset.set_custom([1, 0, 0, 0, -1], allow_unverified=True)
    assert result.outcome is WriteOutcome.ALREADY_SET
    assert eq_writes(sim) == []


@pytest.mark.parametrize("bands", [[0, 0, 0, 0], [6, 0, 0, 0, 0], [0, 0, 0, 0, -6]])
async def test_invalid_custom_eq_sends_nothing(sim: SimHeadset, bands: list[int]) -> None:
    headset = await open_headset(sim)
    with pytest.raises(ShokzError) as caught:
        await headset.set_custom(bands, allow_unverified=True)
    assert caught.value.category is ErrorCategory.INVALID_INPUT
    assert sim.sent == []


# --- multipoint ------------------------------------------------------------------------------


async def test_multipoint_status_and_devices(sim: SimHeadset) -> None:
    sim.state.garbled_tail = True
    headset = await open_headset(sim)
    state = await headset.read_multipoint(allow_unverified=True)
    assert state.status.enabled is True
    assert [(p.address, p.name, p.connected) for p in state.devices.peers] == [
        (THIS_COMPUTER, "fedora", True),
        (PHONE, "Pixel 10", False),
    ]
    assert state.devices.reported_count == 8
    assert state.devices.skipped == 1


async def test_multipoint_on_is_confirmed(sim: SimHeadset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(responses, "MULTIPOINT_OFF_VALUES", frozenset({MULTIPOINT_OFF_RAW}))
    sim.state.multipoint_raw = MULTIPOINT_OFF_RAW
    headset = await open_headset(sim)
    result = await headset.set_multipoint(True, allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert result.previous == "off"


async def test_multipoint_change_from_an_unknown_value_is_refused(sim: SimHeadset) -> None:
    sim.state.multipoint_raw = UNNAMED_MULTIPOINT_RAW
    headset = await open_headset(sim)
    with pytest.raises(ShokzError, match="no source names"):
        await headset.set_multipoint(True, allow_unverified=True)
    assert sim.writes() == []


async def test_multipoint_off_sends_this_computers_address(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    result = await headset.set_multipoint(False, allow_unverified=True)
    off = [m for m in sim.sent if (m.group, m.code) == (0x01, 0x0010)]
    assert off[0].args == bytes.fromhex(THIS_COMPUTER.replace(":", ""))[::-1]
    assert result.outcome is WriteOutcome.CONFIRMED  # off value observed in test C2


async def test_multipoint_off_to_an_unnamed_value_is_uncertain(
    sim: SimHeadset, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(responses, "MULTIPOINT_OFF_VALUES", frozenset())
    headset = await open_headset(sim)
    result = await headset.set_multipoint(False, allow_unverified=True)
    assert result.outcome is WriteOutcome.UNCERTAIN
    assert "no source names" in (result.detail or "")


async def test_multipoint_refusal_status(sim: SimHeadset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(responses, "MULTIPOINT_OFF_VALUES", frozenset({MULTIPOINT_OFF_RAW}))
    sim.state.multipoint_raw = MULTIPOINT_OFF_RAW
    sim.state.write_status = 0x05
    headset = await open_headset(sim)
    result = await headset.set_multipoint(True, allow_unverified=True)
    assert result.outcome is WriteOutcome.REJECTED
    assert "0x00000005" in (result.detail or "")


# --- remembered devices ----------------------------------------------------------------------


async def test_connect_remembered_device(sim: SimHeadset) -> None:
    sim.state.apply_after_reads = 2
    headset = await open_headset(sim, peer_timeout=1)
    result = await headset.connect_peer(PHONE.lower(), allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    connects = [m for m in sim.sent if (m.group, m.code) == (0x01, 0x000B)]
    assert len(connects) == 1
    assert connects[0].args == bytes.fromhex("665544332211")


async def test_too_many_connected_is_reported(sim: SimHeadset) -> None:
    sim.state.write_status = responses.TOO_MANY_CONNECTED
    headset = await open_headset(sim)
    result = await headset.connect_peer(PHONE, allow_unverified=True)
    assert result.outcome is WriteOutcome.REJECTED
    assert "too many devices" in (result.detail or "")


async def test_disconnect_that_never_happens_is_not_applied(sim: SimHeadset) -> None:
    sim.state.peers[1].connected = True
    sim.state.apply_writes = False
    headset = await open_headset(sim)
    result = await headset.disconnect_peer(PHONE, allow_unverified=True)
    assert result.outcome is WriteOutcome.NOT_APPLIED
    assert len([m for m in sim.sent if (m.group, m.code) == (0x01, 0x000C)]) == 1


async def test_this_computer_is_never_a_peer_target(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    with pytest.raises(ShokzError, match="this computer"):
        await headset.disconnect_peer(THIS_COMPUTER, allow_unverified=True)
    assert sim.sent == []


async def test_unknown_device_is_refused(sim: SimHeadset) -> None:
    headset = await open_headset(sim)
    with pytest.raises(ShokzError, match="remembered devices"):
        await headset.connect_peer(LAPTOP, allow_unverified=True)
    assert [m for m in sim.sent if m.group == 0x01] == []


async def test_already_connected_device(sim: SimHeadset) -> None:
    sim.state.peers.append(Peer(LAPTOP, "Laptop", True))
    headset = await open_headset(sim)
    result = await headset.connect_peer(LAPTOP, allow_unverified=True)
    assert result.outcome is WriteOutcome.ALREADY_SET


async def test_unexplained_status_is_judged_by_the_readback(sim: SimHeadset) -> None:
    sim.state.peers[1].connected = True
    sim.state.write_status = 0x07
    sim.state.apply_writes = True
    headset = await open_headset(sim)
    # The simulated headset ignores writes with a non-zero status, so the readback decides.
    result = await headset.disconnect_peer(PHONE, allow_unverified=True)
    assert result.outcome is WriteOutcome.REJECTED
    assert "0x00000007" in (result.detail or "")


async def test_no_acknowledgement_and_old_value_is_uncertain(sim: SimHeadset) -> None:
    sim.state.ack_writes = False
    sim.state.apply_writes = False
    headset = await open_headset(sim)
    result = await headset.set_preset(EqPreset.VOCAL, allow_unverified=True)
    assert result.outcome is WriteOutcome.UNCERTAIN
    assert "read again before retrying" in (result.detail or "")
    assert len(eq_writes(sim)) == 1


async def test_peer_changes_need_this_computers_address(sim: SimHeadset) -> None:
    import dataclasses

    headset = await open_headset(sim)
    adapter = dataclasses.replace(headset.report.adapter, address=None)
    headset.report = dataclasses.replace(headset.report, adapter=adapter)
    with pytest.raises(ShokzError, match="can't be told apart"):
        await headset.disconnect_peer(PHONE, allow_unverified=True)
    assert sim.sent == []


async def test_connect_needs_multipoint_on(
    sim: SimHeadset, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(responses, "MULTIPOINT_OFF_VALUES", frozenset({MULTIPOINT_OFF_RAW}))
    sim.state.multipoint_raw = MULTIPOINT_OFF_RAW
    headset = await open_headset(sim)
    with pytest.raises(ShokzError, match="could drop this computer"):
        await headset.connect_peer(PHONE, allow_unverified=True)
    assert sim.writes() == []


async def test_connect_status_0x300_is_judged_by_readback(sim: SimHeadset) -> None:
    # The real headset acknowledges a connect with 0x00000300 and then connects (test C4).
    sim.state.ack_status = 0x300
    sim.state.apply_after_reads = 2
    headset = await open_headset(sim, peer_timeout=1)
    result = await headset.connect_peer(PHONE, allow_unverified=True)
    assert result.outcome is WriteOutcome.CONFIRMED
    assert result.acknowledged is True
