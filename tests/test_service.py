from __future__ import annotations

from pathlib import Path

import pytest
from fake_headset import TEST_FIRMWARE, FakeConnector, FakeHeadset, frame
from fakes import HEADSET, OWNER, adapter, device, device_path, headset_tree, tree

from shokzctl import capabilities as caps
from shokzctl.device import Selector, build_report
from shokzctl.errors import ErrorCategory, ShokzError
from shokzctl.lock import SessionLock
from shokzctl.protocol.packets import Message
from shokzctl.service import read_status
from shokzctl.state import DiagnoseReport, ProtocolReadiness, Support
from shokzctl.transport.bluez import BluezTree, Objects, Snapshot


def report_for(objects: Objects | None = None) -> DiagnoseReport:
    t = BluezTree()
    t.load(Snapshot(objects or headset_tree(battery=60), OWNER, 1))
    return build_report(t, Selector())


@pytest.fixture
def lock(tmp_path: Path) -> SessionLock:
    return SessionLock(tmp_path / "shokzctl.lock")


async def test_reads_firmware_battery_and_eq(lock: SessionLock) -> None:
    connector = FakeConnector()
    result = await read_status(report_for(), connector, allow_unverified=True, lock=lock)

    assert connector.device_path == device_path(HEADSET)
    assert [m.code for m in connector.headset.sent] == [0x0002, 0x0003, 0x0008]
    assert result.firmware.value == TEST_FIRMWARE
    assert result.firmware.source == "headset:FIRMWARE_GET"
    assert result.headset_battery.value == 60
    assert result.active_eq.value == "standard"
    assert result.protocol is ProtocolReadiness.READY
    assert result.management_transport.value == "rfcomm"
    assert result.device.battery.value == 60, "BlueZ's battery stays separate"
    assert not result.identity.model_verified
    assert set(result.capabilities.values()) == {Support.UNRESOLVED}
    assert any("hasn't been verified" in w for w in result.warnings)
    assert connector.closed
    assert connector.headset.closed


async def test_firmware_only(lock: SessionLock) -> None:
    connector = FakeConnector()
    result = await read_status(
        report_for(), connector, queries=("firmware",), allow_unverified=True, lock=lock
    )
    assert connector.headset.sent == [Message(0x02, 0x0002)]
    assert result.headset_battery.value is None
    assert result.active_eq.value is None


async def test_firmware_is_always_first(lock: SessionLock) -> None:
    connector = FakeConnector()
    await read_status(report_for(), connector, queries=("eq",), allow_unverified=True, lock=lock)
    assert [m.code for m in connector.headset.sent] == [0x0002, 0x0008]


async def test_verified_firmware_enables_its_capabilities(
    lock: SessionLock, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = frozenset({"firmware_query", "battery_query", "eq_read"})
    monkeypatch.setattr(caps, "VERIFIED", {TEST_FIRMWARE: verified})
    result = await read_status(report_for(), FakeConnector(), lock=lock)
    assert result.identity.model_verified
    assert result.capabilities["firmware_query"] is Support.SUPPORTED
    assert result.capabilities["eq_write_vocal"] is Support.UNRESOLVED
    assert not any("verified" in w for w in result.warnings)


async def test_unknown_eq_code_is_kept(lock: SessionLock) -> None:
    headset = FakeHeadset()
    headset.replies[(0x02, 0x0008)] = [frame(0x02, 0x8008, b"\x00\x06")]
    result = await read_status(
        report_for(), FakeConnector(headset), allow_unverified=True, lock=lock
    )
    assert result.active_eq.value == "unknown (0x06)"


@pytest.mark.parametrize(
    ("objects", "category"),
    [
        (headset_tree(connected=False), ErrorCategory.UNAVAILABLE),
        (headset_tree(uuids=("0000110B-0000-1000-8000-00805F9B34FB",)), ErrorCategory.UNSUPPORTED),
    ],
)
async def test_refuses_before_touching_the_headset(
    objects: Objects, category: ErrorCategory, lock: SessionLock
) -> None:
    connector = FakeConnector()
    with pytest.raises(ShokzError) as caught:
        await read_status(report_for(objects), connector, allow_unverified=True, lock=lock)
    assert caught.value.category is category
    assert connector.device_path is None


async def test_refuses_a_device_with_another_name(lock: SessionLock) -> None:
    objects = tree(
        (device_path(HEADSET).rsplit("/", 1)[0], adapter()),
        (device_path(HEADSET), device(name="Other")),
    )
    t = BluezTree()
    t.load(Snapshot(objects, OWNER, 1))
    report = build_report(t, Selector(device=HEADSET))
    connector = FakeConnector()
    with pytest.raises(ShokzError) as caught:
        await read_status(report, connector, allow_unverified=True, lock=lock)
    assert caught.value.category is ErrorCategory.UNSUPPORTED
    assert connector.device_path is None


async def test_second_session_is_refused(lock: SessionLock) -> None:
    other = SessionLock(lock.path)
    other.acquire()
    connector = FakeConnector()
    try:
        with pytest.raises(ShokzError, match="Another shokzctl session"):
            await read_status(report_for(), connector, allow_unverified=True, lock=lock)
    finally:
        other.release()
    assert connector.device_path is None


async def test_timeout_cleans_up(lock: SessionLock) -> None:
    headset = FakeHeadset()
    del headset.replies[(0x02, 0x0003)]
    connector = FakeConnector(headset)
    with pytest.raises(ShokzError) as caught:
        await read_status(
            report_for(), connector, reply_timeout=0.05, allow_unverified=True, lock=lock
        )
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert "BATTERY_GET" in caught.value.message
    assert connector.closed
    assert headset.closed
    with SessionLock(lock.path):
        pass  # the lock was released


async def test_open_failure_still_closes_connector(lock: SessionLock) -> None:
    connector = FakeConnector(error=ShokzError(ErrorCategory.UNAVAILABLE, "no"))
    with pytest.raises(ShokzError):
        await read_status(report_for(), connector, allow_unverified=True, lock=lock)
    assert connector.closed


async def test_bluez_disconnect_request_fails_the_query(lock: SessionLock) -> None:
    headset = FakeHeadset(replies={})
    connector = FakeConnector(headset)
    headset.on_send = lambda _: (
        connector.on_disconnect_requested and connector.on_disconnect_requested()
    )
    with pytest.raises(ShokzError, match="BlueZ closed"):
        await read_status(
            report_for(), connector, reply_timeout=1, allow_unverified=True, lock=lock
        )
    assert connector.closed


def test_hardware_tested_features_are_verified_for_that_firmware_only() -> None:
    tested = caps.capabilities("BOS_HU_R_47_20251023")
    assert {k for k, v in tested.items() if v is Support.SUPPORTED} == set(
        caps.PROPRIETARY_FEATURES
    )
    # The app's label for the same firmware isn't what the headset reports.
    assert set(caps.capabilities("HU_R_47_20251023").values()) == {Support.UNRESOLVED}


async def test_unverified_reads_are_skipped_with_a_warning(lock: SessionLock) -> None:
    connector = FakeConnector()
    result = await read_status(report_for(), connector, lock=lock)
    assert connector.headset.sent == [Message(0x02, 0x0002)]
    assert result.headset_battery.value is None
    assert any("Skipped reading the battery level" in w for w in result.warnings)
    assert any("Skipped reading the EQ" in w for w in result.warnings)
