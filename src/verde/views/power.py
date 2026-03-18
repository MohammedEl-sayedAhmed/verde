"""Power view — suspend and hibernate management."""

from gi.repository import Adw, Gio, Gtk


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


if _has_resource("/com/verde/app/ui/power_page.ui"):

    @Gtk.Template(resource_path="/com/verde/app/ui/power_page.ui")
    class PowerPage(Adw.PreferencesPage):
        __gtype_name__ = "PowerPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

else:

    class PowerPage(Adw.PreferencesPage):  # type: ignore[no-redef]
        __gtype_name__ = "PowerPage"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_title("Power")
            self.set_icon_name("battery-symbolic")
            group = Adw.PreferencesGroup()
            self.add(group)
            status = Adw.StatusPage(
                icon_name="battery-symbolic",
                title="Power",
                description="Power management will appear here.",
            )
            group.add(status)
