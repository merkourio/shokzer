# Licensing

Reviewed 2026-11-13 from the actual licence files and package metadata. Not legal advice.

## shokzctl

AGPL-3.0-or-later (`LICENSE`, the FSF text).

The protocol codecs translate behaviour from Gadgetbridge, which is AGPL-3.0 (`LICENSE` at commit `37f76243b7e7`). No Gadgetbridge source files are copied, but the frame layout, command table, EQ argument bytes and reply parsing follow its Kotlin closely, so shokzctl is treated as a derived work and keeps the same licence. `NOTICE` gives the attribution. Anyone who receives a build, including an RPM, is entitled to the corresponding source.

## Runtime dependencies

| Component | Licence | How it's used |
|---|---|---|
| dbus-fast | MIT | Python dependency (PyPI wheel in development, `python3-dbus-fast` in the RPM) |
| PyGObject | LGPL-2.1-or-later | System package, GUI only |
| GTK 4 | LGPL-2.0-or-later AND LGPL-2.1-or-later AND others (Fedora `gtk4` package) | System library, GUI only |
| libadwaita | LGPL-2.1-or-later AND MIT | System library, GUI only |
| GLib | LGPL-2.1-or-later | System library |
| BlueZ | GPL-2.0-or-later | Separate system service, talked to over D-Bus only |

All are compatible with AGPL-3.0-or-later for local use and distribution. Nothing is bundled: the wheel contains only shokzctl's own code, and the RPM depends on Fedora's packages.

## Development-only tools

pytest (MIT), pytest-asyncio (Apache-2.0), Ruff (MIT), mypy (MIT), hatchling (MIT). Not shipped.

## Before any public release

- Pick a real application ID; `org.example.Shokzctl` is a placeholder.
- Keep `NOTICE` and the Gadgetbridge commit reference in source archives.
- Recheck these licences against the versions actually shipped.
