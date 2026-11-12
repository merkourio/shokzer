"""A scripted BlueZ for offline tests. Addresses are made up and never real observations."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any

from shokzctl.errors import ShokzError
from shokzctl.transport.bluez import (
    ADAPTER,
    BATTERY,
    BEARER_BREDR,
    DEVICE,
    Event,
    Interfaces,
    InterfacesAdded,
    InterfacesRemoved,
    Objects,
    OwnerChanged,
    PropertiesChanged,
    Snapshot,
)

HCI0 = "/org/bluez/hci0"
HCI1 = "/org/bluez/hci1"
HEADSET = "AA:BB:CC:00:00:01"
HEADSET_2 = "AA:BB:CC:00:00:02"
MOUSE = "AA:BB:CC:00:00:03"
OWNER = ":1.7"


def device_path(address: str, adapter: str = HCI0) -> str:
    return f"{adapter}/dev_{address.replace(':', '_')}"


def adapter(*, address: str = "AA:BB:CC:FF:FF:01", powered: bool = True) -> Interfaces:
    return {ADAPTER: {"Address": address, "Powered": powered, "Discovering": False}}


def device(
    address: str = HEADSET,
    *,
    adapter_path: str = HCI0,
    name: str | None = "OpenRun Pro 2 by Shokz",
    connected: bool = True,
    paired: bool = True,
    battery: int | None = None,
    uuids: tuple[str, ...] = (
        "0000110B-0000-1000-8000-00805F9B34FB",
        "0000FEF0-0000-1000-8000-00805F9B34FB",
    ),
) -> Interfaces:
    props: dict[str, Any] = {
        "Address": address,
        "Adapter": adapter_path,
        "Alias": name or address,
        "Paired": paired,
        "Bonded": paired,
        "Trusted": paired,
        "Connected": connected,
        "ServicesResolved": connected,
        "UUIDs": list(uuids),
    }
    if name is not None:
        props["Name"] = name
    interfaces: Interfaces = {DEVICE: props, BEARER_BREDR: {}}
    if battery is not None:
        interfaces[BATTERY] = {"Percentage": battery}
    return interfaces


def tree(*entries: tuple[str, Interfaces]) -> Objects:
    return {path: copy.deepcopy(interfaces) for path, interfaces in entries}


def headset_tree(**kwargs: Any) -> Objects:
    return tree((HCI0, adapter()), (device_path(HEADSET), device(**kwargs)))


class FakeBluez:
    """Implements BluezConnection. Serials rise across replies and signals like BlueZ's."""

    def __init__(
        self,
        objects: Objects | None = None,
        *,
        owner: str | None = OWNER,
        error: ShokzError | None = None,
        during_snapshot: Callable[[FakeBluez], Awaitable[None]] | None = None,
    ) -> None:
        self.objects: Objects = copy.deepcopy(objects or {})
        self.owner = owner
        self.error = error
        self.during_snapshot = during_snapshot
        self.handler: Callable[[Event], None] | None = None
        self.closed = False
        self.snapshots = 0
        self._serial = 100

    async def __call__(self) -> FakeBluez:
        """Lets the instance itself act as the connection factory."""
        return self

    async def subscribe(self, handler: Callable[[Event], None]) -> None:
        self.handler = handler

    async def name_owner(self) -> str | None:
        if self.error is not None:
            raise self.error
        return self.owner

    async def managed_objects(self) -> Snapshot:
        if self.error is not None:
            raise self.error
        self.snapshots += 1
        snapshot = Snapshot(copy.deepcopy(self.objects), self.owner or "", self._next())
        if self.during_snapshot is not None:
            hook, self.during_snapshot = self.during_snapshot, None
            await hook(self)
        return snapshot

    async def close(self) -> None:
        self.closed = True

    # --- signals; each one also updates the fake's own objects -----------------------

    def add(self, path: str, interfaces: Interfaces) -> None:
        self.objects.setdefault(path, {}).update(copy.deepcopy(interfaces))
        self._emit(InterfacesAdded(self._sender(), self._next(), path, copy.deepcopy(interfaces)))

    def remove(self, path: str, names: tuple[str, ...]) -> None:
        obj = self.objects.get(path, {})
        for name in names:
            obj.pop(name, None)
        if not obj:
            self.objects.pop(path, None)
        self._emit(InterfacesRemoved(self._sender(), self._next(), path, names))

    def change(
        self, path: str, interface: str, changed: dict[str, Any], invalidated: tuple[str, ...] = ()
    ) -> None:
        props = self.objects[path][interface]
        props.update(changed)
        for name in invalidated:
            props.pop(name, None)
        self._emit(
            PropertiesChanged(self._sender(), self._next(), path, interface, changed, invalidated)
        )

    def stop(self) -> None:
        """BlueZ exits: the name loses its owner."""
        self.owner = None
        self._emit(OwnerChanged(None))

    def restart(self, objects: Objects, owner: str = ":1.99") -> None:
        """A new BlueZ instance takes the name with fresh objects and serials."""
        self.objects = copy.deepcopy(objects)
        self.owner = owner
        self._serial = 1
        self._emit(OwnerChanged(owner))

    def _emit(self, event: Event) -> None:
        assert self.handler is not None, "observer hasn't subscribed"
        self.handler(event)

    def _sender(self) -> str:
        assert self.owner is not None
        return self.owner

    def _next(self) -> int:
        self._serial += 1
        return self._serial


async def settle() -> None:
    """Let scheduled observer tasks (resyncs) run."""
    for _ in range(5):
        await asyncio.sleep(0)
