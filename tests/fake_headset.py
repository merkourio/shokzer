"""A scripted headset and BlueZ profile bus for offline tests.

Reply bytes are built with our encoder from the layout Gadgetbridge's decoder reads. The
firmware string is made up; nothing here is a hardware observation.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Callable
from typing import Any

from dbus_fast import Message, MessageType

from shokzer.protocol.packets import Message as Frame
from shokzer.protocol.packets import StreamDecoder, decode_payload, encode
from shokzer.transport.management import PROFILE, PROFILE_PATH

BLUEZ_OWNER = ":1.4"
TEST_FIRMWARE = "TEST_FW_1"


def frame(group: int, code: int, args: bytes = b"") -> bytes:
    return encode(Frame(group, code, args))


def fragmented(group: int, code: int, args: bytes = b"", first: int = 116) -> list[bytes]:
    """Split a reply into two fragments the way the Pro 2 did for its device list (C1):
    the first carries the header and `first` payload bytes, the second the rest."""
    whole = frame(group, code, args)
    payload = whole[12:]
    if len(payload) <= first:
        return [whole]
    head = whole[:2] + struct.pack("<HBBH", first + 4, 2, 1, 0) + whole[8:12]
    rest = payload[first:]
    tail = whole[:2] + struct.pack("<HBBH", len(rest), 2, 2, 0)
    return [head + payload[:first], tail + rest]


def default_replies() -> dict[tuple[int, int], list[bytes]]:
    return {
        (0x02, 0x0002): [frame(0x02, 0x8002, b"\x00" + TEST_FIRMWARE.encode() + b"\x00")],
        (0x02, 0x0003): [frame(0x02, 0x8003, b"\x00\x05")],
        (0x02, 0x0008): [frame(0x02, 0x8008, b"\x00\x01")],
    }


class FakeHeadset:
    """Implements Channel. Answers requests from a script; records what it was sent."""

    def __init__(
        self,
        replies: dict[tuple[int, int], list[bytes]] | None = None,
        *,
        on_send: Callable[[Frame], None] | None = None,
    ) -> None:
        self.replies = default_replies() if replies is None else replies
        self.on_send = on_send
        self.sent: list[Frame] = []
        self.raw_sent: list[bytes] = []
        self.closed = False
        self.send_error: OSError | None = None
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue()

    async def send(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.raw_sent.append(data)
        frames = StreamDecoder().feed(data)
        assert len(frames) == 1
        assert frames[0].crc_valid
        message = decode_payload(frames[0].payload)
        self.sent.append(message)
        if self.on_send is not None:
            self.on_send(message)
        for reply in self.replies.get((message.group, message.code), []):
            self.push(reply)

    def push(self, data: bytes) -> None:
        self._incoming.put_nowait(data)

    def hang_up(self) -> None:
        self._incoming.put_nowait(b"")

    async def receive(self) -> bytes:
        return await self._incoming.get()

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, headset: FakeHeadset | None = None, *, error: Exception | None = None):
        self.headset = headset if headset is not None else FakeHeadset()
        self.error = error
        self.on_disconnect_requested: Callable[[], None] | None = None
        self.device_path: str | None = None
        self.opened = False
        self.closed = False

    async def __call__(self, device_path: str) -> FakeConnector:
        """Lets the instance act as the connector factory."""
        self.device_path = device_path
        return self

    async def open(self) -> FakeHeadset:
        if self.error is not None:
            raise self.error
        self.opened = True
        return self.headset

    async def close(self) -> None:
        self.closed = True


class FakeProfileBus:
    """Implements ProfileBus with BlueZ's side of RegisterProfile/ConnectProfile.

    ConnectProfile delivers one end of a real socketpair through NewConnection; the other end
    is kept as `peer` so tests can talk to the connector's socket.
    """

    def __init__(
        self,
        *,
        owner: str = BLUEZ_OWNER,
        new_connection_sender: str | None = None,
        new_connection_device: str | None = None,
        errors: dict[str, tuple[str, str]] | None = None,
        hang: set[str] | None = None,
        delay: dict[str, float] | None = None,
    ) -> None:
        self.owner = owner
        self.new_connection_sender = new_connection_sender or owner
        self.new_connection_device = new_connection_device
        self.errors = errors or {}
        self.hang = hang or set()
        self.delay = delay or {}
        self.events: list[str] = []
        self.handlers: list[Callable[[Message], Any]] = []
        self.calls: list[tuple[str, str, list[Any]]] = []
        self.handler_replies: list[Message | None] = []
        self.peer: socket.socket | None = None
        self.closed = False
        self._serial = 0

    def add_message_handler(self, handler: Callable[[Message], Any]) -> None:
        self.handlers.append(handler)

    def remove_message_handler(self, handler: Callable[[Message], Any]) -> None:
        if handler in self.handlers:  # dbus-fast ignores unknown handlers too
            self.handlers.remove(handler)

    async def aclose(self) -> None:
        self.closed = True

    async def call(self, message: Message) -> Message:
        assert message.member is not None
        message.serial = self._next()
        self.calls.append((message.member, message.path or "", list(message.body)))
        if message.member in self.hang:
            await asyncio.sleep(3600)
        if message.member in self.delay:
            await asyncio.sleep(self.delay[message.member])
        if message.member in self.errors:
            name, text = self.errors[message.member]
            return Message.new_error(message, name, text)
        if message.member == "GetNameOwner":
            return Message.new_method_return(message, "s", [self.owner])
        if message.member == "ConnectProfile":
            mine, self.peer = socket.socketpair()
            self.deliver(
                "NewConnection",
                "oha{sv}",
                [self.new_connection_device or message.path, 0, {}],
                fds=[mine.detach()],
                sender=self.new_connection_sender,
            )
        if message.member == "DisconnectProfile":
            self.deliver("RequestDisconnection", "o", [message.path])
        self.events.append(f"{message.member}:done")
        return Message.new_method_return(message)

    def deliver(
        self,
        member: str,
        signature: str,
        body: list[Any],
        *,
        fds: list[int] | None = None,
        sender: str | None = None,
    ) -> Message | None:
        call = Message(
            destination=":1.99",
            path=PROFILE_PATH,
            interface=PROFILE,
            member=member,
            signature=signature,
            body=body,
            unix_fds=fds or [],
            sender=sender or self.owner,
            serial=self._next(),
        )
        reply = None
        for handler in self.handlers:
            result = handler(call)
            if result:
                reply = result if isinstance(result, Message) else None
                break
        self.handler_replies.append(reply)
        return reply

    def members(self) -> list[str]:
        return [member for member, _, _ in self.calls]

    def _next(self) -> int:
        self._serial += 1
        return self._serial


def is_method_return(message: Message | None) -> bool:
    return message is not None and message.message_type is MessageType.METHOD_RETURN


def is_error(message: Message | None) -> bool:
    return message is not None and message.message_type is MessageType.ERROR


def fd_is_closed(sock: socket.socket) -> bool:
    """True when the other end of a socketpair is gone (recv returns EOF)."""
    sock.settimeout(1)
    try:
        return sock.recv(1) == b""
    except OSError:
        return True
