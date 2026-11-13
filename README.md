# shokzctl

Manage a Shokz OpenRun Pro 2 from Fedora over Bluetooth, from the command line or a GTK app. Fedora's audio stack keeps handling playback and calls; shokzctl is for headset settings.

**Status:** everything here is verified on firmware `BOS_HU_R_47_20251023` (2026-11-13, `docs/test-results.md`): reads, all four EQ presets, custom EQ, multipoint and the app. Other firmware versions need `--allow-unverified`, and the app greys those features out. The headset takes one management connection at a time, so close the Shokz phone app first.

## Use

```sh
shokzctl diagnose                  # what BlueZ knows about the headset; sends nothing to it
shokzctl diagnose --watch          # print again whenever something changes; Ctrl-C to stop
shokzctl status                    # read firmware, battery and EQ from the headset
shokzctl eq get
shokzctl eq set vocal              # standard, vocal, bass or treble
shokzctl eq custom -2 0 1 0 3      # five band gains, each -5 to 5
shokzctl multipoint status         # on/off and the headset's remembered devices
shokzctl multipoint on|off
shokzctl multipoint connect|disconnect ADDRESS
shokzctl-gui                       # the GTK app
```

Every command takes `--json`. Headset commands also take `--trace FILE` (every byte, as JSON lines; the repo ignores `local/`) and `--allow-unverified`.

`diagnose` is passive: it reads BlueZ's state over the system D-Bus and sends nothing to the headset. Everything else opens the headset's management channel (RFCOMM, service `0xFEF0`, through BlueZ's profile API). The headset must already be connected to Fedora, and the Shokz phone app should be closed so it doesn't hold the channel. One session runs at a time per user.

A change is reported as **confirmed** only when the headset reads the new value back. If it reads back the old value the change is **not applied**; if it can't be read back the outcome is **uncertain**, and you should read the current state before trying again. shokzctl never resends a change, never connects or pairs devices itself, and never disconnects this computer.

Output redacts addresses so it can be shared; `--show-identifiers` prints them in full. With more than one paired OpenRun Pro 2, pick one with `--device ADDRESS` (and `--adapter hciN` if needed).

| Exit | Meaning |
|---|---|
| 0 | Done (for a change: confirmed, or already set) |
| 2 | Invalid input |
| 3 | Unavailable: no system bus, BlueZ not running, no adapter, headset not paired or not connected |
| 4 | Ambiguous selection |
| 5 | Permission denied |
| 6 | Unsupported, or not verified on this firmware without `--allow-unverified` |
| 7 | Timeout |
| 8 | Headset protocol error |
| 9 | Change sent but its outcome is uncertain |
| 10 | Change not applied or refused by the headset |
| 130 | Interrupted |

## Develop

Needs Fedora's Python 3, PyGObject, GTK 4 and Libadwaita, plus [uv](https://docs.astral.sh/uv/). The venv uses system site packages so the GTK bindings are visible.

```sh
uv venv --system-site-packages --python /usr/bin/python3 .venv
uv sync --group dev
.venv/bin/python -m pytest
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```

Tests are offline. Two opt-in groups:

```sh
# Real headset (read-only queries)
SHOKZ_HARDWARE=1 SHOKZ_DEVICE=<address> .venv/bin/python -m pytest tests/hardware
# GUI rendering and a full app run against a simulated headset, on a headless display
gtk4-broadwayd :7 &
GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 SHOKZ_GUI=1 .venv/bin/python -m pytest tests/gui
```

`packaging/build-rpm.sh` builds a local RPM (see `docs/hardware-test-plan.md`, step F). Read `AGENTS.md` before changing anything that talks to the headset.

## License

AGPL-3.0-or-later. The protocol implementation is derived from [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) (AGPL-3.0); see `NOTICE`, `docs/licensing.md` and `docs/protocol-analysis.md`. Not affiliated with Shokz.
