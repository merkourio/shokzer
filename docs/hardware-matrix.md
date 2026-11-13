# Hardware matrix

What has been observed on real hardware. A mock or source fact is never a row here.

## Tested combinations

| Item | Value |
|---|---|
| Headset | Shokz OpenRun Pro 2 (`OpenRun Pro 2 by Shokz`). Firmware as reported by the headset: `BOS_HU_R_47_20251023`; the Shokz app shows `HU_R_47_20251023` |
| PnP ID (BlueZ Modalias) | `bluetooth:v02B0p0000d001F` |
| Host | Fedora Linux 44 Workstation, kernel 7.2.13-200.fc44.x86_64 |
| BlueZ | bluez-5.87+1.git8750129efca8-1.fc44 |
| Audio | pipewire 1.6.10, wireplumber 0.5.18; headset on `a2dp-sink` |
| Python | 3.14.8, then 3.14.10 after the RPM build deps (Fedora); dbus-fast 5.2.0 in the venv, Fedora's 2.45.1 for the RPM. shokzctl `e559b79`-`d82b3ee` |
| Adapter | `hci0`, MediaTek USB `0e8d:8c38`, driver `btusb`, HCI version 13 (Bluetooth 5.4) |
| Phone (reference) | Pixel 10, latest Android available on 2026-11-12, Shokz app working. Exact build and app version not recorded |

## Capability results

| Capability | Result | Evidence |
|---|---|---|
| Passive discovery via BlueZ D-Bus | Pass | `test-results.md` M2 |
| Battery via BlueZ `Battery1` | Pass | M2 |
| Management channel (RFCOMM `0xFEF0` via BlueZ profile API) | Pass | M3 |
| Firmware, battery and EQ reads | Pass | M3, A1 |
| EQ presets Standard, Vocal, Bass Boost, Treble Boost | Pass: read back and heard | A2-A5 |
| EQ persistence over a power cycle | Pass (Treble Boost) | A6 |
| Custom EQ | Pass: bands read back; band 1 low, band 5 high | B |
| Multipoint status and device list | Pass (fragmented reply, 3 devices) | C1 |
| Multipoint off and on | Pass: phone dropped, this computer stayed | C2, C3 |
| Connect and disconnect a remembered device | Pass (Pixel 10, ~3 s to connect) | C4 |
| Shokz app connected at the same time | Not supported by the headset: second connection refused | D1 |
| Headset off mid-use, concurrent CLI, BlueZ restart | Pass: clean errors, recovery, lock works | D2, D4, D6 |
| GUI against the real headset | Pass after the shutdown fix | E |
| Local RPM build, install, GNOME launcher | Pass; removal not tested (kept installed) | F |
| Suspend/resume, Android comparison | Not tested | D5, G |
