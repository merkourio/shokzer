# Working on shokzer

shokzer manages settings on a Shokz OpenRun Pro 2 from Fedora. Fedora's audio stack keeps playback and calls; this tool never touches them.

## Safety rules

- **Passive by default.** `diagnose` only reads BlueZ state over D-Bus. No discovery scans, pairing, connect/disconnect, GATT probing, proprietary sockets, or protocol queries.
- **Evidence before bytes.** Every proprietary command needs a row in `docs/protocol-analysis.md` with the pinned reference commit, path, symbol, and lines. No guessed opcodes, channels, or recovery packets. No blind probing.
- **Hardware actions need Markos.** Before the first run of any proprietary query or write, describe the exact bytes and their evidence and get approval. Run hardware work at low volume, restore the original setting, and stop on unexplained responses, resets, loudness, or audio disruption.
- **Sent is not accepted.** Track requested and confirmed state separately. After an uncertain write, query before any retry. Never replay a non-idempotent write.
- **Unknown stays unknown.** Missing battery is unknown, not zero. A friendly name makes a device a candidate, not a verified model.
- Run unprivileged. No sudo, BlueZ policy changes, or root execution without asking.

## Tests

- Ordinary tests are offline. `tests/conftest.py` blocks real D-Bus connections outside hardware tests. `tests/sim_headset.py` simulates write behaviour, including the failure cases.
- GUI tests (`tests/gui`, marker `gui`) run only with `SHOKZ_GUI=1` and a display; use Broadway so nothing appears on screen.
- Hardware tests are marked `hardware` and run only with both `SHOKZ_HARDWARE=1` and `SHOKZ_DEVICE=<address>`. Collection never touches hardware. `tests/hardware/test_status_read.py` sends the three verified read-only queries; nothing in `tests/hardware` may write.
- Fixtures come from evidence (source-derived or sanitized captures), labelled with their provenance. Never use packet examples from older OpenRun tools.

## Checks

On the host, through `ht` (from Flatpak sandboxes) or directly:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```

## Evidence that stays out of Git

`reference/` (upstream checkouts), raw captures, bug reports, logs, and device addresses. Commit only reviewed, redacted excerpts and fixtures.

## Layout

- `src/shokzer/transport/bluez.py`: BlueZ object tree, signal handling, and the dbus-fast connection.
- `src/shokzer/device.py`: candidate selection and the diagnose report.
- `src/shokzer/state.py`: state contract shared by the CLI and the GUI.
- `src/shokzer/output.py`: readable and JSON formatters, redaction.
- `src/shokzer/errors.py`: error categories and exit codes.
- `src/shokzer/protocol/`: pure frame, command and reply codecs. No I/O.
- `src/shokzer/transport/management.py`: BlueZ profile API connector for the RFCOMM management channel.
- `src/shokzer/session.py`: one request in flight, replies matched on group and code, no resends.
- `src/shokzer/service.py`: every headset operation, shared by CLI and GUI. Writes read first, send once, then read back.
- `src/shokzer/capabilities.py`: what's verified per firmware. Add a feature only after its hardware test passes.
- `src/shokzer/ui/`: `controller.py` (no GTK, tested offline), `window.py`, `app.py`.
- `src/shokzer/lock.py`, `trace.py`: one session per user; JSON-lines byte traces.
- `docs/`: protocol analysis, hardware test plan, hardware matrix, test results, licensing.
- `data/`, `packaging/`: desktop file, AppStream metadata, icon, RPM spec. `APP_ID` in `__init__.py` is a placeholder.
