"""Diagnostics view — system reports and audit log."""

from gi.repository import Adw, Gio, Gtk


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


if _has_resource("/com/verde/app/ui/diagnostics_page.ui"):

    @Gtk.Template(resource_path="/com/verde/app/ui/diagnostics_page.ui")
    class DiagnosticsPage(Adw.PreferencesPage):
        __gtype_name__ = "DiagnosticsPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

else:

    class DiagnosticsPage(Adw.PreferencesPage):  # type: ignore[no-redef]
        __gtype_name__ = "DiagnosticsPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_title("Diagnostics")
            self.set_icon_name("utilities-system-monitor-symbolic")
            group = Adw.PreferencesGroup()
            self.add(group)
            status = Adw.StatusPage(
                icon_name="utilities-system-monitor-symbolic",
                title="Diagnostics",
                description="System diagnostics will appear here.",
            )
            group.add(status)
