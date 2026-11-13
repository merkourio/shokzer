from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fake_headset import TEST_FIRMWARE, FakeHeadset, frame

from shokzer.errors import ErrorCategory, ShokzError
from shokzer.protocol.commands import (
    BATTERY_GET,
    EQUALIZER_GET,
    FIRMWARE_GET,
    MULTIPOINT_DEVICES_GET,
)
from shokzer.protocol.packets import Message
from shokzer.protocol.responses import Battery, Firmware, MultipointDevices
from shokzer.session import ManagementSession

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "protocol_vectors.json").read_text())
UPSTREAM_FIRMWARE_GET = VECTORS["vectors"][0]["hex"]


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, **fields})

    def names(self) -> list[str]:
        return [e["event"] for e in self.events]


async def started(headset: FakeHeadset, **kwargs: Any) -> ManagementSession:
    session = ManagementSession(headset, reply_timeout=kwargs.pop("reply_timeout", 0.05), **kwargs)
    session.start()
    return session


async def test_firmware_query_sends_the_upstream_vector() -> None:
    headset = FakeHeadset()
    session = await started(headset)
    assert await session.query(FIRMWARE_GET) == Firmware(TEST_FIRMWARE)
    assert [data.hex() for data in headset.raw_sent] == [UPSTREAM_FIRMWARE_GET]
    await session.close()


async def test_reply_split_into_single_bytes() -> None:
    reply = frame(0x02, 0x8003, b"\x00\x05")
    headset = FakeHeadset(replies={})
    headset.on_send = lambda _: [headset.push(reply[i : i + 1]) for i in range(len(reply))]
    session = await started(headset)
    assert await session.query(BATTERY_GET) == Battery(60, 5)
    await session.close()


async def test_unprompted_message_before_reply_is_kept() -> None:
    notice = frame(0x03, 0x0005, b"\x08")
    replies = {(0x02, 0x0003): [notice, frame(0x02, 0x8003, b"\x00\x09")]}
    headset = FakeHeadset(replies=replies)
    session = await started(headset)
    assert await session.query(BATTERY_GET) == Battery(100, 9)
    assert session.unprompted == [Message(0x03, 0x0005, b"\x08")]
    await session.close()


async def test_timeout_is_not_resent() -> None:
    headset = FakeHeadset(replies={})
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert "not resent" in caught.value.message
    await asyncio.sleep(0.1)
    assert len(headset.sent) == 1
    await session.close()


async def test_late_reply_is_not_taken_for_the_next_request() -> None:
    headset = FakeHeadset(replies={(0x02, 0x0008): [frame(0x02, 0x8008, b"\x00\x02")]})
    session = await started(headset)
    with pytest.raises(ShokzError):
        await session.query(BATTERY_GET)
    headset.push(frame(0x02, 0x8003, b"\x00\x05"))  # battery reply arrives late
    result = await session.query(EQUALIZER_GET)
    assert getattr(result, "code", None) == 2
    assert Message(0x02, 0x8003, b"\x00\x05") in session.unprompted
    await session.close()


async def test_language_ack_is_not_the_firmware_reply() -> None:
    headset = FakeHeadset(replies={(0x02, 0x0002): [frame(0x01, 0x8002, b"\x00")]})
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert session.unprompted == [Message(0x01, 0x8002, b"\x00")]
    await session.close()


async def test_bad_crc_reply_is_dropped() -> None:
    bad = bytearray(frame(0x02, 0x8002, b"\x00X\x00"))
    bad[-1] ^= 0xFF
    headset = FakeHeadset(replies={(0x02, 0x0002): [bytes(bad)]})
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert session.dropped_frames == 1
    await session.close()


async def test_malformed_reply_is_a_protocol_error() -> None:
    headset = FakeHeadset(replies={(0x02, 0x0002): [frame(0x02, 0x8002, b"\x01X\x00")]})
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.PROTOCOL
    await session.close()


async def test_headset_hangs_up_while_waiting() -> None:
    headset = FakeHeadset(replies={})
    headset.on_send = lambda _: headset.hang_up()
    session = await started(headset, reply_timeout=1)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.UNAVAILABLE
    with pytest.raises(ShokzError):
        await session.query(BATTERY_GET)
    assert len(headset.sent) == 1, "nothing is sent after the channel closed"
    await session.close()


async def test_send_failure() -> None:
    headset = FakeHeadset()
    headset.send_error = BrokenPipeError(32, "Broken pipe")
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(FIRMWARE_GET)
    assert caught.value.category is ErrorCategory.UNAVAILABLE
    await session.close()


async def test_abort_fails_the_request_in_flight() -> None:
    headset = FakeHeadset(replies={})
    session = await started(headset, reply_timeout=1)
    task = asyncio.create_task(session.query(FIRMWARE_GET))
    await asyncio.sleep(0)
    session.abort(ShokzError(ErrorCategory.UNAVAILABLE, "BlueZ closed it."))
    with pytest.raises(ShokzError, match="BlueZ closed it"):
        await task
    await session.close()


async def test_close_closes_the_channel() -> None:
    headset = FakeHeadset()
    session = await started(headset)
    await session.close()
    assert headset.closed
    with pytest.raises(ShokzError):
        await session.query(FIRMWARE_GET)


async def test_trace_records_traffic() -> None:
    trace = Recorder()
    headset = FakeHeadset()
    session = await started(headset, trace=trace)
    await session.query(FIRMWARE_GET)
    await session.close()
    assert trace.names() == ["tx", "rx", "reply"]
    assert trace.events[0]["hex"] == UPSTREAM_FIRMWARE_GET
    assert trace.events[2]["name"] == "FIRMWARE_RET"


async def test_bad_crc_is_dropped_for_every_reply_type() -> None:
    bad = bytearray(frame(0x02, 0x8004, bytes([0, 0])))
    bad[10] ^= 0xFF  # corrupt the CRC field
    headset = FakeHeadset(replies={(0x02, 0x0004): [bytes(bad)]})
    session = await started(headset)
    with pytest.raises(ShokzError) as caught:
        await session.query(MULTIPOINT_DEVICES_GET)
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert session.dropped_frames == 1
    await session.close()


async def test_fragmented_reply_is_matched_once_reassembled() -> None:
    fragments = [bytes.fromhex(h) for h in VECTORS["fragmented_device_list"]["fragments"]]
    trace = Recorder()
    headset = FakeHeadset(replies={(0x02, 0x0004): fragments})
    session = await started(headset, trace=trace)
    result = await session.query(MULTIPOINT_DEVICES_GET)
    assert isinstance(result, MultipointDevices)
    assert len(result.peers) == 3
    assert [e.get("fragments") for e in trace.events if e["event"] == "reply"] == [2]
    await session.close()


async def test_broken_trace_doesnt_break_the_session() -> None:
    def broken(event: str, **fields: object) -> None:
        raise OSError(28, "No space left on device")

    headset = FakeHeadset()
    session = await started(headset, trace=broken)
    assert await session.query(FIRMWARE_GET) == Firmware(TEST_FIRMWARE)
    assert session.trace_failed
    await session.close()
    assert headset.closed
