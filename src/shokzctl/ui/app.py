"""Entry point for the GTK app (`shokzctl-gui`)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gio  # noqa: E402

from .. import APP_ID  # noqa: E402
from ..transport.bluez import connect_system_bus  # noqa: E402
from ..transport.management import open_connector  # noqa: E402
from .controller import Controller  # noqa: E402
from .window import MainWindow  # noqa: E402


def _real_controller() -> Controller:
    return Controller(connect_bus=connect_system_bus, open_connector=open_connector)


class Application(Adw.Application):  # type: ignore[misc]
    def __init__(
        self,
        controller_factory: Callable[[], Controller] = _real_controller,
        *,
        application_id: str = APP_ID,
    ) -> None:
        super().__init__(application_id=application_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._controller_factory = controller_factory
        self.controller: Controller | None = None

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            self.controller = self._controller_factory()
            window = MainWindow(self, self.controller)
            self.controller.spawn(self.controller.start())
        window.present()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="shokzctl-gui: %(levelname)s %(message)s")
    # Runs asyncio on GLib's main loop, so dbus-fast and GTK share one thread and loop.
    with GLibEventLoopPolicy():
        status: int = Application().run(argv if argv is not None else sys.argv)
    return status


if __name__ == "__main__":
    sys.exit(main())
