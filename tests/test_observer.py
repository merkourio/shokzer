from __future__ import annotations

from fakes import (
    HEADSET,
    OWNER,
    FakeBluez,
    adapter,
    device,
    device_path,
    headset_tree,
    settle,
    tree,
)

from shokzctl.errors import ErrorCategory, ShokzError
from shokzctl.transport.bluez import BATTERY, DEVICE, BluezObserver

HEADSET_PATH = device_path(HEADSET)


async def started(bluez: FakeBluez) -> BluezObserver:
    observer = BluezObserver(bluez)
    await observer.start()
    return observer


async def test_start_loads_snapshot() -> None:
    observer = await started(FakeBluez(headset_tree()))
    assert observer.tree.owner == OWNER
    assert HEADSET_PATH in observer.tree.objects


async def test_signal_after_start_updates_tree() -> None:
    bluez = FakeBluez(headset_tree())
    observer = await started(bluez)
    observer.changed.clear()
    bluez.change(HEADSET_PATH, DEVICE, {"Connected": False})
    assert observer.changed.is_set()
    assert observer.tree.objects[HEADSET_PATH][DEVICE]["Connected"] is False


async def test_signal_during_snapshot_fetch_is_not_lost() -> None:
    async def battery_arrives(bluez: FakeBluez) -> None:
        bluez.add(HEADSET_PATH, {BATTERY: {"Percentage": 40}})

    bluez = FakeBluez(headset_tree(), during_snapshot=battery_arrives)
    observer = await started(bluez)
    assert observer.tree.objects[HEADSET_PATH][BATTERY] == {"Percentage": 40}


async def test_bluez_absent() -> None:
    observer = await started(FakeBluez(owner=None))
    assert observer.tree.owner is None
    assert observer.tree.objects == {}


async def test_bluez_stops_clears_state() -> None:
    bluez = FakeBluez(headset_tree())
    observer = await started(bluez)
    bluez.stop()
    assert observer.tree.owner is None
    assert observer.tree.objects == {}


async def test_bluez_restart_resyncs_without_stale_objects() -> None:
    bluez = FakeBluez(headset_tree())
    observer = await started(bluez)
    bluez.stop()
    bluez.restart(tree(("/org/bluez/hci0", adapter())))
    await settle()
    assert observer.tree.owner == ":1.99"
    assert HEADSET_PATH not in observer.tree.objects
    assert "/org/bluez/hci0" in observer.tree.objects


async def test_restart_during_snapshot_discards_old_snapshot() -> None:
    async def restart_now(bluez: FakeBluez) -> None:
        bluez.restart(tree(("/org/bluez/hci0", adapter())), owner=":1.50")

    bluez = FakeBluez(headset_tree(), during_snapshot=restart_now)
    observer = await started(bluez)
    await settle()
    assert observer.tree.owner == ":1.50"
    assert HEADSET_PATH not in observer.tree.objects
    assert bluez.snapshots == 2


async def test_resync_failure_is_recorded() -> None:
    bluez = FakeBluez(headset_tree())
    observer = await started(bluez)
    bluez.error = ShokzError(ErrorCategory.PERMISSION, "denied")
    bluez.restart(headset_tree())
    await settle()
    assert observer.last_error is bluez.error
    assert observer.tree.objects == {}


async def test_new_device_appears() -> None:
    bluez = FakeBluez(tree(("/org/bluez/hci0", adapter())))
    observer = await started(bluez)
    bluez.add(HEADSET_PATH, device())
    assert observer.tree.objects[HEADSET_PATH][DEVICE]["Name"] == "OpenRun Pro 2 by Shokz"


async def test_close_closes_connection() -> None:
    bluez = FakeBluez(headset_tree())
    observer = await started(bluez)
    await observer.close()
    assert bluez.closed
