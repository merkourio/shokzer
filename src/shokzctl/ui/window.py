"""The main window. Renders ViewState; every action goes through the Controller.

GTK runs on the GLib main loop, which also runs asyncio (gi.events.GLibEventLoopPolicy), so
Bluetooth I/O happens in tasks on the same thread and never blocks drawing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from ..output import redact_address  # noqa: E402
from ..protocol.commands import CUSTOM_BANDS, CUSTOM_GAIN_MAX, CUSTOM_GAIN_MIN, EqPreset  # noqa: E402
from ..service import multipoint_label  # noqa: E402
from ..state import Connection  # noqa: E402
from .controller import PRESET_LABELS, Controller, ViewState  # noqa: E402

WINDOW_TITLE = "OpenRun Pro 2"
_PRESETS = (EqPreset.STANDARD, EqPreset.VOCAL, EqPreset.BASS, EqPreset.TREBLE, EqPreset.CUSTOM)
_FEATURE = {
    EqPreset.STANDARD: "eq_write_standard",
    EqPreset.VOCAL: "eq_write_vocal",
    EqPreset.BASS: "eq_write_bass",
    EqPreset.TREBLE: "eq_write_treble",
    EqPreset.CUSTOM: "eq_write_custom",
}


class MainWindow(Adw.ApplicationWindow):  # type: ignore[misc]
    def __init__(self, application: Adw.Application, controller: Controller) -> None:
        super().__init__(application=application, title=WINDOW_TITLE)
        self.set_default_size(460, 760)
        self._controller = controller
        self._rendering = False
        self._shown_message = 0
        self._custom_source: tuple[int, ...] | None = None
        self._closing = False
        self._build()
        controller.on_change = self.render
        self.connect("close-request", self._on_close_request)
        self.render(controller.state)

    # --- layout ------------------------------------------------------------------------------

    def _build(self) -> None:
        self._toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._title = Adw.WindowTitle(title=WINDOW_TITLE)
        header.set_title_widget(self._title)
        self._refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh.set_tooltip_text("Read from the headset")
        self._refresh.update_property([Gtk.AccessibleProperty.LABEL], ["Read from the headset"])
        self._refresh.connect("clicked", lambda *_: self._run(self._controller.refresh()))
        header.pack_start(self._refresh)
        self._spinner = Adw.Spinner()
        header.pack_end(self._spinner)
        view.add_top_bar(header)

        self._stack = Gtk.Stack()
        self._missing = Adw.StatusPage(icon_name="audio-headset-symbolic")
        self._stack.add_named(self._missing, "missing")
        page = Adw.PreferencesPage()
        self._stack.add_named(page, "headset")
        view.set_content(self._stack)
        self._toasts.set_child(view)
        self.set_content(self._toasts)

        device = Adw.PreferencesGroup(title="Device")
        self._bluetooth_row = _property_row("Bluetooth")
        self._battery_row = _property_row("Battery")
        self._firmware_row = _property_row("Firmware")
        for row in (self._bluetooth_row, self._battery_row, self._firmware_row):
            device.add(row)
        page.add(device)

        self._eq_group = Adw.PreferencesGroup(title="Equalizer")
        self._preset_checks: dict[EqPreset, Gtk.CheckButton] = {}
        self._preset_rows: dict[EqPreset, Adw.ActionRow] = {}
        self._preset_spinners: dict[EqPreset, Adw.Spinner] = {}
        first: Gtk.CheckButton | None = None
        for preset in _PRESETS:
            label = PRESET_LABELS[preset]
            check = Gtk.CheckButton()
            check.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} EQ"])
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.connect("toggled", self._on_preset_toggled, preset)
            row = Adw.ActionRow(title=label)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            spinner = Adw.Spinner(visible=False)
            row.add_suffix(spinner)
            self._eq_group.add(row)
            self._preset_checks[preset] = check
            self._preset_rows[preset] = row
            self._preset_spinners[preset] = spinner
        page.add(self._eq_group)

        self._custom_group = Adw.PreferencesGroup(
            title="Custom EQ",
            description="Five bands, each from -5 to 5. Band frequencies aren't published.",
        )
        self._apply = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        self._apply.add_css_class("suggested-action")
        self._apply.connect("clicked", self._on_apply_custom)
        self._custom_group.set_header_suffix(self._apply)
        self._bands: list[Adw.SpinRow] = []
        for index in range(CUSTOM_BANDS):
            band = Adw.SpinRow.new_with_range(CUSTOM_GAIN_MIN, CUSTOM_GAIN_MAX, 1)
            band.set_title(f"Band {index + 1}")
            band.set_digits(0)
            band.set_value(0)
            self._custom_group.add(band)
            self._bands.append(band)
        page.add(self._custom_group)

        self._connections = Adw.PreferencesGroup(title="Connections")
        self._multipoint_row = Adw.SwitchRow(title="Multipoint")
        self._multipoint_row.connect("notify::active", self._on_multipoint_toggled)
        self._connections.add(self._multipoint_row)
        self._peer_rows: list[Adw.ActionRow] = []
        page.add(self._connections)

    # --- rendering ---------------------------------------------------------------------------

    def render(self, state: ViewState) -> None:
        self._rendering = True
        try:
            self._render(state)
        finally:
            self._rendering = False

    def _render(self, state: ViewState) -> None:
        busy = state.busy is not None
        self._spinner.set_visible(busy)
        self._title.set_subtitle(state.busy or "")
        self._refresh.set_sensitive(state.connected and not busy)

        if state.report is None:
            self._missing.set_title("No headset")
            self._missing.set_description(
                state.bluez_error or "Pair the OpenRun Pro 2 in GNOME Settings."
            )
            self._stack.set_visible_child_name("missing")
        else:
            self._stack.set_visible_child_name("headset")
            self._render_device(state)
            self._render_eq(state)
            self._render_connections(state)

        if state.message and state.message_id != self._shown_message:
            self._shown_message = state.message_id
            self._toasts.add_toast(Adw.Toast(title=state.message, timeout=4))

    def _render_device(self, state: ViewState) -> None:
        assert state.report is not None
        device = state.report.device
        self._bluetooth_row.set_subtitle(
            {
                Connection.CONNECTED: "Connected",
                Connection.DISCONNECTED: "Disconnected",
                Connection.UNKNOWN: "Unknown",
            }[device.connection]
        )
        if state.headset_battery is not None:
            battery = f"{state.headset_battery} %"
        elif device.battery.known:
            battery = f"{device.battery.value} %"
        else:
            battery = "Unknown"
        self._battery_row.set_subtitle(battery)
        self._firmware_row.set_subtitle(state.firmware or "Unknown")

    def _render_eq(self, state: ViewState) -> None:
        idle = state.connected and state.busy is None
        current = state.eq.preset if state.eq is not None else None
        writable = [p for p in _PRESETS if state.can(_FEATURE[p])]
        if state.eq is None:
            self._eq_group.set_description("Unknown until read from the headset.")
        elif not writable:
            self._eq_group.set_description(
                "Changing the EQ isn't verified on this firmware yet. "
                "Test it from the command line with shokzctl eq set --allow-unverified."
            )
        else:
            self._eq_group.set_description(None)
        for preset in _PRESETS:
            check = self._preset_checks[preset]
            check.set_active(preset is current)
            can_write = preset in writable
            pending = state.pending == f"eq:{preset.name.lower()}"
            row = self._preset_rows[preset]
            row.set_sensitive(idle and can_write)
            subtitle = None
            if pending:
                subtitle = "Changing…"
            elif preset is EqPreset.CUSTOM and state.eq is not None and state.eq.bands:
                subtitle = " ".join(f"{b:+d}" if b else "0" for b in state.eq.bands)
            elif state.firmware is not None and not can_write:
                subtitle = "Not verified on this firmware"
            row.set_subtitle(subtitle or "")
            self._preset_spinners[preset].set_visible(pending)
        if state.eq is not None and state.eq.code not in {p.value for p in _PRESETS}:
            self._eq_group.set_description(
                f"The headset reports an unknown EQ (0x{state.eq.code:02x})."
            )

        can_custom = state.can("eq_write_custom")
        self._custom_group.set_visible(can_custom)
        self._custom_group.set_sensitive(idle and can_custom)
        bands = state.eq.bands if state.eq is not None and state.eq.bands else None
        if bands is not None and bands != self._custom_source:
            self._custom_source = bands  # follow the headset, but don't clobber edits otherwise
            for row, value in zip(self._bands, bands, strict=True):
                row.set_value(value)

    def _render_connections(self, state: ViewState) -> None:
        multipoint = state.multipoint
        self._connections.set_visible(state.can("multipoint_read") and multipoint is not None)
        if multipoint is None or state.report is None:
            return
        idle = state.connected and state.busy is None
        self._multipoint_row.set_active(multipoint.status.enabled is True)
        self._multipoint_row.set_subtitle(
            "" if multipoint.status.enabled is not None else multipoint_label(multipoint.status)
        )
        self._multipoint_row.set_sensitive(idle and state.can("multipoint_write"))
        for row in self._peer_rows:
            self._connections.remove(row)
        self._peer_rows = []
        own = state.report.adapter.address
        for peer in multipoint.devices.peers:
            this_computer = peer.address == own
            details = [
                redact_address(peer.address) or "",
                "Connected" if peer.connected else "Not connected",
            ]
            if this_computer:
                details.append("This computer")
            row = Adw.ActionRow(title=peer.name, subtitle=" · ".join(details))
            if state.can("multipoint_peers") and not this_computer:
                button = Gtk.Button(
                    label="Disconnect" if peer.connected else "Connect",
                    valign=Gtk.Align.CENTER,
                    sensitive=idle,
                )
                button.connect(
                    "clicked",
                    lambda _b, a=peer.address, c=not peer.connected: self._run(
                        self._controller.set_peer(a, c)
                    ),
                )
                row.add_suffix(button)
            self._connections.add(row)
            self._peer_rows.append(row)

    # --- events ------------------------------------------------------------------------------

    def _on_preset_toggled(self, check: Gtk.CheckButton, preset: EqPreset) -> None:
        if self._rendering or not check.get_active():
            return
        if preset is EqPreset.CUSTOM:
            self._on_apply_custom()
            return
        self._run(self._controller.set_preset(preset))

    def _on_apply_custom(self, *_: Any) -> None:
        if self._rendering:
            return
        values = [int(row.get_value()) for row in self._bands]
        self._run(self._controller.set_custom(values))

    def _on_multipoint_toggled(self, row: Adw.SwitchRow, _param: Any) -> None:
        if self._rendering:
            return
        self._run(self._controller.set_multipoint(row.get_active()))

    def _on_close_request(self, _window: Any) -> bool:
        if self._closing:
            return False
        self._closing = True
        self.set_visible(False)

        async def shut_down() -> None:
            await self._controller.close()
            self.close()

        self._shutdown = asyncio.ensure_future(shut_down())
        return True  # stay alive until sessions are closed and handed back to BlueZ

    def _run(self, action: Any) -> None:
        self._controller.spawn(action)


def _property_row(title: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle="Unknown")
    row.add_css_class("property")
    return row
