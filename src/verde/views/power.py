"""Power view — suspend/hibernate status, fixes, and power profile info."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from verde.widgets.preflight_banner import PreflightPanel
from verde.widgets.progress_overlay import OperationProgressPanel
from verde.widgets.status_indicator import StatusIndicator

if TYPE_CHECKING:
    from verde.dbus_client import VerdeDBusClient
    from verde.gpu_state import GPUState

log = logging.getLogger("verde.views.power")


# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]


def _sanitize_dbus_error(error_text: str) -> str:
    """Convert raw D-Bus/GLib errors into user-friendly messages."""
    if not error_text:
        return _("An unexpected error occurred")  # type: ignore[used-before-def]
    if ": " in error_text and error_text.startswith("g-"):
        error_text = error_text.split(": ", 1)[1]
    return error_text


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


class PowerPage(Adw.PreferencesPage):
    """Power view — suspend/hibernate status with expandable details and fix buttons."""

    __gtype_name__ = "PowerPage"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title(_("Power"))
        self.set_icon_name("battery-symbolic")

        self._dbus_client: VerdeDBusClient | None = None
        self._gpu_state: GPUState | None = None
        self._current_op_id: str | None = None
        self._signal_handler_ids: list[tuple] = []
        self._active_dialog: Adw.MessageDialog | None = None
        self._fix_in_progress: bool = False
        self._suggested_assigned: bool = False
        self._pending_fix_types: list[str] = []

        # Track dynamically added rows for proper cleanup
        self._suspend_issue_rows: list[Adw.ActionRow] = []
        self._suspend_change_rows: list[Adw.ActionRow] = []
        self._wayland_issue_rows: list[Adw.ActionRow] = []

        self._build_ui()

    # ══════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        """Build the view programmatically."""
        # ── Reboot Banner ──
        self._reboot_banner_group = Adw.PreferencesGroup()
        self._reboot_banner_group.set_visible(False)
        self.add(self._reboot_banner_group)

        self._reboot_banner = Adw.Banner()
        self._reboot_banner.set_title(_("Restart required to apply power configuration changes"))
        self._reboot_banner.set_revealed(True)
        self._reboot_banner_group.add(self._reboot_banner)

        # ── Suspend / Hibernate Group ──
        self._suspend_group = Adw.PreferencesGroup(
            title=_("Suspend / Hibernate"),
        )
        self.add(self._suspend_group)

        self._suspend_row = Adw.ActionRow(title=_("Suspend / Hibernate"))
        self._suspend_row.set_subtitle(_("Loading\u2026"))
        self._suspend_indicator = StatusIndicator()
        self._suspend_row.add_suffix(self._suspend_indicator)
        self._suspend_fix_btn = Gtk.Button(label=_("Fix"))
        self._suspend_fix_btn.set_valign(Gtk.Align.CENTER)
        self._suspend_fix_btn.set_visible(False)
        self._suspend_fix_btn.connect("clicked", self._on_fix_clicked, "suspend")
        self._suspend_fix_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Fix suspend and hibernate issues")],
        )
        self._suspend_row.add_suffix(self._suspend_fix_btn)
        self._suspend_group.add(self._suspend_row)

        # What's wrong expander
        self._suspend_issues_expander = Adw.ExpanderRow(
            title=_("What\u2019s wrong"),
        )
        self._suspend_issues_expander.set_show_enable_switch(False)
        self._suspend_issues_expander.set_expanded(False)
        self._suspend_issues_expander.set_visible(False)
        self._suspend_issues_expander.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Expand to see suspend and hibernate issues")],
        )
        self._suspend_group.add(self._suspend_issues_expander)

        # What Fix will do expander
        self._suspend_changes_expander = Adw.ExpanderRow(
            title=_("What \u201cFix\u201d will do"),
        )
        self._suspend_changes_expander.set_show_enable_switch(False)
        self._suspend_changes_expander.set_expanded(False)
        self._suspend_changes_expander.set_visible(False)
        self._suspend_changes_expander.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Expand to see what the fix will change")],
        )
        self._suspend_group.add(self._suspend_changes_expander)

        # ── Secure Boot Group ──
        self._secureboot_group = Adw.PreferencesGroup(
            title=_("Secure Boot"),
        )
        self.add(self._secureboot_group)

        self._secureboot_row = Adw.ActionRow(title=_("Secure Boot"))
        self._secureboot_row.set_subtitle(_("Loading\u2026"))
        self._secureboot_indicator = StatusIndicator()
        self._secureboot_row.add_suffix(self._secureboot_indicator)
        self._secureboot_group.add(self._secureboot_row)

        # ── Wayland Configuration Group ──
        self._wayland_group = Adw.PreferencesGroup(
            title=_("Wayland Configuration"),
        )
        self._wayland_group.set_visible(False)
        self.add(self._wayland_group)

        self._wayland_row = Adw.ActionRow(title=_("Wayland Configuration"))
        self._wayland_row.set_subtitle(_("Loading\u2026"))
        self._wayland_indicator = StatusIndicator()
        self._wayland_row.add_suffix(self._wayland_indicator)

        # Wayland badge
        wayland_badge = Gtk.Label(label=_("Wayland"))
        wayland_badge.add_css_class("caption")
        wayland_badge.add_css_class("dim-label")
        wayland_badge.set_valign(Gtk.Align.CENTER)
        self._wayland_row.add_suffix(wayland_badge)

        self._wayland_group.add(self._wayland_row)

        # Wayland issues expander
        self._wayland_issues_expander = Adw.ExpanderRow(
            title=_("What\u2019s wrong"),
        )
        self._wayland_issues_expander.set_show_enable_switch(False)
        self._wayland_issues_expander.set_expanded(False)
        self._wayland_issues_expander.set_visible(False)
        self._wayland_group.add(self._wayland_issues_expander)

        # ── Power Profile Group ──
        self._power_profile_group = Adw.PreferencesGroup(
            title=_("Power Profile"),
        )
        self.add(self._power_profile_group)

        self._power_mode_row = Adw.ActionRow(title=_("Power Profile"))
        self._power_mode_row.set_subtitle(_("Unknown"))
        self._power_mode_row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Current power profile")],
        )
        self._power_profile_group.add(self._power_mode_row)

        self._power_state_row = Adw.ActionRow(title=_("GPU Power State"))
        self._power_state_row.set_subtitle(_("Unavailable"))
        self._power_state_row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("GPU power state")],
        )
        self._power_profile_group.add(self._power_state_row)

        self._power_draw_row = Adw.ActionRow(title=_("Power Draw"))
        self._power_draw_row.set_subtitle(_("Unavailable"))
        self._power_draw_row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("GPU power draw")],
        )
        self._power_profile_group.add(self._power_draw_row)

        # ── Daemon Unreachable ──
        self._unreachable_group = Adw.PreferencesGroup()
        self._unreachable_group.set_visible(False)
        self.add(self._unreachable_group)

        self._unreachable_status = Adw.StatusPage()
        self._unreachable_status.set_icon_name("network-error-symbolic")
        self._unreachable_status.set_title(_("Service Unavailable"))
        self._unreachable_status.set_description(
            _(
                "The Verde system service is not responding. "
                "Check that the service is running and try again."
            )
        )
        self._unreachable_group.add(self._unreachable_status)

        retry_btn = Gtk.Button(label=_("Retry"))
        retry_btn.add_css_class("suggested-action")
        retry_btn.add_css_class("pill")
        retry_btn.set_halign(Gtk.Align.CENTER)
        retry_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Retry connecting to Verde service")],
        )
        retry_btn.connect("clicked", self._on_retry_clicked)
        self._unreachable_status.set_child(retry_btn)

    # ══════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════

    def _disconnect_signals(self) -> None:
        """Disconnect all tracked signal handlers."""
        for obj, handler_id in self._signal_handler_ids:
            with contextlib.suppress(Exception):
                obj.disconnect(handler_id)
        self._signal_handler_ids.clear()

    def bind_state(self, gpu_state: GPUState, dbus_client: VerdeDBusClient) -> None:
        """Bind to GPUState and VerdeDBusClient for live updates."""
        self._disconnect_signals()

        self._gpu_state = gpu_state
        self._dbus_client = dbus_client

        self._signal_handler_ids.extend(
            [
                (
                    dbus_client,
                    dbus_client.connect(
                        "notify::connected",
                        self._on_connection_changed,
                    ),
                ),
                (
                    dbus_client,
                    dbus_client.connect(
                        "operation-progress",
                        self._on_operation_progress,
                    ),
                ),
                (
                    dbus_client,
                    dbus_client.connect(
                        "operation-complete",
                        self._on_operation_complete,
                    ),
                ),
            ]
        )

        # Bind GPUState power properties
        self._signal_handler_ids.extend(
            [
                (
                    gpu_state,
                    gpu_state.connect(
                        "notify::p-state",
                        self._on_power_info_changed,
                    ),
                ),
                (
                    gpu_state,
                    gpu_state.connect(
                        "notify::power-draw",
                        self._on_power_info_changed,
                    ),
                ),
                (
                    gpu_state,
                    gpu_state.connect(
                        "notify::power-limit",
                        self._on_power_info_changed,
                    ),
                ),
            ]
        )

        if dbus_client.get_property("connected"):
            self._load_power_status()
        else:
            self._show_unreachable()

        # Initial power profile update
        self._update_power_profile()

    # ══════════════════════════════════════════════════════════════════
    # Connection state
    # ══════════════════════════════════════════════════════════════════

    def _on_connection_changed(self, client: VerdeDBusClient, _pspec) -> None:
        if client.get_property("connected"):
            self._hide_unreachable()
            self._load_power_status()
        else:
            self._show_unreachable()

    def _show_unreachable(self) -> None:
        self._suspend_group.set_visible(False)
        self._secureboot_group.set_visible(False)
        self._wayland_group.set_visible(False)
        self._power_profile_group.set_visible(False)
        self._unreachable_group.set_visible(True)

    def _hide_unreachable(self) -> None:
        self._unreachable_group.set_visible(False)
        self._suspend_group.set_visible(True)
        self._secureboot_group.set_visible(True)
        self._power_profile_group.set_visible(True)
        # Wayland group visibility depends on detection

    def _on_retry_clicked(self, _btn: Gtk.Button) -> None:
        if self._dbus_client is not None:
            self._dbus_client.connect_async()

    # ══════════════════════════════════════════════════════════════════
    # Data loading (async)
    # ══════════════════════════════════════════════════════════════════

    def _load_power_status(self) -> None:
        """Load power status from daemon via GetPowerStatus D-Bus call."""
        if self._dbus_client is None:
            return

        def _on_reply(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
                data = reply.unpack()[0]
                GLib.idle_add(self._update_status_display, data)
            except GLib.Error as exc:
                log.warning("GetPowerStatus failed: %s", exc.message)
                GLib.idle_add(self._on_power_status_error)

        self._dbus_client.call_method_async("GetPowerStatus", None, _on_reply)

    def _on_power_status_error(self) -> bool:
        """Handle GetPowerStatus failure on main thread."""
        self._suspend_row.set_subtitle(_("Unable to load power status"))
        self._suspend_indicator.set_status(_("Error"), "warn")
        self._secureboot_row.set_subtitle(_("Unable to load"))
        self._secureboot_indicator.set_status(_("Error"), "warn")
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    # ══════════════════════════════════════════════════════════════════
    # Status display
    # ══════════════════════════════════════════════════════════════════

    def _update_status_display(self, data: dict) -> bool:
        """Parse GetPowerStatus response and update all UI elements."""
        data.get("overall_status", "unknown")
        issues = data.get("issues", [])
        suspend_active = data.get("suspend_service_active", False)
        hibernate_active = data.get("hibernate_service_active", False)
        sb_enabled = data.get("secure_boot_enabled", False)
        mok_enrolled = data.get("mok_enrolled", False)
        wayland = data.get("wayland_session", False)

        # Classify issues by type
        suspend_issues = [i for i in issues if i.get("type") in ("suspend", "hibernate")]
        secureboot_issues = [i for i in issues if i.get("type") == "secure_boot"]
        wayland_issues = [i for i in issues if i.get("type") == "wayland"]

        # Reset suggested-action tracking (UX-DR15)
        self._suggested_assigned = False

        # ── Suspend / Hibernate ──
        self._update_suspend_section(suspend_issues, suspend_active, hibernate_active)

        # ── Secure Boot ──
        self._update_secureboot_section(sb_enabled, mok_enrolled, secureboot_issues)

        # ── Wayland ──
        self._update_wayland_section(wayland, wayland_issues)

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _update_suspend_section(
        self,
        issues: list[dict],
        suspend_active: bool,
        hibernate_active: bool,
    ) -> None:
        """Update the Suspend / Hibernate group."""
        # Clear old tracked rows
        self._remove_tracked_rows(self._suspend_issues_expander, self._suspend_issue_rows)
        self._remove_tracked_rows(self._suspend_changes_expander, self._suspend_change_rows)

        broken_issues = [
            i for i in issues if not i.get("already_fixed", False) and i.get("severity") != "ok"
        ]
        has_issues = len(broken_issues) > 0

        if has_issues:
            level, label = "crit", _("Issues Found")
            self._suspend_row.set_subtitle(
                _("{count} issue(s) detected").format(count=len(broken_issues))
            )
        elif suspend_active and hibernate_active:
            level, label = "good", _("Working")
            self._suspend_row.set_subtitle(_("All services configured correctly"))
        else:
            level, label = "warn", _("Unknown")
            self._suspend_row.set_subtitle(_("Could not determine status"))

        self._suspend_indicator.set_status(label, level)

        # Determine which fix types are needed (P-2)
        fixable = [i for i in broken_issues if i.get("fixable", False)]
        self._pending_fix_types = []
        for issue in fixable:
            itype = issue.get("type", "")
            if itype == "suspend" and "suspend" not in self._pending_fix_types:
                self._pending_fix_types.append("suspend")
            elif itype == "hibernate" and "hibernate" not in self._pending_fix_types:
                self._pending_fix_types.append("hibernate")
        # If any suspend/hibernate issue is fixable, both fixes may be needed
        if fixable and not self._pending_fix_types:
            self._pending_fix_types = ["suspend"]

        # Fix button visibility — only show when fixable issues exist
        if fixable and not self._fix_in_progress:
            self._suspend_fix_btn.set_visible(True)
            if not self._suggested_assigned:
                self._suspend_fix_btn.add_css_class("suggested-action")
                self._suggested_assigned = True
            else:
                self._suspend_fix_btn.remove_css_class("suggested-action")
        else:
            self._suspend_fix_btn.set_visible(False)

        # Populate "What's wrong" expander
        if broken_issues:
            self._suspend_issues_expander.set_visible(True)
            for issue in broken_issues:
                row = Adw.ActionRow(
                    title=issue.get("summary", _("Issue")),
                    subtitle=issue.get("detail", ""),
                )
                self._suspend_issues_expander.add_row(row)
                self._suspend_issue_rows.append(row)
        else:
            self._suspend_issues_expander.set_visible(False)

        # "What Fix will do" gets populated when preflight loads (on Fix click)
        self._suspend_changes_expander.set_visible(False)

    def _update_secureboot_section(
        self,
        sb_enabled: bool,
        mok_enrolled: bool,
        issues: list[dict],
    ) -> None:
        """Update the Secure Boot group."""
        broken = [
            i for i in issues if not i.get("already_fixed", False) and i.get("severity") != "ok"
        ]

        if not sb_enabled:
            self._secureboot_row.set_subtitle(_("Secure Boot is disabled"))
            self._secureboot_indicator.set_status(_("Disabled"), "good")
        elif mok_enrolled:
            self._secureboot_row.set_subtitle(_("MOK key is enrolled"))
            self._secureboot_indicator.set_status(_("Enrolled"), "good")
        elif broken:
            self._secureboot_row.set_subtitle(broken[0].get("summary", _("MOK key not enrolled")))
            self._secureboot_indicator.set_status(_("Not Enrolled"), "warn")
        else:
            self._secureboot_row.set_subtitle(_("Secure Boot enabled"))
            self._secureboot_indicator.set_status(_("OK"), "good")

    def _update_wayland_section(
        self,
        wayland_session: bool,
        issues: list[dict],
    ) -> None:
        """Update the Wayland Configuration group."""
        self._remove_tracked_rows(self._wayland_issues_expander, self._wayland_issue_rows)

        broken = [
            i for i in issues if not i.get("already_fixed", False) and i.get("severity") != "ok"
        ]

        if not wayland_session:
            self._wayland_group.set_visible(False)
            return

        self._wayland_group.set_visible(True)

        if broken:
            self._wayland_row.set_subtitle(
                _("{count} issue(s) detected").format(count=len(broken))
            )
            self._wayland_indicator.set_status(_("Issues Found"), "crit")

            self._wayland_issues_expander.set_visible(True)
            for issue in broken:
                row = Adw.ActionRow(
                    title=issue.get("summary", _("Issue")),
                    subtitle=issue.get("detail", ""),
                )
                self._wayland_issues_expander.add_row(row)
                self._wayland_issue_rows.append(row)
        else:
            self._wayland_row.set_subtitle(_("All Wayland settings configured"))
            self._wayland_indicator.set_status(_("Working"), "good")
            self._wayland_issues_expander.set_visible(False)

    @staticmethod
    def _remove_tracked_rows(
        expander: Adw.ExpanderRow,
        rows: list[Adw.ActionRow],
    ) -> None:
        """Remove previously tracked rows from an ExpanderRow and clear the list."""
        for row in rows:
            expander.remove(row)
        rows.clear()

    # ══════════════════════════════════════════════════════════════════
    # Fix flow: Preflight -> Confirm -> Progress -> Result
    # ══════════════════════════════════════════════════════════════════

    def _on_fix_clicked(self, _btn: Gtk.Button, _fix_type: str) -> None:
        """Handle Fix button click — show preflight dialog for all pending fix types."""
        if self._fix_in_progress or self._dbus_client is None:
            return
        if not self._pending_fix_types:
            return

        self._fix_in_progress = True
        self._suspend_fix_btn.set_sensitive(False)

        window = self.get_root()

        # Build label from pending fix types
        if "suspend" in self._pending_fix_types and "hibernate" in self._pending_fix_types:
            fix_label = _("Fix Suspend & Hibernate")
        elif "hibernate" in self._pending_fix_types:
            fix_label = _("Fix Hibernate")
        else:
            fix_label = _("Fix Suspend")

        dialog = Adw.MessageDialog.new(window, fix_label)
        dialog.set_body_use_markup(False)

        preflight = PreflightPanel()
        preflight.set_loading()
        dialog.set_extra_child(preflight)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("fix", fix_label)
        dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("fix", False)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        dialog._preflight = preflight
        dialog._fix_types = list(self._pending_fix_types)

        dialog.connect("response", self._on_fix_dialog_response)
        dialog.connect("close-request", self._on_dialog_closed)

        self._active_dialog = dialog
        dialog.present()

        # Load preflight for the first fix type
        first_type = self._pending_fix_types[0]
        self._load_power_preflight(dialog, first_type)

    def _on_dialog_closed(self, dialog: Adw.MessageDialog) -> bool:
        """Clean up when dialog is closed."""
        self._active_dialog = None
        self._fix_in_progress = False
        self._suspend_fix_btn.set_sensitive(True)
        return False

    def _load_power_preflight(self, dialog: Adw.MessageDialog, fix_type: str) -> None:
        """Load pre-flight check results for power fix."""
        if self._dbus_client is None:
            return

        operation = "fix_suspend" if fix_type == "suspend" else "fix_hibernate"

        def _on_reply(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
                data = reply.unpack()[0]
                GLib.idle_add(self._on_preflight_result, dialog, data)
            except GLib.Error as exc:
                log.warning("GetPreflightCheck(%s) failed: %s", operation, exc.message)
                GLib.idle_add(self._on_preflight_error, dialog, str(exc.message))

        self._dbus_client.call_method_async(
            "GetPreflightCheck",
            GLib.Variant("(s)", (operation,)),
            _on_reply,
        )

    def _on_preflight_result(self, dialog: Adw.MessageDialog, data: dict) -> bool:
        """Handle power preflight result on main thread."""
        preflight: PreflightPanel = dialog._preflight

        changes = data.get("changes", [])
        ready = data.get("ready", False)
        already_fixed = data.get("already_fixed", False)

        if already_fixed:
            preflight.set_error(_("All changes have already been applied."))
            dialog.set_response_enabled("fix", False)
        elif not ready:
            preflight.set_error(
                _("Required system components are missing. Is the NVIDIA driver installed?")
            )
            dialog.set_response_enabled("fix", False)
        else:
            # Convert changes to the format PreflightPanel expects
            check_items = []
            for ch in changes:
                status = "pass" if ch.get("current_state") == ch.get("target_state") else "action"
                check_items.append(
                    {
                        "name": ch.get("description", ""),
                        "status": status,
                        "description": _("{current} \u2192 {target}").format(
                            current=ch.get("current_state", "?"),
                            target=ch.get("target_state", "?"),
                        ),
                    }
                )
            preflight.set_checks(check_items)
            dialog.set_response_enabled("fix", True)

        # Also populate the "What Fix will do" expander in the main view
        self._populate_changes_expander(changes)

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _populate_changes_expander(self, changes: list[dict]) -> None:
        """Populate the changes expander with planned changes."""
        self._remove_tracked_rows(self._suspend_changes_expander, self._suspend_change_rows)
        if changes:
            self._suspend_changes_expander.set_visible(True)
            for ch in changes:
                current = ch.get("current_state", "?")
                target = ch.get("target_state", "?")
                row = Adw.ActionRow(
                    title=ch.get("description", ""),
                    subtitle=_("{current} \u2192 {target}").format(
                        current=current,
                        target=target,
                    ),
                )
                self._suspend_changes_expander.add_row(row)
                self._suspend_change_rows.append(row)

    def _on_preflight_error(self, dialog: Adw.MessageDialog, error_text: str) -> bool:
        """Handle preflight error."""
        dialog._preflight.set_error(_sanitize_dbus_error(error_text))
        dialog.set_response_enabled("fix", False)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_fix_dialog_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle fix dialog confirmation or diagnostic navigation."""
        if response == "diagnostic":
            window = self.get_root()
            if window is not None and hasattr(window, "view_stack"):
                window.view_stack.set_visible_child_name("diagnostics")
            return

        if response != "fix":
            return

        fix_types = getattr(dialog, "_fix_types", ["suspend"])

        # Switch to progress mode
        progress_panel = OperationProgressPanel()
        progress_panel.set_stage(_("Starting fix\u2026"), 0.0)
        dialog.set_extra_child(progress_panel)
        if len(fix_types) > 1:
            dialog.set_heading(_("Fixing Suspend & Hibernate"))
        elif "hibernate" in fix_types:
            dialog.set_heading(_("Fixing Hibernate"))
        else:
            dialog.set_heading(_("Fixing Suspend"))
        dialog.set_body("")

        dialog.set_close_response("")
        dialog.set_response_enabled("cancel", False)
        dialog.set_response_enabled("fix", False)

        dialog._progress_panel = progress_panel
        dialog._remaining_fix_types = list(fix_types)

        # Start first fix
        self._start_next_fix(dialog)

    def _start_next_fix(self, dialog: Adw.MessageDialog) -> None:
        """Start the next fix from the remaining fix types list."""
        remaining = getattr(dialog, "_remaining_fix_types", [])
        if not remaining or self._dbus_client is None:
            return

        fix_type = remaining.pop(0)
        method = "FixSuspend" if fix_type == "suspend" else "FixHibernate"

        def _on_reply(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
                op_id = reply.unpack()[0]
                GLib.idle_add(self._on_fix_started, op_id)
            except GLib.Error as exc:
                log.warning("%s failed: %s", method, exc.message)
                GLib.idle_add(self._on_fix_start_error, str(exc.message))

        self._dbus_client.call_method_async(method, None, _on_reply)

    def _on_fix_started(self, op_id: str) -> bool:
        """Handle successful FixSuspend/FixHibernate call."""
        self._current_op_id = op_id
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_fix_start_error(self, error_text: str) -> bool:
        """Handle fix start failure."""
        if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
            panel = self._active_dialog._progress_panel
            panel.set_error(_sanitize_dbus_error(error_text))
            self._active_dialog.set_close_response("cancel")
            self._active_dialog.set_response_enabled("cancel", True)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    # ══════════════════════════════════════════════════════════════════
    # D-Bus signal handlers
    # ══════════════════════════════════════════════════════════════════

    def _on_operation_progress(
        self,
        client: VerdeDBusClient,
        op_id: str,
        percent: float,
        message: str,
    ) -> None:
        """Handle OperationProgress signal."""
        if op_id != self._current_op_id:
            return
        if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
            panel = self._active_dialog._progress_panel
            panel.set_stage(message, percent / 100.0)

    def _on_operation_complete(
        self,
        client: VerdeDBusClient,
        op_id: str,
        success: bool,
        message: str,
    ) -> None:
        """Handle OperationComplete signal."""
        if op_id != self._current_op_id:
            return
        self._current_op_id = None

        if self._active_dialog is None or not hasattr(self._active_dialog, "_progress_panel"):
            return

        dialog = self._active_dialog
        panel = dialog._progress_panel

        if success:
            # Check for reboot message
            if "reboot" in message.lower():
                self._reboot_banner_group.set_visible(True)

            # Chain next fix if there are remaining types
            remaining = getattr(dialog, "_remaining_fix_types", [])
            if remaining:
                panel.set_stage(_("Continuing with next fix\u2026"), 0.5)
                self._start_next_fix(dialog)
                return

            panel.set_success(message or _("Fix applied successfully"))
            dialog.set_heading(_("Fix Complete"))
        else:
            safe_msg = _sanitize_dbus_error(message) if message else _("Fix failed")
            panel.set_error(safe_msg)
            dialog.set_heading(_("Fix Failed"))

            # UX-DR16: primary + secondary actions
            if not dialog.has_response("diagnostic"):
                dialog.add_response("diagnostic", _("Generate Diagnostic Report"))

        # Re-enable close
        dialog.set_close_response("cancel")
        if not dialog.has_response("cancel"):
            dialog.add_response("cancel", _("Close"))
        dialog.set_response_enabled("cancel", True)

        # Reload power status after fix completes
        self._load_power_status()

    # ══════════════════════════════════════════════════════════════════
    # Power profile info (GPUState bindings)
    # ══════════════════════════════════════════════════════════════════

    def _on_power_info_changed(self, gpu_state: GPUState, _pspec) -> None:
        self._update_power_profile()

    def _update_power_profile(self) -> None:
        """Update power profile rows from GPUState properties."""
        if self._gpu_state is None:
            return

        p_state = self._gpu_state.get_property("p-state")
        power_draw = self._gpu_state.get_property("power-draw")
        power_limit = self._gpu_state.get_property("power-limit")

        # Power Profile row — derive friendly label from P-state
        if p_state:
            p_num = p_state.upper().replace("P", "")
            try:
                p_val = int(p_num)
                if p_val <= 2:
                    profile = _("Performance")
                elif p_val <= 5:
                    profile = _("Balanced")
                else:
                    profile = _("Power Saver")
            except ValueError:
                profile = _("Unknown")
            self._power_mode_row.set_subtitle(profile)
        else:
            self._power_mode_row.set_subtitle(_("Unknown"))

        if p_state:
            self._power_state_row.set_subtitle(p_state)
        else:
            self._power_state_row.set_subtitle(_("Unavailable"))

        if power_limit > 0:
            self._power_draw_row.set_subtitle(
                _("{draw}W / {limit}W").format(
                    draw=int(power_draw),
                    limit=int(power_limit),
                )
            )
        elif power_draw > 0:
            self._power_draw_row.set_subtitle(_("{draw}W").format(draw=int(power_draw)))
        else:
            self._power_draw_row.set_subtitle(_("Unavailable"))
