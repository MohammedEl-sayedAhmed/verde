"""PreflightPanel — pre-flight check results, planned changes, and rollback plan."""

from __future__ import annotations

from gi.repository import Adw, Gtk


class PreflightPanel(Gtk.Box):
    """Pre-flight check results, planned changes, and rollback plan.

    Used as extra child inside the install confirmation Adw.MessageDialog.
    """

    __gtype_name__ = "PreflightPanel"

    def __init__(self, **kwargs) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, **kwargs)
        self._all_passed = False
        self._checks_group = Adw.PreferencesGroup(title=_("Pre-flight Checks"))
        self.append(self._checks_group)

        self._changes_group = Adw.PreferencesGroup(title=_("Changes"))
        self.append(self._changes_group)

        self._rollback_group = Adw.PreferencesGroup(title=_("Rollback Plan"))
        self.append(self._rollback_group)

        # Loading state
        self._spinner = Gtk.Spinner(spinning=True)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._spinner.set_size_request(32, 32)
        self._checks_group.add(self._spinner)

        self._check_rows: list[Adw.ActionRow] = []
        self._change_rows: list[Adw.ActionRow] = []
        self._rollback_row: Adw.ActionRow | None = None
        self._error_row: Adw.ActionRow | None = None

    @property
    def all_passed(self) -> bool:
        """True when all checks passed — Install button can be enabled."""
        return self._all_passed

    def set_loading(self) -> None:
        """Show loading state with spinner."""
        self._clear_checks()
        self._spinner.set_visible(True)
        self._spinner.set_spinning(True)
        self._all_passed = False

    def set_checks(self, checks: list[dict]) -> None:
        """Populate check rows.

        Each dict: {name: str, status: "passed"|"failed", explanation: str}.
        """
        self._clear_checks()
        self._spinner.set_visible(False)
        self._spinner.set_spinning(False)

        all_ok = True
        for check in checks:
            row = Adw.ActionRow()
            name = check.get("name", "Check")
            status = check.get("status", "unknown")
            explanation = check.get("explanation", "")

            row.set_title(name)

            icon = Gtk.Image()
            icon.set_valign(Gtk.Align.CENTER)

            if status == "passed":
                icon.set_from_icon_name("emblem-ok-symbolic")
                icon.add_css_class("verde-status-good")
            else:
                icon.set_from_icon_name("dialog-error-symbolic")
                icon.add_css_class("verde-status-crit")
                row.set_subtitle(explanation)
                all_ok = False

            row.add_suffix(icon)

            # Accessibility
            desc = f"{name}: {status}"
            if explanation:
                desc += f", {explanation}"
            row.update_property(
                [Gtk.AccessibleProperty.DESCRIPTION],
                [desc],
            )

            self._checks_group.add(row)
            self._check_rows.append(row)

        self._all_passed = all_ok

    def set_changes(self, changes: list[dict]) -> None:
        """Populate change rows.

        Each dict: {action: str, package: str, version: str}.
        """
        self._clear_changes()
        for change in changes:
            row = Adw.ActionRow()
            action = change.get("action", "")
            package = change.get("package", "")
            version = change.get("version", "")

            row.set_title(f"{action} {package}")
            if version:
                row.set_subtitle(f"Version {version}")

            self._changes_group.add(row)
            self._change_rows.append(row)

    def set_rollback_plan(self, plan: str) -> None:
        """Set rollback plan description."""
        if self._rollback_row is not None:
            self._rollback_group.remove(self._rollback_row)
        self._rollback_row = Adw.ActionRow()
        self._rollback_row.set_title(_("Recovery"))
        self._rollback_row.set_subtitle(plan)
        self._rollback_group.add(self._rollback_row)

    def set_error(self, error_text: str) -> None:
        """Show error state with message and retry option."""
        self._clear_checks()
        self._spinner.set_visible(False)
        self._spinner.set_spinning(False)
        self._all_passed = False

        self._error_row = Adw.ActionRow()
        self._error_row.set_title(_("Error"))
        self._error_row.set_subtitle(error_text)

        icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        icon.add_css_class("verde-status-crit")
        icon.set_valign(Gtk.Align.CENTER)
        self._error_row.add_suffix(icon)

        self._checks_group.add(self._error_row)

    def _clear_checks(self) -> None:
        for row in self._check_rows:
            self._checks_group.remove(row)
        self._check_rows.clear()
        if self._error_row is not None:
            self._checks_group.remove(self._error_row)
            self._error_row = None

    def _clear_changes(self) -> None:
        for row in self._change_rows:
            self._changes_group.remove(row)
        self._change_rows.clear()


# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]
