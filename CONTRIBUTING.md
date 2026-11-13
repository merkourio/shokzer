# Contributing

Firmware reports, bug reports and pull requests are all welcome. This is a hobby project without active development, so there's no promise of a reply, a review or a merge; I'll get to things when I get to them. Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Firmware reports

If you have a firmware version that isn't in `VERIFIED` (`src/shokzer/capabilities.py`), [file a firmware report](https://github.com/merkourio/shokzer/issues/new?template=firmware-report.yml) saying what you tried and what worked. Once a version is confirmed, it's added to that table and stops showing the "untested" notice.

## Development setup

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and for the app your system's PyGObject 3.50+, GTK 4 and libadwaita 1.4+. Create the venv with system site packages, so it sees the system's GTK bindings:

```sh
uv venv --system-site-packages --python /usr/bin/python3 .venv
uv sync --group dev
.venv/bin/shokzer status
.venv/bin/shokzer-gui
```

If uv rebuilds the venv later (for example after a Python update), recreate it this way again; otherwise the app can't import `gi`.

## Checks

```sh
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest
```

Tests run offline against a simulated headset. Two groups are opt-in:

```sh
# The real headset: read-only queries plus passive diagnostics
SHOKZ_HARDWARE=1 SHOKZ_DEVICE=<address> .venv/bin/python -m pytest tests/hardware

# The app, on a headless display (nothing appears on screen)
gtk4-broadwayd :7 &
GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 SHOKZ_GUI=1 .venv/bin/python -m pytest tests/gui
```

CI runs all of this, plus an RPM build on Fedora.

## Changing what's sent to the headset

Read [AGENTS.md](AGENTS.md) first. In short:

- Every byte sent needs evidence: a source reference (with commit, file and lines) or a capture, recorded in `docs/protocol-analysis.md`. No guessed commands or probing.
- A write reads the current value first, sends once, and is confirmed only by reading it back. Never resend a write.
- Test new hardware behaviour at low volume, restore the original setting, and record the result in `docs/test-results.md`.

## Pull requests

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Keep each pull request to one change, with tests, and fill in the checklist in the template.
- Add user-facing changes to `CHANGELOG.md` under `## Unreleased`.
- CI has to pass before merging. Pull requests are squash-merged, so the title becomes the commit message: write it as what the change does.
- Never commit real device addresses, names, raw captures or logs (see AGENTS.md).

## Licence

Shokzer is licensed under AGPL-3.0-or-later. By submitting a contribution, you agree that it's licensed under the same terms. You keep your copyright, and there's no CLA to sign. Only submit work you have the right to license this way. If part of it comes from other code, say where from and check that its licence is compatible (see [docs/licensing.md](docs/licensing.md)).

## Releasing

See [docs/releasing.md](docs/releasing.md).
