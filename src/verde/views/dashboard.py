"""Dashboard view — GPU monitoring at a glance."""

from gi.repository import Adw, Gio, Gtk


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


if _has_resource("/com/verde/app/ui/dashboard_page.ui"):

    @Gtk.Template(resource_path="/com/verde/app/ui/dashboard_page.ui")
    class DashboardPage(Adw.PreferencesPage):
        __gtype_name__ = "DashboardPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

else:

    class DashboardPage(Adw.PreferencesPage):  # type: ignore[no-redef]
        __gtype_name__ = "DashboardPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_title("Dashboard")
            self.set_icon_name("speedometer-symbolic")
            group = Adw.PreferencesGroup()
            self.add(group)
            status = Adw.StatusPage(
                icon_name="speedometer-symbolic",
                title="Dashboard",
                description="GPU monitoring will appear here.",
            )
            group.add(status)
