# Working on Shokzer

Guidance for contributors and coding agents. Shokzer changes settings on a Shokz OpenRun Pro 2 over Bluetooth; the system audio stack keeps playback and calls, and Shokzer never touches them. Setup and checks are in `CONTRIBUTING.md`.

## Safety rules

- **Passive by default.** `diagnose` only reads BlueZ state over D-Bus: no discovery scans, pairing, connect/disconnect, or headset queries.
- **Evidence before bytes.** Every command sent to the headset needs a row in `docs/protocol-analysis.md` with its source (pinned commit, file, lines) or a capture. No guessed opcodes, channels or recovery packets, and no probing.
- **A person approves new hardware behaviour.** Before the first run of a new query or write on a real headset, describe the exact bytes and their evidence to whoever owns the headset and get their approval. Test at low volume, restore the original setting, and stop on unexplained replies, resets, loud sound or audio dropouts.
- **Sent is not accepted.** Track requested and confirmed state separately. A write is confirmed only by reading it back; after an uncertain write, read before anything else. Never resend a write.
- **Never abandon a BlueZ `ConnectProfile`.** BlueZ stays busy until it answers (see `docs/protocol-analysis.md`). Let a session finish rather than cancel it.
- **Unknown stays unknown.** Missing battery is unknown, not zero. A device name makes a device a candidate, not a verified model; other models are refused.
- Run unprivileged: no sudo, BlueZ policy changes or root.

## Tests

- Ordinary tests are offline. `tests/conftest.py` blocks real D-Bus connections outside hardware tests. `tests/sim_headset.py` simulates the headset, including its failure cases and fragmented replies.
- GUI tests (`tests/gui`, marker `gui`) run only with `SHOKZ_GUI=1` and a display; use Broadway so nothing appears on screen.
- Hardware tests are marked `hardware` and run only with both `SHOKZ_HARDWARE=1` and `SHOKZ_DEVICE=<address>`. Collection never touches hardware. Nothing in `tests/hardware` may write.
- Fixtures come from evidence (source-derived or sanitized captures) and say where they came from.

## Evidence that stays out of Git

`reference/` (upstream checkouts), `local/` (traces, screenshots), raw captures, logs, and real device addresses or names. Commit only reviewed, redacted excerpts and fixtures.

## Layout

- `src/shokzer/transport/bluez.py`: BlueZ object tree, signal handling, and the dbus-fast connection.
- `src/shokzer/transport/management.py`: BlueZ profile API connector for the RFCOMM management channel.
- `src/shokzer/protocol/`: pure frame (including fragment reassembly), command and reply codecs. No I/O.
- `src/shokzer/session.py`: one request in flight, replies matched on group and code, no resends.
- `src/shokzer/service.py`: every headset operation, shared by CLI and GUI. Writes read first, send once, then read back.
- `src/shokzer/capabilities.py`: model check, and which firmware each feature was tested on.
- `src/shokzer/device.py`, `state.py`, `output.py`, `errors.py`, `cli.py`: selection, state contract, formatting, exit codes, commands.
- `src/shokzer/ui/`: `controller.py` (no GTK, tested offline), `window.py`, `app.py`; `__init__.py` checks the GTK stack.
- `src/shokzer/lock.py`, `trace.py`: one session per user; JSON-lines byte traces.
- `docs/`: protocol analysis, test results, hardware matrix and test plan, licensing, releasing.
- `data/`, `packaging/`, `.copr/`, `install.sh`, `.github/`: desktop file, AppStream metadata, icon, RPM spec, COPR build, installer, CI and release workflows.
