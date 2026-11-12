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

- **Commit:** see the commit that adds `src/shokzctl/protocol/`.
- **Checks:** `pytest` 110 passed, 1 skipped; ruff and mypy clean.
- **Evidence:** our `FIRMWARE_GET` encoding matches Gadgetbridge's unit-test vector byte for byte. Reply decoding is tested against frames we built from the decoder's layout, not captured bytes.
- **Result:** offline pass. **Not hardware evidence.**
