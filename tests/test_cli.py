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
from sim_headset import SimHeadset, SimState

from shokzer import FIRMWARE_REPORT_URL
from shokzer.cli import _watch, run
from shokzer.device import Selector
from shokzer.errors import ErrorCategory, ExitCode, ShokzError
from shokzer.lock import SessionLock
from shokzer.output import DIAGNOSE_SCHEMA, STATUS_SCHEMA, Formatter
from shokzer.transport.bluez import DEVICE, BluezObserver, Snapshot

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


# --- eq and multipoint commands ----------------------------------------------------------------


def headset_cli(sim: SimHeadset, tmp_path: Any, *args: str) -> tuple[int, str, str]:
    return status_like(FakeConnector(sim), tmp_path, *args)


def status_like(connector: Any, tmp_path: Any, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(
        list(args),
        connect=FakeBluez(headset_tree()),
        open_connector=connector,
        lock=SessionLock(tmp_path / "lock"),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_eq_get(tmp_path: Any) -> None:
    code, out, _ = headset_cli(SimHeadset(), tmp_path, "eq", "get")
    assert code == ExitCode.OK
    assert out.startswith("EQ  standard (headset EQUALIZER_GET)")


def test_eq_set_on_untested_firmware_works_and_asks_for_a_report(tmp_path: Any) -> None:
    sim = SimHeadset()
    code, out, _ = headset_cli(sim, tmp_path, "eq", "set", "vocal")
    assert code == ExitCode.OK
    assert "hasn't been tested yet" in out
    assert FIRMWARE_REPORT_URL in out
    assert len(sim.writes()) == 1


def test_eq_set_json(tmp_path: Any) -> None:
    sim = SimHeadset()
    code, out, _ = headset_cli(sim, tmp_path, "eq", "set", "vocal", "--json")
    assert code == ExitCode.OK
    data = json.loads(out)
    assert data["schema"] == "shokzer.eq.set/1"
    assert data["result"]["outcome"] == "confirmed"
    assert data["result"]["readback"] == "vocal"
    assert data["firmware"]["value"] == TEST_FIRMWARE


def test_eq_set_readable(tmp_path: Any) -> None:
    code, out, _ = headset_cli(SimHeadset(), tmp_path, "eq", "set", "bass")
    assert code == ExitCode.OK
    assert out.splitlines()[0] == "EQ bass: confirmed"
    assert "hasn't been tested yet" in out


def test_eq_set_uncertain_exit_code(tmp_path: Any) -> None:
    sim = SimHeadset(SimState(hang_up_after_write=True, ack_writes=False))
    code, out, _ = headset_cli(sim, tmp_path, "eq", "set", "vocal")
    assert code == ExitCode.WRITE_UNCERTAIN
    assert "read the current state first" in out


def test_eq_set_not_applied_exit_code(tmp_path: Any) -> None:
    sim = SimHeadset(SimState(apply_writes=False))
    code, _, _ = headset_cli(sim, tmp_path, "eq", "set", "vocal")
    assert code == ExitCode.WRITE_NOT_APPLIED


def test_eq_custom_accepts_negative_gains(tmp_path: Any) -> None:
    sim = SimHeadset()
    code, out, _ = headset_cli(sim, tmp_path, "eq", "custom", "-2", "0", "1", "0", "-5")
    assert code == ExitCode.OK, out
    assert "custom (-2, 0, +1, 0, -5): confirmed" in out


def test_eq_custom_rejects_out_of_range_before_connecting(tmp_path: Any) -> None:
    with pytest.raises(SystemExit) as caught:
        headset_cli(SimHeadset(), tmp_path, "eq", "custom", "6", "0", "0", "0", "0")
    assert caught.value.code == 2


def test_multipoint_status_is_redacted(tmp_path: Any) -> None:
    code, out, _ = headset_cli(SimHeadset(), tmp_path, "multipoint", "status")
    assert code == ExitCode.OK
    assert "Multipoint  on" in out
    assert "Pixel 10  11:XX:XX:XX:XX:66  not connected" in out
    assert "this computer" in out
    assert "11:22:33:44:55:66" not in out


def test_multipoint_connect_rejects_bad_address(tmp_path: Any) -> None:
    with pytest.raises(SystemExit):
        headset_cli(SimHeadset(), tmp_path, "multipoint", "connect", "Pixel")


def test_write_trace_records_outcome(tmp_path: Any) -> None:
    trace = tmp_path / "t.jsonl"
    headset_cli(SimHeadset(), tmp_path, "eq", "set", "vocal", "--trace", str(trace))
    last = json.loads(trace.read_text().splitlines()[-1])
    assert last["event"] == "result"
    assert (last["outcome"], last["readback"]) == ("confirmed", "vocal")
