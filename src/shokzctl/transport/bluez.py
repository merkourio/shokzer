"""Passive BlueZ observer over the system D-Bus.

Reads BlueZ's object tree and follows its signals. It never calls a BlueZ method that changes
state: no discovery, pairing, connect/disconnect, or GATT access.

Ordering: D-Bus keeps the order of messages from one sender, and BlueZ numbers its method
returns and signals from one serial counter. Signals that arrive while a snapshot is being
fetched are queued, and only those numbered after the snapshot reply are applied on top of it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from dbus_fast import BusType, Message, MessageType, unpack_variants
from dbus_fast.aio import MessageBus
from dbus_fast.errors import AuthError

from ..errors import ErrorCategory, ShokzError

BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
BATTERY = "org.bluez.Battery1"
BEARER_BREDR = "org.bluez.Bearer.BREDR1"
BEARER_LE = "org.bluez.Bearer.LE1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"

# Removing one of these takes the object's children with it (devices under an adapter,
# media endpoints and players under a device).
_PARENT_INTERFACES = frozenset({ADAPTER, DEVICE})

MATCH_RULES = (
    f"type='signal',sender='{BLUEZ}',interface='{OBJECT_MANAGER}'",
    f"type='signal',sender='{BLUEZ}',interface='{PROPERTIES}',member='PropertiesChanged'",
    f"type='signal',sender='{DBUS}',interface='{DBUS}',member='NameOwnerChanged',arg0='{BLUEZ}'",
)

type Properties = dict[str, Any]
type Interfaces = dict[str, Properties]
type Objects = dict[str, Interfaces]


@dataclass(frozen=True)
class Snapshot:
    objects: Objects
    sender: str
    serial: int


@dataclass(frozen=True)
class InterfacesAdded:
    sender: str
    serial: int
    path: str
    interfaces: Interfaces


@dataclass(frozen=True)
class InterfacesRemoved:
    sender: str
    serial: int
    path: str
    interfaces: tuple[str, ...]


@dataclass(frozen=True)
class PropertiesChanged:
    sender: str
    serial: int
    path: str
    interface: str
    changed: Properties
    invalidated: tuple[str, ...]


@dataclass(frozen=True)
class OwnerChanged:
    """BlueZ left the bus (new_owner is None) or a new BlueZ instance took the name."""

    new_owner: str | None


type BluezSignal = InterfacesAdded | InterfacesRemoved | PropertiesChanged
type Event = BluezSignal | OwnerChanged


class BluezTree:
    """BlueZ's objects as last reported by one BlueZ instance."""

    def __init__(self) -> None:
        self._objects: Objects = {}
        self._owner: str | None = None
        self._snapshot_serial = 0

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def objects(self) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
        return self._objects

    def load(self, snapshot: Snapshot) -> None:
        self._objects = {
            path: {name: dict(props) for name, props in interfaces.items()}
            for path, interfaces in snapshot.objects.items()
        }
        self._owner = snapshot.sender
        self._snapshot_serial = snapshot.serial

    def clear(self) -> None:
        self._objects = {}
        self._owner = None
        self._snapshot_serial = 0

    def apply(self, signal: BluezSignal) -> bool:
        """Apply a signal from the current BlueZ instance. Returns whether anything changed."""
        if (
            self._owner is None
            or signal.sender != self._owner
            or signal.serial <= self._snapshot_serial
        ):
            return False
        match signal:
            case InterfacesAdded():
                return self._add(signal.path, signal.interfaces)
            case InterfacesRemoved():
                return self._remove(signal.path, signal.interfaces)
            case PropertiesChanged():
                return self._change(signal)

    def _add(self, path: str, interfaces: Interfaces) -> bool:
        if not interfaces:
            return False
        obj = self._objects.setdefault(path, {})
        for name, props in interfaces.items():
            obj[name] = dict(props)
        return True

    def _remove(self, path: str, names: tuple[str, ...]) -> bool:
        obj = self._objects.get(path)
        if obj is None:
            return False
        for name in names:
            obj.pop(name, None)
        if _PARENT_INTERFACES.intersection(names):
            prefix = path + "/"
            for child in [p for p in self._objects if p.startswith(prefix)]:
                del self._objects[child]
        if not obj:
            del self._objects[path]
        return True

    def _change(self, signal: PropertiesChanged) -> bool:
        props = self._objects.get(signal.path, {}).get(signal.interface)
        if props is None:
            return False
        props.update(signal.changed)
        for name in signal.invalidated:
            props.pop(name, None)
        return True


def parse_signal(
    *,
    sender: str | None,
    serial: int,
    path: str | None,
    interface: str | None,
    member: str | None,
    body: list[Any],
) -> Event | None:
    """Turn a raw D-Bus signal into an Event, or None if it isn't one we follow."""
    if interface == DBUS and member == "NameOwnerChanged":
        name, _old, new = body
        if name != BLUEZ:
            return None
        return OwnerChanged(new_owner=new or None)
    if sender is None or path is None:
        return None
    if interface == OBJECT_MANAGER and member == "InterfacesAdded":
        obj_path, interfaces = body
        return InterfacesAdded(sender, serial, obj_path, unpack_variants(interfaces))
    if interface == OBJECT_MANAGER and member == "InterfacesRemoved":
        obj_path, names = body
        return InterfacesRemoved(sender, serial, obj_path, tuple(names))
    if interface == PROPERTIES and member == "PropertiesChanged":
        iface, changed, invalidated = body
        if not iface.startswith(BLUEZ + "."):
            return None
        return PropertiesChanged(
            sender, serial, path, iface, unpack_variants(changed), tuple(invalidated)
        )
    return None


class BluezConnection(Protocol):
    """What the observer needs from the bus. Tests replace it with a fake."""

    async def subscribe(self, handler: Callable[[Event], None]) -> None: ...

    async def name_owner(self) -> str | None: ...

    async def managed_objects(self) -> Snapshot: ...

    async def close(self) -> None: ...


type ConnectionFactory = Callable[[], Awaitable[BluezConnection]]


class BluezObserver:
    """Keeps a BluezTree current, including across BlueZ restarts."""

    def __init__(self, connection: BluezConnection) -> None:
        self.tree = BluezTree()
        self.changed = asyncio.Event()
        self.last_error: ShokzError | None = None
        self._connection = connection
        self._lock = asyncio.Lock()
        self._syncing = False
        self._stale = False
        self._pending: list[BluezSignal] = []
        self._resync_task: asyncio.Task[None] | None = None
        self._resync_again = False

    async def start(self) -> None:
        """Subscribe, then load the first snapshot. Errors propagate to the caller."""
        await self._connection.subscribe(self._on_event)
        await self._sync()

    async def close(self) -> None:
        if self._resync_task is not None:
            self._resync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._resync_task
        await self._connection.close()

    async def _sync(self) -> None:
        async with self._lock:
            self._syncing = True
            self._stale = False
            self._pending = []
            try:
                owner = await self._connection.name_owner()
                snapshot = None if owner is None else await self._connection.managed_objects()
                if snapshot is None or self._stale:
                    # No BlueZ, or it restarted mid-fetch; a resync follows if it came back.
                    self.tree.clear()
                else:
                    self.tree.load(snapshot)
                    for signal in self._pending:
                        self.tree.apply(signal)
            finally:
                self._syncing = False
                self._pending = []
        self.last_error = None
        self.changed.set()

    def _on_event(self, event: Event) -> None:
        if isinstance(event, OwnerChanged):
            self.tree.clear()
            self._stale = self._syncing
            self.changed.set()
            if event.new_owner is not None:
                self._schedule_resync()
            return
        if self._syncing:
            self._pending.append(event)
        elif self.tree.apply(event):
            self.changed.set()

    def _schedule_resync(self) -> None:
        if self._resync_task is not None and not self._resync_task.done():
            self._resync_again = True
            return
        self._resync_task = asyncio.get_running_loop().create_task(self._resync_loop())

    async def _resync_loop(self) -> None:
        while True:
            self._resync_again = False
            try:
                await self._sync()
            except ShokzError as error:
                self.tree.clear()
                self.last_error = error
                self.changed.set()
            if not self._resync_again:
                return


# --- Real system-bus connection -------------------------------------------------------------

_DBUS_ERRORS = {
    "org.freedesktop.DBus.Error.ServiceUnknown": ErrorCategory.UNAVAILABLE,
    "org.freedesktop.DBus.Error.NameHasNoOwner": ErrorCategory.UNAVAILABLE,
    "org.freedesktop.DBus.Error.AccessDenied": ErrorCategory.PERMISSION,
    "org.freedesktop.DBus.Error.AuthFailed": ErrorCategory.PERMISSION,
    "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired": ErrorCategory.PERMISSION,
    "org.freedesktop.DBus.Error.NoReply": ErrorCategory.TIMEOUT,
    "org.freedesktop.DBus.Error.Timeout": ErrorCategory.TIMEOUT,
    "org.freedesktop.DBus.Error.TimedOut": ErrorCategory.TIMEOUT,
}


def dbus_error(name: str | None, text: str | None) -> ShokzError:
    """Map a D-Bus error reply to a ShokzError."""
    detail = f"{name}: {text}" if text else str(name)
    match _DBUS_ERRORS.get(name or ""):
        case ErrorCategory.UNAVAILABLE:
            return bluez_not_running()
        case ErrorCategory.PERMISSION:
            return ShokzError(ErrorCategory.PERMISSION, f"Not allowed to read BlueZ ({detail}).")
        case ErrorCategory.TIMEOUT:
            return ShokzError(ErrorCategory.TIMEOUT, f"BlueZ didn't answer in time ({detail}).")
        case _:
            return ShokzError(ErrorCategory.UNAVAILABLE, f"BlueZ request failed ({detail}).")


def bluez_not_running() -> ShokzError:
    return ShokzError(
        ErrorCategory.UNAVAILABLE,
        "BlueZ isn't running.",
        hint="Check `systemctl status bluetooth`.",
    )


async def connect_system_bus() -> SystemBusConnection:
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except PermissionError as error:
        raise ShokzError(
            ErrorCategory.PERMISSION, f"Not allowed to open the system D-Bus ({error})."
        ) from error
    except (OSError, AuthError) as error:
        raise ShokzError(
            ErrorCategory.UNAVAILABLE,
            f"The system D-Bus isn't reachable ({error}).",
            hint="Run on the Fedora host, not inside a sandbox without system-bus access.",
        ) from error
    return SystemBusConnection(bus)


class SystemBusConnection:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._handler: Callable[[Event], None] | None = None

    async def subscribe(self, handler: Callable[[Event], None]) -> None:
        self._handler = handler
        self._bus.add_message_handler(self._on_message)
        for rule in MATCH_RULES:
            await self._call(DBUS, DBUS_PATH, DBUS, "AddMatch", "s", [rule])

    async def name_owner(self) -> str | None:
        reply = await self._bus.call(
            Message(
                destination=DBUS,
                path=DBUS_PATH,
                interface=DBUS,
                member="GetNameOwner",
                signature="s",
                body=[BLUEZ],
            )
        )
        if reply.message_type is MessageType.ERROR:
            if reply.error_name == "org.freedesktop.DBus.Error.NameHasNoOwner":
                return None
            raise dbus_error(reply.error_name, _error_text(reply))
        owner: str = reply.body[0]
        return owner

    async def managed_objects(self) -> Snapshot:
        reply = await self._call(BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects")
        return Snapshot(
            objects=unpack_variants(reply.body[0]),
            sender=reply.sender or "",
            serial=reply.serial,
        )

    async def close(self) -> None:
        self._bus.remove_message_handler(self._on_message)
        self._bus.disconnect()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._bus.wait_for_disconnect(), timeout=1)  # type: ignore[no-untyped-call]

    async def _call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
    ) -> Message:
        reply = await self._bus.call(
            Message(
                destination=destination,
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body or [],
            )
        )
        if reply.message_type is MessageType.ERROR:
            raise dbus_error(reply.error_name, _error_text(reply))
        return reply

    def _on_message(self, message: Message) -> None:
        if message.message_type is not MessageType.SIGNAL or self._handler is None:
            return
        event = parse_signal(
            sender=message.sender,
            serial=message.serial,
            path=message.path,
            interface=message.interface,
            member=message.member,
            body=message.body,
        )
        if event is not None:
            self._handler(event)


def _error_text(reply: Message) -> str | None:
    if reply.body and isinstance(reply.body[0], str):
        text: str = reply.body[0]
        return text
    return None
