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

from .capabilities import (
    DESCRIPTIONS,
    capabilities,
    firmware_verified,
    is_verified,
    preset_feature,
    require,
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

    @property
    def firmware(self) -> str | None:
        return self.report.firmware.value

    # --- reads -------------------------------------------------------------------------------

    async def read_battery(self, *, allow_unverified: bool = False) -> int:
        require("battery_query", self.firmware, allow_unverified=allow_unverified)
        battery = await self._query(BATTERY_GET, Battery)
        self.report = dataclasses.replace(
            self.report,
            headset_battery=Observed(battery.percent, SOURCE.format(BATTERY_GET.name)),
        )
        return battery.percent

    async def read_eq(self, *, allow_unverified: bool = False) -> Equalizer:
        require("eq_read", self.firmware, allow_unverified=allow_unverified)
        return await self._read_eq()

    async def read_multipoint(self, *, allow_unverified: bool = False) -> Multipoint:
        require("multipoint_read", self.firmware, allow_unverified=allow_unverified)
        status = await self._query(MULTIPOINT_GET, MultipointStatus)
        devices = await self._query(MULTIPOINT_DEVICES_GET, MultipointDevices)
        return Multipoint(status, devices)

    # --- EQ writes ---------------------------------------------------------------------------

    async def set_preset(self, preset: EqPreset, *, allow_unverified: bool = False) -> WriteResult:
        feature = preset_feature(preset)
        if feature is None or preset not in WRITABLE_PRESETS:
            raise ShokzError(
                ErrorCategory.UNSUPPORTED,
                f"There are no OpenRun Pro 2 bytes for the {preset.name.lower()} preset.",
            )
        verified = require(feature, self.firmware, allow_unverified=allow_unverified)

        def is_preset(eq: Equalizer) -> bool:
            return eq.code == preset.value

        return await self._write_eq(
            requested=preset.name.lower(),
            args=pro2_preset_request(preset).args,
            already=is_preset,
            confirmed=is_preset,
            verified=verified,
        )

    async def set_custom(
        self, bands: Sequence[int], *, allow_unverified: bool = False
    ) -> WriteResult:
        try:
            args = custom_eq_request(list(bands)).args
        except ValueError as error:
            raise ShokzError(ErrorCategory.INVALID_INPUT, str(error)) from None
        verified = require("eq_write_custom", self.firmware, allow_unverified=allow_unverified)
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
        if sent.failure is not None:
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=sent.outcome,
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
            outcome, detail = WriteOutcome.NOT_APPLIED, None
        elif after.preset is EqPreset.CUSTOM and after.bands is None:
            outcome = WriteOutcome.PARTLY_CONFIRMED
            detail = "The headset reported the custom preset but not its band values."
        else:
            outcome, detail = WriteOutcome.CONFIRMED, None
        return result(
            acknowledged=sent.acknowledged, readback=eq_label(after), outcome=outcome, detail=detail
        )

    # --- multipoint writes -------------------------------------------------------------------

    async def set_multipoint(self, enabled: bool, *, allow_unverified: bool = False) -> WriteResult:
        verified = require("multipoint_write", self.firmware, allow_unverified=allow_unverified)
        before = await self._query(MULTIPOINT_GET, MultipointStatus)
        result = functools.partial(
            WriteResult,
            setting="multipoint",
            requested="on" if enabled else "off",
            previous=multipoint_label(before),
            verified=verified,
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
        if sent.failure is not None:
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=sent.outcome,
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
            outcome, detail = WriteOutcome.NOT_APPLIED, None
        return result(
            acknowledged=sent.acknowledged,
            readback=None if after is None else multipoint_label(after),
            outcome=outcome,
            detail=detail,
        )

    async def connect_peer(self, address: str, *, allow_unverified: bool = False) -> WriteResult:
        return await self._peer(address, connect=True, allow_unverified=allow_unverified)

    async def disconnect_peer(self, address: str, *, allow_unverified: bool = False) -> WriteResult:
        return await self._peer(address, connect=False, allow_unverified=allow_unverified)

    async def _peer(self, address: str, *, connect: bool, allow_unverified: bool) -> WriteResult:
        verified = require("multipoint_peers", self.firmware, allow_unverified=allow_unverified)
        address = address.upper()
        try:
            args = address_arg(address)
        except ValueError as invalid:
            raise ShokzError(ErrorCategory.INVALID_INPUT, str(invalid)) from None
        own = self.report.adapter.address
        if own is not None and address == own.upper():
            raise ShokzError(
                ErrorCategory.INVALID_INPUT,
                "That address is this computer. Use GNOME Settings to connect or disconnect it.",
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
        if sent.failure is not None:
            return result(
                acknowledged=sent.acknowledged,
                readback=None,
                outcome=sent.outcome,
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
        outcome = WriteOutcome.CONFIRMED if after.connected is connect else WriteOutcome.NOT_APPLIED
        return result(acknowledged=sent.acknowledged, readback=_peer_label(after), outcome=outcome)

    # --- helpers -----------------------------------------------------------------------------

    async def _read_eq(self) -> Equalizer:
        eq = await self._query(EQUALIZER_GET, Equalizer)
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
        if isinstance(reply, Acknowledgement) and reply.status not in (None, 0):
            if reply.status == TOO_MANY_CONNECTED:
                reason = "too many devices are connected to the headset"
            else:
                reason = f"status 0x{reply.status:08x}"
            return _Sent(
                acknowledged=True, failure=f"The headset refused: {reason}.", rejected=True
            )
        return _Sent(acknowledged=True)

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
    acknowledged: bool | None
    failure: str | None = None
    rejected: bool = False

    @property
    def outcome(self) -> WriteOutcome:
        return WriteOutcome.REJECTED if self.rejected else WriteOutcome.UNCERTAIN


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
            if session is not None:
                await session.close()
            await connector.close()
            trace("closed")


async def read_status(
    report: DiagnoseReport,
    open_connector: ConnectorFactory,
    *,
    queries: Sequence[str] = QUERIES,
    allow_unverified: bool = False,
    reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
    trace: Trace = no_trace,
    lock: SessionLock | None = None,
) -> DiagnoseReport:
    """Firmware always, then the other requested reads. Read-only."""
    async with connect(
        report, open_connector, reply_timeout=reply_timeout, trace=trace, lock=lock
    ) as headset:
        return await status_reads(headset, queries, allow_unverified=allow_unverified)


async def status_reads(
    headset: Headset, queries: Sequence[str], *, allow_unverified: bool
) -> DiagnoseReport:
    """Run the requested reads. A read that isn't verified for this firmware is skipped with
    a warning unless allow_unverified is set, so status stays useful on new firmware."""
    reads: list[tuple[str, Callable[..., Awaitable[object]]]] = []
    if "battery" in queries:
        reads.append(("battery_query", headset.read_battery))
    if "eq" in queries:
        reads.append(("eq_read", headset.read_eq))
    for feature, read in reads:
        if not allow_unverified and not is_verified(feature, headset.firmware):
            warning = f"Skipped {DESCRIPTIONS[feature]}: not verified on this firmware."
            headset.report = dataclasses.replace(
                headset.report, warnings=(*headset.report.warnings, warning)
            )
            continue
        await read(allow_unverified=allow_unverified)
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
        warning = f"Firmware {version} hasn't been verified with shokzctl."
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
