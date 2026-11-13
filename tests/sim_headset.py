"""A stateful simulated headset for write-path tests.

Replies follow the layouts Gadgetbridge decodes, plus the M3 observation that EQUALIZER_RET
carries six bytes after the preset code. Behaviour knobs model the failure cases the write
logic must handle; none of this is a hardware observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fake_headset import TEST_FIRMWARE, FakeHeadset, frame

from shokzctl.protocol.packets import Message
from shokzctl.protocol.responses import MULTIPOINT_ON_VALUE

THIS_COMPUTER = "AA:BB:CC:FF:FF:01"  # matches fakes.adapter()
PHONE = "11:22:33:44:55:66"
LAPTOP = "77:88:99:AA:BB:CC"
MULTIPOINT_OFF_RAW = 0x00000100  # made up; real value unknown until hardware


@dataclass
class Peer:
    address: str
    name: str
    connected: bool


@dataclass
class SimState:
    eq_code: int = 0x01
    bands: tuple[int, ...] = (0, 0, 0, 0, 0)
    report_bands: bool = True
    ack_writes: bool = True
    apply_writes: bool = True
    apply_after_reads: int = 0  # the change shows up only after this many readbacks
    hang_up_after_write: bool = False
    notify_eq: bool = True
    multipoint_raw: int = MULTIPOINT_ON_VALUE
    write_status: int = 0
    peers: list[Peer] = field(
        default_factory=lambda: [
            Peer(THIS_COMPUTER, "fedora", True),
            Peer(PHONE, "Pixel 10", False),
        ]
    )
    reported_slots: int = 8
    garbled_tail: bool = False


class SimHeadset(FakeHeadset):
    def __init__(self, state: SimState | None = None) -> None:
        super().__init__(replies={})
        self.state = state or SimState()
        self._pending: dict[str, object] | None = None
        self._reads_since_write = 0
        self.on_send = self._respond

    def writes(self) -> list[Message]:
        return [m for m in self.sent if m.group == 0x01]

    def _respond(self, message: Message) -> None:
        st = self.state
        key = (message.group, message.code)
        if key == (0x02, 0x0002):
            self.push(frame(0x02, 0x8002, b"\x00" + TEST_FIRMWARE.encode() + b"\x00"))
        elif key == (0x02, 0x0003):
            self.push(frame(0x02, 0x8003, b"\x00\x05\xff\x00"))
        elif key == (0x02, 0x0008):
            self._maybe_apply()
            tail = bytes(b & 0xFF for b in st.bands) + b"\x00" if st.eq_code == 5 else bytes(6)
            if st.eq_code == 5 and not st.report_bands:
                tail = b""
            self.push(frame(0x02, 0x8008, bytes([0x00, st.eq_code]) + tail))
        elif key == (0x01, 0x000E):
            code = message.args[0]
            bands = tuple(b - 256 if b > 127 else b for b in message.args[1:6])
            self._write({"eq_code": code, "bands": bands if code == 5 else st.bands})
            if st.ack_writes:
                self.push(frame(0x01, 0x800E, b"\x00"))
            if st.notify_eq and st.apply_writes and st.apply_after_reads == 0:
                self.push(frame(0x03, 0x0009, bytes([code])))
            self._after_write()
        elif key == (0x02, 0x0010):
            self._maybe_apply()
            self.push(frame(0x02, 0x8010, st.multipoint_raw.to_bytes(4, "little")))
        elif key == (0x02, 0x0004):
            self._maybe_apply()
            self.push(frame(0x02, 0x8004, self._device_list()))
        elif key in ((0x01, 0x0011), (0x01, 0x0010)):
            on = key == (0x01, 0x0011)
            self._write({"multipoint_raw": MULTIPOINT_ON_VALUE if on else MULTIPOINT_OFF_RAW})
            if st.ack_writes:
                self.push(frame(0x01, key[1] | 0x8000, st.write_status.to_bytes(4, "little")))
            self._after_write()
        elif key in ((0x01, 0x000B), (0x01, 0x000C)):
            address = ":".join(f"{b:02X}" for b in message.args[::-1])
            self._write({"peer": (address, key == (0x01, 0x000B))})
            if st.ack_writes:
                self.push(frame(0x01, key[1] | 0x8000, st.write_status.to_bytes(4, "little")))
            self._after_write()

    def _write(self, change: dict[str, object]) -> None:
        if self.state.write_status != 0 or not self.state.apply_writes:
            return
        self._pending = change
        self._reads_since_write = 0
        if self.state.apply_after_reads == 0:
            self._apply()

    def _maybe_apply(self) -> None:
        if self._pending is None:
            return
        self._reads_since_write += 1
        if self._reads_since_write > self.state.apply_after_reads:
            self._apply()

    def _apply(self) -> None:
        change, self._pending = self._pending or {}, None
        for name, value in change.items():
            if name == "peer":
                address, connected = value  # type: ignore[misc]
                for peer in self.state.peers:
                    if peer.address == address:
                        peer.connected = connected
            else:
                setattr(self.state, name, value)

    def _after_write(self) -> None:
        if self.state.hang_up_after_write:
            self.hang_up()

    def _device_list(self) -> bytes:
        body = bytearray([0x00, self.state.reported_slots])
        for index, peer in enumerate(self.state.peers):
            name = peer.name.encode()
            body += bytes([index]) + bytes.fromhex(peer.address.replace(":", ""))[::-1]
            body += bytes([1 if peer.connected else 0, len(name) + 1]) + name + b"\x00"
        if self.state.garbled_tail:
            body += bytes([7]) + bytes(6) + bytes([0, 3]) + b"\x00\x00\x00"
        return bytes(body)
