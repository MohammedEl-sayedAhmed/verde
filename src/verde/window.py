"""Verde main application window."""

from gi.repository import Adw, Gio, Gtk


def _has_ui_resource() -> bool:
    """Check if Blueprint-compiled UI template is available in GResource."""
    try:
        Gio.resources_lookup_data(
            "/com/verde/app/ui/window.ui", Gio.ResourceLookupFlags.NONE
        )
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

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.view_switcher.connect(
                "notify::title-visible",
                lambda sw, _: self.bottom_bar.set_reveal(sw.get_title_visible()),
            )
            self._load_css()

        def _load_css(self):
            css_provider = Gtk.CssProvider()
            css_provider.load_from_resource("/com/verde/app/style.css")
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

else:

    class VerdeWindow(Adw.ApplicationWindow):
        """Main window built programmatically (blueprint-compiler not available)."""

        __gtype_name__ = "VerdeWindow"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_default_size(800, 600)
            self.set_title("Verde")

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.set_content(box)

            header = Adw.HeaderBar()
            self.view_switcher = Adw.ViewSwitcher()
            self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(self.view_switcher)
            box.append(header)

            self.view_stack = Adw.ViewStack()
            self.view_switcher.set_stack(self.view_stack)

            self.bottom_bar = Adw.ViewSwitcherBar()
            self.bottom_bar.set_stack(self.view_stack)
            self.view_switcher.connect(
                "notify::title-visible",
                lambda sw, _: self.bottom_bar.set_reveal(sw.get_title_visible()),
            )

            pages = [
                ("dashboard", "Dashboard", "speedometer-symbolic"),
                ("drivers", "Drivers", "application-x-firmware-symbolic"),
                ("power", "Power", "battery-symbolic"),
                ("diagnostics", "Diagnostics", "utilities-system-monitor-symbolic"),
            ]
            for name, title, icon in pages:
                page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
                clamp = Adw.Clamp(maximum_size=600)
                page.set_child(clamp)
                clamp.set_child(
                    Adw.StatusPage(
                        icon_name=icon,
                        title=title,
                        description=f"{title} will appear here.",
                    )
                )
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


class VerdeApplication(Adw.Application):
    """Verde GTK application."""

    def __init__(self, application_id: str, version: str, **kwargs):
        super().__init__(application_id=application_id, **kwargs)
        self._version = version
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
