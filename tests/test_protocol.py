"""Codec tests. Byte vectors come from tests/fixtures/protocol_vectors.json (with provenance).

Replies are built with our encoder following the layout Gadgetbridge's decoder reads; no
captured reply bytes exist yet, so these prove internal consistency, not device behaviour.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from shokzctl.protocol.commands import (
    BATTERY_GET,
    EQUALIZER_GET,
    EQUALIZER_SET,
    FIRMWARE_GET,
    PRO2_PRESET_ARGS,
    EqPreset,
    firmware_request,
    pro2_preset_request,
)
from shokzctl.protocol.packets import (
    MalformedFrameError,
    Message,
    StreamDecoder,
    crc16_maxim,
    decode_payload,
    encode,
)
from shokzctl.protocol.responses import (
    Acknowledgement,
    Battery,
    Equalizer,
    Firmware,
    Unhandled,
    decode,
)

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "protocol_vectors.json").read_text()
)
FIRMWARE_GET_HEX = VECTORS["vectors"][0]["hex"]


def frame(group: int, code: int, args: bytes = b"") -> bytes:
    return encode(Message(group, code, args))


# --- framing ---------------------------------------------------------------------------------


def test_crc16_maxim_check_value() -> None:
    assert crc16_maxim(b"123456789") == 0x44C2


def test_firmware_request_matches_upstream_vector() -> None:
    assert encode(firmware_request()).hex() == FIRMWARE_GET_HEX


def test_upstream_vector_decodes_to_firmware_get() -> None:
    frames = StreamDecoder().feed(bytes.fromhex(FIRMWARE_GET_HEX))
    assert len(frames) == 1
    assert frames[0].crc_valid
    assert frames[0].flags == b"\x01\x00\x00\x00"
    assert decode_payload(frames[0].payload) == Message(0x02, 0x0002)


@pytest.mark.parametrize("preset", list(PRO2_PRESET_ARGS))
def test_pro2_preset_args_match_source(preset: EqPreset) -> None:
    expected = bytes.fromhex(VECTORS["pro2_equalizer_set_args"][preset.name])
    message = pro2_preset_request(preset)
    assert message == Message(0x01, 0x000E, expected)
    data = encode(message)
    assert len(data) == 52 + 8
    assert struct.unpack_from("<HH", data, 8) == (48, crc16_maxim(data[12:]))


@pytest.mark.parametrize("preset", [EqPreset.CUSTOM, EqPreset.CLASSIC, EqPreset.VOLUME_BOOST])
def test_presets_without_pro2_bytes_are_refused(preset: EqPreset) -> None:
    with pytest.raises(ValueError, match="no OpenRun Pro 2 evidence"):
        pro2_preset_request(preset)


def test_reply_identity_includes_group() -> None:
    assert FIRMWARE_GET.reply == (0x02, 0x8002)
    assert BATTERY_GET.reply == (0x02, 0x8003)
    assert EQUALIZER_GET.reply == (0x02, 0x8008)
    assert EQUALIZER_SET.reply == (0x01, 0x800E)
    # LANGUAGE_ACK shares code 0x8002 in group 0x01; it must not count as the firmware reply.
    assert FIRMWARE_GET.reply != (0x01, 0x8002)


# --- stream decoding -------------------------------------------------------------------------


def test_every_split_point_yields_the_same_frame() -> None:
    data = frame(0x02, 0x8003, b"\x00\x05")
    for cut in range(1, len(data)):
        decoder = StreamDecoder()
        assert decoder.feed(data[:cut]) == []
        frames = decoder.feed(data[cut:])
        assert [decode_payload(f.payload) for f in frames] == [Message(0x02, 0x8003, b"\x00\x05")]
        assert decoder.pending == 0


def test_coalesced_frames_and_partial_tail() -> None:
    a, b, c = frame(0x02, 0x8003, b"\x00\x05"), frame(0x03, 0x0009, b"\x02"), frame(0x02, 0x8002)
    decoder = StreamDecoder()
    frames = decoder.feed(a + b + c[:7])
    assert [decode_payload(f.payload).code for f in frames] == [0x8003, 0x0009]
    frames = decoder.feed(c[7:])
    assert [decode_payload(f.payload).code for f in frames] == [0x8002]


def test_junk_before_preamble_is_skipped() -> None:
    decoder = StreamDecoder()
    frames = decoder.feed(b"\x00\xff\x12" + frame(0x02, 0x8003, b"\x00\x05"))
    assert len(frames) == 1
    assert decoder.skipped_bytes == 3


def test_preamble_split_across_reads() -> None:
    data = frame(0x02, 0x8003, b"\x00\x05")
    decoder = StreamDecoder()
    assert decoder.feed(b"\x99" + data[:1]) == []
    assert len(decoder.feed(data[1:])) == 1
    assert decoder.skipped_bytes == 1


def test_bad_crc_is_reported_not_hidden() -> None:
    data = bytearray(frame(0x02, 0x8003, b"\x00\x05"))
    data[-1] ^= 0xFF
    frames = StreamDecoder().feed(bytes(data))
    assert len(frames) == 1
    assert not frames[0].crc_valid


def test_inconsistent_lengths_resync_to_next_frame() -> None:
    bad = bytearray(frame(0x02, 0x8003, b"\x00\x05"))
    bad[2] ^= 0x01  # frame length no longer equals payload length + 4
    good = frame(0x02, 0x8002, b"\x00X\x00")
    frames = StreamDecoder().feed(bytes(bad) + good)
    assert [decode_payload(f.payload) for f in frames] == [Message(0x02, 0x8002, b"\x00X\x00")]


def test_oversized_length_is_rejected() -> None:
    header = b"\xa5\x5a" + struct.pack("<H", 4000 + 4) + b"\x01\x00\x00\x00"
    header += struct.pack("<HH", 4000, 0)
    decoder = StreamDecoder()
    assert decoder.feed(header) == []
    assert decoder.pending < len(header)


def test_truncated_frame_waits() -> None:
    data = frame(0x02, 0x8003, b"\x00\x05")
    decoder = StreamDecoder()
    assert decoder.feed(data[:-1]) == []
    assert decoder.pending == len(data) - 1


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda p: p[:20], "need 40"),
        (lambda p: struct.pack("<I", 9) + p[4:], "tags"),
        (lambda p: p[:4] + struct.pack("<I", 99) + p[8:], "follow"),
        (lambda p: p[:36] + struct.pack("<I", 7) + p[40:], "args length"),
    ],
)
def test_malformed_payloads(mutate: Any, error: str) -> None:
    payload = frame(0x02, 0x8003, b"\x00\x05")[12:]
    with pytest.raises(MalformedFrameError, match=error):
        decode_payload(mutate(payload))


# --- replies ---------------------------------------------------------------------------------


def test_firmware_reply() -> None:
    assert decode(Message(0x02, 0x8002, b"\x00TEST_FW_1\x00")) == Firmware("TEST_FW_1")


@pytest.mark.parametrize(
    "args", [b"", b"\x01TEST\x00", b"\x00TEST", b"\x00\x00", b"\x00\xff\xfe\x00"]
)
def test_malformed_firmware_reply(args: bytes) -> None:
    with pytest.raises(MalformedFrameError):
        decode(Message(0x02, 0x8002, args))


@pytest.mark.parametrize(("raw", "percent"), [(0, 10), (5, 60), (9, 100)])
def test_battery_reply(raw: int, percent: int) -> None:
    assert decode(Message(0x02, 0x8003, bytes([0, raw]))) == Battery(percent, raw)


@pytest.mark.parametrize("args", [b"\x00", b"\x01\x05", b"\x00\x0a"])
def test_malformed_battery_reply(args: bytes) -> None:
    with pytest.raises(MalformedFrameError):
        decode(Message(0x02, 0x8003, args))


def test_equalizer_reply() -> None:
    assert decode(Message(0x02, 0x8008, b"\x00\x02")) == Equalizer(
        code=2, preset=EqPreset.VOCAL, bands=None, unprompted=False
    )


def test_equalizer_reply_keeps_unknown_code() -> None:
    result = decode(Message(0x02, 0x8008, b"\x00\x06"))
    assert result == Equalizer(code=6, preset=None, bands=None, unprompted=False)


def test_custom_equalizer_bands_are_signed() -> None:
    result = decode(Message(0x02, 0x8008, bytes([0, 5, 0xFB, 0xFF, 0, 1, 5])))
    assert isinstance(result, Equalizer)
    assert result.bands == (-5, -1, 0, 1, 5)


def test_equalizer_notification_has_no_status_byte() -> None:
    assert decode(Message(0x03, 0x0009, b"\x04")) == Equalizer(
        code=4, preset=EqPreset.TREBLE, bands=None, unprompted=True
    )


def test_equalizer_set_acknowledgement_is_kept_raw() -> None:
    assert decode(Message(0x01, 0x800E, b"\x00")) == Acknowledgement(0x01, 0x800E, b"\x00")


def test_unhandled_messages_are_named_when_known() -> None:
    assert decode(Message(0x03, 0x0005, b"\x08")) == Unhandled(
        Message(0x03, 0x0005, b"\x08"), "VOLUME_NOTIFY"
    )
    assert decode(Message(0x07, 0x1234)) == Unhandled(Message(0x07, 0x1234), None)
