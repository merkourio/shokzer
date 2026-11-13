"""State contract shared by the CLI and the future GUI.

Each field is independent: a connected headset can still have an unresolved management
transport and an unverified protocol. Unknown values stay unknown and carry no source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Connection(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class ManagementTransport(StrEnum):
    UNRESOLVED = "unresolved"
    RFCOMM = "rfcomm"


class SessionState(StrEnum):
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    FAILED = "failed"


class ProtocolReadiness(StrEnum):
    UNVERIFIED = "unverified"
    INITIALIZING = "initializing"
    READY = "ready"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class Support(StrEnum):
    SUPPORTED = "supported"  # tested on this firmware
    UNTESTED = "untested"  # available, but not yet tested on this firmware
    UNSUPPORTED = "unsupported"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Observed[T]:
    """A value with where it came from. Both are None when the value is unknown."""

    value: T | None = None
    source: str | None = None

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class AdapterInfo:
    path: str
    name: str
    address: str | None
    powered: bool | None
    discovering: bool | None


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    adapter: str
    address: str
    name: str | None
    alias: str | None
    paired: bool | None
    bonded: bool | None
    trusted: bool | None
    connection: Connection
    services_resolved: bool | None
    bearers: tuple[str, ...]
    battery: Observed[int]
    modalias: str | None
    uuids: tuple[str, ...]


@dataclass(frozen=True)
class Identity:
    """What is known about the model. A name match makes a candidate, never a verified model."""

    name_matches_model: bool
    model_verified: bool = False


@dataclass(frozen=True)
class DiagnoseReport:
    adapter: AdapterInfo
    device: DeviceInfo
    selection: Literal["automatic", "explicit"]
    identity: Identity
    firmware: Observed[str] = field(default_factory=Observed)
    headset_battery: Observed[int] = field(default_factory=Observed)
    active_eq: Observed[str] = field(default_factory=Observed)
    management_transport: ManagementTransport = ManagementTransport.UNRESOLVED
    management_session: SessionState = SessionState.CLOSED
    protocol: ProtocolReadiness = ProtocolReadiness.UNVERIFIED
    capabilities: Mapping[str, Support] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
