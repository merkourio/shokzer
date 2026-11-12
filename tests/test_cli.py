from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from fakes import (
    HCI0,
    HEADSET,
    HEADSET_2,
    FakeBluez,
    adapter,
    device,
    device_path,
    headset_tree,
    tree,
)

from shokzctl.cli import _watch, run
from shokzctl.device import Selector
from shokzctl.errors import ErrorCategory, ExitCode, ShokzError
from shokzctl.output import DIAGNOSE_SCHEMA, Formatter
from shokzctl.transport.bluez import DEVICE, BluezObserver, Snapshot

HEADSET_PATH = device_path(HEADSET)


def diagnose(bluez: Any, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(["diagnose", *args], connect=bluez, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def diagnose_json(bluez: Any, *args: str) -> tuple[int, dict[str, Any]]:
    code, out, _ = diagnose(bluez, "--json", *args)
    return code, json.loads(out)


def test_readable_report() -> None:
    code, out, err = diagnose(FakeBluez(headset_tree(battery=60)))
    assert code == ExitCode.OK
    assert err == ""
    assert "OpenRun Pro 2 by Shokz  AA:XX:XX:XX:XX:01" in out
    assert "connected (BR/EDR)" in out
    assert "60 % (BlueZ Battery1)" in out
    assert "Firmware    unknown" in out
    assert "Protocol    unverified" in out


def test_json_report_shape_is_stable() -> None:
    code, data = diagnose_json(FakeBluez(headset_tree(battery=60)))
    assert code == ExitCode.OK
    assert data["schema"] == DIAGNOSE_SCHEMA
    assert data["ok"] is True
    assert data["redacted"] is True
    report = data["report"]
    assert set(report) == {
        "adapter",
        "device",
        "selection",
        "identity",
        "firmware",
        "active_eq",
        "management",
        "protocol",
        "capabilities",
        "warnings",
    }
    assert set(report["device"]) == {
        "name",
        "address",
        "adapter",
        "connection",
        "paired",
        "bonded",
        "trusted",
        "services_resolved",
        "bearers",
        "battery_percent",
        "modalias",
        "uuids",
    }
    assert report["device"]["battery_percent"] == {
        "value": 60,
        "source": "bluez:org.bluez.Battery1.Percentage",
    }
    assert report["firmware"] == {"value": None, "source": None}
    assert report["management"] == {"transport": "unresolved", "session": "closed"}
    assert report["identity"]["model_verified"] is False


def test_output_is_redacted_by_default() -> None:
    _, out, _ = diagnose(FakeBluez(headset_tree()), "--json")
    assert HEADSET not in out
    assert HEADSET.replace(":", "_") not in out
    _, text, _ = diagnose(FakeBluez(headset_tree()))
    assert HEADSET not in text


def test_show_identifiers_prints_full_address_and_path() -> None:
    _, data = diagnose_json(FakeBluez(headset_tree()), "--show-identifiers")
    assert data["redacted"] is False
    assert data["report"]["device"]["address"] == HEADSET
    assert data["report"]["device"]["path"] == HEADSET_PATH


def test_missing_battery_prints_unknown() -> None:
    _, out, _ = diagnose(FakeBluez(headset_tree()))
    assert "Battery     unknown" in out
    _, data = diagnose_json(FakeBluez(headset_tree()))
    assert data["report"]["device"]["battery_percent"] == {"value": None, "source": None}


def test_disconnected_headset_still_reports() -> None:
    code, out, _ = diagnose(FakeBluez(headset_tree(connected=False)))
    assert code == ExitCode.OK
    assert "disconnected" in out


def test_bluez_absent() -> None:
    code, out, err = diagnose(FakeBluez(owner=None))
    assert code == ExitCode.UNAVAILABLE
    assert out == ""
    assert "BlueZ isn't running" in err


def test_bluez_absent_json() -> None:
    code, data = diagnose_json(FakeBluez(owner=None))
    assert code == ExitCode.UNAVAILABLE
    assert data["ok"] is False
    assert data["error"]["category"] == "unavailable"


def test_system_bus_unreachable() -> None:
    async def no_bus() -> FakeBluez:
        raise ShokzError(ErrorCategory.UNAVAILABLE, "The system D-Bus isn't reachable.")

    code, _, err = diagnose(no_bus)
    assert code == ExitCode.UNAVAILABLE
    assert "system D-Bus" in err


def test_permission_denied() -> None:
    bluez = FakeBluez(error=ShokzError(ErrorCategory.PERMISSION, "Not allowed to read BlueZ."))
    code, _, _ = diagnose(bluez)
    assert code == ExitCode.PERMISSION
    assert bluez.closed


def test_timeout() -> None:
    class Hanging(FakeBluez):
        async def managed_objects(self) -> Snapshot:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    bluez = Hanging(headset_tree())
    code, _, err = diagnose(bluez, "--timeout", "0.05")
    assert code == ExitCode.TIMEOUT
    assert "didn't answer" in err
    assert bluez.closed


def test_ambiguous_selection_lists_redacted_candidates() -> None:
    objects = tree(
        (HCI0, adapter()),
        (HEADSET_PATH, device()),
        (device_path(HEADSET_2), device(HEADSET_2)),
    )
    code, data = diagnose_json(FakeBluez(objects))
    assert code == ExitCode.AMBIGUOUS_SELECTION
    assert data["error"]["category"] == "ambiguous_selection"
    assert [c["address"] for c in data["error"]["candidates"]] == [
        "AA:XX:XX:XX:XX:01",
        "AA:XX:XX:XX:XX:02",
    ]
    code, out, _ = diagnose(FakeBluez(objects), "--device", HEADSET_2)
    assert code == ExitCode.OK
    assert "AA:XX:XX:XX:XX:02" in out


def test_invalid_device_argument() -> None:
    bluez = FakeBluez(headset_tree())
    code, _, err = diagnose(bluez, "--device", "OpenRun")
    assert code == ExitCode.INVALID_INPUT
    assert "--device" in err
    assert bluez.handler is None, "should fail before touching the bus"


def test_invalid_timeout_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        diagnose(FakeBluez(headset_tree()), "--timeout", "-1")
    assert caught.value.code == 2


def test_no_command_prints_help() -> None:
    err = io.StringIO()
    assert run([], connect=FakeBluez(), out=io.StringIO(), err=err) == ExitCode.INVALID_INPUT
    assert "diagnose" in err.getvalue()


async def test_watch_prints_on_change_and_survives_bluez_restart() -> None:
    bluez = FakeBluez(headset_tree(battery=60))
    observer = BluezObserver(bluez)
    await observer.start()
    out = io.StringIO()
    task = asyncio.create_task(
        _watch(observer, Selector(), Formatter(show_identifiers=False), json_output=True, out=out)
    )
    await asyncio.sleep(0)
    bluez.change(HEADSET_PATH, DEVICE, {"Connected": False})
    await asyncio.sleep(0)
    bluez.change(HEADSET_PATH, DEVICE, {"Connected": False})  # no visible change
    await asyncio.sleep(0)
    bluez.stop()
    await asyncio.sleep(0)
    bluez.restart(headset_tree(battery=55))
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    states = [
        line["report"]["device"]["connection"] if line["ok"] else line["error"]["category"]
        for line in lines
    ]
    assert states == ["connected", "disconnected", "unavailable", "connected"]
    assert lines[-1]["report"]["device"]["battery_percent"]["value"] == 55
    assert all("observed_at" in line for line in lines)
