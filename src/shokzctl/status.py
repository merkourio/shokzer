"""Read state from the headset over the management channel.

Read-only: FIRMWARE_GET first (Gadgetbridge's initial request, ShokzSupport.kt:82-86), then
the other requested queries. Nothing here changes a headset setting.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from .capabilities import capabilities, firmware_verified
from .errors import ErrorCategory, ShokzError
from .lock import SessionLock
from .protocol.commands import BATTERY_GET, EQUALIZER_GET, FIRMWARE_GET, Command
from .protocol.responses import Battery, Decoded, Equalizer, Firmware
from .session import DEFAULT_REPLY_TIMEOUT, ManagementSession, Trace, no_trace
from .state import (
    Connection,
    DiagnoseReport,
    ManagementTransport,
    Observed,
    ProtocolReadiness,
    SessionState,
)
from .transport.base import Channel
from .transport.management import SHOKZ_SERVICE_UUID

QUERIES = ("firmware", "battery", "eq")
SOURCE = "headset:{}"


class Connector(Protocol):
    on_disconnect_requested: Callable[[], None] | None

    async def open(self) -> Channel: ...

    async def close(self) -> None: ...


type ConnectorFactory = Callable[[str], Awaitable[Connector]]


def check_ready(report: DiagnoseReport) -> None:
    """Refuse before touching the headset unless BlueZ already shows everything we need."""
    if report.device.connection is not Connection.CONNECTED:
        raise ShokzError(
            ErrorCategory.UNAVAILABLE,
            "The headset isn't connected to Fedora.",
            hint="Connect it in GNOME Settings; shokzctl never connects devices itself.",
        )
    if not report.identity.name_matches_model:
        raise ShokzError(
            ErrorCategory.UNSUPPORTED,
            "The selected device isn't an OpenRun Pro 2 by name; not sending it anything.",
        )
    if SHOKZ_SERVICE_UUID not in report.device.uuids:
        raise ShokzError(
            ErrorCategory.UNSUPPORTED,
            "BlueZ doesn't list the Shokz management service (0xFEF0) for this device.",
        )


async def read_status(
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    *,
    queries: Sequence[str] = QUERIES,
    reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
    trace: Trace = no_trace,
    lock: SessionLock | None = None,
) -> DiagnoseReport:
    check_ready(report)
    with lock or SessionLock():
        connector = await open_connector(report.device.path)
        session: ManagementSession | None = None
        try:
            trace("connect", uuid=SHOKZ_SERVICE_UUID)
            channel = await connector.open()
            trace("open")
            session = ManagementSession(channel, reply_timeout=reply_timeout, trace=trace)
            active = session
            connector.on_disconnect_requested = lambda: active.abort(
                ShokzError(ErrorCategory.UNAVAILABLE, "BlueZ closed the management channel.")
            )
            session.start()

            firmware = _expect(await session.query(FIRMWARE_GET), Firmware, FIRMWARE_GET)
            result = dataclasses.replace(
                report,
                firmware=Observed(firmware.version, SOURCE.format(FIRMWARE_GET.name)),
                management_transport=ManagementTransport.RFCOMM,
                management_session=SessionState.OPEN,
                protocol=ProtocolReadiness.READY,
                capabilities=capabilities(firmware.version),
                identity=dataclasses.replace(
                    report.identity, model_verified=firmware_verified(firmware.version)
                ),
            )
            if not firmware_verified(firmware.version):
                result = dataclasses.replace(
                    result,
                    warnings=(
                        *result.warnings,
                        f"Firmware {firmware.version} hasn't been verified with shokzctl.",
                    ),
                )
            if "battery" in queries:
                battery = _expect(await session.query(BATTERY_GET), Battery, BATTERY_GET)
                result = dataclasses.replace(
                    result,
                    headset_battery=Observed(battery.percent, SOURCE.format(BATTERY_GET.name)),
                )
            if "eq" in queries:
                eq = _expect(await session.query(EQUALIZER_GET), Equalizer, EQUALIZER_GET)
                name = eq.preset.name.lower() if eq.preset else f"unknown (0x{eq.code:02x})"
                result = dataclasses.replace(
                    result, active_eq=Observed(name, SOURCE.format(EQUALIZER_GET.name))
                )
            return result
        finally:
            if session is not None:
                await session.close()
            await connector.close()
            trace("closed")


def _expect[T](decoded: Decoded, kind: type[T], command: Command) -> T:
    if not isinstance(decoded, kind):
        raise ShokzError(
            ErrorCategory.PROTOCOL, f"Unexpected reply to {command.name}: {decoded!r}."
        )
    return decoded
