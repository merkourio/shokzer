from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from fake_headset import TEST_FIRMWARE, FakeConnector, FakeHeadset
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
from shokzctl.lock import SessionLock
from shokzctl.output import DIAGNOSE_SCHEMA, STATUS_SCHEMA, Formatter
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
        "headset_battery_percent",
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


# --- status ----------------------------------------------------------------------------------


def status(bluez: Any, connector: Any, tmp_path: Any, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(
        ["status", *args],
        connect=bluez,
        open_connector=connector,
        lock=SessionLock(tmp_path / "lock"),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_status_json(tmp_path: Any) -> None:
    connector = FakeConnector()
    code, out, _ = status(FakeBluez(headset_tree(battery=60)), connector, tmp_path, "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["schema"] == STATUS_SCHEMA
    report = data["report"]
    assert report["firmware"] == {"value": TEST_FIRMWARE, "source": "headset:FIRMWARE_GET"}
    assert report["headset_battery_percent"]["value"] == 60
    assert report["active_eq"]["value"] == "standard"
    assert report["protocol"] == {"readiness": "ready"}
    assert report["management"] == {"transport": "rfcomm", "session": "open"}
    assert HEADSET not in out


def test_status_readable(tmp_path: Any) -> None:
    code, out, _ = status(FakeBluez(headset_tree(battery=60)), FakeConnector(), tmp_path)
    assert code == ExitCode.OK
    assert f"Firmware    {TEST_FIRMWARE} (headset FIRMWARE_GET)" in out
    assert "60 % (BlueZ Battery1), 60 % (headset BATTERY_GET)" in out
    assert "Active EQ   standard (headset EQUALIZER_GET)" in out


def test_status_disconnected_headset(tmp_path: Any) -> None:
    connector = FakeConnector()
    code, _, err = status(FakeBluez(headset_tree(connected=False)), connector, tmp_path)
    assert code == ExitCode.UNAVAILABLE
    assert "isn't connected" in err
    assert not connector.opened


def test_status_query_selection(tmp_path: Any) -> None:
    connector = FakeConnector()
    code, _, _ = status(FakeBluez(headset_tree()), connector, tmp_path, "--query", "firmware")
    assert code == ExitCode.OK
    assert len(connector.headset.sent) == 1


def test_status_timeout_exit_code(tmp_path: Any) -> None:
    connector = FakeConnector(FakeHeadset(replies={}))
    code, out, _ = status(
        FakeBluez(headset_tree()), connector, tmp_path, "--json", "--reply-timeout", "0.05"
    )
    assert code == ExitCode.TIMEOUT
    assert json.loads(out)["error"]["category"] == "timeout"
    assert connector.closed


def test_status_trace_file(tmp_path: Any) -> None:
    trace = tmp_path / "local" / "trace.jsonl"
    code, _, _ = status(
        FakeBluez(headset_tree()),
        FakeConnector(),
        tmp_path,
        "--query",
        "firmware",
        "--trace",
        str(trace),
    )
    assert code == ExitCode.OK
    lines = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [line["event"] for line in lines] == [
        "start",
        "connect",
        "open",
        "tx",
        "rx",
        "reply",
        "closed",
        "result",
    ]
    assert lines[-1]["firmware"] == TEST_FIRMWARE
    assert HEADSET not in trace.read_text()
