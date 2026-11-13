"""Decode replies and unprompted messages. Unknown values are kept, never guessed."""

from __future__ import annotations

from dataclasses import dataclass

from .commands import EqPreset, name_of
from .packets import MalformedFrameError, Message

FIRMWARE_RET = (0x02, 0x8002)
BATTERY_RET = (0x02, 0x8003)
EQUALIZER_RET = (0x02, 0x8008)
EQUALIZER_NOTIFY = (0x03, 0x0009)  # Gadgetbridge calls it EQUALIZER_ACK
EQUALIZER_SET_ACK = (0x01, 0x800E)  # Gadgetbridge: EQ_UNK_ACK_1, args not decoded
MULTIPOINT_RET = (0x02, 0x8010)
MULTIPOINT_DEVICES_RET = (0x02, 0x8004)
MULTIPOINT_ON_ACK = (0x01, 0x8011)
MULTIPOINT_OFF_ACK = (0x01, 0x8010)
MULTIPOINT_CONNECT_ACK = (0x01, 0x800B)
MULTIPOINT_DISCONNECT_ACK = (0x01, 0x800C)
# Sent unprompted when a device connects or disconnects. Layout observed 2026-11-13 (test C2,
# C4): address (reversed), connected, name length, name, like one device-list entry without
# the index. Gadgetbridge only uses it as a cue to re-read the list (ShokzSupport.kt:729-732).
MULTIPOINT_CONNECTION_NOTIFY = (0x03, 0x000B)
_STATUS_ACKS = frozenset(
    {MULTIPOINT_ON_ACK, MULTIPOINT_OFF_ACK, MULTIPOINT_CONNECT_ACK, MULTIPOINT_DISCONNECT_ACK}
)

# Gadgetbridge reads MULTIPOINT_RET as one u32 and treats exactly this value as "on"
# (ShokzSupport.kt:634-639). It names no "off" value. 0x00010000 was read back right after
# MULTIPOINT_OFF was acknowledged and the phone dropped (2026-11-13, test C2). Anything else
# stays unknown.
MULTIPOINT_ON_VALUE = 0x00010100
MULTIPOINT_OFF_VALUES: frozenset[int] = frozenset({0x00010000})
TOO_MANY_CONNECTED = 0x00000400  # MULTIPOINT_CONNECT_ACK status (ShokzSupport.kt:719-727)


@dataclass(frozen=True)
class Firmware:
    version: str


@dataclass(frozen=True)
class Battery:
    percent: int
    raw: int


@dataclass(frozen=True)
class Equalizer:
    code: int
    preset: EqPreset | None  # None: a code Gadgetbridge doesn't know
    bands: tuple[int, ...] | None  # custom EQ gains, when present
    unprompted: bool


@dataclass(frozen=True)
class Acknowledgement:
    """A set-group reply. `status` is the leading u32 where Gadgetbridge reads one (0 = ok)."""

    group: int
    code: int
    args: bytes
    status: int | None = None


@dataclass(frozen=True)
class MultipointStatus:
    enabled: bool | None  # None: a value no source has named yet
    raw: int


@dataclass(frozen=True)
class MultipointPeer:
    index: int
    address: str
    connected: bool
    name: str


@dataclass(frozen=True)
class MultipointConnectionNotice:
    address: str
    connected: bool
    name: str


@dataclass(frozen=True)
class MultipointDevices:
    reported_count: int
    peers: tuple[MultipointPeer, ...]
    skipped: int  # entries dropped as implausible or truncated


@dataclass(frozen=True)
class Unhandled:
    message: Message
    name: str | None


type Decoded = (
    Firmware
    | Battery
    | Equalizer
    | Acknowledgement
    | MultipointStatus
    | MultipointDevices
    | MultipointConnectionNotice
    | Unhandled
)


def decode(message: Message) -> Decoded:
    key = (message.group, message.code)
    args = message.args
    if key == FIRMWARE_RET:
        return _firmware(args)
    if key == BATTERY_RET:
        return _battery(args)
    if key == EQUALIZER_RET:
        _expect_status_zero(args, "EQUALIZER_RET")
        return _equalizer(args[1:], unprompted=False)
    if key == EQUALIZER_NOTIFY:
        return _equalizer(args, unprompted=True)
    if key == EQUALIZER_SET_ACK:
        return Acknowledgement(message.group, message.code, args)
    if key in _STATUS_ACKS:
        status = int.from_bytes(args[:4], "little") if len(args) >= 4 else None
        return Acknowledgement(message.group, message.code, args, status)
    if key == MULTIPOINT_RET:
        return _multipoint_status(args)
    if key == MULTIPOINT_DEVICES_RET:
        return _multipoint_devices(args)
    if key == MULTIPOINT_CONNECTION_NOTIFY:
        return _connection_notice(args)
    return Unhandled(message, name_of(message.group, message.code))


def _multipoint_status(args: bytes) -> MultipointStatus:
    if len(args) < 4:
        raise MalformedFrameError("MULTIPOINT_RET is shorter than 4 bytes")
    raw = int.from_bytes(args[:4], "little")
    if raw == MULTIPOINT_ON_VALUE:
        return MultipointStatus(True, raw)
    return MultipointStatus(False if raw in MULTIPOINT_OFF_VALUES else None, raw)


def _connection_notice(args: bytes) -> MultipointConnectionNotice:
    if len(args) < 8 or len(args) < 8 + args[7]:
        raise MalformedFrameError("MULTIPOINT_DEVICE_CONNECTION_NOTIFY is too short")
    address = ":".join(f"{b:02X}" for b in args[:6][::-1])
    name = args[8 : 8 + args[7]].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return MultipointConnectionNotice(address, args[6] == 1, name)


def _multipoint_devices(args: bytes) -> MultipointDevices:
    """Layout from ShokzSupport.kt:667-717: status, count, then per entry index, address
    (reversed), connected, name length, name. The Pro 2 over-reports the count and can send a
    garbled tail, so parsing stops at the data and drops entries that don't look real."""
    if len(args) < 2:
        raise MalformedFrameError("MULTIPOINT_DEVICES_RET is shorter than 2 bytes")
    count = args[1]
    pos, peers, parsed, skipped = 2, [], 0, 0
    while parsed < count and len(args) - pos >= 9:
        index = args[pos]
        address_bytes = args[pos + 1 : pos + 7][::-1]
        connected = args[pos + 7] == 1
        name_len = args[pos + 8]
        pos += 9
        if len(args) - pos < name_len:
            skipped += 1
            break
        name = args[pos : pos + name_len].split(b"\x00", 1)[0].decode("utf-8", "replace")
        pos += name_len
        parsed += 1
        if not any(address_bytes) or not name:
            skipped += 1
            continue
        address = ":".join(f"{b:02X}" for b in address_bytes)
        peers.append(MultipointPeer(index, address, connected, name))
    return MultipointDevices(count, tuple(peers), skipped)


def _firmware(args: bytes) -> Firmware:
    _expect_status_zero(args, "FIRMWARE_RET")
    end = args.find(b"\x00", 1)
    if end < 0:
        raise MalformedFrameError("FIRMWARE_RET has no NUL terminator")
    try:
        version = args[1:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedFrameError("FIRMWARE_RET isn't UTF-8") from error
    if not version:
        raise MalformedFrameError("FIRMWARE_RET is empty")
    return Firmware(version)


def _battery(args: bytes) -> Battery:
    _expect_status_zero(args, "BATTERY_RET")
    if len(args) < 2:
        raise MalformedFrameError("BATTERY_RET has no level byte")
    raw = args[1]
    percent = (raw + 1) * 10
    if percent > 100:
        raise MalformedFrameError(f"BATTERY_RET level byte {raw:#04x} is above 100 %")
    return Battery(percent=percent, raw=raw)


def _equalizer(args: bytes, *, unprompted: bool) -> Equalizer:
    if not args:
        raise MalformedFrameError("equalizer message has no preset byte")
    code = args[0]
    try:
        preset: EqPreset | None = EqPreset(code)
    except ValueError:
        preset = None
    bands = None
    if preset is EqPreset.CUSTOM and len(args) >= 6:
        bands = tuple(b - 256 if b > 127 else b for b in args[1:6])
    return Equalizer(code=code, preset=preset, bands=bands, unprompted=unprompted)


def _expect_status_zero(args: bytes, name: str) -> None:
    if not args:
        raise MalformedFrameError(f"{name} has no arguments")
    if args[0] != 0:
        raise MalformedFrameError(f"{name} status byte is {args[0]:#04x}, expected 0x00")
