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

from shokzer.protocol.commands import (
    BATTERY_GET,
    EQUALIZER_GET,
    EQUALIZER_SET,
    FIRMWARE_GET,
    PRO2_PRESET_ARGS,
    EqPreset,
    address_arg,
    custom_eq_request,
    firmware_request,
    pro2_preset_request,
)
from shokzer.protocol.packets import (
    MalformedFrameError,
    Message,
    StreamDecoder,
    crc16_maxim,
    decode_payload,
    encode,
)
from shokzer.protocol.responses import (
    Acknowledgement,
    Battery,
    Equalizer,
    Firmware,
    MultipointConnectionNotice,
    MultipointDevices,
    MultipointStatus,
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


# --- replies captured from the headset (see fixture provenance) -------------------------------

CAPTURED = VECTORS["hardware_replies"]


def captured(name: str) -> tuple[bytes, Message]:
    frames = StreamDecoder().feed(bytes.fromhex(CAPTURED[name]))
    assert len(frames) == 1
    assert frames[0].crc_valid
    return frames[0].flags, decode_payload(frames[0].payload)


@pytest.mark.parametrize("name", ["firmware_ret", "battery_ret", "equalizer_ret"])
def test_captured_replies_follow_the_framing(name: str) -> None:
    data = bytes.fromhex(CAPTURED[name])
    frame_len, payload_len = struct.unpack_from("<H4xH", data, 2)
    assert frame_len == payload_len + 4
    flags, _ = captured(name)
    assert flags == b"\x01\x01\x00\x00", "replies set header byte 5; requests send 0"


def test_captured_firmware_reply() -> None:
    _, message = captured("firmware_ret")
    assert (message.group, message.code) == FIRMWARE_GET.reply
    assert decode(message) == Firmware("BOS_HU_R_47_20251023")


def test_captured_battery_reply() -> None:
    _, message = captured("battery_ret")
    assert (message.group, message.code) == BATTERY_GET.reply
    assert message.args == b"\x00\x05\xff\x00"  # trailing ff 00: meaning unknown, ignored
    assert decode(message) == Battery(60, 5)


def test_captured_equalizer_reply() -> None:
    _, message = captured("equalizer_ret")
    assert (message.group, message.code) == EQUALIZER_GET.reply
    assert decode(message) == Equalizer(
        code=1, preset=EqPreset.STANDARD, bands=None, unprompted=False
    )


# --- custom EQ and multipoint codecs ---------------------------------------------------------


def test_address_arg_is_reversed() -> None:
    assert address_arg("11:22:33:44:55:66") == bytes.fromhex("665544332211")
    with pytest.raises(ValueError, match="not a Bluetooth address"):
        address_arg("11:22:33")


def test_custom_eq_request_layout() -> None:
    assert custom_eq_request([-5, 0, 5, -1, 1]).args == bytes.fromhex("05fb0005ff010000")


def test_multipoint_status_decoding() -> None:
    on = decode(Message(0x02, 0x8010, (0x00010100).to_bytes(4, "little")))
    assert on == MultipointStatus(True, 0x00010100)
    other = decode(Message(0x02, 0x8010, (0x00000100).to_bytes(4, "little")))
    assert other == MultipointStatus(None, 0x00000100)


def test_multipoint_devices_stops_at_the_data() -> None:
    entry = bytes([0]) + bytes.fromhex("665544332211") + bytes([1, 6]) + b"Phone\x00"
    devices = decode(Message(0x02, 0x8004, bytes([0, 8]) + entry + bytes([1, 2, 3])))
    assert isinstance(devices, MultipointDevices)
    assert [(p.address, p.name, p.connected) for p in devices.peers] == [
        ("11:22:33:44:55:66", "Phone", True)
    ]
    assert devices.reported_count == 8


def test_multipoint_name_stops_at_nul() -> None:
    entry = bytes([0]) + bytes.fromhex("665544332211") + bytes([0, 8]) + b"Ab\x00junk!"
    devices = decode(Message(0x02, 0x8004, bytes([0, 1]) + entry))
    assert isinstance(devices, MultipointDevices)
    assert devices.peers[0].name == "Ab"


def test_status_acknowledgement() -> None:
    ack = decode(Message(0x01, 0x8011, (0).to_bytes(4, "little")))
    assert ack == Acknowledgement(0x01, 0x8011, bytes(4), 0)


# --- fragmented frames (observed on the Pro 2 for the multipoint device list) ---------------

FRAGMENTED = VECTORS["fragmented_device_list"]
FRAG1, FRAG2 = (bytes.fromhex(h) for h in FRAGMENTED["fragments"])


def test_captured_fragments_reassemble_with_a_valid_crc() -> None:
    decoder = StreamDecoder()
    assert decoder.feed(FRAG1) == []
    frames = decoder.feed(FRAG2)
    assert len(frames) == 1
    assert frames[0].crc_valid
    assert frames[0].fragments == 2
    assert frames[0].flags == b"\x02\x01\x00\x00"
    devices = decode(decode_payload(frames[0].payload))
    assert isinstance(devices, MultipointDevices)
    assert [(p.address, p.name, p.connected) for p in devices.peers] == [
        (d["address"], d["name"], d["connected"]) for d in FRAGMENTED["devices"]
    ]
    assert devices.skipped == 0


def test_fragments_survive_any_split_of_the_stream() -> None:
    data = FRAG1 + FRAG2
    for cut in range(1, len(data)):
        decoder = StreamDecoder()
        frames = decoder.feed(data[:cut]) + decoder.feed(data[cut:])
        assert len(frames) == 1, cut
        assert frames[0].crc_valid
        assert decoder.pending == 0


def test_fragments_coalesced_with_a_normal_frame() -> None:
    normal = encode(Message(0x02, 0x8003, b"\x00\x05"))
    frames = StreamDecoder().feed(normal + FRAG1 + FRAG2 + normal)
    assert [f.fragments for f in frames] == [1, 2, 1]


def test_continuation_without_a_first_fragment_is_dropped() -> None:
    decoder = StreamDecoder()
    assert decoder.feed(FRAG2) == []
    assert decoder.dropped_fragments == 1


def test_missing_continuation_abandons_the_frame() -> None:
    decoder = StreamDecoder()
    normal = encode(Message(0x02, 0x8003, b"\x00\x05"))
    frames = decoder.feed(FRAG1 + normal)
    assert [decode_payload(f.payload).code for f in frames] == [0x8003]
    assert decoder.dropped_fragments == 1
    assert decoder.feed(FRAG2) == []  # too late: its first part is gone


def test_fragment_numbers_must_follow_in_order() -> None:
    out_of_order = bytearray(FRAG2)
    out_of_order[5] = 3
    decoder = StreamDecoder()
    assert decoder.feed(FRAG1 + bytes(out_of_order)) == []
    assert decoder.dropped_fragments >= 1


def test_first_fragment_must_be_shorter_than_the_total() -> None:
    bad = bytearray(FRAG1)
    struct.pack_into("<H", bad, 8, len(FRAG1) - 12)  # claims the first part is everything
    decoder = StreamDecoder()
    assert decoder.feed(bytes(bad)) == []
    assert decoder.skipped_bytes > 0


def test_connection_notice_from_the_headset() -> None:
    # Layout as observed in tests C2/C4; address and name here are made up.
    args = bytes.fromhex("665544332211") + bytes([0, 31]) + b"Phone".ljust(31, b"\x00")
    notice = decode(Message(0x03, 0x000B, args))
    assert notice == MultipointConnectionNotice("11:22:33:44:55:66", False, "Phone")


def test_multipoint_off_value_observed_on_hardware() -> None:
    off = decode(Message(0x02, 0x8010, bytes.fromhex("00000100")))
    assert off == MultipointStatus(False, 0x00010000)
