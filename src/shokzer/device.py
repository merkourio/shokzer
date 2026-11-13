"""Find the headset in BlueZ's object tree and describe it."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .capabilities import MODEL_NAME, capabilities, name_matches_model
from .errors import ErrorCategory, ShokzError
from .state import AdapterInfo, Connection, DeviceInfo, DiagnoseReport, Identity, Observed
from .transport.bluez import (
    ADAPTER,
    BATTERY,
    BEARER_BREDR,
    BEARER_LE,
    DEVICE,
    BluezTree,
    bluez_not_running,
)

BATTERY_SOURCE = "bluez:org.bluez.Battery1.Percentage"

_ADDRESS = re.compile(r"^[0-9A-Fa-f]{2}(?:[:_-][0-9A-Fa-f]{2}){5}$")
_DEVICE_PATH = re.compile(r"^/org/bluez/[A-Za-z0-9_]+/dev(?:_[0-9A-Fa-f]{2}){6}$")
_ADAPTER_NAME = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Selector:
    """Explicit choices from the command line. None means choose automatically."""

    device: str | None = None
    adapter: str | None = None


class AmbiguousSelectionError(ShokzError):
    def __init__(self, message: str, candidates: tuple[DeviceInfo, ...]) -> None:
        super().__init__(
            ErrorCategory.AMBIGUOUS_SELECTION,
            message,
            hint="Pick one with --device ADDRESS (add --show-identifiers to see full addresses).",
        )
        self.candidates = candidates


def parse_device(text: str) -> str:
    """Normalise --device to an upper-case colon address or a BlueZ device object path."""
    value = text.strip()
    if _ADDRESS.match(value):
        return re.sub(r"[_-]", ":", value).upper()
    if _DEVICE_PATH.match(value):
        return value
    raise ShokzError(
        ErrorCategory.INVALID_INPUT,
        f"--device must be a Bluetooth address like AA:BB:CC:DD:EE:FF, got {text!r}.",
    )


def parse_address(text: str) -> str:
    """A Bluetooth address only, normalised to upper-case with colons."""
    value = text.strip()
    if not _ADDRESS.match(value):
        raise ShokzError(
            ErrorCategory.INVALID_INPUT,
            f"Expected a Bluetooth address like AA:BB:CC:DD:EE:FF, got {text!r}.",
        )
    return re.sub(r"[_-]", ":", value).upper()


def parse_adapter(text: str) -> str:
    value = text.strip()
    if not _ADAPTER_NAME.match(value):
        raise ShokzError(
            ErrorCategory.INVALID_INPUT, f"--adapter must be a name like hci0, got {text!r}."
        )
    return value


def adapters(tree: BluezTree) -> list[AdapterInfo]:
    found = []
    for path, interfaces in tree.objects.items():
        props = interfaces.get(ADAPTER)
        if props is None:
            continue
        found.append(
            AdapterInfo(
                path=path,
                name=path.rsplit("/", 1)[-1],
                address=_str(props.get("Address")),
                powered=_bool(props.get("Powered")),
                discovering=_bool(props.get("Discovering")),
            )
        )
    return sorted(found, key=lambda a: a.path)


def devices(tree: BluezTree) -> list[DeviceInfo]:
    found = []
    for path, interfaces in tree.objects.items():
        props = interfaces.get(DEVICE)
        if props is None or _str(props.get("Address")) is None:
            continue
        found.append(_device_info(path, interfaces, props))
    return sorted(found, key=lambda d: d.path)


def select(tree: BluezTree, selector: Selector) -> tuple[AdapterInfo, DeviceInfo]:
    if tree.owner is None:
        raise bluez_not_running()
    all_adapters = adapters(tree)
    if not all_adapters:
        raise ShokzError(ErrorCategory.UNAVAILABLE, "No Bluetooth adapter found.")
    if selector.adapter is not None:
        all_adapters = [a for a in all_adapters if a.name == selector.adapter]
        if not all_adapters:
            raise ShokzError(ErrorCategory.UNAVAILABLE, f"Adapter {selector.adapter} not found.")
    by_path = {a.path: a for a in all_adapters}
    candidates = [d for d in devices(tree) if _adapter_path(d) in by_path]

    if selector.device is not None:
        wanted = selector.device
        matches = [d for d in candidates if wanted in (d.address, d.path)]
        if not matches:
            raise ShokzError(
                ErrorCategory.UNAVAILABLE,
                "BlueZ doesn't know a device with that address.",
                hint="Pair the headset in your Bluetooth settings first.",
            )
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                "That address is known on more than one adapter.", tuple(matches)
            )
    else:
        matches = [d for d in candidates if d.paired and name_matches_model(d.name or d.alias)]
        if not matches:
            raise ShokzError(
                ErrorCategory.UNAVAILABLE,
                f"No paired {MODEL_NAME} found.",
                hint="Pair the headset in your Bluetooth settings, or pass --device ADDRESS.",
            )
        if len(matches) > 1:
            raise AmbiguousSelectionError(
                f"More than one paired {MODEL_NAME} found.", tuple(matches)
            )
    device = matches[0]
    return by_path[_adapter_path(device)], device


def build_report(tree: BluezTree, selector: Selector) -> DiagnoseReport:
    adapter, device = select(tree, selector)
    name_ok = name_matches_model(device.name or device.alias)
    warnings = []
    if adapter.powered is False:
        warnings.append(f"Adapter {adapter.name} is powered off.")
    if not name_ok:
        warnings.append(
            f"The selected device's name doesn't match {MODEL_NAME}; the model is unverified."
        )
    if device.paired is False:
        warnings.append("The selected device isn't paired.")
    return DiagnoseReport(
        adapter=adapter,
        device=device,
        selection="explicit" if selector.device is not None else "automatic",
        identity=Identity(name_matches_model=name_ok),
        capabilities=capabilities(),
        warnings=tuple(warnings),
    )


def _device_info(path: str, interfaces: Mapping[str, Any], props: Mapping[str, Any]) -> DeviceInfo:
    connected = _bool(props.get("Connected"))
    battery = interfaces.get(BATTERY, {}).get("Percentage")
    bearers = tuple(
        name for iface, name in ((BEARER_BREDR, "bredr"), (BEARER_LE, "le")) if iface in interfaces
    )
    return DeviceInfo(
        path=path,
        adapter=path.rsplit("/", 2)[-2],
        address=str(props["Address"]).upper(),
        name=_str(props.get("Name")),
        alias=_str(props.get("Alias")),
        paired=_bool(props.get("Paired")),
        bonded=_bool(props.get("Bonded")),
        trusted=_bool(props.get("Trusted")),
        connection=(
            Connection.UNKNOWN
            if connected is None
            else Connection.CONNECTED
            if connected
            else Connection.DISCONNECTED
        ),
        services_resolved=_bool(props.get("ServicesResolved")),
        bearers=bearers,
        battery=(
            Observed(value=int(battery), source=BATTERY_SOURCE)
            if isinstance(battery, int) and not isinstance(battery, bool)
            else Observed()
        ),
        modalias=_str(props.get("Modalias")),
        uuids=tuple(sorted(str(u).lower() for u in props.get("UUIDs", ()))),
    )


def _adapter_path(device: DeviceInfo) -> str:
    return device.path.rsplit("/", 1)[0]


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
