"""Command line: argument parsing, output and exit codes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .device import Selector, build_report, parse_adapter, parse_device
from .errors import ErrorCategory, ExitCode, ShokzError
from .lock import SessionLock
from .output import DIAGNOSE_SCHEMA, STATUS_SCHEMA, Formatter, to_json
from .session import DEFAULT_REPLY_TIMEOUT, Trace, no_trace
from .state import DiagnoseReport
from .status import QUERIES, ConnectorFactory, read_status
from .trace import TraceFile
from .transport.bluez import BluezObserver, ConnectionFactory, connect_system_bus
from .transport.management import open_connector

DEFAULT_TIMEOUT = 5.0


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
            "is changed. The headset must already be connected to Fedora."
        ),
    )
    _add_common(status)
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
    status.add_argument(
        "--trace",
        type=Path,
        metavar="FILE",
        help="Append every byte sent and received to FILE as JSON lines.",
    )
    status.add_argument(
        "--reply-timeout",
        type=_positive_seconds,
        default=DEFAULT_REPLY_TIMEOUT,
        metavar="SECONDS",
        help=f"How long to wait for each reply (default {DEFAULT_REPLY_TIMEOUT:g}).",
    )
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


async def _command(
    args: argparse.Namespace,
    connect: ConnectionFactory,
    open_connector: ConnectorFactory,
    lock: SessionLock | None,
    out: TextIO,
    err: TextIO,
) -> int:
    schema = STATUS_SCHEMA if args.command == "status" else DIAGNOSE_SCHEMA
    fmt = Formatter(show_identifiers=args.show_identifiers, schema=schema)
    observer: BluezObserver | None = None
    trace: TraceFile | None = None
    try:
        selector = Selector(
            device=parse_device(args.device) if args.device is not None else None,
            adapter=parse_adapter(args.adapter) if args.adapter is not None else None,
        )
        try:
            async with asyncio.timeout(args.timeout):
                observer = BluezObserver(await connect())
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
        if args.command == "status":
            await observer.close()
            observer = None
            if args.trace is not None:
                trace = TraceFile(args.trace)
            report = await _status(args, report, open_connector, lock, trace)
        out.write(to_json(fmt.report_dict(report)) if args.json else fmt.report_text(report))
        return ExitCode.OK
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


async def _status(
    args: argparse.Namespace,
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    lock: SessionLock | None,
    trace: TraceFile | None,
) -> DiagnoseReport:
    queries = tuple(args.query or QUERIES)
    tracer: Trace = trace if trace is not None else no_trace
    if trace is not None:
        trace.start(shokzctl=__version__, queries=list(queries), reply_timeout=args.reply_timeout)
    result = await read_status(
        report,
        open_connector,
        queries=queries,
        reply_timeout=args.reply_timeout,
        trace=tracer,
        lock=lock,
    )
    tracer(
        "result",
        ok=True,
        firmware=result.firmware.value,
        headset_battery=result.headset_battery.value,
        active_eq=result.active_eq.value,
    )
    return result


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


def _positive_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0 < value <= 300:
        raise argparse.ArgumentTypeError("must be between 0 and 300 seconds")
    return value
