"""Snapshot row — Adw.ExpanderRow for snapshot display with rollback/delete actions."""

from __future__ import annotations

import datetime
from collections.abc import Callable

from gi.repository import Adw, Gtk

# gettext stub
try:
    _("test")
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _


def _format_timestamp(iso_ts: str) -> str:
    """Convert ISO 8601 timestamp to human-readable date string."""
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_ts or _("Unknown date")


def _format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def build_snapshot_row(
    snapshot: dict,
    on_rollback_clicked: Callable | None = None,
    on_delete_clicked: Callable | None = None,
) -> Adw.ExpanderRow:
    """Build an ExpanderRow for a single snapshot from D-Bus aa{sv} data.

    Parameters
    ----------
    snapshot : dict
        Snapshot metadata with keys: id, timestamp, driver_version,
        kernel_version, packages, dkms_status, file_size, sha256.
    on_rollback_clicked : Callable | None
        Callback receiving (button, snapshot_dict) on Rollback click.
    on_delete_clicked : Callable | None
        Callback receiving (button, snapshot_dict) on Delete click.

    Returns
    -------
    Adw.ExpanderRow
        Configured expander row ready to add to a PreferencesGroup.
    """
    snapshot.get("id", "")
    timestamp = snapshot.get("timestamp", "")
    driver_version = snapshot.get("driver_version", "")
    kernel_version = snapshot.get("kernel_version", "")
    packages = snapshot.get("packages", [])
    dkms_status = snapshot.get("dkms_status", "")
    file_size = snapshot.get("file_size", 0)
    sha256 = snapshot.get("sha256", "")

    human_date = _format_timestamp(timestamp)

    row = Adw.ExpanderRow()
    row.set_title(human_date)
    row.set_subtitle(_("Driver {}").format(driver_version) if driver_version else _("Snapshot"))
    row.set_show_enable_switch(False)
    row.update_property(
        [Gtk.AccessibleProperty.DESCRIPTION],
        [_("Snapshot from {}, driver {}").format(human_date, driver_version)],
    )

    # ── Suffix: Rollback + Delete buttons ──
    suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    suffix_box.set_valign(Gtk.Align.CENTER)

    rollback_btn = Gtk.Button(label=_("Rollback"))
    rollback_btn.add_css_class("flat")
    rollback_btn.set_valign(Gtk.Align.CENTER)
    rollback_btn.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [_("Rollback to snapshot from {}").format(human_date)],
    )
    if on_rollback_clicked is not None:
        rollback_btn.connect("clicked", on_rollback_clicked, snapshot)
    suffix_box.append(rollback_btn)

    delete_btn = Gtk.Button()
    delete_btn.set_icon_name("user-trash-symbolic")
    delete_btn.add_css_class("flat")
    delete_btn.add_css_class("error")
    delete_btn.set_valign(Gtk.Align.CENTER)
    delete_btn.set_tooltip_text(_("Delete snapshot"))
    delete_btn.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [_("Delete snapshot from {}").format(human_date)],
    )
    if on_delete_clicked is not None:
        delete_btn.connect("clicked", on_delete_clicked, snapshot)
    suffix_box.append(delete_btn)

    row.add_suffix(suffix_box)

    # ── Expanded detail rows ──
    if packages:
        pkg_row = Adw.ActionRow()
        pkg_row.set_title(_("Packages"))
        pkg_row.set_subtitle("\n".join(packages))
        row.add_row(pkg_row)

    if kernel_version:
        kernel_row = Adw.ActionRow()
        kernel_row.set_title(_("Kernel Version"))
        kernel_row.set_subtitle(kernel_version)
        row.add_row(kernel_row)

    if dkms_status:
        dkms_row = Adw.ActionRow()
        dkms_row.set_title(_("DKMS Status"))
        dkms_row.set_subtitle(dkms_status)
        row.add_row(dkms_row)

    if file_size:
        size_row = Adw.ActionRow()
        size_row.set_title(_("File Size"))
        size_row.set_subtitle(_format_file_size(file_size))
        row.add_row(size_row)

    if sha256:
        hash_row = Adw.ActionRow()
        hash_row.set_title(_("SHA-256"))
        hash_row.set_subtitle(sha256)
        row.add_row(hash_row)

    return row
