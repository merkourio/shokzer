"""Open the headset's management channel through BlueZ's profile API.

Evidence: docs/protocol-analysis.md, "Transport". The channel is RFCOMM for SDP service
0xFEF0. We register a client-role profile and ask BlueZ to connect it; BlueZ looks the channel
up in the headset's SDP record and hands us the connected socket in Profile1.NewConnection.
We never pick or probe a channel number.

Ownership: BlueZ keeps its own reference to the socket, so the link stays open until BlueZ
lets go. close() asks it to (DisconnectProfile, then UnregisterProfile). If this process dies,
BlueZ sees our bus name vanish and drops the profile and its connection itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Callable
from typing import Any, Protocol

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from ..errors import ErrorCategory, ShokzError
from .bluez import BLUEZ, DBUS, DBUS_PATH, DEVICE, dbus_error, open_system_bus

SHOKZ_SERVICE_UUID = "0000fef0-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/pm/markos/Shokzer/management"
PROFILE = "org.bluez.Profile1"
PROFILE_MANAGER = "org.bluez.ProfileManager1"
REJECTED = "org.bluez.Error.Rejected"
RECEIVE_SIZE = 2048

PROFILE_OPTIONS = {
    "Name": Variant("s", "shokzer"),
    "Role": Variant("s", "client"),
    "AutoConnect": Variant("b", False),
    "RequireAuthentication": Variant("b", True),
    "RequireAuthorization": Variant("b", False),
}

type Handler = Callable[[Message], Message | bool | None]


class ProfileBus(Protocol):
    """The parts of a system-bus connection the connector uses. Tests replace it."""

    async def call(self, message: Message) -> Message: ...

    def add_message_handler(self, handler: Handler) -> None: ...

    def remove_message_handler(self, handler: Handler) -> None: ...

    async def aclose(self) -> None: ...


class SocketChannel:
    """A connected stream socket used through the event loop."""

    def __init__(self, sock: socket.socket) -> None:
        sock.setblocking(False)
        self._sock = sock
        self._loop = asyncio.get_running_loop()
        self._closed = False

    async def send(self, data: bytes) -> None:
        await self._loop.sock_sendall(self._sock, data)

    async def receive(self) -> bytes:
        return await self._loop.sock_recv(self._sock, RECEIVE_SIZE)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._sock.close()


class RfcommConnector:
    def __init__(self, bus: ProfileBus, device_path: str, *, connect_timeout: float = 15.0) -> None:
        self._bus = bus
        self._device_path = device_path
        self._connect_timeout = connect_timeout
        self._owner: str | None = None
        self._fd: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._registered = False
        self._connected = False
        self._handed_out = False
        self._connect_call: asyncio.Future[Message] | None = None
        self._closed = False
        self.on_disconnect_requested: Callable[[], None] | None = None

    async def open(self) -> SocketChannel:
        self._bus.add_message_handler(self._on_message)
        self._owner = await self._bluez_owner()
        await self._call(
            BLUEZ,
            "/org/bluez",
            PROFILE_MANAGER,
            "RegisterProfile",
            "osa{sv}",
            [PROFILE_PATH, SHOKZ_SERVICE_UUID, PROFILE_OPTIONS],
            what="register the management profile",
        )
        self._registered = True
        # From here BlueZ may hand over a socket even if the call then fails or times out,
        # so close() asks it to disconnect the profile in any case.
        self._connected = True
        # BlueZ keeps a ConnectProfile in progress even if we stop waiting, and refuses new ones
        # with br-connection-busy until it finishes (seen 2026-11-13 after closing the app
        # mid-connect). So the call runs as its own task: if open() is cancelled or times
        # out, close() waits for BlueZ to answer before releasing the profile.
        self._connect_call = asyncio.ensure_future(
            self._call(
                BLUEZ,
                self._device_path,
                DEVICE,
                "ConnectProfile",
                "s",
                [SHOKZ_SERVICE_UUID],
                what="open the headset's management channel",
            )
        )
        try:
            async with asyncio.timeout(self._connect_timeout):
                await asyncio.shield(self._connect_call)
                fd = await self._fd
        except TimeoutError:
            raise ShokzError(
                ErrorCategory.TIMEOUT,
                f"The management channel didn't open within {self._connect_timeout:g} s.",
            ) from None
        try:
            channel = SocketChannel(socket.socket(fileno=fd))
        except OSError:
            os.close(fd)
            raise
        self._handed_out = True
        return channel

    async def close(self) -> None:
        """Hand the link back to BlueZ. Best effort: each step is bounded and errors are kept
        quiet so one failure doesn't skip the rest."""
        if self._closed:
            return
        self._closed = True
        if not self._fd.done():
            self._fd.cancel()  # a NewConnection arriving now is refused, which ends the call
        call = self._connect_call
        if call is not None and not call.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(call), timeout=self._connect_timeout)
        if call is not None and call.done() and not call.cancelled():
            call.exception()  # retrieved here so it isn't logged as unhandled
        elif not self._fd.cancelled() and not self._handed_out:
            _close_fds([self._fd.result()])
        if self._connected:
            await self._quietly(
                BLUEZ, self._device_path, DEVICE, "DisconnectProfile", "s", [SHOKZ_SERVICE_UUID]
            )
        if self._registered:
            await self._quietly(
                BLUEZ, "/org/bluez", PROFILE_MANAGER, "UnregisterProfile", "o", [PROFILE_PATH]
            )
        self._bus.remove_message_handler(self._on_message)
        await self._bus.aclose()

    # --- calls from BlueZ ------------------------------------------------------------------

    def _on_message(self, message: Message) -> Message | None:
        if message.message_type is not MessageType.METHOD_CALL or message.path != PROFILE_PATH:
            return None
        if message.interface not in (PROFILE, None):
            _close_fds(message.unix_fds)
            return None
        if self._owner is None or message.sender != self._owner:
            _close_fds(message.unix_fds)
            return Message.new_error(message, REJECTED, "Only BlueZ may call this profile.")
        match message.member:
            case "NewConnection":
                return self._new_connection(message)
            case "RequestDisconnection":
                if self.on_disconnect_requested is not None:
                    self.on_disconnect_requested()
                return Message.new_method_return(message)
            case "Release":
                self._registered = False
                return Message.new_method_return(message)
            case _:
                _close_fds(message.unix_fds)
                return None

    def _new_connection(self, message: Message) -> Message:
        fds = list(message.unix_fds)
        device, index = _new_connection_args(message.body)
        fd = fds.pop(index) if index is not None and 0 <= index < len(fds) else None
        _close_fds(fds)
        if fd is None:
            return Message.new_error(message, REJECTED, "No file descriptor.")
        if device != self._device_path or self._closed or self._fd.done():
            os.close(fd)
            return Message.new_error(message, REJECTED, "Not expecting this connection.")
        self._fd.set_result(fd)
        return Message.new_method_return(message)

    # --- helpers ---------------------------------------------------------------------------

    async def _bluez_owner(self) -> str:
        reply = await self._bus.call(
            Message(
                destination=DBUS,
                path=DBUS_PATH,
                interface=DBUS,
                member="GetNameOwner",
                signature="s",
                body=[BLUEZ],
            )
        )
        if reply.message_type is MessageType.ERROR:
            raise dbus_error(reply.error_name, _error_text(reply))
        owner: str = reply.body[0]
        return owner

    async def _call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str,
        body: list[Any],
        *,
        what: str,
    ) -> Message:
        reply = await self._bus.call(
            Message(
                destination=destination,
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body,
            )
        )
        if reply.message_type is MessageType.ERROR:
            raise _bluez_error(reply, what)
        return reply

    async def _quietly(
        self, destination: str, path: str, interface: str, member: str, sig: str, body: list[Any]
    ) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(5):
                await self._bus.call(
                    Message(
                        destination=destination,
                        path=path,
                        interface=interface,
                        member=member,
                        signature=sig,
                        body=body,
                    )
                )


class SystemProfileBus:
    """ProfileBus over a real system-bus connection that accepts file descriptors."""

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def call(self, message: Message) -> Message:
        return await self._bus.call(message)

    def add_message_handler(self, handler: Handler) -> None:
        self._bus.add_message_handler(handler)

    def remove_message_handler(self, handler: Handler) -> None:
        self._bus.remove_message_handler(handler)

    async def aclose(self) -> None:
        self._bus.disconnect()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._bus.wait_for_disconnect(), timeout=1)  # type: ignore[no-untyped-call]


async def open_connector(device_path: str) -> RfcommConnector:
    bus = await open_system_bus(negotiate_unix_fd=True)
    return RfcommConnector(SystemProfileBus(bus), device_path)


def _new_connection_args(body: list[Any]) -> tuple[str | None, int | None]:
    if len(body) < 2 or not isinstance(body[0], str) or not isinstance(body[1], int):
        return None, None
    return body[0], body[1]


def _bluez_error(reply: Message, what: str) -> ShokzError:
    name = reply.error_name or ""
    if name.startswith("org.freedesktop.DBus.Error."):
        return dbus_error(name, _error_text(reply))
    detail = f"{name}: {_error_text(reply)}" if _error_text(reply) else name
    if name in ("org.bluez.Error.NotPermitted", "org.bluez.Error.NotAuthorized"):
        return ShokzError(ErrorCategory.PERMISSION, f"BlueZ wouldn't {what} ({detail}).")
    hint = None
    if "br-connection-refused" in (_error_text(reply) or ""):
        # Seen 2026-11-13 (test D1) while the Shokz phone app was connected.
        hint = (
            "The headset takes one management connection at a time. "
            "If the Shokz phone app is open, force-stop it and try again."
        )
    return ShokzError(ErrorCategory.UNAVAILABLE, f"BlueZ couldn't {what} ({detail}).", hint=hint)


def _error_text(reply: Message) -> str | None:
    if reply.body and isinstance(reply.body[0], str):
        text: str = reply.body[0]
        return text
    return None


def _close_fds(fds: list[int]) -> None:
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)
