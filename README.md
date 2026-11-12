# shokzctl

Manage a Shokz OpenRun Pro 2 from Fedora over Bluetooth. Fedora's audio stack keeps handling playback and calls; shokzctl is for headset settings.

**Status:** passive diagnostics only. Protocol codecs exist and are tested offline against source evidence, but nothing talks to the headset yet: the first management query needs a controlled hardware test (see `docs/protocol-analysis.md`).

## Use

```sh
shokzctl diagnose            # what BlueZ knows about the headset
shokzctl diagnose --json     # same, as JSON (schema shokzctl.diagnose/1)
shokzctl diagnose --watch    # print again whenever something changes; Ctrl-C to stop
```

`diagnose` is passive. It reads BlueZ's state over the system D-Bus and sends nothing to the headset: no scanning, pairing, connecting, or headset queries. Battery comes from BlueZ's `Battery1` when the headset reports it there; otherwise it shows as unknown.

Output redacts addresses so it can be shared. `--show-identifiers` prints them in full. With more than one paired OpenRun Pro 2, pick one with `--device ADDRESS` (and `--adapter hciN` if it's known on several adapters).

| Exit | Meaning |
|---|---|
| 0 | Report printed |
| 2 | Invalid input |
| 3 | Unavailable: no system bus, BlueZ not running, no adapter, or headset not paired |
| 4 | Ambiguous selection |
| 5 | Permission denied |
| 6 | Unsupported feature |
| 7 | Timeout |
| 8 | Headset protocol error |
| 130 | Interrupted |

## Develop

Needs Fedora's Python 3 and [uv](https://docs.astral.sh/uv/). The venv uses system site packages so Fedora's PyGObject/GTK bindings stay visible for the later GUI.

```sh
uv venv --system-site-packages --python /usr/bin/python3 .venv
uv sync --group dev
.venv/bin/python -m pytest
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```

Tests are offline. Hardware tests run only on explicit opt-in:

```sh
SHOKZ_HARDWARE=1 SHOKZ_DEVICE=<address> .venv/bin/python -m pytest tests/hardware
```

Read `AGENTS.md` before changing anything that talks to the headset.

## License

AGPL-3.0-or-later. Protocol knowledge comes from [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge), which is AGPL-3.0; see `docs/protocol-analysis.md` for provenance.
