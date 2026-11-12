from __future__ import annotations

import pytest
from fakes import (
    HCI0,
    HCI1,
    HEADSET,
    HEADSET_2,
    MOUSE,
    OWNER,
    adapter,
    device,
    device_path,
    headset_tree,
    tree,
)

from shokzctl.device import (
    AmbiguousSelectionError,
    Selector,
    build_report,
    parse_adapter,
    parse_device,
    select,
)
from shokzctl.errors import ErrorCategory, ShokzError
from shokzctl.state import Connection, Support
from shokzctl.transport.bluez import BluezTree, Objects, Snapshot


def loaded(objects: Objects) -> BluezTree:
    t = BluezTree()
    t.load(Snapshot(objects, OWNER, 1))
    return t


def error_of(objects: Objects, selector: Selector | None = None) -> ShokzError:
    with pytest.raises(ShokzError) as caught:
        select(loaded(objects), selector or Selector())
    return caught.value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aa:bb:cc:00:00:01", HEADSET),
        ("AA_BB_CC_00_00_01", HEADSET),
        ("aa-bb-cc-00-00-01", HEADSET),
        (device_path(HEADSET), device_path(HEADSET)),
    ],
)
def test_parse_device_accepts_addresses_and_paths(text: str, expected: str) -> None:
    assert parse_device(text) == expected


@pytest.mark.parametrize(
    "text", ["", "OpenRun", "AA:BB:CC:00:00", "/org/bluez/hci0", "ZZ:BB:CC:00:00:01"]
)
def test_parse_device_rejects_other_input(text: str) -> None:
    with pytest.raises(ShokzError) as caught:
        parse_device(text)
    assert caught.value.category is ErrorCategory.INVALID_INPUT


def test_parse_adapter_rejects_paths() -> None:
    assert parse_adapter("hci1") == "hci1"
    with pytest.raises(ShokzError):
        parse_adapter("/org/bluez/hci0")


def test_selects_single_paired_headset() -> None:
    objects = tree(
        (HCI0, adapter()),
        (device_path(HEADSET), device()),
        (device_path(MOUSE), device(MOUSE, name="MX Master")),
    )
    _, chosen = select(loaded(objects), Selector())
    assert chosen.address == HEADSET


def test_bluez_not_running() -> None:
    with pytest.raises(ShokzError) as caught:
        select(BluezTree(), Selector())
    assert caught.value.category is ErrorCategory.UNAVAILABLE
    assert "BlueZ" in caught.value.message


def test_no_adapter() -> None:
    error = error_of(tree((device_path(HEADSET), device())))
    assert error.category is ErrorCategory.UNAVAILABLE
    assert "adapter" in error.message


def test_unknown_adapter_selected() -> None:
    error = error_of(headset_tree(), Selector(adapter="hci5"))
    assert error.category is ErrorCategory.UNAVAILABLE


def test_missing_headset() -> None:
    error = error_of(tree((HCI0, adapter()), (device_path(MOUSE), device(MOUSE, name="MX"))))
    assert error.category is ErrorCategory.UNAVAILABLE
    assert "No paired OpenRun Pro 2" in error.message


def test_unpaired_headset_is_not_a_candidate() -> None:
    error = error_of(headset_tree(paired=False))
    assert error.category is ErrorCategory.UNAVAILABLE


def test_two_headsets_are_ambiguous() -> None:
    objects = tree(
        (HCI0, adapter()),
        (device_path(HEADSET), device()),
        (device_path(HEADSET_2), device(HEADSET_2)),
    )
    error = error_of(objects)
    assert isinstance(error, AmbiguousSelectionError)
    assert {d.address for d in error.candidates} == {HEADSET, HEADSET_2}


def test_explicit_device_resolves_ambiguity() -> None:
    objects = tree(
        (HCI0, adapter()),
        (device_path(HEADSET), device()),
        (device_path(HEADSET_2), device(HEADSET_2)),
    )
    _, chosen = select(loaded(objects), Selector(device=HEADSET_2))
    assert chosen.address == HEADSET_2


def test_same_address_on_two_adapters_needs_adapter() -> None:
    objects = tree(
        (HCI0, adapter()),
        (HCI1, adapter(address="AA:BB:CC:FF:FF:02")),
        (device_path(HEADSET), device()),
        (device_path(HEADSET, HCI1), device(adapter_path=HCI1)),
    )
    assert isinstance(error_of(objects, Selector(device=HEADSET)), AmbiguousSelectionError)
    chosen_adapter, chosen = select(loaded(objects), Selector(device=HEADSET, adapter="hci1"))
    assert chosen_adapter.name == "hci1"
    assert chosen.adapter == "hci1"


def test_explicit_device_not_known() -> None:
    error = error_of(headset_tree(), Selector(device=HEADSET_2))
    assert error.category is ErrorCategory.UNAVAILABLE


def test_report_battery_from_bluez() -> None:
    report = build_report(loaded(headset_tree(battery=60)), Selector())
    assert report.device.battery.value == 60
    assert report.device.battery.source == "bluez:org.bluez.Battery1.Percentage"


def test_report_missing_battery_is_unknown_not_zero() -> None:
    report = build_report(loaded(headset_tree()), Selector())
    assert report.device.battery.value is None
    assert report.device.battery.source is None


def test_report_disconnected_headset() -> None:
    report = build_report(loaded(headset_tree(connected=False)), Selector())
    assert report.device.connection is Connection.DISCONNECTED


def test_report_missing_connected_property_is_unknown() -> None:
    objects = headset_tree()
    del objects[device_path(HEADSET)]["org.bluez.Device1"]["Connected"]
    report = build_report(loaded(objects), Selector())
    assert report.device.connection is Connection.UNKNOWN


def test_report_powered_off_adapter_warns() -> None:
    objects = tree((HCI0, adapter(powered=False)), (device_path(HEADSET), device(connected=False)))
    report = build_report(loaded(objects), Selector())
    assert any("powered off" in w for w in report.warnings)


def test_report_explicit_non_matching_name_is_flagged() -> None:
    objects = tree((HCI0, adapter()), (device_path(MOUSE), device(MOUSE, name="MX Master")))
    report = build_report(loaded(objects), Selector(device=MOUSE))
    assert report.selection == "explicit"
    assert not report.identity.name_matches_model
    assert any("unverified" in w for w in report.warnings)


def test_report_never_claims_proprietary_state() -> None:
    report = build_report(loaded(headset_tree(battery=60)), Selector())
    assert not report.identity.model_verified
    assert report.firmware.value is None
    assert report.active_eq.value is None
    assert report.protocol.value == "unverified"
    assert report.management_transport.value == "unresolved"
    assert set(report.capabilities.values()) == {Support.UNRESOLVED}
