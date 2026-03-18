"""Verde main application window."""

import logging

from gi.repository import Adw, Gio, Gtk

from verde.dbus_client import VerdeDBusClient
from verde.gpu_state import GPUState
from verde.views.dashboard import DashboardPage
from verde.views.diagnostics import DiagnosticsPage
from verde.views.drivers import DriversPage
from verde.views.power import PowerPage

log = logging.getLogger("verde.window")


def _get_settings() -> Gio.Settings | None:
    """Return GSettings for com.verde.app, or None if schema is not installed."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return None
    schema = source.lookup("com.verde.app", True)
    if schema is None:
        return None
    return Gio.Settings.new("com.verde.app")


def _has_ui_resource() -> bool:
    """Check if Blueprint-compiled UI template is available in GResource."""
    try:
        Gio.resources_lookup_data("/com/verde/app/ui/window.ui", Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


if _has_ui_resource():

    @Gtk.Template(resource_path="/com/verde/app/ui/window.ui")
    class VerdeWindow(Adw.ApplicationWindow):
        """Main window loaded from Blueprint template."""

        __gtype_name__ = "VerdeWindow"

        view_switcher = Gtk.Template.Child()
        view_stack = Gtk.Template.Child()
        bottom_bar = Gtk.Template.Child()
        banner = Gtk.Template.Child()

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._settings = _get_settings()
            self._restore_window_size()
            self.view_switcher.connect(
                "notify::title-visible",
                lambda sw, _: self.bottom_bar.set_reveal(sw.get_title_visible()),
            )
            self._load_css()

        def _restore_window_size(self):
            if self._settings is not None:
                width, height = self._settings.get_value("window-size").unpack()
                self.set_default_size(max(width, 400), max(height, 300))
            else:
                self.set_default_size(800, 600)

        def do_close_request(self):
            if self._settings is not None:
                from gi.repository import GLib

                width = self.get_width()
                height = self.get_height()
                self._settings.set_value("window-size", GLib.Variant("(ii)", (width, height)))
            return super().do_close_request()

        def _load_css(self):
            css_provider = Gtk.CssProvider()
            css_provider.load_from_resource("/com/verde/app/style.css")
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

else:

    class VerdeWindow(Adw.ApplicationWindow):  # type: ignore[no-redef]
        """Main window built programmatically (blueprint-compiler not available)."""

        __gtype_name__ = "VerdeWindow"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._settings = _get_settings()
            if self._settings is not None:
                width, height = self._settings.get_value("window-size").unpack()
                self.set_default_size(max(width, 400), max(height, 300))
            else:
                self.set_default_size(800, 600)
            self.set_title("Verde")

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.set_content(box)

            header = Adw.HeaderBar()
            self.view_switcher = Adw.ViewSwitcher()
            self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(self.view_switcher)
            box.append(header)

            self.banner = Adw.Banner()
            self.banner.set_revealed(False)
            box.append(self.banner)

            self.view_stack = Adw.ViewStack()
            self.view_switcher.set_stack(self.view_stack)

            self.bottom_bar = Adw.ViewSwitcherBar()
            self.bottom_bar.set_stack(self.view_stack)
            self.view_switcher.connect(
                "notify::title-visible",
                lambda sw, _: self.bottom_bar.set_reveal(sw.get_title_visible()),
            )

            pages = [
                ("dashboard", "Dashboard", "speedometer-symbolic", DashboardPage),
                ("drivers", "Drivers", "application-x-firmware-symbolic", DriversPage),
                ("power", "Power", "battery-symbolic", PowerPage),
                (
                    "diagnostics",
                    "Diagnostics",
                    "utilities-system-monitor-symbolic",
                    DiagnosticsPage,
                ),
            ]
            for name, title, icon, page_class in pages:
                page = page_class()
                self.view_stack.add_titled_with_icon(page, name, title, icon)

            box.append(self.view_stack)
            box.append(self.bottom_bar)

            css_provider = Gtk.CssProvider()
            css_provider.load_from_resource("/com/verde/app/style.css")
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        def do_close_request(self):
            if self._settings is not None:
                from gi.repository import GLib

                width = self.get_width()
                height = self.get_height()
                self._settings.set_value("window-size", GLib.Variant("(ii)", (width, height)))
            return super().do_close_request()


class VerdeApplication(Adw.Application):
    """Verde GTK application."""

    def __init__(self, application_id: str, version: str, **kwargs):
        super().__init__(application_id=application_id, **kwargs)
        self._version = version
        self.gpu_state = GPUState()
        self.dbus_client = VerdeDBusClient(gpu_state=self.gpu_state)
        self.connect("activate", self._on_activate)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

    def _on_activate(self, _app):
        win = self.props.active_window
        if not win:
            win = VerdeWindow(application=self)
        win.present()
        self.dbus_client.connect_async()

    def do_shutdown(self):
        """Clean up D-Bus connection on application shutdown."""
        self.dbus_client.close()
        Adw.Application.do_shutdown(self)
