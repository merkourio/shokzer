"""Command identities and request encoders.

Group 0x01 sets, 0x02 gets, 0x03 is sent by the headset unprompted. A reply carries the
request's group and its code with bit 15 set. Source: Gadgetbridge ShokzCommand.kt and
ShokzSupport.handlePayload (MASK_RESPONSE). Only the requests below have encoders; the
rest of the table is for naming what the headset sends.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .packets import Message

GROUP_SET = 0x01
GROUP_GET = 0x02
GROUP_NOTIFY = 0x03
REPLY_BIT = 0x8000


@dataclass(frozen=True)
class Command:
    name: str
    group: int
    code: int

    def message(self, args: bytes = b"") -> Message:
        return Message(self.group, self.code, args)

    @property
    def reply(self) -> tuple[int, int]:
        """(group, code) of the reply. Gadgetbridge matches on code only; we also match group."""
        return self.group, self.code | REPLY_BIT


FIRMWARE_GET = Command("FIRMWARE_GET", GROUP_GET, 0x0002)
BATTERY_GET = Command("BATTERY_GET", GROUP_GET, 0x0003)
EQUALIZER_GET = Command("EQUALIZER_GET", GROUP_GET, 0x0008)
EQUALIZER_SET = Command("EQUALIZER_SET", GROUP_SET, 0x000E)

# Every identity in Gadgetbridge's ShokzCommand.kt, for naming received messages.
KNOWN_NAMES: dict[tuple[int, int], str] = {
    (0x02, 0x0003): "BATTERY_GET",
    (0x02, 0x8003): "BATTERY_RET",
    (0x01, 0x8024): "CONTROLS_ACK",
    (0x02, 0x001B): "CONTROLS_GET",
    (0x02, 0x801B): "CONTROLS_RET",
    (0x01, 0x0024): "CONTROLS_SET",
    (0x01, 0x8008): "BUTTON_NEXT_ACK",
    (0x01, 0x0008): "BUTTON_NEXT",
    (0x01, 0x8004): "BUTTON_PAUSE_ACK",
    (0x01, 0x0004): "BUTTON_PAUSE",
    (0x01, 0x8003): "BUTTON_PLAY_ACK",
    (0x01, 0x0003): "BUTTON_PLAY",
    (0x01, 0x8007): "BUTTON_PREV_ACK",
    (0x01, 0x0007): "BUTTON_PREV",
    (0x01, 0x800E): "EQ_UNK_ACK_1",
    (0x03, 0x0009): "EQUALIZER_ACK",
    (0x02, 0x0008): "EQUALIZER_GET",
    (0x02, 0x8008): "EQUALIZER_RET",
    (0x01, 0x000E): "EQUALIZER_SET",
    (0x02, 0x0002): "FIRMWARE_GET",
    (0x02, 0x8002): "FIRMWARE_RET",
    (0x01, 0x8002): "LANGUAGE_ACK",
    (0x02, 0x0006): "LANGUAGE_GET",
    (0x02, 0x8006): "LANGUAGE_RET",
    (0x01, 0x0002): "LANGUAGE_SET",
    (0x01, 0x8025): "MEDIA_SOURCE_ACK",
    (0x03, 0x000C): "MEDIA_SOURCE_NOTIFY",
    (0x01, 0x0025): "MEDIA_SOURCE_SET",
    (0x03, 0x000D): "MODE_BLUETOOTH_UNK_0",
    (0x02, 0x001C): "MEDIA_SOURCE_GET",
    (0x02, 0x801C): "MEDIA_SOURCE_RET",
    (0x02, 0x801E): "MODE_MP3_ACK",
    (0x02, 0x001E): "MODE_MP3_SET",
    (0x01, 0x8026): "MP3_PLAYBACK_MODE_ACK",
    (0x02, 0x001D): "MP3_PLAYBACK_MODE_GET",
    (0x02, 0x801D): "MP3_PLAYBACK_MODE_RET",
    (0x01, 0x0026): "MP3_PLAYBACK_MODE_SET",
    (0x01, 0x800B): "MULTIPOINT_CONNECT_ACK",
    (0x01, 0x000B): "MULTIPOINT_CONNECT_REQ",
    (0x03, 0x000B): "MULTIPOINT_DEVICE_CONNECTION_NOTIFY",
    (0x02, 0x0004): "MULTIPOINT_DEVICES_GET",
    (0x02, 0x8004): "MULTIPOINT_DEVICES_RET",
    (0x01, 0x800C): "MULTIPOINT_DISCONNECT_ACK",
    (0x01, 0x000C): "MULTIPOINT_DISCONNECT_REQ",
    (0x01, 0x8010): "MULTIPOINT_OFF_ACK",
    (0x01, 0x0010): "MULTIPOINT_OFF",
    (0x01, 0x8011): "MULTIPOINT_ON_ACK",
    (0x01, 0x0011): "MULTIPOINT_ON",
    (0x01, 0x0023): "MULTIPOINT_PAIR_SECOND_FINISH",
    (0x01, 0x8022): "MULTIPOINT_START_PAIR_ACK",
    (0x01, 0x001D): "MULTIPOINT_START_PAIR_REQ",
    (0x02, 0x0005): "PLAYBACK_STATUS_GET",
    (0x03, 0x0006): "PLAYBACK_STATUS_NOTIFY",
    (0x02, 0x8005): "PLAYBACK_STATUS_RET",
    (0x02, 0x0010): "MULTIPOINT_GET",
    (0x02, 0x8010): "MULTIPOINT_RET",
    (0x01, 0x800A): "VOLUME_ACK",
    (0x02, 0x0007): "VOLUME_GET",
    (0x03, 0x0005): "VOLUME_NOTIFY",
    (0x02, 0x8007): "VOLUME_RET",
    (0x01, 0x000A): "VOLUME_SET",
}


class EqPreset(Enum):
    """Preset codes from ShokzArguments.kt ShokzEqualizer."""

    STANDARD = 0x01
    VOCAL = 0x02
    BASS = 0x03
    TREBLE = 0x04
    CUSTOM = 0x05
    SWIMMING = 0x07
    CLASSIC = 0x08
    VOLUME_BOOST = 0x0A


# EQUALIZER_SET arguments for the OpenRun Pro 2, captured by Gadgetbridge from the official
# app (ShokzOpenRunPro2Coordinator.equalizerArgs). Other models use different bytes.
PRO2_PRESET_ARGS: dict[EqPreset, bytes] = {
    EqPreset.STANDARD: bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
    EqPreset.VOCAL: bytes([0x02, 0xFA, 0xFE, 0x04, 0x05, 0x01, 0x00, 0x00]),
    EqPreset.BASS: bytes([0x03, 0x09, 0xF9, 0x00, 0x00, 0x00, 0x00, 0x00]),
    EqPreset.TREBLE: bytes([0x04, 0x00, 0x00, 0x01, 0x04, 0x05, 0x00, 0x00]),
}


def name_of(group: int, code: int) -> str | None:
    return KNOWN_NAMES.get((group, code))


def firmware_request() -> Message:
    return FIRMWARE_GET.message()


def battery_request() -> Message:
    return BATTERY_GET.message()


def equalizer_request() -> Message:
    return EQUALIZER_GET.message()


def pro2_preset_request(preset: EqPreset) -> Message:
    """EQUALIZER_SET for a fixed preset. Only presets with captured Pro 2 bytes are allowed."""
    args = PRO2_PRESET_ARGS.get(preset)
    if args is None:
        raise ValueError(f"no OpenRun Pro 2 evidence for preset {preset.name}")
    return EQUALIZER_SET.message(args)
