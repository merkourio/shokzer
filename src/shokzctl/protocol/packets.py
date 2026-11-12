"""Frame encoding and stream decoding.

Layout (all little-endian), from Gadgetbridge ShokzSupport.encodeCommand and onSocketRead:

    offset  size  field
    0       2     preamble A5 5A
    2       2     frame length after the first 8 bytes (= payload length + 4)
    4       4     header flags, sent as 01 00 00 00; meaning of bytes 5-7 unknown
    8       2     payload length
    10      2     CRC-16/MAXIM of the payload
    12      n     payload:
                    u32 1, u32 remaining length,
                    u32 2, u32 4, u32 group,
                    u32 3, u32 4, u32 code,
                    u32 4, u32 args length, args
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PREAMBLE = b"\xa5\x5a"
HEADER_SIZE = 12
PAYLOAD_FIXED_SIZE = 40
REQUEST_FLAGS = b"\x01\x00\x00\x00"
MAX_FRAME_SIZE = 2048  # Gadgetbridge's receive buffer (MAX_MTU)


class MalformedFrameError(ValueError):
    """Bytes that don't follow the evidenced layout."""


@dataclass(frozen=True)
class Message:
    group: int
    code: int
    args: bytes = b""


@dataclass(frozen=True)
class Frame:
    flags: bytes
    payload: bytes
    crc_valid: bool


def crc16_maxim(data: bytes) -> int:
    """CRC-16/MAXIM: reflected poly 0x8005, init 0, final XOR 0xFFFF."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def encode_payload(message: Message) -> bytes:
    _check_u32(message.group, "group")
    _check_u32(message.code, "code")
    body = struct.pack("<8I", 2, 4, message.group, 3, 4, message.code, 4, len(message.args))
    body += message.args
    return struct.pack("<2I", 1, len(body)) + body


def encode(message: Message) -> bytes:
    payload = encode_payload(message)
    if HEADER_SIZE + len(payload) > MAX_FRAME_SIZE:
        raise MalformedFrameError("message too large")
    header = PREAMBLE + struct.pack("<H", len(payload) + 4) + REQUEST_FLAGS
    header += struct.pack("<HH", len(payload), crc16_maxim(payload))
    return header + payload


def decode_payload(payload: bytes) -> Message:
    if len(payload) < PAYLOAD_FIXED_SIZE:
        raise MalformedFrameError(f"payload is {len(payload)} bytes, need {PAYLOAD_FIXED_SIZE}")
    tag1, remaining, tag2, len2, group, tag3, len3, code, tag4, args_len = struct.unpack_from(
        "<10I", payload
    )
    if (tag1, tag2, len2, tag3, len3, tag4) != (1, 2, 4, 3, 4, 4):
        raise MalformedFrameError("unexpected payload tags")
    if remaining != len(payload) - 8:
        raise MalformedFrameError(f"payload says {remaining} bytes follow, {len(payload) - 8} do")
    if args_len != len(payload) - PAYLOAD_FIXED_SIZE:
        raise MalformedFrameError(
            f"args length says {args_len}, {len(payload) - PAYLOAD_FIXED_SIZE} present"
        )
    return Message(group=group, code=code, args=payload[PAYLOAD_FIXED_SIZE:])


class StreamDecoder:
    """Turns RFCOMM reads into frames. Handles split and coalesced frames and skips junk.

    A frame whose length fields disagree is treated as junk: the decoder skips its preamble
    and searches again. CRC failures are reported on the frame, not dropped here, because
    Gadgetbridge documents one reply type with a known bad CRC.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.skipped_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer += data
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(PREAMBLE)
            if start < 0:
                # Keep a trailing A5 in case the next read starts with 5A.
                keep = 1 if self._buffer.endswith(PREAMBLE[:1]) else 0
                self._skip(len(self._buffer) - keep)
                return frames
            self._skip(start)
            if len(self._buffer) < HEADER_SIZE:
                return frames
            frame_len, payload_len, crc = struct.unpack_from("<H4xHH", self._buffer, 2)
            if frame_len != payload_len + 4 or HEADER_SIZE + payload_len > MAX_FRAME_SIZE:
                self._skip(len(PREAMBLE))
                continue
            end = HEADER_SIZE + payload_len
            if len(self._buffer) < end:
                return frames
            payload = bytes(self._buffer[HEADER_SIZE:end])
            flags = bytes(self._buffer[4:8])
            del self._buffer[:end]
            frames.append(Frame(flags, payload, crc16_maxim(payload) == crc))

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def _skip(self, count: int) -> None:
        if count > 0:
            del self._buffer[:count]
            self.skipped_bytes += count


def _check_u32(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise MalformedFrameError(f"{name} {value} doesn't fit in u32")
