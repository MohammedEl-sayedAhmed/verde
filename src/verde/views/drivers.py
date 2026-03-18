"""Drivers view — driver installation and management."""

from gi.repository import Adw, Gio, Gtk


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


if _has_resource("/com/verde/app/ui/drivers_page.ui"):

    @Gtk.Template(resource_path="/com/verde/app/ui/drivers_page.ui")
    class DriversPage(Adw.PreferencesPage):
        __gtype_name__ = "DriversPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

else:

    class DriversPage(Adw.PreferencesPage):  # type: ignore[no-redef]
        __gtype_name__ = "DriversPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_title("Drivers")
            self.set_icon_name("application-x-firmware-symbolic")
            group = Adw.PreferencesGroup()
            self.add(group)
            status = Adw.StatusPage(
                icon_name="application-x-firmware-symbolic",
                title="Drivers",
                description="Driver management will appear here.",
            )
            group.add(status)
