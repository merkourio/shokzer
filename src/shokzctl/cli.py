"""Command line: argument parsing, output and exit codes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .device import Selector, build_report, parse_adapter, parse_address, parse_device
from .errors import ErrorCategory, ExitCode, ShokzError
from .lock import SessionLock
from .output import Formatter, to_json
from .protocol.commands import CUSTOM_BANDS, CUSTOM_GAIN_MAX, CUSTOM_GAIN_MIN, EqPreset
from .service import (
    QUERIES,
    WRITABLE_PRESETS,
    ConnectorFactory,
    Headset,
    WriteOutcome,
    WriteResult,
    connect,
    status_reads,
)
from .session import DEFAULT_REPLY_TIMEOUT, Trace, no_trace
from .state import DiagnoseReport
from .trace import TraceFile
from .transport.bluez import BluezObserver, ConnectionFactory, connect_system_bus
from .transport.management import open_connector

DEFAULT_TIMEOUT = 5.0
PRESET_NAMES = tuple(p.name.lower() for p in WRITABLE_PRESETS)

_WRITE_EXIT_CODES = {
    WriteOutcome.CONFIRMED: ExitCode.OK,
    WriteOutcome.PARTLY_CONFIRMED: ExitCode.OK,
    WriteOutcome.ALREADY_SET: ExitCode.OK,
    WriteOutcome.UNCERTAIN: ExitCode.WRITE_UNCERTAIN,
    WriteOutcome.NOT_APPLIED: ExitCode.WRITE_NOT_APPLIED,
    WriteOutcome.REJECTED: ExitCode.WRITE_NOT_APPLIED,
}


def main(argv: list[str] | None = None) -> int:
    return run(
        argv,
        connect=connect_system_bus,
        open_connector=open_connector,
        out=sys.stdout,
        err=sys.stderr,
    )


def run(
    argv: list[str] | None,
    *,
    connect: ConnectionFactory,
    open_connector: ConnectorFactory = open_connector,
    lock: SessionLock | None = None,
    out: TextIO,
    err: TextIO,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(err)
        return ExitCode.INVALID_INPUT
    try:
        return asyncio.run(_command(args, connect, open_connector, lock, out, err))
    except KeyboardInterrupt:
        return ExitCode.INTERRUPTED


# --- parser ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shokzctl", description="Manage a Shokz OpenRun Pro 2 from Fedora."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    diagnose = commands.add_parser(
        "diagnose",
        help="Show what BlueZ knows about the headset. Passive: sends nothing to the headset.",
        description=(
            "Show what BlueZ knows about the headset. Passive: no scanning, pairing, "
            "connecting, or headset queries."
        ),
    )
    _add_common(diagnose)
    diagnose.add_argument(
        "--watch", action="store_true", help="Keep running and print again when anything changes."
    )

    status = commands.add_parser(
        "status",
        help="Read firmware, battery and EQ from the headset. Read-only.",
        description=(
            "Open the headset's management channel and read its state. Read-only: no setting "
            "is changed. The headset must already be connected to Fedora. Reads that aren't "
            "verified on the headset's firmware are skipped unless --allow-unverified is given."
        ),
    )
    _add_common(status)
    _add_session(status)
    status.add_argument(
        "--query",
        action="append",
        choices=QUERIES,
        metavar="WHAT",
        help=(
            "What to read: firmware, battery or eq. Repeatable; default all. "
            "Firmware is always read first."
        ),
    )

    eq = commands.add_parser("eq", help="Read or change the EQ.")
    eq_actions = eq.add_subparsers(dest="action", metavar="ACTION", required=True)
    eq_get = eq_actions.add_parser("get", help="Read the active EQ.")
    eq_set = eq_actions.add_parser(
        "set",
        help="Switch to a preset.",
        description=(
            "Switch to a preset. Reads the current EQ, sends the change once, and reports it as "
            "confirmed only when the headset reads back the new preset. Never resent."
        ),
    )
    eq_set.add_argument("preset", choices=PRESET_NAMES, help="Preset to switch to.")
    eq_custom = eq_actions.add_parser(
        "custom",
        help="Switch to the custom EQ with five band gains.",
        description=(
            f"Switch to the custom EQ with {CUSTOM_BANDS} band gains, each a whole number from "
            f"{CUSTOM_GAIN_MIN} to {CUSTOM_GAIN_MAX}. Band frequencies aren't known. Sent once, "
            "then read back."
        ),
    )
    eq_custom.add_argument(
        "bands", nargs=CUSTOM_BANDS, type=_gain, metavar="GAIN", help="Band 1 to band 5 gain."
    )
    for sub in (eq_get, eq_set, eq_custom):
        _add_common(sub)
        _add_session(sub)

    multipoint = commands.add_parser(
        "multipoint", help="Multipoint status and the headset's remembered devices."
    )
    mp_actions = multipoint.add_subparsers(dest="action", metavar="ACTION", required=True)
    mp_status = mp_actions.add_parser(
        "status", help="Read multipoint status and the remembered devices. Read-only."
    )
    mp_on = mp_actions.add_parser("on", help="Turn multipoint on.")
    mp_off = mp_actions.add_parser(
        "off", help="Turn multipoint off. Other devices may disconnect; this computer stays."
    )
    mp_connect = mp_actions.add_parser(
        "connect", help="Ask the headset to connect a remembered device."
    )
    mp_disconnect = mp_actions.add_parser(
        "disconnect",
        help="Ask the headset to disconnect a device. Never this computer; never unpairs.",
    )
    for sub in (mp_connect, mp_disconnect):
        sub.add_argument("address", type=_address, help="Address from `multipoint status`.")
    for sub in (mp_status, mp_on, mp_off, mp_connect, mp_disconnect):
        _add_common(sub)
        _add_session(sub)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", metavar="ADDRESS", help="Select a device by address.")
    parser.add_argument("--adapter", metavar="NAME", help="Only look on this adapter, e.g. hci0.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument(
        "--show-identifiers",
        action="store_true",
        help="Show full addresses. Output is redacted by default so it can be shared.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"How long to wait for D-Bus and BlueZ (default {DEFAULT_TIMEOUT:g}).",
    )


def _add_session(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Allow operations not yet verified on this firmware. For controlled tests.",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        metavar="FILE",
        help="Append every byte sent and received to FILE as JSON lines.",
    )
    parser.add_argument(
        "--reply-timeout",
        type=_positive_seconds,
        default=DEFAULT_REPLY_TIMEOUT,
        metavar="SECONDS",
        help=f"How long to wait for each reply (default {DEFAULT_REPLY_TIMEOUT:g}).",
    )


# --- dispatch --------------------------------------------------------------------------------


async def _command(
    args: argparse.Namespace,
    connect_bus: ConnectionFactory,
    open_connector: ConnectorFactory,
    lock: SessionLock | None,
    out: TextIO,
    err: TextIO,
) -> int:
    name = _name(args)
    fmt = Formatter(show_identifiers=args.show_identifiers, schema=f"shokzctl.{name}/1")
    observer: BluezObserver | None = None
    trace: TraceFile | None = None
    try:
        selector = Selector(
            device=parse_device(args.device) if args.device is not None else None,
            adapter=parse_adapter(args.adapter) if args.adapter is not None else None,
        )
        try:
            async with asyncio.timeout(args.timeout):
                observer = BluezObserver(await connect_bus())
                await observer.start()
        except TimeoutError:
            raise ShokzError(
                ErrorCategory.TIMEOUT,
                f"D-Bus or BlueZ didn't answer within {args.timeout:g} s.",
            ) from None
        if args.command == "diagnose" and args.watch:
            await _watch(observer, selector, fmt, json_output=args.json, out=out)
            return ExitCode.OK
        report = build_report(observer.tree, selector)
        if args.command == "diagnose":
            out.write(to_json(fmt.report_dict(report)) if args.json else fmt.report_text(report))
            return ExitCode.OK
        await observer.close()
        observer = None
        if args.trace is not None:
            trace = TraceFile(args.trace)
            trace.start(shokzctl=__version__, command=name, reply_timeout=args.reply_timeout)
        payload, text, code = await _headset_command(args, report, open_connector, lock, trace, fmt)
        if trace is not None:
            trace("result", ok=code == ExitCode.OK, exit=int(code), **payload.get("trace", {}))
        payload.pop("trace", None)
        out.write(to_json(payload) if args.json else text)
        return code
    except ShokzError as error:
        if trace is not None:
            trace("result", ok=False, category=error.category.value, message=error.message)
        if args.json:
            out.write(to_json(fmt.error_dict(error)))
        else:
            err.write(fmt.error_text(error))
        return error.exit_code
    finally:
        if observer is not None:
            await observer.close()
        if trace is not None:
            trace.close()


async def _headset_command(
    args: argparse.Namespace,
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    lock: SessionLock | None,
    trace: TraceFile | None,
    fmt: Formatter,
) -> tuple[dict[str, Any], str, ExitCode]:
    tracer: Trace = trace if trace is not None else no_trace
    allow = args.allow_unverified
    async with connect(
        report, open_connector, reply_timeout=args.reply_timeout, trace=tracer, lock=lock
    ) as headset:
        action = getattr(args, "action", None)
        if args.command == "status":
            result = await status_reads(
                headset, tuple(args.query or QUERIES), allow_unverified=allow
            )
            payload = fmt.report_dict(result)
            payload["trace"] = {
                "firmware": result.firmware.value,
                "headset_battery": result.headset_battery.value,
                "active_eq": result.active_eq.value,
            }
            return payload, fmt.report_text(result), ExitCode.OK
        if (args.command, action) == ("eq", "get"):
            eq = await headset.read_eq(allow_unverified=allow)
            return (
                fmt.eq_dict(headset.report, eq),
                fmt.eq_text(headset.report),
                ExitCode.OK,
            )
        if (args.command, action) == ("multipoint", "status"):
            state = await headset.read_multipoint(allow_unverified=allow)
            return (
                fmt.multipoint_dict(headset.report, state),
                fmt.multipoint_text(headset.report, state),
                ExitCode.OK,
            )
        write = await _write(args, headset, allow)
        payload = fmt.write_dict(headset.report, write)
        payload["trace"] = {"outcome": write.outcome.value, "readback": write.readback}
        return payload, fmt.write_text(write), _WRITE_EXIT_CODES[write.outcome]


async def _write(args: argparse.Namespace, headset: Headset, allow: bool) -> WriteResult:
    match (args.command, args.action):
        case ("eq", "set"):
            return await headset.set_preset(EqPreset[args.preset.upper()], allow_unverified=allow)
        case ("eq", "custom"):
            return await headset.set_custom(args.bands, allow_unverified=allow)
        case ("multipoint", "on" | "off"):
            return await headset.set_multipoint(args.action == "on", allow_unverified=allow)
        case ("multipoint", "connect"):
            return await headset.connect_peer(args.address, allow_unverified=allow)
        case ("multipoint", "disconnect"):
            return await headset.disconnect_peer(args.address, allow_unverified=allow)
    raise AssertionError(f"unhandled command {args.command} {args.action}")


def _name(args: argparse.Namespace) -> str:
    action = getattr(args, "action", None)
    return f"{args.command}.{action}" if action else str(args.command)


async def _watch(
    observer: BluezObserver,
    selector: Selector,
    fmt: Formatter,
    *,
    json_output: bool,
    out: TextIO,
) -> None:
    """Print the report whenever it changes. Errors are printed and watching continues."""
    last: dict[str, Any] | None = None
    while True:
        observer.changed.clear()
        try:
            error = observer.last_error
            if error is not None:
                raise error
            report = build_report(observer.tree, selector)
            current = fmt.report_dict(report)
            text = fmt.report_text(report)
        except ShokzError as error:
            current = fmt.error_dict(error)
            text = fmt.error_text(error)
        if current != last:
            last = current
            now = datetime.now(UTC).astimezone()
            if json_output:
                out.write(to_json({**current, "observed_at": now.isoformat()}, compact=True))
            else:
                out.write(f"[{now:%H:%M:%S}]\n{text}\n")
            out.flush()
        await observer.changed.wait()


# --- argument types --------------------------------------------------------------------------


def _positive_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0 < value <= 300:
        raise argparse.ArgumentTypeError("must be between 0 and 300 seconds")
    return value


def _gain(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if not CUSTOM_GAIN_MIN <= value <= CUSTOM_GAIN_MAX:
        raise argparse.ArgumentTypeError(
            f"must be from {CUSTOM_GAIN_MIN} to {CUSTOM_GAIN_MAX}, got {value}"
        )
    return value


def _address(text: str) -> str:
    try:
        return parse_address(text)
    except ShokzError as error:
        raise argparse.ArgumentTypeError(error.message) from None
