"""Headset operations shared by the CLI and the GUI.

Every operation runs inside one management session opened by connect(). The session always
starts with FIRMWARE_GET, so each feature gate knows which firmware it is checking against.

Writes follow one pattern: read the current value, send the change once, then read back.
A write is confirmed only by the readback. Nothing is ever resent; when the readback can't
be done, the outcome is reported as uncertain.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from . import APP_NAME, FIRMWARE_REPORT_URL
from .capabilities import (
    capabilities,
    firmware_verified,
    is_verified,
    preset_feature,
)
from .errors import ErrorCategory, ShokzError
from .lock import SessionLock
from .protocol.commands import (
    BATTERY_GET,
    EQUALIZER_GET,
    EQUALIZER_SET,
    FIRMWARE_GET,
    MULTIPOINT_CONNECT,
    MULTIPOINT_DEVICES_GET,
    MULTIPOINT_DISCONNECT,
    MULTIPOINT_GET,
    MULTIPOINT_OFF,
    MULTIPOINT_ON,
    Command,
    EqPreset,
    address_arg,
    custom_eq_request,
    multipoint_off_request,
    pro2_preset_request,
)
from .protocol.responses import (
    TOO_MANY_CONNECTED,
    Acknowledgement,
    Battery,
    Decoded,
    Equalizer,
    Firmware,
    MultipointDevices,
    MultipointPeer,
    MultipointStatus,
)
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
WRITABLE_PRESETS = (EqPreset.STANDARD, EqPreset.VOCAL, EqPreset.BASS, EqPreset.TREBLE)


class Connector(Protocol):
    on_disconnect_requested: Callable[[], None] | None

    async def open(self) -> Channel: ...

    async def close(self) -> None: ...


type ConnectorFactory = Callable[[str], Awaitable[Connector]]


class WriteOutcome(StrEnum):
    CONFIRMED = "confirmed"  # read back as requested
    PARTLY_CONFIRMED = "partly_confirmed"  # the preset read back, its band values didn't
    ALREADY_SET = "already_set"  # it was already that way; nothing was sent
    NOT_APPLIED = "not_applied"  # read back, and it isn't what was asked for
    REJECTED = "rejected"  # the headset answered with a failure status
    UNCERTAIN = "uncertain"  # no readback was possible; the state is unknown


@dataclass(frozen=True)
class WriteResult:
    setting: str
    requested: str
    previous: str | None
    acknowledged: bool | None  # None: nothing was sent, or no acknowledgement exists
    readback: str | None
    outcome: WriteOutcome
    verified: bool
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in (
            WriteOutcome.CONFIRMED,
            WriteOutcome.PARTLY_CONFIRMED,
            WriteOutcome.ALREADY_SET,
        )


@dataclass(frozen=True)
class Multipoint:
    status: MultipointStatus
    devices: MultipointDevices


def eq_label(eq: Equalizer) -> str:
    if eq.preset is EqPreset.CUSTOM:
        if eq.bands is None:
            return "custom"
        return "custom (" + ", ".join(f"{b:+d}" if b else "0" for b in eq.bands) + ")"
    if eq.preset is not None:
        return eq.preset.name.lower()
    return f"unknown (0x{eq.code:02x})"


def multipoint_label(status: MultipointStatus) -> str:
    if status.enabled is None:
        return f"unknown (0x{status.raw:08x})"
    return "on" if status.enabled else "off"


def check_ready(report: DiagnoseReport) -> None:
    """Refuse before touching the headset unless BlueZ already shows everything we need."""
    if report.device.connection is not Connection.CONNECTED:
        raise ShokzError(
            ErrorCategory.UNAVAILABLE,
            "The headset isn't connected to this computer.",
            hint="Connect it in your Bluetooth settings; Shokzer never connects devices itself.",
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


class Headset:
    """Operations on one open management session."""

    def __init__(
        self,
        session: ManagementSession,
        report: DiagnoseReport,
        *,
        readback_attempts: int = 3,
        readback_interval: float = 0.3,
        peer_timeout: float = 8.0,
        peer_interval: float = 1.0,
    ) -> None:
        self._session = session
        self.report = report
        self._readback_attempts = readback_attempts
        self._readback_interval = readback_interval
        self._peer_timeout = peer_timeout
        self._peer_interval = peer_interval
        self.last_eq: Equalizer | None = None

    @property
    def firmware(self) -> str | None:
        return self.report.firmware.value

    # --- reads -------------------------------------------------------------------------------

    async def read_battery(self) -> int:
        battery = await self._query(BATTERY_GET, Battery)
        self.report = dataclasses.replace(
            self.report,
            headset_battery=Observed(battery.percent, SOURCE.format(BATTERY_GET.name)),
        )
        return battery.percent

    async def read_eq(self) -> Equalizer:
        return await self._read_eq()

    async def read_multipoint(self) -> Multipoint:
        status = await self._query(MULTIPOINT_GET, MultipointStatus)
        devices = await self._query(MULTIPOINT_DEVICES_GET, MultipointDevices)
        return Multipoint(status, devices)

    # --- EQ writes ---------------------------------------------------------------------------

    async def set_preset(self, preset: EqPreset) -> WriteResult:
        feature = preset_feature(preset)
        if feature is None or preset not in WRITABLE_PRESETS:
            raise ShokzError(
                ErrorCategory.UNSUPPORTED,
                f"There are no OpenRun Pro 2 bytes for the {preset.name.lower()} preset.",
            )
        verified = is_verified(feature, self.firmware)

        def is_preset(eq: Equalizer) -> bool:
            return eq.code == preset.value

        return await self._write_eq(
            requested=preset.name.lower(),
            args=pro2_preset_request(preset).args,
            already=is_preset,
            confirmed=is_preset,
            verified=verified,
        )

    async def set_custom(self, bands: Sequence[int]) -> WriteResult:
        try:
            args = custom_eq_request(list(bands)).args
        except ValueError as error:
            raise ShokzError(ErrorCategory.INVALID_INPUT, str(error)) from None
        verified = is_verified("eq_write_custom", self.firmware)
        wanted = tuple(bands)

        def already(eq: Equalizer) -> bool:
            return eq.preset is EqPreset.CUSTOM and eq.bands == wanted

        def confirmed(eq: Equalizer) -> bool:
            # A readback without band values only confirms the preset (see PARTLY_CONFIRMED).
            return eq.preset is EqPreset.CUSTOM and eq.bands in (wanted, None)

        return await self._write_eq(
            requested=eq_label(Equalizer(EqPreset.CUSTOM.value, EqPreset.CUSTOM, wanted, False)),
            args=args,
            already=already,
            confirmed=confirmed,
            verified=verified,
        )

    async def _write_eq(
        self,
        *,
        requested: str,
        args: bytes,
        already: Callable[[Equalizer], bool],
        confirmed: Callable[[Equalizer], bool],
        verified: bool,
    ) -> WriteResult:
        before = await self._read_eq()
        result = functools.partial(
            WriteResult,
            setting="eq",
            requested=requested,
            previous=eq_label(before),
            verified=verified,
        )
        if already(before):
            return result(
                acknowledged=None, readback=eq_label(before), outcome=WriteOutcome.ALREADY_SET
            )
        sent = await self._send_once(EQUALIZER_SET, args)
        if sent.failure is not None:  # the channel failed: nothing can be read back
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=WriteOutcome.UNCERTAIN,
                detail=sent.failure,
            )
        back = await self._read_until(self._read_eq, confirmed)
        after, error = back.value, back.error
        if after is None:
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=WriteOutcome.UNCERTAIN,
                detail=error,
            )
        if not confirmed(after):
            outcome, detail = sent.unconfirmed()
        elif after.preset is EqPreset.CUSTOM and after.bands is None:
            outcome = WriteOutcome.PARTLY_CONFIRMED
            detail = "The headset reported the custom preset but not its band values."
        else:
            outcome, detail = WriteOutcome.CONFIRMED, None
        return result(
            acknowledged=sent.acknowledged, readback=eq_label(after), outcome=outcome, detail=detail
        )

    # --- multipoint writes -------------------------------------------------------------------

    async def set_multipoint(self, enabled: bool) -> WriteResult:
        verified = is_verified("multipoint_write", self.firmware)
        before = await self._query(MULTIPOINT_GET, MultipointStatus)
        result = functools.partial(
            WriteResult,
            setting="multipoint",
            requested="on" if enabled else "off",
            previous=multipoint_label(before),
            verified=verified,
        )
        if before.enabled is None:
            raise ShokzError(
                ErrorCategory.UNSUPPORTED,
                f"Multipoint reads 0x{before.raw:08x}, which no source names as on or off; "
                "not changing it.",
                hint="Record the value from `shokzer multipoint status` so it can be added.",
            )
        if before.enabled is enabled:
            return result(
                acknowledged=None,
                readback=multipoint_label(before),
                outcome=WriteOutcome.ALREADY_SET,
            )
        if enabled:
            command, args = MULTIPOINT_ON, b""
        else:
            own = self.report.adapter.address
            if own is None:
                raise ShokzError(
                    ErrorCategory.UNAVAILABLE,
                    "BlueZ doesn't report this adapter's address, which turning multipoint "
                    "off needs.",
                )
            command, args = MULTIPOINT_OFF, multipoint_off_request(own).args
        sent = await self._send_once(command, args)
        if sent.failure is not None:  # the channel failed: nothing can be read back
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=WriteOutcome.UNCERTAIN,
                detail=sent.failure,
            )

        def matches(status: MultipointStatus) -> bool:
            return status.enabled is enabled

        back = await self._read_until(
            lambda: self._query(MULTIPOINT_GET, MultipointStatus), matches
        )
        after, error = back.value, back.error
        if after is None:
            outcome, detail = WriteOutcome.UNCERTAIN, error
        elif matches(after):
            outcome, detail = WriteOutcome.CONFIRMED, None
        elif after.enabled is None:
            outcome = WriteOutcome.UNCERTAIN
            detail = f"Multipoint now reads 0x{after.raw:08x}, a value no source names yet."
        else:
            outcome, detail = sent.unconfirmed()
        return result(
            acknowledged=sent.acknowledged,
            readback=None if after is None else multipoint_label(after),
            outcome=outcome,
            detail=detail,
        )

    async def connect_peer(self, address: str) -> WriteResult:
        return await self._peer(address, connect=True)

    async def disconnect_peer(self, address: str) -> WriteResult:
        return await self._peer(address, connect=False)

    async def _peer(self, address: str, *, connect: bool) -> WriteResult:
        verified = is_verified("multipoint_peers", self.firmware)
        address = address.upper()
        try:
            args = address_arg(address)
        except ValueError as invalid:
            raise ShokzError(ErrorCategory.INVALID_INPUT, str(invalid)) from None
        own = self.report.adapter.address
        if own is None:
            raise ShokzError(
                ErrorCategory.UNAVAILABLE,
                "BlueZ doesn't report this adapter's address, so this computer can't be told "
                "apart from other devices; not changing connections.",
            )
        if address == own.upper():
            raise ShokzError(
                ErrorCategory.INVALID_INPUT,
                "That address is this computer. Connect or disconnect it in your Bluetooth "
                "settings.",
            )
        if connect:
            status = await self._query(MULTIPOINT_GET, MultipointStatus)
            if status.enabled is not True:
                raise ShokzError(
                    ErrorCategory.UNSUPPORTED,
                    f"Multipoint is {multipoint_label(status)}. Connecting another device "
                    "could drop this computer, so it's only done with multipoint on.",
                )
        peer = _find_peer(await self._query(MULTIPOINT_DEVICES_GET, MultipointDevices), address)
        if peer is None:
            raise ShokzError(
                ErrorCategory.UNAVAILABLE,
                "The headset doesn't list that address among its remembered devices.",
            )
        result = functools.partial(
            WriteResult,
            setting="peer",
            requested="connected" if connect else "disconnected",
            previous=_peer_label(peer),
            verified=verified,
        )
        if peer.connected is connect:
            return result(
                acknowledged=None, readback=_peer_label(peer), outcome=WriteOutcome.ALREADY_SET
            )
        sent = await self._send_once(MULTIPOINT_CONNECT if connect else MULTIPOINT_DISCONNECT, args)
        if sent.failure is not None:  # the channel failed: nothing can be read back
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=WriteOutcome.UNCERTAIN,
                detail=sent.failure,
            )

        async def read_peer() -> MultipointPeer | None:
            return _find_peer(await self._query(MULTIPOINT_DEVICES_GET, MultipointDevices), address)

        # Connecting takes seconds, so poll the list (reads only) until it changes or time's up.
        attempts = max(1, int(self._peer_timeout / self._peer_interval) + 1)
        back = await self._read_until(
            read_peer,
            lambda p: p is not None and p.connected is connect,
            attempts=attempts,
            interval=self._peer_interval,
        )
        after, error = back.value, back.error
        if after is None:
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=WriteOutcome.UNCERTAIN,
                detail=error or "The device no longer appears in the headset's list.",
            )
        if after.connected is connect:
            outcome, detail = WriteOutcome.CONFIRMED, None
        else:
            outcome, detail = sent.unconfirmed()
        return result(
            acknowledged=sent.acknowledged,
            readback=_peer_label(after),
            outcome=outcome,
            detail=detail,
        )

    # --- helpers -----------------------------------------------------------------------------

    async def _read_eq(self) -> Equalizer:
        eq = await self._query(EQUALIZER_GET, Equalizer)
        self.last_eq = eq
        self.report = dataclasses.replace(
            self.report, active_eq=Observed(eq_label(eq), SOURCE.format(EQUALIZER_GET.name))
        )
        return eq

    async def _query[T](self, command: Command, kind: type[T]) -> T:
        return _expect(await self._session.query(command), kind, command)

    async def _send_once(self, command: Command, args: bytes) -> _Sent:
        """Send a write exactly once. A timeout isn't a failure: the write may still have
        landed, so the caller reads back."""
        try:
            reply = await self._session.query(command, args)
        except ShokzError as error:
            if error.category is ErrorCategory.TIMEOUT:
                return _Sent(acknowledged=False)
            return _Sent(acknowledged=None, failure=error.message)
        status = reply.status if isinstance(reply, Acknowledgement) else None
        # A status is only interpreted after the readback: sources name 0 and, for connect,
        # 0x400; any other value alone doesn't prove the change failed.
        return _Sent(acknowledged=True, status=status)

    async def _read_until[T](
        self,
        read: Callable[[], Awaitable[T]],
        matches: Callable[[T], bool],
        *,
        attempts: int | None = None,
        interval: float | None = None,
    ) -> _Readback[T]:
        """Read back until it matches or attempts run out. Reads are safe to repeat; writes
        aren't. Returns the last value read (None if no read succeeded) and the last error."""
        last: T | None = None
        problem: str | None = None
        for attempt in range(attempts or self._readback_attempts):
            if attempt:
                await asyncio.sleep(self._readback_interval if interval is None else interval)
            try:
                current = await read()
            except ShokzError as failed:
                problem = failed.message
                if failed.category is not ErrorCategory.TIMEOUT:
                    break
                continue
            last, problem = current, None
            if matches(current):
                break
        return _Readback(last, problem)


@dataclass(frozen=True)
class _Readback[T]:
    value: T | None
    error: str | None


@dataclass(frozen=True)
class _Sent:
    acknowledged: bool | None  # False: no reply in time; None: the channel failed
    status: int | None = None
    failure: str | None = None  # channel failure; set only when acknowledged is None

    def unconfirmed(self) -> tuple[WriteOutcome, str | None]:
        """Outcome when the readback still shows something other than what was asked for."""
        if self.status == TOO_MANY_CONNECTED:
            return WriteOutcome.REJECTED, "The headset refused: too many devices are connected."
        if self.status not in (None, 0):
            return WriteOutcome.REJECTED, f"The headset answered with status 0x{self.status:08x}."
        if self.acknowledged is False:
            return (
                WriteOutcome.UNCERTAIN,
                "No acknowledgement, and it still reads the old value. It may not have arrived "
                "or may still land; read again before retrying.",
            )
        return WriteOutcome.NOT_APPLIED, None


@asynccontextmanager
async def connect(
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    *,
    reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
    trace: Trace = no_trace,
    lock: SessionLock | None = None,
) -> AsyncIterator[Headset]:
    """Open the management channel, read the firmware, and hand out a Headset."""
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
            yield Headset(session, _with_firmware(report, firmware.version))
        finally:
            try:
                if session is not None:
                    await session.close()
            finally:
                await connector.close()
                trace("closed")


async def read_status(
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    *,
    queries: Sequence[str] = QUERIES,
    reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
    trace: Trace = no_trace,
    lock: SessionLock | None = None,
) -> DiagnoseReport:
    """Firmware always, then the other requested reads. Read-only."""
    async with connect(
        report, open_connector, reply_timeout=reply_timeout, trace=trace, lock=lock
    ) as headset:
        return await status_reads(headset, queries)


async def status_reads(headset: Headset, queries: Sequence[str]) -> DiagnoseReport:
    """Run the requested reads (firmware was already read when the session opened)."""
    if "battery" in queries:
        await headset.read_battery()
    if "eq" in queries:
        await headset.read_eq()
    return headset.report


def _with_firmware(report: DiagnoseReport, version: str) -> DiagnoseReport:
    result = dataclasses.replace(
        report,
        firmware=Observed(version, SOURCE.format(FIRMWARE_GET.name)),
        management_transport=ManagementTransport.RFCOMM,
        management_session=SessionState.OPEN,
        protocol=ProtocolReadiness.READY,
        capabilities=capabilities(version),
        identity=dataclasses.replace(report.identity, model_verified=firmware_verified(version)),
    )
    if not firmware_verified(version):
        warning = (
            f"Firmware {version} hasn't been tested with {APP_NAME} yet. Everything should "
            f"work; please report how it goes: {FIRMWARE_REPORT_URL}"
        )
        result = dataclasses.replace(result, warnings=(*result.warnings, warning))
    return result


def _find_peer(devices: MultipointDevices | None, address: str) -> MultipointPeer | None:
    if devices is None:
        return None
    return next((p for p in devices.peers if p.address == address), None)


def _peer_label(peer: MultipointPeer) -> str:
    return "connected" if peer.connected else "disconnected"


def _expect[T](decoded: Decoded, kind: type[T], command: Command) -> T:
    if not isinstance(decoded, kind):
        raise ShokzError(
            ErrorCategory.PROTOCOL, f"Unexpected reply to {command.name}: {decoded!r}."
        )
    return decoded
