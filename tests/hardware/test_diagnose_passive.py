"""Passive diagnose against the real BlueZ. Reads D-Bus state only; sends nothing to the headset.

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

pytestmark = pytest.mark.hardware


def test_diagnose_reports_selected_device() -> None:
    address = os.environ["SHOKZ_DEVICE"].upper()
    out, err = io.StringIO(), io.StringIO()
    code = run(
        ["diagnose", "--device", address, "--json", "--show-identifiers"],
        connect=connect_system_bus,
        out=out,
        err=err,
    )
    assert code == ExitCode.OK, err.getvalue() or out.getvalue()
    report = json.loads(out.getvalue())["report"]
    assert report["device"]["address"] == address
    assert report["device"]["connection"] in {"connected", "disconnected"}
    assert report["protocol"]["readiness"] == "unverified"
