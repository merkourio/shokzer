"""One management session per user at a time, across CLI and future GUI processes.

An flock on a file in $XDG_RUNTIME_DIR. The kernel releases it when the process exits, so a
crash never leaves a stale lock.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from types import TracebackType

from .errors import ErrorCategory, ShokzError


def default_lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "shokzer.lock"
    return Path(tempfile.gettempdir()) / f"shokzer-{os.getuid()}.lock"


class SessionLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_lock_path()
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ShokzError(
                ErrorCategory.UNAVAILABLE,
                "Another shokzer session is using the headset's management channel.",
            ) from None
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
