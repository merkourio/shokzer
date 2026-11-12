"""Read-only status against the real headset: firmware, battery and EQ queries.

Opens the management channel and sends FIRMWARE_GET, BATTERY_GET and EQUALIZER_GET. Changes
no setting. Close the Shokz phone app first so it doesn't hold the channel.

Run: SHOKZ_HARDWARE=1 SHOKZ_DEVICE=<address> .venv/bin/python -m pytest tests/hardware
"""

from __future__ import annotations

import io
import json
import os

import pytest

from shokzctl.cli import run
from shokzctl.errors import ExitCode
from shokzctl.transport.bluez import connect_system_bus
from shokzctl.transport.management import open_connector

pytestmark = pytest.mark.hardware


def test_status_reads_firmware_battery_and_eq() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = run(
        ["status", "--device", os.environ["SHOKZ_DEVICE"], "--json"],
        connect=connect_system_bus,
        open_connector=open_connector,
        out=out,
        err=err,
    )
    assert code == ExitCode.OK, err.getvalue() or out.getvalue()
    report = json.loads(out.getvalue())["report"]
    assert report["protocol"]["readiness"] == "ready"
    assert report["firmware"]["value"]
    battery = report["headset_battery_percent"]["value"]
    assert battery is not None
    assert 10 <= battery <= 100
    assert report["active_eq"]["value"] is not None
