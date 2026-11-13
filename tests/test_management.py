from __future__ import annotations

import socket

import pytest
from dbus_fast import Message
from fake_headset import FakeProfileBus, fd_is_closed, is_error, is_method_return
from fakes import HEADSET, HEADSET_2, device_path

from shokzer.errors import ErrorCategory, ShokzError
from shokzer.transport.management import (
    PROFILE_PATH,
    SHOKZ_SERVICE_UUID,
    RfcommConnector,
)

DEVICE_PATH = device_path(HEADSET)


async def test_open_registers_client_profile_and_connects_by_uuid() -> None:
    bus = FakeProfileBus()
    connector = RfcommConnector(bus, DEVICE_PATH)
    channel = await connector.open()

    assert bus.members() == ["GetNameOwner", "RegisterProfile", "ConnectProfile"]
    _, register_path, (path, uuid, options) = bus.calls[1]
    assert register_path == "/org/bluez"
    assert (path, uuid) == (PROFILE_PATH, SHOKZ_SERVICE_UUID)
    assert {k: v.value for k, v in options.items()} == {
        "Name": "shokzer",
        "Role": "client",
        "AutoConnect": False,
        "RequireAuthentication": True,
        "RequireAuthorization": False,
    }
    assert "Channel" not in options, "BlueZ resolves the channel from SDP; we never pick one"
    assert bus.calls[2][1:] == (DEVICE_PATH, [SHOKZ_SERVICE_UUID])
    assert is_method_return(bus.handler_replies[0])

    assert bus.peer is not None
    await channel.send(b"ping")
    assert bus.peer.recv(4) == b"ping"
    bus.peer.send(b"pong")
    assert await channel.receive() == b"pong"

    await channel.close()
    await connector.close()


async def test_close_hands_the_link_back_to_bluez() -> None:
    bus = FakeProfileBus()
    connector = RfcommConnector(bus, DEVICE_PATH)
    requested: list[bool] = []
    connector.on_disconnect_requested = lambda: requested.append(True)
    channel = await connector.open()
    await channel.close()
    await connector.close()

    assert bus.members()[-2:] == ["DisconnectProfile", "UnregisterProfile"]
    assert requested == [True]
    assert is_method_return(bus.handler_replies[-1])
    assert bus.handlers == []
    assert bus.closed
    assert bus.peer is not None
    assert fd_is_closed(bus.peer)


async def test_new_connection_from_another_sender_is_rejected() -> None:
    bus = FakeProfileBus(new_connection_sender=":1.666")
    connector = RfcommConnector(bus, DEVICE_PATH, connect_timeout=0.05)
    with pytest.raises(ShokzError) as caught:
        await connector.open()
    assert caught.value.category is ErrorCategory.TIMEOUT
    assert is_error(bus.handler_replies[0])
    assert bus.peer is not None
    assert fd_is_closed(bus.peer)
    await connector.close()


async def test_new_connection_for_another_device_is_rejected() -> None:
    bus = FakeProfileBus(new_connection_device=device_path(HEADSET_2))
    connector = RfcommConnector(bus, DEVICE_PATH, connect_timeout=0.05)
    with pytest.raises(ShokzError):
        await connector.open()
    assert is_error(bus.handler_replies[0])
    assert bus.peer is not None
    assert fd_is_closed(bus.peer)
    await connector.close()


async def test_connect_failure_is_reported_and_profile_unregistered() -> None:
    bus = FakeProfileBus(
        errors={"ConnectProfile": ("org.bluez.Error.Failed", "br-connection-profile-unavailable")}
    )
    connector = RfcommConnector(bus, DEVICE_PATH)
    with pytest.raises(ShokzError) as caught:
        await connector.open()
    assert caught.value.category is ErrorCategory.UNAVAILABLE
    assert "br-connection-profile-unavailable" in caught.value.message
    await connector.close()
    # BlueZ may have handed over a socket before failing, so the profile is released anyway.
    assert bus.members()[-2:] == ["DisconnectProfile", "UnregisterProfile"]


async def test_register_failure_skips_unregister() -> None:
    bus = FakeProfileBus(
        errors={"RegisterProfile": ("org.bluez.Error.NotPermitted", "UUID already registered")}
    )
    connector = RfcommConnector(bus, DEVICE_PATH)
    with pytest.raises(ShokzError) as caught:
        await connector.open()
    assert caught.value.category is ErrorCategory.PERMISSION
    await connector.close()
    assert bus.members() == ["GetNameOwner", "RegisterProfile"]
    assert bus.closed


async def test_connect_that_never_finishes_times_out() -> None:
    bus = FakeProfileBus(hang={"ConnectProfile"})
    connector = RfcommConnector(bus, DEVICE_PATH, connect_timeout=0.05)
    with pytest.raises(ShokzError) as caught:
        await connector.open()
    assert caught.value.category is ErrorCategory.TIMEOUT
    await connector.close()
    assert bus.members()[-1] == "UnregisterProfile"


async def test_new_connection_after_close_is_rejected() -> None:
    bus = FakeProfileBus()
    connector = RfcommConnector(bus, DEVICE_PATH)
    await connector.close()
    connector._owner = bus.owner  # as if open() had started
    bus.add_message_handler(connector._on_message)
    mine, peer = socket.socketpair()
    reply = bus.deliver("NewConnection", "oha{sv}", [DEVICE_PATH, 0, {}], fds=[mine.detach()])
    assert is_error(reply)
    assert fd_is_closed(peer)


async def test_release_skips_unregister() -> None:
    bus = FakeProfileBus()
    connector = RfcommConnector(bus, DEVICE_PATH)
    channel = await connector.open()
    assert is_method_return(bus.deliver("Release", "", []))
    await channel.close()
    await connector.close()
    assert "UnregisterProfile" not in bus.members()


async def test_messages_for_other_paths_are_left_alone() -> None:
    bus = FakeProfileBus()
    connector = RfcommConnector(bus, DEVICE_PATH)
    other = Message(path="/somewhere/else", interface="org.example.X", member="Y", serial=1)
    assert connector._on_message(other) is None


async def test_refused_connection_suggests_closing_the_phone_app() -> None:
    bus = FakeProfileBus(
        errors={"ConnectProfile": ("org.bluez.Error.Failed", "br-connection-refused")}
    )
    connector = RfcommConnector(bus, DEVICE_PATH)
    with pytest.raises(ShokzError) as caught:
        await connector.open()
    assert "Shokz phone app" in (caught.value.hint or "")
    await connector.close()


async def test_cancelled_connect_finishes_before_the_profile_is_released() -> None:
    import asyncio

    bus = FakeProfileBus(delay={"ConnectProfile": 0.2})
    connector = RfcommConnector(bus, DEVICE_PATH)
    opening = asyncio.create_task(connector.open())
    await asyncio.sleep(0.05)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    await connector.close()
    # BlueZ answered the ConnectProfile before we asked it to let go of the profile.
    assert bus.events.index("ConnectProfile:done") < bus.events.index("DisconnectProfile:done")
    assert bus.members()[-2:] == ["DisconnectProfile", "UnregisterProfile"]
    # The connection that arrived late was refused and its socket closed.
    assert is_error(bus.handler_replies[0])
    assert bus.peer is not None
    assert fd_is_closed(bus.peer)
