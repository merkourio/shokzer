"""GTK4/Libadwaita interface. Only window.py and app.py import GTK.

`main` checks the system GTK stack first, so a missing or too-old piece gets a clear message
instead of a traceback.
"""

from __future__ import annotations

import sys

MIN_ADWAITA = (1, 4)  # ToolbarView, SpinRow, SwitchRow


def main() -> int:
    problem = missing_requirement()
    if problem is not None:
        print(f"shokzer-gui: {problem}", file=sys.stderr)
        return 1
    from .app import main as run_app

    return run_app()


def missing_requirement() -> str | None:
    try:
        import gi
    except ImportError:
        return (
            "PyGObject isn't installed. Install python3-gobject (Fedora), python3-gi "
            "(Debian/Ubuntu) or python-gobject (Arch)."
        )
    for namespace, version, name in (("Gtk", "4.0", "GTK 4"), ("Adw", "1", "libadwaita")):
        try:
            gi.require_version(namespace, version)
        except ValueError:
            return f"{name} isn't installed, or its GObject introspection data is missing."
    try:
        import gi.events  # PyGObject 3.50+: runs asyncio on the GLib loop
    except ImportError:
        return "PyGObject 3.50 or newer is needed for the app. The shokzer command still works."
    from gi.repository import Adw

    if (Adw.get_major_version(), Adw.get_minor_version()) < MIN_ADWAITA:
        return "libadwaita 1.4 or newer is needed for the app. The shokzer command still works."
    return None
