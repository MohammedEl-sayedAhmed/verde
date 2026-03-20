"""Verde main application window."""

import logging

from gi.repository import Adw, Gio, GLib, Gtk

from verde.dbus_client import VerdeDBusClient
from verde.gpu_state import GPUState
from verde.views.dashboard import DashboardPage
from verde.views.diagnostics import DiagnosticsPage
from verde.views.drivers import DriversPage
from verde.views.power import PowerPage

log = logging.getLogger("verde.window")

# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]


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
        # toast_overlay is only in the programmatic path; None here so
        # hasattr checks work and toasts are silently skipped in Blueprint path.
        toast_overlay: Adw.ToastOverlay | None = None

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
            return False

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

            self.toast_overlay = Adw.ToastOverlay()
            self.toast_overlay.set_child(self.view_stack)
            box.append(self.toast_overlay)
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
            return False


class VerdeApplication(Adw.Application):
    """Verde GTK application."""

    def __init__(self, application_id: str, version: str, **kwargs):
        super().__init__(application_id=application_id, **kwargs)
        self._version = version
        self._gpu_lost_dialog_shown = False
        self._post_reboot_checked = False
        self.gpu_state = GPUState()
        self.dbus_client = VerdeDBusClient(gpu_state=self.gpu_state)
        self.dbus_client.connect("notify::connected", self._on_dbus_connected)
        self.connect("activate", self._on_activate)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

    def _on_activate(self, _app):
        win = self.props.active_window
        if not win:
            win = VerdeWindow(application=self)
            dashboard = win.view_stack.get_child_by_name("dashboard")
            if hasattr(dashboard, "bind_state"):
                dashboard.bind_state(self.gpu_state, self.dbus_client)
            drivers = win.view_stack.get_child_by_name("drivers")
            if hasattr(drivers, "bind_state"):
                drivers.bind_state(self.gpu_state, self.dbus_client)
            power = win.view_stack.get_child_by_name("power")
            if hasattr(power, "bind_state"):
                power.bind_state(self.gpu_state, self.dbus_client)
            diagnostics = win.view_stack.get_child_by_name("diagnostics")
            if hasattr(diagnostics, "bind_state"):
                diagnostics.bind_state(self.gpu_state, self.dbus_client)
            self.gpu_state.connect("notify::degraded-state", self._on_degraded_state, win)
            self.dbus_client.connect(
                "external-changes-detected",
                self._on_external_changes,
                win,
            )
        win.present()
        self.dbus_client.connect_async()

    def _on_degraded_state(self, gpu_state: GPUState, _pspec, win: Adw.ApplicationWindow) -> None:
        """Show GPU-lost dialog when degraded state transitions to gpu_lost."""
        if gpu_state.get_property("degraded-state") == "gpu_lost":
            if self._gpu_lost_dialog_shown:
                return
            self._gpu_lost_dialog_shown = True
            dashboard = win.view_stack.get_child_by_name("dashboard")
            if hasattr(dashboard, "show_gpu_lost_dialog"):
                dashboard.show_gpu_lost_dialog(win)
        else:
            # Reset guard when leaving gpu_lost state
            self._gpu_lost_dialog_shown = False

    def _on_external_changes(
        self,
        _client,
        changes: list,
        integrity: list,
        win: Adw.ApplicationWindow,
    ) -> None:
        """Show a banner when external changes are detected (Story 6.1)."""
        if not changes and not integrity:
            return
        msgs = []
        for ch in changes:
            field = ch.get("field", "")
            old = ch.get("old_value", "")
            new = ch.get("new_value", "")
            if field == "driver_version":
                msgs.append(
                    _("NVIDIA driver changed from {old} to {new} outside of Verde").format(
                        old=old, new=new
                    )
                )
            elif field == "driver_type":
                msgs.append(_("Driver type changed from {old} to {new}").format(old=old, new=new))
            elif field == "kernel_version":
                msgs.append(_("Kernel updated from {old} to {new}").format(old=old, new=new))
        for iss in integrity:
            path = iss.get("file_path", "")
            issue = iss.get("issue_type", "")
            if issue == "deleted":
                msgs.append(_("Config file {path} was deleted").format(path=path))
            elif issue == "modified":
                msgs.append(_("Config file {path} was modified externally").format(path=path))
            elif issue == "created_externally":
                msgs.append(_("Config file {path} was created externally").format(path=path))
        if msgs:
            win.banner.set_title("; ".join(msgs))
            win.banner.set_button_label(_("Dismiss"))
            win.banner.set_revealed(True)
            if not getattr(win, "_banner_dismiss_connected", False):
                win.banner.connect("button-clicked", lambda b: b.set_revealed(False))
                win._banner_dismiss_connected = True

    def _on_dbus_connected(self, client, _pspec) -> None:
        """Query post-reboot summary once when D-Bus connects (Story 3.5)."""
        if not client.get_property("connected"):
            return
        if self._post_reboot_checked:
            return
        self._post_reboot_checked = True
        client.call_method_async(
            "GetPostRebootSummary",
            None,
            self._on_post_reboot_summary_reply,
        )

    def _on_post_reboot_summary_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        """Handle GetPostRebootSummary reply."""
        try:
            reply = proxy.call_finish(result)
        except GLib.Error as exc:
            log.warning("GetPostRebootSummary failed: %s", exc.message)
            return

        summary_variant = reply.get_child_value(0)
        has_pending = summary_variant.lookup_value("has_pending", GLib.VariantType("b"))
        if has_pending is None or not has_pending.get_boolean():
            return

        # Extract summary fields
        def _get_str(key: str) -> str:
            v = summary_variant.lookup_value(key, GLib.VariantType("s"))
            return v.get_string() if v else ""

        def _get_bool(key: str) -> bool:
            v = summary_variant.lookup_value(key, GLib.VariantType("b"))
            return v.get_boolean() if v else False

        result_str = _get_str("result")
        message = _get_str("message")
        guidance = _get_str("recovery_guidance")

        GLib.idle_add(
            self._show_post_reboot_dialog,
            result_str,
            message,
            guidance,
        )

    def _show_post_reboot_dialog(
        self,
        result_str: str,
        message: str,
        guidance: str,
    ) -> bool:
        """Show the post-reboot summary dialog on the main thread."""
        win = self.props.active_window
        if win is None:
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        if result_str == "success":
            heading = _("Operation Complete")
            body = message
        elif result_str == "partial":
            heading = _("Operation Complete — Review Recommended")
            body = message
        else:
            heading = _("Operation May Have Failed")
            body = f"{message}\n\n{guidance}" if guidance else message

        dialog = Adw.MessageDialog(
            transient_for=win,
            heading=heading,
            body=body,
        )
        dialog.add_response("ok", _("OK"))

        if result_str == "partial":
            dialog.add_response("drivers", _("View Drivers"))
            dialog.set_response_appearance("drivers", Adw.ResponseAppearance.SUGGESTED)
        elif result_str == "failed":
            dialog.add_response("diagnostics", _("Open Diagnostics"))
            dialog.set_response_appearance("diagnostics", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect("response", self._on_post_reboot_response)
        dialog.present()
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_post_reboot_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle post-reboot dialog response — clear state and navigate."""
        # Clear the pending summary on the daemon
        self.dbus_client.call_method_async("ClearPostRebootSummary", None)

        win = self.props.active_window
        if win is None:
            return

        if response == "drivers" and hasattr(win, "view_stack"):
            win.view_stack.set_visible_child_name("drivers")
        elif response == "diagnostics" and hasattr(win, "view_stack"):
            win.view_stack.set_visible_child_name("diagnostics")

    def do_shutdown(self):
        """Clean up D-Bus connection on application shutdown."""
        self.dbus_client.close()
        Adw.Application.do_shutdown(self)
