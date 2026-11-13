# Test results

Each entry: date, application commit, what ran, outcome, and limits. Raw logs stay outside Git.

## 2026-11-13 — passive diagnose on Fedora (M2)

- **Commit:** `e3652e4`
- **Setup:** headset connected to Fedora on A2DP; playback and phone state not recorded (passive test).
- **Offline checks:** `pytest` 69 passed, 1 skipped (hardware); `ruff check`, `ruff format --check`, `mypy` (strict) clean.
- **Ran on the host:** `shokzer diagnose`, `shokzer diagnose --json`, `SHOKZ_HARDWARE=1 SHOKZ_DEVICE=… pytest tests/hardware` (1 passed), `diagnose --watch` for 3-4 s then SIGINT (exit 130, no traceback).
- **What went on the bus:** `GetNameOwner`, three `AddMatch` rules, one `GetManagedObjects`. Nothing was sent to the headset.

| Field | Observed |
|---|---|
| Selection | automatic, one candidate |
| Connection | connected, BR/EDR |
| Battery | 60 %, source `bluez:org.bluez.Battery1.Percentage` |
| Firmware, active EQ | unknown (no proprietary query) |
| Management / protocol | unresolved, session closed / unverified |
| Warnings | none |
| Exit code | 0 |

**Result:** pass. **Limits:** `--watch` subscribed on the real bus, but no BlueZ change happened during the run, so live signal handling is proven only by the offline tests. BlueZ restart and adapter removal weren't exercised on hardware.

## 2026-11-13 — protocol codecs, offline only

- **Commit:** `83747ff`.
- **Checks:** `pytest` 110 passed, 1 skipped; ruff and mypy clean.
- **Evidence:** our `FIRMWARE_GET` encoding matches Gadgetbridge's unit-test vector byte for byte. Reply decoding is tested against frames we built from the decoder's layout, not captured bytes.
- **Result:** offline pass. **Not hardware evidence.**

## 2026-11-13 — first management queries on Fedora (M3)

- **Commit:** `e559b79` (`shokzer status`). Offline before the run: `pytest` 151 passed, ruff and mypy clean.
- **Approval:** The maintainer approved M3 (read-only) and was at the computer. Battery and EQ reads were approved conditionally on a clean firmware reply.
- **Setup:** headset connected to Fedora only, on `a2dp-sink`, volume 0.12. Shokz app force-stopped on the phone. The maintainer reported the app showed EQ **Standard** beforehand.
- **Transport:** BlueZ profile API as planned: `RegisterProfile(0xFEF0, Role=client, AutoConnect=false, RequireAuthentication=true)`, then `Device1.ConnectProfile`. BlueZ resolved the RFCOMM channel from SDP and passed the socket in `NewConnection`. No channel number was chosen or probed. Cleanup: socket closed, `DisconnectProfile`, `UnregisterProfile`.
- **Traces:** `local/traces/2026-11-13-m3-1-firmware.jsonl`, `local/traces/2026-11-13-m3-2-all-reads.jsonl` (outside Git). The three reply frames are fixtures in `tests/fixtures/protocol_vectors.json` ("hardware_replies").

| Run | Sent | Channel open | Reply | Result |
|---|---|---|---|---|
| 1 | `FIRMWARE_GET` | 2.08 s | `FIRMWARE_RET` after 0.44 s: `BOS_HU_R_47_20251023` | Pass |
| 2 | `FIRMWARE_GET`, `BATTERY_GET`, `EQUALIZER_GET` | 0.53 s | Same firmware (0.12 s); battery level byte 5 → 60 % (0.05 s); EQ code 1 → Standard (0.05 s) | Pass |
| 3 | Opt-in `pytest tests/hardware` (passive diagnose + the same three reads), then `status --query firmware` | — | Both tests passed; firmware reported as verified | Pass |

| Check | Observed |
|---|---|
| Firmware vs app | Headset reports `BOS_HU_R_47_20251023`; the app shows `HU_R_47_20251023`. Same version; the app hides the `BOS_` prefix |
| Battery vs BlueZ | 60 % from both |
| EQ vs app | Standard from both |
| CRC, framing | Every reply had a valid CRC and frame length = payload length + 4 |
| Header flags | Replies `01 01 00 00`, requests `01 00 00 00`: byte 5 marks a reply |
| Unprompted messages | None |
| Audio | Still connected on `a2dp-sink` after each run. The maintainer noticed nothing (no beep, click, dropout or prompt) on runs 1 and 2 |

**Result:** M3 gate met: repeatable traced response from the selected headset, with bounded cleanup. **Limits:**
- The first channel open took 2.08 s, later ones about 0.5 s. Not explained (likely SDP caching).
- Playback wasn't running during the runs, so "no dropout" is about the link, not about audio under load.
- Not tested: the phone app connected at the same time, a disconnect mid-query, or BlueZ restarting during a session.

## 2026-11-13 — EQ writes, custom EQ, multipoint, GUI and packaging (offline only)

- **Commits:** `8f1d5ee` (EQ and multipoint commands), `911643b` (GTK app), and the packaging commit after it.
- **Checks:** `pytest` 215 passed (7 skipped: hardware and GUI opt-ins); ruff and mypy strict clean. GUI tests on a headless Broadway display: 5 passed (four rendered window states, plus one full app run against a simulated headset with clean shutdown). The same 215 tests pass with Fedora's `dbus-fast` 2.45.1 against the installed wheel.
- **Packaging:** wheel and sdist build; fresh install into a new venv runs `shokzer --version` and passive `diagnose`, and the GUI modules import. `desktop-file-validate` passes; `appstreamcli validate --no-net` passes with the missing homepage downgraded to info. The RPM isn't built yet: it needs `rpm-build` and the spec's build dependencies installed with sudo.
- **Not hardware evidence.** Nothing new was sent to the headset. The write behaviour is tested against `tests/sim_headset.py`, which models acknowledgements, slow changes, unapplied changes and lost channels; none of that is observed behaviour.
- **Review fixes** (commit after `fbdf641`): an independent review of the write path found no double sends and no stuck locks, and six issues that are now fixed. A non-zero acknowledgement status is judged by the readback. No acknowledgement plus the old value is reported as uncertain, not as not applied. Multipoint changes are refused while the status value is unknown. Connecting a peer needs multipoint on. Peer changes are refused when BlueZ doesn't report this computer's address. Profile and session cleanup always run, even after a failed connect or a broken trace file. The GUI's Custom option no longer writes. `pytest` 221 passed; GUI tests 5 passed.

## 2026-11-13 — hardware session A-G (writes, multipoint, robustness, GUI, RPM)

- **Commits during the session:** `14e6d78` at the start; fixes `83bd5d2` (fragment reassembly), `122cbd7` (verified features), `d82b3ee` (shutdown). Firmware `BOS_HU_R_47_20251023`, Fedora 44, BlueZ 5.87.
- **Setup:** maintainer present, music playing quietly from Fedora (volume 0.32), Shokz app force-stopped except in A8 and D1. Traces in `local/traces/2026-11-13-*.jsonl` (outside Git).

| Step | Result | Notes |
|---|---|---|
| A1 status | Pass | EQ Standard |
| A2 Vocal | Pass | Ack in 40 ms; readback `02 fa fe 04 05 01 00` matches the bytes sent; heard |
| A3 Standard | Pass | Sounded as before |
| A4 Bass Boost, back to Standard | Pass | Heard |
| A5 Treble Boost | Pass | Heard |
| A6 power cycle | Pass | Treble Boost persisted; channel reopened after reconnect |
| A7 GNOME reconnect | Skipped | A6 covered a reconnect |
| A8 phone app view | Pass | App showed Treble Boost set by shokzer |
| B custom EQ | Pass | Bands read back exactly. +2 too subtle to hear; at +5 band 1 is the low end, band 5 the high end. Restored Standard |
| C1 multipoint status | Pass after `83bd5d2` | First attempt timed out: the device list arrives in two fragments the decoder dropped. Reassembled, CRC valid; 3 devices |
| C2 off | Pass | Ack 0; the phone dropped, this computer stayed; headset sent a connection notice; status reads `0x00010000` (now the known off value) |
| C3 on | Pass | Confirmed `0x00010100` |
| C4 connect, disconnect, connect | Pass | Connect ack status `0x300`, connected after ~3.3 s; disconnect ack 0 |
| D1 app connected | Headset refuses | `br-connection-refused`, exit 3; one management connection at a time. Hint added |
| D2 headset off during reads | Pass | 80 reads over 45 s: 39 clean "isn't connected" (exit 3), no hang (longest gap 0.93 s), recovered after reconnect. No read happened to straddle the exact power-off |
| D3 CLI while GUI idle | Pass | |
| D4 two sessions | Pass | Second refused by the lock |
| D5 suspend/resume | Skipped | |
| D6 BlueZ restart | Pass | `diagnose --watch` showed every transition within 1 s; headset reconnected on its own after ~29 s |
| E GUI | Pass after `d82b3ee` | Values, EQ switching, connections, disconnect/reconnect all correct. Closing during a change once left a hidden process, and closing 30 ms into a connect left BlueZ answering `br-connection-busy` until a power cycle. Fixed; re-tested closing 10 and 30 ms into a change on the real headset: change completed and confirmed, app exited in 0.3-0.4 s, BlueZ fine |
| F RPM | Pass | Build deps from Fedora repos only (pulled Python 3.14.8 → 3.14.10). RPM `%check` 235 passed; desktop and AppStream valid. Installed; `/usr/bin/shokzer status` works on Fedora's dbus-fast 2.45; app in GNOME grid with icon, opens and works. Kept installed, so removal wasn't tested |
| G Android comparison | Skipped | Android build and app version not recorded |

**Result:** every built feature is now verified on this firmware (`capabilities.VERIFIED`). Offline suite: 235 passed.
