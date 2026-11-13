"""The byte channel a management session runs over. Tests replace it with a fake."""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    async def send(self, data: bytes) -> None: ...

    async def receive(self) -> bytes:
        """Next chunk of bytes; b"" when the other side closed the channel."""
        ...

    async def close(self) -> None: ...
