"""Readable and JSON output. Addresses and object paths are redacted unless asked for."""

from __future__ import annotations

import json
from typing import Any

from . import FIRMWARE_REPORT_URL
from .capabilities import MODEL_NAME
from .device import AmbiguousSelectionError
from .errors import ShokzError
from .protocol.responses import Equalizer
from .service import Multipoint, WriteOutcome, WriteResult, multipoint_label
from .state import AdapterInfo, DeviceInfo, DiagnoseReport, Observed

_OUTCOME_TEXT = {
    WriteOutcome.CONFIRMED: "confirmed",
    WriteOutcome.PARTLY_CONFIRMED: "preset confirmed, band values not reported",
    WriteOutcome.ALREADY_SET: "already set, nothing sent",
    WriteOutcome.NOT_APPLIED: "not applied",
    WriteOutcome.REJECTED: "refused by the headset",
    WriteOutcome.UNCERTAIN: "outcome uncertain",
}

DIAGNOSE_SCHEMA = "shokzer.diagnose/1"
STATUS_SCHEMA = "shokzer.status/1"

_SOURCE_LABELS = {"bluez:org.bluez.Battery1.Percentage": "BlueZ Battery1"}


def redact_address(address: str | None) -> str | None:
    if address is None:
        return None
    parts = address.split(":")
    if len(parts) != 6:
        return "XX:XX:XX:XX:XX:XX"
    return ":".join([parts[0], "XX", "XX", "XX", "XX", parts[5]])


class Formatter:
    def __init__(self, *, show_identifiers: bool, schema: str = DIAGNOSE_SCHEMA) -> None:
        self.show_identifiers = show_identifiers
        self.schema = schema

    def address(self, address: str | None) -> str | None:
        return address if self.show_identifiers else redact_address(address)

    # --- JSON ---------------------------------------------------------------------------

    def report_dict(self, report: DiagnoseReport) -> dict[str, Any]:
        return self._envelope(
            ok=True,
            report={
                "adapter": self._adapter_dict(report.adapter),
                "device": self._device_dict(report.device),
                "selection": report.selection,
                "identity": {
                    "model": MODEL_NAME,
                    "name_matches_model": report.identity.name_matches_model,
                    "model_verified": report.identity.model_verified,
                },
                "firmware": _observed(report.firmware),
                "headset_battery_percent": _observed(report.headset_battery),
                "active_eq": _observed(report.active_eq),
                "management": {
                    "transport": report.management_transport.value,
                    "session": report.management_session.value,
                },
                "protocol": {"readiness": report.protocol.value},
                "capabilities": {k: v.value for k, v in sorted(report.capabilities.items())},
                "warnings": list(report.warnings),
            },
        )

    def error_dict(self, error: ShokzError) -> dict[str, Any]:
        body: dict[str, Any] = {
            "category": error.category.value,
            "message": error.message,
            "hint": error.hint,
        }
        if isinstance(error, AmbiguousSelectionError):
            body["candidates"] = [self._device_dict(d) for d in error.candidates]
        return self._envelope(ok=False, error=body)

    def _envelope(self, *, ok: bool, **payload: Any) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": ok,
            "redacted": not self.show_identifiers,
            **payload,
        }

    def _adapter_dict(self, adapter: AdapterInfo) -> dict[str, Any]:
        return {
            "name": adapter.name,
            "address": self.address(adapter.address),
            "powered": adapter.powered,
            "discovering": adapter.discovering,
        }

    def _device_dict(self, device: DeviceInfo) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": device.name,
            "address": self.address(device.address),
            "adapter": device.adapter,
            "connection": device.connection.value,
            "paired": device.paired,
            "bonded": device.bonded,
            "trusted": device.trusted,
            "services_resolved": device.services_resolved,
            "bearers": list(device.bearers),
            "battery_percent": _observed(device.battery),
            "modalias": device.modalias,
            "uuids": list(device.uuids),
        }
        if self.show_identifiers:
            data["alias"] = device.alias
            data["path"] = device.path
        return data

    # --- Readable -----------------------------------------------------------------------

    def report_text(self, report: DiagnoseReport) -> str:
        d, a = report.device, report.adapter
        bearers = ", ".join(b.upper().replace("BREDR", "BR/EDR") for b in d.bearers)
        connection = d.connection.value + (f" ({bearers})" if bearers else "")
        batteries = [
            f"{b.value} % ({_label(b.source)})"
            for b in (d.battery, report.headset_battery)
            if b.known
        ]
        battery = ", ".join(batteries) or "unknown"
        if report.identity.model_verified:
            model = f"{MODEL_NAME}, tested firmware"
        elif report.identity.name_matches_model and report.firmware.known:
            model = f"{MODEL_NAME}, firmware not tested yet"
        elif report.identity.name_matches_model:
            model = f"{MODEL_NAME} (by name)"
        else:
            model = f"name doesn't match {MODEL_NAME}"
        power = {True: "powered on", False: "powered off", None: "power unknown"}[a.powered]
        adapter = " ".join(p for p in (a.name, self.address(a.address), power) if p)
        management = (
            f"{report.management_transport.value}, session {report.management_session.value}"
        )
        rows = [
            ("Bluetooth", connection),
            ("Battery", battery),
            ("Firmware", _text(report.firmware)),
            ("Active EQ", _text(report.active_eq)),
            ("Management", management),
            ("Protocol", report.protocol.value),
            ("Model", model),
            ("Adapter", adapter),
        ]
        lines = [f"{d.name or d.alias or 'Unnamed device'}  {self.address(d.address)}"]
        lines += [f"  {label:<11} {value}" for label, value in rows]
        lines += [f"  ! {w}" for w in report.warnings]
        return "\n".join(lines) + "\n"

    # --- headset commands -----------------------------------------------------------------

    def eq_dict(self, report: DiagnoseReport, eq: Equalizer) -> dict[str, Any]:
        return self._headset_envelope(
            report,
            result={
                "eq": _observed(report.active_eq),
                "code": eq.code,
                "bands": list(eq.bands) if eq.bands is not None else None,
            },
        )

    def eq_text(self, report: DiagnoseReport) -> str:
        return f"EQ  {_text(report.active_eq)}\n" + self._warnings(report)

    def multipoint_dict(self, report: DiagnoseReport, state: Multipoint) -> dict[str, Any]:
        return self._headset_envelope(
            report,
            result={
                "enabled": state.status.enabled,
                "raw": f"0x{state.status.raw:08x}",
                "reported_slots": state.devices.reported_count,
                "skipped_entries": state.devices.skipped,
                "devices": [
                    {
                        "index": peer.index,
                        "name": peer.name,
                        "address": self.address(peer.address),
                        "connected": peer.connected,
                        "this_computer": peer.address == report.adapter.address,
                    }
                    for peer in state.devices.peers
                ],
            },
        )

    def multipoint_text(self, report: DiagnoseReport, state: Multipoint) -> str:
        lines = [f"Multipoint  {multipoint_label(state.status)}"]
        if not state.devices.peers:
            lines.append("  No remembered devices reported.")
        for peer in state.devices.peers:
            marks = ["connected" if peer.connected else "not connected"]
            if peer.address == report.adapter.address:
                marks.append("this computer")
            lines.append(f"  {peer.name}  {self.address(peer.address)}  {', '.join(marks)}")
        if state.devices.skipped:
            lines.append(f"  ({state.devices.skipped} implausible entries skipped)")
        return "\n".join(lines) + "\n" + self._warnings(report)

    def write_dict(self, report: DiagnoseReport, result: WriteResult) -> dict[str, Any]:
        body = self._headset_envelope(
            report,
            result={
                "setting": result.setting,
                "requested": result.requested,
                "previous": result.previous,
                "acknowledged": result.acknowledged,
                "readback": result.readback,
                "outcome": result.outcome.value,
                "verified": result.verified,
                "detail": result.detail,
            },
        )
        body["ok"] = result.ok
        return body

    def write_text(self, result: WriteResult) -> str:
        what = {"eq": "EQ", "multipoint": "Multipoint", "peer": "Device"}[result.setting]
        lines = [f"{what} {result.requested}: {_OUTCOME_TEXT[result.outcome]}"]
        rows = [
            ("Before", result.previous or "unknown"),
            ("Read back", result.readback or "nothing"),
            ("Acknowledged", {True: "yes", False: "no reply", None: "n/a"}[result.acknowledged]),
        ]
        lines += [f"  {label:<12} {value}" for label, value in rows]
        if result.detail:
            lines.append(f"  {result.detail}")
        if result.outcome is WriteOutcome.UNCERTAIN:
            lines.append("  Don't repeat the change blindly; read the current state first.")
        if not result.verified and result.acknowledged is not None:
            lines.append(
                "  ! This feature hasn't been tested yet on this firmware. "
                f"Please report how it went: {FIRMWARE_REPORT_URL}"
            )
        return "\n".join(lines) + "\n"

    def _headset_envelope(self, report: DiagnoseReport, **payload: Any) -> dict[str, Any]:
        return self._envelope(
            ok=True,
            firmware=_observed(report.firmware),
            warnings=list(report.warnings),
            **payload,
        )

    @staticmethod
    def _warnings(report: DiagnoseReport) -> str:
        return "".join(f"  ! {w}\n" for w in report.warnings)

    def error_text(self, error: ShokzError) -> str:
        lines = [f"shokzer: {error.message}"]
        if isinstance(error, AmbiguousSelectionError):
            lines += [
                f"  {d.name or d.alias or 'Unnamed device'}  {self.address(d.address)}"
                f"  on {d.adapter}, {d.connection.value}"
                for d in error.candidates
            ]
        if error.hint:
            lines.append(error.hint)
        return "\n".join(lines) + "\n"


def to_json(data: dict[str, Any], *, compact: bool = False) -> str:
    if compact:
        return json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def _observed[T](observed: Observed[T]) -> dict[str, Any]:
    return {"value": observed.value, "source": observed.source}


def _text[T](observed: Observed[T]) -> str:
    if observed.value is None:
        return "unknown"
    return f"{observed.value} ({_label(observed.source)})"


def _label(source: str | None) -> str:
    if source is None:
        return "no source"
    if source.startswith("headset:"):
        return "headset " + source.removeprefix("headset:")
    return _SOURCE_LABELS.get(source, source)
