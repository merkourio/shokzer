"""Model identity and which features have been tested on which firmware.

Every feature is available on any OpenRun Pro 2, whatever its firmware. VERIFIED only records
the firmware versions a feature was tested on (docs/test-results.md); on other firmware the
feature is reported as untested and the user is asked to report how it went.
"""

from __future__ import annotations

from collections.abc import Mapping

from .protocol.commands import EqPreset
from .state import Support

MODEL_NAME = "OpenRun Pro 2"

PROPRIETARY_FEATURES = (
    "firmware_query",
    "battery_query",
    "eq_read",
    "eq_write_standard",
    "eq_write_vocal",
    "eq_write_bass",
    "eq_write_treble",
    "eq_write_custom",
    "multipoint_read",
    "multipoint_write",
    "multipoint_peers",
)

DESCRIPTIONS = {
    "firmware_query": "reading the firmware version",
    "battery_query": "reading the battery level",
    "eq_read": "reading the EQ",
    "eq_write_standard": "setting the Standard EQ",
    "eq_write_vocal": "setting the Vocal EQ",
    "eq_write_bass": "setting the Bass Boost EQ",
    "eq_write_treble": "setting the Treble Boost EQ",
    "eq_write_custom": "setting a custom EQ",
    "multipoint_read": "reading multipoint status",
    "multipoint_write": "turning multipoint on or off",
    "multipoint_peers": "connecting or disconnecting other devices",
}

# Keyed by the firmware string the headset itself reports (FIRMWARE_RET), not the app's label.
VERIFIED: Mapping[str, frozenset[str]] = {
    # 2026-11-13, Fedora 44: reads at M3 (e559b79); EQ, custom EQ and multipoint in hardware
    # tests A-C (14e6d78, 83bd5d2). docs/test-results.md.
    "BOS_HU_R_47_20251023": frozenset(
        {
            "firmware_query",
            "battery_query",
            "eq_read",
            "eq_write_standard",
            "eq_write_vocal",
            "eq_write_bass",
            "eq_write_treble",
            "eq_write_custom",
            "multipoint_read",
            "multipoint_write",
            "multipoint_peers",
        }
    ),
}


def name_matches_model(name: str | None) -> bool:
    return name is not None and MODEL_NAME.casefold() in name.casefold()


def firmware_verified(firmware: str | None) -> bool:
    return firmware is not None and firmware in VERIFIED


def capabilities(firmware: str | None = None) -> dict[str, Support]:
    """Unresolved until the firmware is known; then supported (tested) or untested."""
    if firmware is None:
        return dict.fromkeys(PROPRIETARY_FEATURES, Support.UNRESOLVED)
    verified = VERIFIED.get(firmware, frozenset())
    return {
        feature: Support.SUPPORTED if feature in verified else Support.UNTESTED
        for feature in PROPRIETARY_FEATURES
    }


def is_verified(feature: str, firmware: str | None) -> bool:
    return feature in VERIFIED.get(firmware or "", frozenset())


_PRESET_FEATURES = {
    EqPreset.STANDARD: "eq_write_standard",
    EqPreset.VOCAL: "eq_write_vocal",
    EqPreset.BASS: "eq_write_bass",
    EqPreset.TREBLE: "eq_write_treble",
    EqPreset.CUSTOM: "eq_write_custom",
}


def preset_feature(preset: EqPreset) -> str | None:
    """None for presets without Pro 2 evidence (Classic, Volume Boost, Swimming)."""
    return _PRESET_FEATURES.get(preset)
