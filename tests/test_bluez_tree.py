from __future__ import annotations

from dbus_fast import Variant
from fakes import HCI0, HEADSET, OWNER, adapter, device, device_path, headset_tree, tree

from shokzctl.errors import ErrorCategory
from shokzctl.transport.bluez import (
    ADAPTER,
    BATTERY,
    BEARER_BREDR,
    DEVICE,
    BluezTree,
    InterfacesAdded,
    InterfacesRemoved,
    OwnerChanged,
    PropertiesChanged,
    Snapshot,
    dbus_error,
    parse_signal,
)

HEADSET_PATH = device_path(HEADSET)


def loaded(serial: int = 10) -> BluezTree:
    t = BluezTree()
    t.load(Snapshot(headset_tree(), OWNER, serial))
    return t


def test_signal_older_than_snapshot_is_ignored() -> None:
    t = loaded(serial=10)
    stale = PropertiesChanged(OWNER, 9, HEADSET_PATH, DEVICE, {"Connected": False}, ())
    assert not t.apply(stale)
    assert t.objects[HEADSET_PATH][DEVICE]["Connected"] is True


def test_signal_newer_than_snapshot_applies() -> None:
    t = loaded(serial=10)
    assert t.apply(PropertiesChanged(OWNER, 11, HEADSET_PATH, DEVICE, {"Connected": False}, ()))
    assert t.objects[HEADSET_PATH][DEVICE]["Connected"] is False


def test_signal_from_another_sender_is_ignored() -> None:
    t = loaded()
    assert not t.apply(PropertiesChanged(":1.99", 50, HEADSET_PATH, DEVICE, {"Name": "x"}, ()))


def test_invalidated_property_is_dropped() -> None:
    t = loaded()
    t.apply(PropertiesChanged(OWNER, 11, HEADSET_PATH, DEVICE, {}, ("Connected",)))
    assert "Connected" not in t.objects[HEADSET_PATH][DEVICE]


def test_change_for_unknown_object_does_not_create_it() -> None:
    t = loaded()
    assert not t.apply(PropertiesChanged(OWNER, 11, "/org/bluez/hci0/dev_X", DEVICE, {"A": 1}, ()))
    assert "/org/bluez/hci0/dev_X" not in t.objects


def test_battery_added_then_removed() -> None:
    t = loaded()
    t.apply(InterfacesAdded(OWNER, 11, HEADSET_PATH, {BATTERY: {"Percentage": 70}}))
    assert t.objects[HEADSET_PATH][BATTERY] == {"Percentage": 70}
    t.apply(InterfacesRemoved(OWNER, 12, HEADSET_PATH, (BATTERY,)))
    assert BATTERY not in t.objects[HEADSET_PATH]


def test_removing_device_drops_object_and_children() -> None:
    t = BluezTree()
    objects = headset_tree()
    objects[HEADSET_PATH + "/sep1"] = {"org.bluez.MediaEndpoint1": {}}
    t.load(Snapshot(objects, OWNER, 10))
    t.apply(InterfacesRemoved(OWNER, 11, HEADSET_PATH, (DEVICE, BEARER_BREDR)))
    assert HEADSET_PATH not in t.objects
    assert HEADSET_PATH + "/sep1" not in t.objects


def test_removing_adapter_drops_its_devices() -> None:
    t = loaded()
    t.apply(InterfacesRemoved(OWNER, 11, HCI0, (ADAPTER,)))
    assert t.objects == {}


def test_clear_forgets_owner() -> None:
    t = loaded()
    t.clear()
    assert t.owner is None
    assert t.objects == {}
    assert not t.apply(InterfacesAdded(OWNER, 99, HCI0, adapter()))


def test_load_copies_snapshot() -> None:
    objects = tree((HCI0, adapter()), (HEADSET_PATH, device()))
    t = BluezTree()
    t.load(Snapshot(objects, OWNER, 1))
    objects[HCI0][ADAPTER]["Powered"] = False
    assert t.objects[HCI0][ADAPTER]["Powered"] is True


# --- raw D-Bus signal parsing ----------------------------------------------------------------


def test_parse_properties_changed_unwraps_variants() -> None:
    event = parse_signal(
        sender=OWNER,
        serial=5,
        path=HEADSET_PATH,
        interface="org.freedesktop.DBus.Properties",
        member="PropertiesChanged",
        body=[DEVICE, {"Connected": Variant("b", False)}, ["Alias"]],
    )
    assert event == PropertiesChanged(
        OWNER, 5, HEADSET_PATH, DEVICE, {"Connected": False}, ("Alias",)
    )


def test_parse_ignores_non_bluez_properties() -> None:
    event = parse_signal(
        sender=OWNER,
        serial=5,
        path=HEADSET_PATH,
        interface="org.freedesktop.DBus.Properties",
        member="PropertiesChanged",
        body=["org.example.Other", {}, []],
    )
    assert event is None


def test_parse_interfaces_added() -> None:
    event = parse_signal(
        sender=OWNER,
        serial=6,
        path="/",
        interface="org.freedesktop.DBus.ObjectManager",
        member="InterfacesAdded",
        body=[HEADSET_PATH, {BATTERY: {"Percentage": Variant("y", 55)}}],
    )
    assert event == InterfacesAdded(OWNER, 6, HEADSET_PATH, {BATTERY: {"Percentage": 55}})


def test_parse_interfaces_removed() -> None:
    event = parse_signal(
        sender=OWNER,
        serial=7,
        path="/",
        interface="org.freedesktop.DBus.ObjectManager",
        member="InterfacesRemoved",
        body=[HEADSET_PATH, [BATTERY]],
    )
    assert event == InterfacesRemoved(OWNER, 7, HEADSET_PATH, (BATTERY,))


def test_parse_name_owner_changed() -> None:
    def owner_signal(name: str, new: str) -> object:
        return parse_signal(
            sender="org.freedesktop.DBus",
            serial=1,
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="NameOwnerChanged",
            body=[name, ":1.1", new],
        )

    assert owner_signal("org.bluez", "") == OwnerChanged(None)
    assert owner_signal("org.bluez", ":1.2") == OwnerChanged(":1.2")
    assert owner_signal("org.other", ":1.2") is None


def test_dbus_errors_map_to_categories() -> None:
    assert dbus_error("org.freedesktop.DBus.Error.ServiceUnknown", None).category is (
        ErrorCategory.UNAVAILABLE
    )
    assert dbus_error("org.freedesktop.DBus.Error.AccessDenied", "no").category is (
        ErrorCategory.PERMISSION
    )
    assert dbus_error("org.freedesktop.DBus.Error.NoReply", None).category is (
        ErrorCategory.TIMEOUT
    )
    assert dbus_error("org.bluez.Error.Failed", "x").category is ErrorCategory.UNAVAILABLE
