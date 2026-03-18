"""Driver card — builds an Adw.ActionRow for a driver entry."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk


def build_driver_row(
    driver: dict,
    on_install_clicked: Callable | None = None,
) -> Adw.ActionRow:
    """Build an ActionRow for a single driver from D-Bus a{sv} dict.

    Parameters
    ----------
    driver : dict
        Driver metadata with keys: package, version, variant, recommended.
    on_install_clicked : Callable | None
        Callback receiving (button, driver_dict) on Install click.

    Returns
    -------
    Adw.ActionRow
        Configured row ready to add to a PreferencesGroup.
    """
    package = driver.get("package", "nvidia-driver")
    version = driver.get("version", "")
    variant = driver.get("variant", "proprietary")
    recommended = driver.get("recommended", False)
    held = driver.get("held", False)

    row = Adw.ActionRow()
    row.set_title(package)

    if held:
        row.set_subtitle(
            _("Package held — run `sudo apt-mark unhold {}` to allow updates").format(package)
        )
    else:
        row.set_subtitle(_("Version {} - {}").format(version, variant.capitalize()))
    row.add_css_class("verde-technical")

    # Suffix box: optional recommended badge + install button
    suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    suffix_box.set_valign(Gtk.Align.CENTER)

    if recommended and not held:
        badge = Gtk.Label(label=_("Recommended"))
        badge.add_css_class("caption")
        badge.set_valign(Gtk.Align.CENTER)
        suffix_box.append(badge)

    version_short = version.split(".")[0] if version else version
    install_btn = Gtk.Button(label=_("Install Driver {}").format(version_short))
    install_btn.set_valign(Gtk.Align.CENTER)

    if held:
        install_btn.set_sensitive(False)
        install_btn.set_tooltip_text(
            _("Package is held. Run `sudo apt-mark unhold {}` to enable updates.").format(package)
        )
    elif recommended:
        install_btn.add_css_class("suggested-action")
    else:
        install_btn.add_css_class("flat")

    install_btn.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [_("Install Driver {}, {}").format(version_short, variant)],
    )

    if on_install_clicked is not None and not held:
        install_btn.connect("clicked", on_install_clicked, driver)

    suffix_box.append(install_btn)
    row.add_suffix(suffix_box)

    return row


# gettext stub — replaced by real gettext in app context
try:
    _("test")
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _
