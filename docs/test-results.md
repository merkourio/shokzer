# Test results

Each entry: date, application commit, what ran, outcome, and limits. Raw logs stay outside Git.

## 2026-11-13 — passive diagnose on Fedora (M2)

- **Commit:** `e3652e4`
- **Setup:** headset connected to Fedora on A2DP; playback and phone state not recorded (passive test).
- **Offline checks:** `pytest` 69 passed, 1 skipped (hardware); `ruff check`, `ruff format --check`, `mypy` (strict) clean.
- **Ran on the host:** `shokzctl diagnose`, `shokzctl diagnose --json`, `SHOKZ_HARDWARE=1 SHOKZ_DEVICE=… pytest tests/hardware` (1 passed), `diagnose --watch` for 3-4 s then SIGINT (exit 130, no traceback).
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

- **Commit:** `e559b79` (`shokzctl status`). Offline before the run: `pytest` 151 passed, ruff and mypy clean.
- **Approval:** Markos approved M3 (read-only) and was at the computer. Battery and EQ reads were approved conditionally on a clean firmware reply.
- **Setup:** headset connected to Fedora only, on `a2dp-sink`, volume 0.12. Shokz app force-stopped on the Pixel 10. Markos reported the app showed EQ **Standard** beforehand.
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
| Audio | Still connected on `a2dp-sink` after each run. Markos noticed nothing (no beep, click, dropout or prompt) on runs 1 and 2 |

**Result:** M3 gate met: repeatable traced response from the selected headset, with bounded cleanup. **Limits:**
- The first channel open took 2.08 s, later ones about 0.5 s. Not explained (likely SDP caching).
- Playback wasn't running during the runs, so "no dropout" is about the link, not about audio under load.
- Not tested: the phone app connected at the same time, a disconnect mid-query, or BlueZ restarting during a session.

## 2026-11-13 — EQ writes, custom EQ, multipoint, GUI and packaging (offline only)

- **Commits:** `8f1d5ee` (EQ and multipoint commands), `911643b` (GTK app), and the packaging commit after it.
- **Checks:** `pytest` 215 passed (7 skipped: hardware and GUI opt-ins); ruff and mypy strict clean. GUI tests on a headless Broadway display: 5 passed (four rendered window states, plus one full app run against a simulated headset with clean shutdown). The same 215 tests pass with Fedora's `dbus-fast` 2.45.1 against the installed wheel.
- **Packaging:** wheel and sdist build; fresh install into a new venv runs `shokzctl --version` and passive `diagnose`, and the GUI modules import. `desktop-file-validate` passes; `appstreamcli validate --no-net` passes with the missing homepage downgraded to info. The RPM isn't built yet: it needs `rpm-build` and the spec's build dependencies installed with sudo.
- **Not hardware evidence.** Nothing new was sent to the headset. The write behaviour is tested against `tests/sim_headset.py`, which models acknowledgements, slow changes, unapplied changes and lost channels; none of that is observed behaviour.
