"""State and actions behind the window. No GTK here, so it runs in offline tests.

The controller follows BlueZ passively the whole time and opens a management session only
for each action (refresh or a change), then closes it. That keeps the channel and the
session lock free for the CLI and the phone app between actions.

Source-supported features are offered on untested firmware too; hardware verification is
tracked separately and the view shows a notice for untested features.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from ..capabilities import capabilities
from ..device import Selector, build_report
from ..errors import ShokzError
from ..lock import SessionLock
from ..protocol.commands import EqPreset
from ..protocol.responses import Equalizer
from ..service import (
    QUERIES,
    ConnectorFactory,
    Headset,
    Multipoint,
    WriteOutcome,
    WriteResult,
    connect,
    status_reads,
)
from ..state import Connection, DiagnoseReport, Support
from ..transport.bluez import BluezObserver, ConnectionFactory

CLOSE_GRACE = 30.0  # longest action: connect (15 s) plus peer polling (8 s)

PRESET_LABELS = {
    EqPreset.STANDARD: "Standard (Hybrid)",
    EqPreset.CLASSIC: "Classic (Bone only)",
    EqPreset.VOCAL: "Vocal",
    EqPreset.BASS: "Bass Boost",
    EqPreset.TREBLE: "Treble Boost",
    EqPreset.CUSTOM: "Custom",
}

_OUTCOME_MESSAGES = {
    WriteOutcome.CONFIRMED: "{what} set to {requested}",
    WriteOutcome.PARTLY_CONFIRMED: "{what} set to {requested}; band values weren't reported back",
    WriteOutcome.ALREADY_SET: "{what} was already {requested}",
    WriteOutcome.NOT_APPLIED: "{what} didn't change to {requested}",
    WriteOutcome.REJECTED: "The headset refused: {detail}",
    WriteOutcome.UNCERTAIN: "Not sure the change landed; refresh before trying again",
}


@dataclass(frozen=True)
class ViewState:
    report: DiagnoseReport | None = None  # BlueZ's view, always current
    bluez_error: str | None = None
    firmware: str | None = None
    headset_battery: int | None = None
    eq: Equalizer | None = None  # last value read from the headset
    pending: str | None = None  # what's being changed, e.g. "eq:vocal"
    multipoint: Multipoint | None = None
    busy: str | None = None  # short label while a session is open
    message: str | None = None
    message_id: int = 0
    capabilities: Mapping[str, Support] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.report is not None and self.report.device.connection is Connection.CONNECTED

    def can(self, feature: str) -> bool:
        return self.capabilities.get(feature) in (Support.SUPPORTED, Support.UNTESTED)

    @property
    def firmware_tested(self) -> bool:
        return (
            self.firmware is not None
            and self.can("eq_read")
            and all(v is Support.SUPPORTED for v in self.capabilities.values())
        )


class Controller:
    def __init__(
        self,
        *,
        connect_bus: ConnectionFactory,
        open_connector: ConnectorFactory,
        selector: Selector | None = None,
        lock_factory: Callable[[], SessionLock] = SessionLock,
        on_change: Callable[[ViewState], None] = lambda _: None,
    ) -> None:
        self._connect_bus = connect_bus
        self._open_connector = open_connector
        self._selector = selector or Selector()
        self._lock_factory = lock_factory
        self.on_change = on_change
        self.state = ViewState()
        self._observer: BluezObserver | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    # --- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        try:
            self._observer = BluezObserver(await self._connect_bus())
            await self._observer.start()
        except ShokzError as error:
            self._update(bluez_error=error.message)
            return
        self._sync_bluez()
        self._watcher = asyncio.get_running_loop().create_task(self._watch())
        if self.state.connected:
            await self.refresh()

    def spawn(self, action: Awaitable[None]) -> None:
        """Run an action in the background, keeping a reference until it finishes."""
        task = asyncio.ensure_future(action)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        # Stop the BlueZ watcher first so it can't start a new session while we wind down.
        self._closing = True
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        # Let a running session finish rather than cancel it: a cancelled write has an
        # unknown outcome, and a cancelled connect leaves BlueZ busy. Cancel only stragglers.
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=CLOSE_GRACE)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self._observer is not None:
            await self._observer.close()

    # --- actions -----------------------------------------------------------------------------

    async def refresh(self) -> None:
        async def read(headset: Headset) -> None:
            await status_reads(headset, QUERIES)
            multipoint = await headset.read_multipoint()
            self._absorb(headset, multipoint=multipoint)

        await self._session("Reading", read)

    async def set_preset(self, preset: EqPreset) -> None:
        label = PRESET_LABELS.get(preset, preset.name.title())

        async def write(headset: Headset) -> None:
            result = await headset.set_preset(preset)
            self._absorb(headset)
            self._report(result, "EQ", label)

        await self._session(f"Setting {label}", write, pending=f"eq:{preset.name.lower()}")

    async def set_custom(self, bands: Sequence[int]) -> None:
        async def write(headset: Headset) -> None:
            result = await headset.set_custom(bands)
            self._absorb(headset)
            self._report(result, "EQ", "Custom")

        await self._session("Applying custom EQ", write, pending="eq:custom")

    async def set_multipoint(self, enabled: bool) -> None:
        async def write(headset: Headset) -> None:
            result = await headset.set_multipoint(enabled)
            self._report(result, "Multipoint", "on" if enabled else "off")
            await self._reread_multipoint(headset)

        await self._session("Changing multipoint", write, pending="multipoint")

    async def set_peer(self, address: str, connected: bool) -> None:
        async def write(headset: Headset) -> None:
            if connected:
                result = await headset.connect_peer(address)
            else:
                result = await headset.disconnect_peer(address)
            self._report(result, "Device", "connected" if connected else "disconnected")
            await self._reread_multipoint(headset)

        verb = "Connecting" if connected else "Disconnecting"
        await self._session(f"{verb} device", write, pending=f"peer:{address}")

    # --- internals ---------------------------------------------------------------------------

    async def _session(
        self,
        busy: str,
        action: Callable[[Headset], Awaitable[None]],
        *,
        pending: str | None = None,
    ) -> None:
        if self._closing or self._busy.locked() or self.state.report is None:
            return
        async with self._busy:
            self._update(busy=busy, pending=pending)
            try:
                async with connect(
                    self.state.report, self._open_connector, lock=self._lock_factory()
                ) as headset:
                    await action(headset)
            except ShokzError as error:
                self._message(error.message)
            finally:
                self._update(busy=None, pending=None)

    async def _reread_multipoint(self, headset: Headset) -> None:
        """Best effort after a write: the write's own result is already reported, so a failed
        follow-up read must not replace it."""
        try:
            multipoint = await headset.read_multipoint()
        except ShokzError:
            self._update(multipoint=None)
            return
        self._absorb(headset, multipoint=multipoint)

    def _absorb(self, headset: Headset, *, multipoint: Multipoint | None = None) -> None:
        report = headset.report
        changes: dict[str, object] = {
            "firmware": headset.firmware,
            "capabilities": report.capabilities,
        }
        if report.headset_battery.known:
            changes["headset_battery"] = report.headset_battery.value
        if headset.last_eq is not None:
            changes["eq"] = headset.last_eq
        if multipoint is not None:
            changes["multipoint"] = multipoint
        self._update(**changes)

    def _report(self, result: WriteResult, what: str, requested: str) -> None:
        template = _OUTCOME_MESSAGES[result.outcome]
        self._message(template.format(what=what, requested=requested, detail=result.detail))

    def _message(self, text: str) -> None:
        self._update(message=text, message_id=self.state.message_id + 1)

    async def _watch(self) -> None:
        assert self._observer is not None
        while True:
            self._observer.changed.clear()
            was_connected = self.state.connected
            self._sync_bluez()
            if self.state.connected and not was_connected:
                self.spawn(self.refresh())
            await self._observer.changed.wait()

    def _sync_bluez(self) -> None:
        assert self._observer is not None
        try:
            if self._observer.last_error is not None:
                raise self._observer.last_error
            report = build_report(self._observer.tree, self._selector)
        except ShokzError as error:
            self._update(report=None, bluez_error=error.message, **_HEADSET_UNKNOWN)
            return
        changes: dict[str, object] = {"report": report, "bluez_error": None}
        if report.device.connection is not Connection.CONNECTED:
            changes |= _HEADSET_UNKNOWN  # nothing read earlier can be trusted any more
        self._update(**changes)

    def _update(self, **changes: object) -> None:
        self.state = dataclasses.replace(self.state, **changes)  # type: ignore[arg-type]
        self.on_change(self.state)


_HEADSET_UNKNOWN: dict[str, object] = {
    "firmware": None,
    "headset_battery": None,
    "eq": None,
    "multipoint": None,
    "capabilities": capabilities(None),
}
