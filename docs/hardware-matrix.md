# Hardware matrix

What has been observed on real hardware. A mock or source fact is never a row here.

## Tested combinations

| Item | Value |
|---|---|
| Headset | Shokz OpenRun Pro 2 (`OpenRun Pro 2 by Shokz`), firmware `HU_R_47_20251023` (Shokz app, reported by Markos 2026-11-12) |
| PnP ID (BlueZ Modalias) | `bluetooth:v02B0p0000d001F` |
| Host | Fedora Linux 44 Workstation, kernel 7.2.13-200.fc44.x86_64 |
| BlueZ | bluez-5.87+1.git8750129efca8-1.fc44 |
| Audio | pipewire 1.6.10, wireplumber 0.5.18; headset on `a2dp-sink` |
| Python | 3.14.8 (Fedora), dbus-fast 5.2.0 |
| Adapter | `hci0`, MediaTek USB `0e8d:8c38`, driver `btusb`, HCI version 13 (Bluetooth 5.4) |
| Phone (reference) | Pixel 10, latest Android available on 2026-11-12, Shokz app working. Exact build and app version not recorded |

## Capability results

| Capability | Result | Evidence |
|---|---|---|
| Passive discovery via BlueZ D-Bus | Pass | `test-results.md` 2026-11-13 |
| Battery via BlueZ `Battery1` | Pass (60 % while on A2DP) | `test-results.md` 2026-11-13 |
| BR/EDR bearer only (no LE object) | Observed | `test-results.md` 2026-11-13 |
| Service UUID `0xFEF0` in BlueZ's cache | Observed | `test-results.md` 2026-11-13 |
| Management channel (RFCOMM `0xFEF0`) | Not tested | Needs approval; see `protocol-analysis.md` |
| Firmware / battery / EQ query | Not tested | |
| EQ preset write | Not tested | |
| Custom EQ, multipoint | Not tested | |
