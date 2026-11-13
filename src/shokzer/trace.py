"""JSON-lines trace of a management session: every byte sent and received, with timing.

Contains frame bytes and decoded names only, never device addresses. Keep trace files out of
Git anyway (the repo ignores local/).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


class TraceFile:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = path.open("a", encoding="utf-8")
        self._start = time.monotonic()

    def __call__(self, event: str, **fields: object) -> None:
        line = {"t": round(time.monotonic() - self._start, 4), "event": event, **fields}
        self._file.write(json.dumps(line, sort_keys=True) + "\n")
        self._file.flush()

    def start(self, **fields: object) -> None:
        self("start", at=datetime.now(UTC).isoformat(), **fields)

    def close(self) -> None:
        self._file.close()
