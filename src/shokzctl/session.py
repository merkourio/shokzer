"""One management session: a single request in flight, replies matched on group and code.

Nothing is resent. A request that times out is reported as a timeout; for a write that means
the outcome is uncertain and the caller must read the state back before doing anything else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .errors import ErrorCategory, ShokzError
from .protocol.commands import Command, name_of
from .protocol.packets import (
    Frame,
    MalformedFrameError,
    Message,
    StreamDecoder,
    decode_payload,
    encode,
)
from .protocol.responses import Decoded, decode
from .transport.base import Channel

DEFAULT_REPLY_TIMEOUT = 2.0  # Gadgetbridge's per-request timeout (ShokzSupport.kt:810)

type Trace = Callable[..., None]


def no_trace(event: str, **fields: object) -> None:
    pass


class ManagementSession:
    def __init__(
        self,
        channel: Channel,
        *,
        reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
        trace: Trace = no_trace,
    ) -> None:
        self._channel = channel
        self._reply_timeout = reply_timeout
        self._trace_sink = trace
        self.trace_failed = False
        self._decoder = StreamDecoder()
        self._lock = asyncio.Lock()
        self._pending: tuple[tuple[int, int], asyncio.Future[Message]] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._failure: ShokzError | None = None
        self._closed = False
        self.unprompted: list[Message] = []
        self.dropped_frames = 0

    def start(self) -> None:
        self._reader = asyncio.get_running_loop().create_task(self._read_loop())

    async def request(self, command: Command, args: bytes = b"") -> Message:
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._closed:
                raise ShokzError(ErrorCategory.UNAVAILABLE, "The management session is closed.")
            future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
            self._pending = (command.reply, future)
            data = encode(command.message(args))
            try:
                self._trace("tx", hex=data.hex(), name=command.name)
                try:
                    await self._channel.send(data)
                except OSError as error:
                    self._pending = None  # nobody will await the future
                    raise self._fail(
                        ShokzError(
                            ErrorCategory.UNAVAILABLE,
                            f"Sending {command.name} failed ({error}).",
                        )
                    ) from error
                try:
                    async with asyncio.timeout(self._reply_timeout):
                        return await future
                except TimeoutError:
                    self._trace("timeout", name=command.name)
                    raise ShokzError(
                        ErrorCategory.TIMEOUT,
                        f"No reply to {command.name} within {self._reply_timeout:g} s. "
                        "It was not resent.",
                    ) from None
            finally:
                self._pending = None

    async def query(self, command: Command, args: bytes = b"") -> Decoded:
        message = await self.request(command, args)
        try:
            return decode(message)
        except MalformedFrameError as error:
            raise ShokzError(
                ErrorCategory.PROTOCOL, f"The reply to {command.name} was malformed: {error}."
            ) from error

    def abort(self, reason: ShokzError) -> None:
        """Fail the request in flight and stop reading. close() still has to be awaited."""
        self._fail(reason)
        if self._reader is not None:
            self._reader.cancel()

    async def close(self) -> None:
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            # The reader may already have died; its error was reported to any waiting request.
            await asyncio.gather(self._reader, return_exceptions=True)
        await self._channel.close()

    def _trace(self, event: str, **fields: object) -> None:
        """A broken trace file must not break the session."""
        try:
            self._trace_sink(event, **fields)
        except Exception:
            self.trace_failed = True

    # --- receiving --------------------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                data = await self._channel.receive()
                if not data:
                    self._trace("eof")
                    self._fail(
                        ShokzError(
                            ErrorCategory.UNAVAILABLE,
                            "The headset closed the management channel.",
                        )
                    )
                    return
                self._trace("rx", hex=data.hex())
                for frame in self._decoder.feed(data):
                    self._on_frame(frame)
        except OSError as error:
            self._trace("error", note=str(error))
            self._fail(
                ShokzError(ErrorCategory.UNAVAILABLE, f"The management channel failed ({error}).")
            )

    def _on_frame(self, frame: Frame) -> None:
        # Gadgetbridge accepts MULTIPOINT_DEVICES_RET with a bad CRC as a "firmware quirk"
        # (ShokzSupport.kt:155-171). On the Pro 2 that reply is fragmented; reassembled, its
        # CRC is valid (docs/test-results.md, C1). So no reply type gets an exception here.
        if not frame.crc_valid:
            self.dropped_frames += 1
            self._trace("drop", hex=frame.payload.hex(), note="bad CRC")
            return
        try:
            message = decode_payload(frame.payload)
        except MalformedFrameError as error:
            self.dropped_frames += 1
            self._trace("drop", hex=frame.payload.hex(), note=str(error))
            return
        key = (message.group, message.code)
        name = name_of(*key)
        if self._pending is not None and self._pending[0] == key:
            future = self._pending[1]
            if not future.done():
                self._trace(
                    "reply",
                    name=name,
                    args=message.args.hex(),
                    flags=frame.flags.hex(),
                    fragments=frame.fragments,
                )
                future.set_result(message)
                return
        self.unprompted.append(message)
        self._trace(
            "unprompted",
            name=name,
            group=message.group,
            code=message.code,
            args=message.args.hex(),
            flags=frame.flags.hex(),
        )

    def _fail(self, error: ShokzError) -> ShokzError:
        if self._failure is None:
            self._failure = error
        if self._pending is not None and not self._pending[1].done():
            self._pending[1].set_exception(self._failure)
        return self._failure
