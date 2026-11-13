# Changelog

## Unreleased

- Classic (bone-only) EQ, and Standard labelled as Hybrid in the app; `eq set hybrid` aliases `eq set standard`.
- Classic uses Gadgetbridge's source-derived command, verified by acknowledgement/readback on `BOS_HU_R_47_20251023`. Other firmware remains flagged as untested. Changes are never resent.

## 0.1.0 — 2026-11-13

First release.

- GTK app and `shokzer` command line for the Shokz OpenRun Pro 2.
- Battery, firmware and current EQ.
- EQ presets (Standard, Vocal, Bass Boost, Treble Boost) and a five-band custom EQ, each confirmed by reading it back.
- Multipoint on/off, and connecting or disconnecting the headset's other remembered devices.
- Passive `diagnose` with `--watch`, JSON output, and addresses hidden by default.
- Tested on firmware `BOS_HU_R_47_20251023` with Fedora 44; other firmware versions work and are flagged as untested.
- RPM for Fedora 43 and 44, and a user-level installer for other distributions.
