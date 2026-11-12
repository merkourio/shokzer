"""Model identity and feature gates.

A proprietary feature is supported only for a firmware version listed in VERIFIED, and an
entry goes there only after a controlled test recorded in docs/hardware-matrix.md. Every
other combination stays unresolved.
"""

from __future__ import annotations

from collections.abc import Mapping

from .state import Support

MODEL_NAME = "OpenRun Pro 2"

PROPRIETARY_FEATURES = (
    "battery_query",
    "firmware_query",
    "eq_read",
    "eq_preset_write",
    "custom_eq",
    "multipoint",
)

# Keyed by the firmware string the headset itself reports (FIRMWARE_RET), not the app's label.
VERIFIED: Mapping[str, frozenset[str]] = {
    # 2026-11-13, Fedora 44, shokzctl e559b79: docs/test-results.md "M3".
    "BOS_HU_R_47_20251023": frozenset({"firmware_query", "battery_query", "eq_read"}),
}


def name_matches_model(name: str | None) -> bool:
    return name is not None and MODEL_NAME.casefold() in name.casefold()


def firmware_verified(firmware: str | None) -> bool:
    return firmware is not None and firmware in VERIFIED


def capabilities(firmware: str | None = None) -> dict[str, Support]:
    verified = VERIFIED.get(firmware or "", frozenset())
    return {
        feature: Support.SUPPORTED if feature in verified else Support.UNRESOLVED
        for feature in PROPRIETARY_FEATURES
    }
