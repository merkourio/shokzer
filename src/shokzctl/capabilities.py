"""Model identity and feature gates.

A proprietary feature is supported only for a firmware version listed in VERIFIED, and an
entry goes there only after a controlled test recorded in docs/hardware-matrix.md. Every
other combination stays unresolved. Unresolved features can still be used from the CLI with
--allow-unverified, which is how they get tested; the GUI never offers them.
"""

from __future__ import annotations

from collections.abc import Mapping

from .errors import ErrorCategory, ShokzError
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


def is_verified(feature: str, firmware: str | None) -> bool:
    return feature in VERIFIED.get(firmware or "", frozenset())


def require(feature: str, firmware: str | None, *, allow_unverified: bool) -> bool:
    """Raise unless the feature is verified for this firmware or the caller opted in.
    Returns whether it was verified."""
    if feature not in DESCRIPTIONS:
        raise ValueError(f"unknown feature {feature!r}")
    if is_verified(feature, firmware):
        return True
    if allow_unverified:
        return False
    raise ShokzError(
        ErrorCategory.UNSUPPORTED,
        f"{DESCRIPTIONS[feature].capitalize()} isn't verified on firmware {firmware}.",
        hint="Pass --allow-unverified to try it anyway, at low volume, with the Shokz app closed.",
    )


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
