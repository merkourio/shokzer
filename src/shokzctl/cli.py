"""Command line: argument parsing, output and exit codes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from . import __version__
from .device import Selector, build_report, parse_adapter, parse_device
from .errors import ErrorCategory, ExitCode, ShokzError
from .output import Formatter, to_json
from .transport.bluez import BluezObserver, ConnectionFactory, connect_system_bus

DEFAULT_TIMEOUT = 5.0


def main(argv: list[str] | None = None) -> int:
    return run(argv, connect=connect_system_bus, out=sys.stdout, err=sys.stderr)


def run(
    argv: list[str] | None,
    *,
    connect: ConnectionFactory,
    out: TextIO,
    err: TextIO,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(err)
        return ExitCode.INVALID_INPUT
    try:
        return asyncio.run(_diagnose(args, connect, out, err))
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
    diagnose.add_argument("--device", metavar="ADDRESS", help="Select a device by address.")
    diagnose.add_argument("--adapter", metavar="NAME", help="Only look on this adapter, e.g. hci0.")
    diagnose.add_argument("--json", action="store_true", help="Print JSON.")
    diagnose.add_argument(
        "--show-identifiers",
        action="store_true",
        help="Show full addresses. Output is redacted by default so it can be shared.",
    )
    diagnose.add_argument(
        "--watch", action="store_true", help="Keep running and print again when anything changes."
    )
    diagnose.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"How long to wait for D-Bus and BlueZ (default {DEFAULT_TIMEOUT:g}).",
    )
    return parser


async def _diagnose(
    args: argparse.Namespace, connect: ConnectionFactory, out: TextIO, err: TextIO
) -> int:
    fmt = Formatter(show_identifiers=args.show_identifiers)
    observer: BluezObserver | None = None
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
        if args.watch:
            await _watch(observer, selector, fmt, json_output=args.json, out=out)
            return ExitCode.OK
        report = build_report(observer.tree, selector)
        out.write(to_json(fmt.report_dict(report)) if args.json else fmt.report_text(report))
        return ExitCode.OK
    except ShokzError as error:
        if args.json:
            out.write(to_json(fmt.error_dict(error)))
        else:
            err.write(fmt.error_text(error))
        return error.exit_code
    finally:
        if observer is not None:
            await observer.close()


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
