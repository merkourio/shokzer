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
    group: int
    code: int
    args: bytes


@dataclass(frozen=True)
class Unhandled:
    message: Message
    name: str | None


type Decoded = Firmware | Battery | Equalizer | Acknowledgement | Unhandled


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
    return Unhandled(message, name_of(message.group, message.code))


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
