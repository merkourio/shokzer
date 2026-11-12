"""Model identity and feature gates.

Proprietary features stay unresolved until docs/protocol-analysis.md has evidence for this
model and firmware and the hardware matrix records a controlled test.
"""

from __future__ import annotations

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


def name_matches_model(name: str | None) -> bool:
    return name is not None and MODEL_NAME.casefold() in name.casefold()


def capabilities() -> dict[str, Support]:
    return dict.fromkeys(PROPRIETARY_FEATURES, Support.UNRESOLVED)
