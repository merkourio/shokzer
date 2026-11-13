"""Frame encoding and stream decoding.

Layout (all little-endian). The single-frame form is from Gadgetbridge ShokzSupport.encodeCommand
and onSocketRead; fragmentation was observed on an OpenRun Pro 2 (docs/test-results.md, C1).

    offset  size  field
    0       2     preamble A5 5A
    2       2     frame length: bytes after offset 8 in this frame
    4       1     fragment count (1 when not fragmented)
    5       1     fragment number: 0 in requests, 1..count in headset frames
    6       2     zero
  first (or only) fragment:
    8       2     total payload length
    10      2     CRC-16/MAXIM of the whole payload
    12      n     payload bytes, n = frame length - 4
  later fragments:
    8       n     payload bytes, n = frame length

Payload:
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
MAX_PAYLOAD_SIZE = MAX_FRAME_SIZE
MAX_FRAGMENTS = 16  # sanity bound only; the largest seen is 2


class MalformedFrameError(ValueError):
    """Bytes that don't follow the evidenced layout."""


@dataclass(frozen=True)
class Message:
    group: int
    code: int
    args: bytes = b""


@dataclass(frozen=True)
class Frame:
    flags: bytes  # of the first fragment
    payload: bytes
    crc_valid: bool
    fragments: int = 1


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
    """Turns RFCOMM reads into frames. Handles split and coalesced reads, reassembles
    fragmented frames, and skips junk.

    A header that doesn't fit the layout is treated as junk: the decoder skips its preamble
    and searches again. A fragment that doesn't continue the frame being assembled abandons
    it (counted in dropped_fragments). CRC failures are reported on the frame for the caller
    to decide.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._assembly: _Assembly | None = None
        self.skipped_bytes = 0
        self.dropped_fragments = 0

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
            if len(self._buffer) < 8:
                return frames
            frame_len, count, number, reserved = struct.unpack_from("<HBBH", self._buffer, 2)
            if not 1 <= count <= MAX_FRAGMENTS or reserved or frame_len > MAX_FRAME_SIZE:
                self._skip(len(PREAMBLE))
                continue
            first = count == 1 or number <= 1
            if first:
                if len(self._buffer) < HEADER_SIZE:
                    return frames
                total, crc = struct.unpack_from("<HH", self._buffer, 8)
                size = frame_len - 4
                fits = size == total if count == 1 else 0 <= size < total
                if frame_len < 4 or total > MAX_PAYLOAD_SIZE or not fits:
                    self._skip(len(PREAMBLE))
                    continue
            end = 8 + frame_len
            if len(self._buffer) < end:
                return frames
            flags = bytes(self._buffer[4:8])
            chunk = bytes(self._buffer[HEADER_SIZE if first else 8 : end])
            del self._buffer[:end]
            if first:
                self._abandon()
                if count == 1:
                    frames.append(Frame(flags, chunk, crc16_maxim(chunk) == crc))
                else:
                    self._assembly = _Assembly(count, total, crc, flags, bytearray(chunk))
                continue
            done = self._continue(count, number, chunk)
            if done is not None:
                frames.append(done)

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def _continue(self, count: int, number: int, chunk: bytes) -> Frame | None:
        assembly = self._assembly
        if assembly is None or count != assembly.count or number != assembly.next:
            self.dropped_fragments += 1
            self._abandon()
            return None
        assembly.data += chunk
        assembly.next += 1
        if len(assembly.data) > assembly.total:
            self._abandon()
            return None
        if number < count:
            return None
        self._assembly = None
        if len(assembly.data) != assembly.total:
            self.dropped_fragments += count
            return None
        payload = bytes(assembly.data)
        return Frame(assembly.flags, payload, crc16_maxim(payload) == assembly.crc, count)

    def _abandon(self) -> None:
        if self._assembly is not None:
            self.dropped_fragments += self._assembly.next - 1
            self._assembly = None

    def _skip(self, count: int) -> None:
        if count > 0:
            del self._buffer[:count]
            self.skipped_bytes += count


@dataclass
class _Assembly:
    count: int
    total: int
    crc: int
    flags: bytes
    data: bytearray
    next: int = 2


def _check_u32(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise MalformedFrameError(f"{name} {value} doesn't fit in u32")
