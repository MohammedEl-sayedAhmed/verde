"""Diagnostics view — system info, health checks, and report generation."""

from __future__ import annotations

import logging
import platform
from datetime import datetime
from datetime import timedelta as _timedelta
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

if TYPE_CHECKING:
    from verde.dbus_client import VerdeDBusClient
    from verde.gpu_state import GPUState

log = logging.getLogger("verde.diagnostics")


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
    # GLib D-Bus errors look like "GDBus.Error:com.verde.Error.Name: message"
    if error_text.startswith("GDBus.Error:") and ": " in error_text:
        # Strip the "GDBus.Error:domain.name: " prefix
        _, _, rest = error_text.partition(": ")
        if ": " in rest:
            _, _, message = rest.partition(": ")
            return message
        return rest
    # Older format: "g-io-error-quark: message"
    if ": " in error_text and error_text.startswith("g-"):
        error_text = error_text.split(": ", 1)[1]
    return error_text


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


def _make_row(title: str, subtitle: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_subtitle_selectable(True)
    return row


def _make_status_row(title: str, subtitle: str, ok: bool) -> Adw.ActionRow:
    row = _make_row(title, subtitle)
    icon_name = "emblem-ok-symbolic" if ok else "dialog-warning-symbolic"
    icon = Gtk.Image(icon_name=icon_name)
    if ok:
        icon.add_css_class("success")
    else:
        icon.add_css_class("warning")
    row.add_suffix(icon)
    return row


class DiagnosticsPage(Adw.PreferencesPage):
    """Diagnostics page showing driver-independent system information."""

    __gtype_name__ = "DiagnosticsPage"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Diagnostics")
        self.set_icon_name("utilities-system-monitor-symbolic")

        self._dbus_client: VerdeDBusClient | None = None
        self._gpu_state: GPUState | None = None
        self._signal_handler_ids: list[tuple] = []

        # ── System Info group ──
        self._system_group = Adw.PreferencesGroup(title=_("System"))
        self.add(self._system_group)

        self._system_group.add(_make_row(_("Kernel"), platform.release()))
        self._system_group.add(_make_row(_("Architecture"), platform.machine()))

        os_name = _read_os_release()
        if os_name:
            self._system_group.add(_make_row(_("OS"), os_name))

        # ── GPU Hardware group (populated from D-Bus) ──
        self._gpu_group = Adw.PreferencesGroup(title=_("GPU Hardware"))
        self.add(self._gpu_group)
        self._gpu_spinner = Gtk.Spinner(spinning=True)
        self._gpu_loading_row = Adw.ActionRow(title=_("Detecting GPU..."))
        self._gpu_loading_row.add_suffix(self._gpu_spinner)
        self._gpu_group.add(self._gpu_loading_row)

        # ── Driver Status group (populated from D-Bus) ──
        self._driver_group = Adw.PreferencesGroup(title=_("Driver Status"))
        self.add(self._driver_group)
        self._driver_spinner = Gtk.Spinner(spinning=True)
        self._driver_loading_row = Adw.ActionRow(title=_("Loading driver info..."))
        self._driver_loading_row.add_suffix(self._driver_spinner)
        self._driver_group.add(self._driver_loading_row)

        # ── Degraded State group ──
        self._state_group = Adw.PreferencesGroup(title=_("Health Status"))
        self.add(self._state_group)
        self._state_loading_row = Adw.ActionRow(title=_("Checking..."))
        self._state_group.add(self._state_loading_row)

        # ── Unreachable status (when daemon not connected) ──
        self._unreachable_group = Adw.PreferencesGroup()
        self._unreachable_group.set_visible(False)
        self.add(self._unreachable_group)
        self._unreachable_status = Adw.StatusPage(
            icon_name="network-error-symbolic",
            title=_("Service Unavailable"),
            description=_(
                "Verde\u2019s system service is not responding.\n"
                "Try: systemctl restart com.verde.Manager"
            ),
        )
        self._unreachable_group.add(self._unreachable_status)

        # ── Report Generation group ──
        self._report_gen_group = Adw.PreferencesGroup(
            title=_("Diagnostic Report"),
            description=_(
                "Generate a comprehensive system report for sharing on forums or with support."
            ),
        )
        self.add(self._report_gen_group)

        self._generate_btn = Gtk.Button(label=_("Generate Report"))
        self._generate_btn.add_css_class("suggested-action")
        self._generate_btn.set_halign(Gtk.Align.CENTER)
        self._generate_btn.connect("clicked", self._on_generate_clicked)
        self._generate_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Generate a diagnostic report")],
        )
        self._report_gen_group.add(self._generate_btn)

        self._generate_spinner = Gtk.Spinner()
        self._generate_spinner.set_visible(False)
        self._generate_spinner.set_halign(Gtk.Align.CENTER)
        self._report_gen_group.add(self._generate_spinner)

        # ── Report Card group (hidden until report generated) ──
        self._report_card_group = Adw.PreferencesGroup(
            title=_("Report Generated"),
        )
        self._report_card_group.set_visible(False)
        self.add(self._report_card_group)

        self._timestamp_row = Adw.ActionRow(title=_("Generated"))
        self._timestamp_row.set_subtitle("")
        self._copy_btn = Gtk.Button(label=_("Copy to Clipboard"))
        self._copy_btn.set_valign(Gtk.Align.CENTER)
        self._copy_btn.connect("clicked", self._on_copy_clicked)
        self._copy_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Copy report to clipboard")],
        )
        self._timestamp_row.add_suffix(self._copy_btn)
        self._report_card_group.add(self._timestamp_row)

        self._preview_expander = Adw.ExpanderRow(title=_("Preview"))
        self._preview_expander.set_show_enable_switch(False)
        self._preview_expander.set_expanded(False)
        self._preview_expander.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Expand to preview diagnostic report")],
        )
        self._report_card_group.add(self._preview_expander)

        self._preview_label = Gtk.Label()
        self._preview_label.set_selectable(True)
        self._preview_label.set_wrap(True)
        self._preview_label.set_xalign(0.0)
        self._preview_label.add_css_class("monospace")
        self._preview_label.set_margin_start(12)
        self._preview_label.set_margin_end(12)
        self._preview_label.set_margin_top(6)
        self._preview_label.set_margin_bottom(6)
        self._preview_expander.add_row(Adw.ActionRow(child=self._preview_label))

        # Internal state
        self._report_text: str = ""
        self._generating: bool = False

        # ── Activity Log group (Story 5.3) ──
        self._audit_group = Adw.PreferencesGroup(title=_("Activity Log"))
        self.add(self._audit_group)

        # Filter bar
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_box.set_margin_start(12)
        filter_box.set_margin_end(12)
        filter_box.set_margin_top(6)
        filter_box.set_margin_bottom(6)

        # Operation type filter
        self._type_filter = Gtk.DropDown.new_from_strings(
            [
                _("All Types"),
                "INSTALL_DRIVER",
                "ROLLBACK_DRIVER",
                "FIX_SUSPEND",
                "FIX_HIBERNATE",
                "AUTH_DENIED",
                "GENERATE_DIAGNOSTIC",
            ]
        )
        self._type_filter.set_enable_search(False)
        self._type_filter.set_selected(0)
        self._type_filter.connect("notify::selected", self._on_filter_changed)
        self._type_filter.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Filter by operation type")],
        )
        filter_box.append(self._type_filter)

        # Result filter
        self._result_filter = Gtk.DropDown.new_from_strings(
            [_("All Results"), "success", "failed", "denied"]
        )
        self._result_filter.set_enable_search(False)
        self._result_filter.set_selected(0)
        self._result_filter.connect("notify::selected", self._on_filter_changed)
        self._result_filter.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Filter by result")],
        )
        filter_box.append(self._result_filter)

        # Date range filter
        self._date_filter = Gtk.DropDown.new_from_strings(
            [_("All Time"), _("Today"), _("Last 7 Days"), _("Last 30 Days")]
        )
        self._date_filter.set_enable_search(False)
        self._date_filter.set_selected(0)
        self._date_filter.connect("notify::selected", self._on_filter_changed)
        self._date_filter.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Filter by date range")],
        )
        filter_box.append(self._date_filter)

        self._audit_group.add(filter_box)

        # Entry count label
        self._entry_count_label = Gtk.Label(label=_("No entries"))
        self._entry_count_label.set_xalign(0.0)
        self._entry_count_label.set_margin_start(12)
        self._entry_count_label.add_css_class("dim-label")
        self._audit_group.add(self._entry_count_label)

        # Export button
        self._export_btn = Gtk.Button(label=_("Export"))
        self._export_btn.set_halign(Gtk.Align.END)
        self._export_btn.set_margin_end(12)
        self._export_btn.set_margin_bottom(6)
        self._export_btn.set_sensitive(False)
        self._export_btn.connect("clicked", self._on_export_clicked)
        self._export_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Export audit log to clipboard")],
        )
        self._audit_group.add(self._export_btn)

        # Empty state
        self._audit_empty = Adw.StatusPage()
        self._audit_empty.set_icon_name("document-open-symbolic")
        self._audit_empty.set_title(_("No operations recorded yet"))
        self._audit_empty.set_description(
            _("Audit entries will appear here after Verde performs system changes.")
        )
        self._audit_empty.set_visible(False)
        self._audit_group.add(self._audit_empty)

        # Tracked entry rows for cleanup
        self._audit_entry_rows: list[Adw.ExpanderRow] = []
        self._audit_entries_raw: list[dict] = []

    # ── State binding ─────────────────────────────────────────────────

    def bind_state(self, gpu_state: GPUState, dbus_client: VerdeDBusClient) -> None:
        """Bind to GPUState and VerdeDBusClient for data loading."""
        self._disconnect_signals()
        self._gpu_state = gpu_state
        self._dbus_client = dbus_client

        self._signal_handler_ids.append(
            (
                dbus_client,
                dbus_client.connect("notify::connected", self._on_connection_changed),
            )
        )

        if dbus_client.get_property("connected"):
            self._load_diagnostics()

    def _disconnect_signals(self) -> None:
        for obj, hid in self._signal_handler_ids:
            obj.disconnect(hid)
        self._signal_handler_ids.clear()

    # ── Connection handling ───────────────────────────────────────────

    def _on_connection_changed(self, client: VerdeDBusClient, _pspec) -> None:
        if client.get_property("connected"):
            self._show_data_groups()
            self._load_diagnostics()
        else:
            self._show_unreachable()

    def _show_unreachable(self) -> None:
        self._gpu_group.set_visible(False)
        self._driver_group.set_visible(False)
        self._state_group.set_visible(False)
        self._unreachable_group.set_visible(True)

    def _show_data_groups(self) -> None:
        self._unreachable_group.set_visible(False)
        self._gpu_group.set_visible(True)
        self._driver_group.set_visible(True)
        self._state_group.set_visible(True)

    # ── Report generation ─────────────────────────────────────────────

    def _on_generate_clicked(self, _btn: Gtk.Button) -> None:
        """Handle Generate Report button click — async D-Bus call on main thread."""
        if self._generating or self._dbus_client is None:
            return

        self._generating = True
        self._generate_btn.set_sensitive(False)
        self._generate_spinner.set_visible(True)
        self._generate_spinner.set_spinning(True)

        # call_method_async is already non-blocking — no background thread needed
        self._dbus_client.call_method_async(
            "GenerateDiagnosticReport",
            GLib.Variant("(s)", ("markdown",)),
            self._on_generate_reply,
        )

    def _on_generate_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        """Handle D-Bus reply for GenerateDiagnosticReport."""
        try:
            reply = proxy.call_finish(result)
            report = reply.unpack()[0]
            self._on_report_received(report)
        except GLib.Error as exc:
            log.warning("GenerateDiagnosticReport failed: %s", exc.message)
            self._on_generate_error(str(exc.message))

    _PREVIEW_MAX_CHARS = 10_000

    def _on_report_received(self, report_text: str) -> None:
        """Handle successful report generation on main thread."""
        self._generating = False
        self._generate_btn.set_sensitive(True)
        self._generate_spinner.set_spinning(False)
        self._generate_spinner.set_visible(False)

        self._report_text = report_text

        # Update report card
        now = datetime.now()
        self._timestamp_row.set_subtitle(now.strftime("%Y-%m-%d %H:%M"))

        # P-4: Cap preview to avoid Pango layout freeze on large reports
        if len(report_text) > self._PREVIEW_MAX_CHARS:
            preview = report_text[: self._PREVIEW_MAX_CHARS] + _(
                "\n\n… (truncated — copy for full report)"
            )
        else:
            preview = report_text
        self._preview_label.set_text(preview)
        self._report_card_group.set_visible(True)

    def _on_generate_error(self, error_text: str) -> None:
        """Handle report generation error on main thread."""
        self._generating = False
        self._generate_btn.set_sensitive(True)
        self._generate_spinner.set_spinning(False)
        self._generate_spinner.set_visible(False)

        window = self.get_root()
        if not isinstance(window, Adw.ApplicationWindow):
            log.warning("Cannot show error dialog — no parent window")
            return

        dialog = Adw.MessageDialog.new(
            window,
            _("Report Generation Failed"),
        )
        dialog.set_body(_sanitize_dbus_error(error_text))
        dialog.set_body_use_markup(False)
        dialog.add_response("retry", _("Retry"))
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.connect("response", self._on_error_dialog_response)
        dialog.present()

    def _on_error_dialog_response(
        self,
        dialog: Adw.MessageDialog,
        response: str,
    ) -> None:
        """Handle error dialog response."""
        dialog.destroy()
        if response == "retry":
            self._on_generate_clicked(self._generate_btn)

    def _on_copy_clicked(self, _btn: Gtk.Button) -> None:
        """Copy the report markdown to system clipboard."""
        if not self._report_text:
            return

        display = self.get_display()
        if display is None:
            return

        clipboard = display.get_clipboard()
        clipboard.set(self._report_text)

        # Show toast via the window's toast overlay
        window = self.get_root()
        if (
            isinstance(window, Adw.ApplicationWindow)
            and getattr(window, "toast_overlay", None) is not None
        ):
            toast = Adw.Toast(title=_("Report copied to clipboard"))
            toast.set_timeout(3)
            window.toast_overlay.add_toast(toast)

    # ── Audit log ──────────────────────────────────────────────────────

    def _load_audit_log(self) -> None:
        """Fetch audit log entries via D-Bus with current filters."""
        if self._dbus_client is None:
            return

        filter_type = self._get_type_filter_value()
        result_filter = self._get_result_filter_value()
        date_from, date_to = self._get_date_filter_values()

        self._dbus_client.call_method_async(
            "GetAuditLog",
            GLib.Variant("(ssss)", (filter_type, date_from, date_to, result_filter)),
            self._on_audit_log_reply,
        )

    def _on_audit_log_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        """Handle GetAuditLog D-Bus reply."""
        try:
            reply = proxy.call_finish(result)
            entries = reply.unpack()[0]
            self._populate_audit_entries(entries)
        except GLib.Error as exc:
            log.warning("GetAuditLog failed: %s", exc.message)
            self._populate_audit_entries([])

    def _populate_audit_entries(self, entries: list[dict]) -> None:
        """Populate audit log rows from entry dicts."""
        # Clear old rows
        for row in self._audit_entry_rows:
            self._audit_group.remove(row)
        self._audit_entry_rows.clear()
        self._audit_entries_raw = list(entries)

        if not entries:
            self._audit_empty.set_visible(True)
            self._entry_count_label.set_label(_("No entries"))
            self._export_btn.set_sensitive(False)
            return

        self._audit_empty.set_visible(False)
        self._entry_count_label.set_label(_("Showing {count} entries").format(count=len(entries)))
        self._export_btn.set_sensitive(True)

        for entry in entries:
            op = entry.get("operation", _("Unknown"))
            result_val = entry.get("result", "")
            ts = entry.get("timestamp", "")
            flagged = entry.get("flagged", False)
            flag_reason = entry.get("flag_reason", "")
            params = entry.get("params", "")
            caller = entry.get("caller", "")
            message = entry.get("message", "")

            # Format timestamp for display
            ts_display = ts[:16].replace("T", " ") if ts else _("Unknown time")

            # Title: operation + badge
            badge = "  \u2713" if result_val == "success" else "  \u2717"
            title = f"{op}{badge}"

            row = Adw.ExpanderRow(title=title, subtitle=ts_display)
            row.set_show_enable_switch(False)
            row.set_expanded(False)

            if flagged:
                icon = Gtk.Image(icon_name="dialog-warning-symbolic")
                icon.add_css_class("warning")
                icon.set_tooltip_text(flag_reason)
                row.add_suffix(icon)

            # Detail rows
            if params and params not in ("{}", ""):
                detail_params = Adw.ActionRow(title=_("Parameters"), subtitle=str(params))
                detail_params.set_subtitle_selectable(True)
                row.add_row(detail_params)
            if caller:
                detail_caller = Adw.ActionRow(title=_("Caller"), subtitle=str(caller))
                row.add_row(detail_caller)
            if message:
                detail_msg = Adw.ActionRow(title=_("Message"), subtitle=str(message))
                detail_msg.set_subtitle_selectable(True)
                row.add_row(detail_msg)
            if flag_reason:
                detail_flag = Adw.ActionRow(title=_("Security Flag"), subtitle=flag_reason)
                detail_flag.add_css_class("warning")
                row.add_row(detail_flag)

            self._audit_group.add(row)
            self._audit_entry_rows.append(row)

    def _on_filter_changed(self, _dropdown: Gtk.DropDown, _pspec) -> None:
        """Reload audit log when any filter changes."""
        self._load_audit_log()

    def _get_type_filter_value(self) -> str:
        """Get selected operation type filter value."""
        idx = self._type_filter.get_selected()
        if idx == 0:
            return ""
        model = self._type_filter.get_model()
        return model.get_string(idx) or ""

    def _get_result_filter_value(self) -> str:
        """Get selected result filter value."""
        idx = self._result_filter.get_selected()
        if idx == 0:
            return ""
        model = self._result_filter.get_model()
        return model.get_string(idx) or ""

    def _get_date_filter_values(self) -> tuple[str, str]:
        """Get date range filter as (date_from, date_to) UTC-aware ISO strings."""
        from datetime import UTC

        idx = self._date_filter.get_selected()
        if idx == 0:  # All Time
            return "", ""
        now = datetime.now(tz=UTC)
        if idx == 1:  # Today
            date_from = now.strftime("%Y-%m-%dT00:00:00+00:00")
        elif idx == 2:  # Last 7 Days
            date_from = (now - _timedelta(days=7)).strftime("%Y-%m-%dT00:00:00+00:00")
        elif idx == 3:  # Last 30 Days
            date_from = (now - _timedelta(days=30)).strftime("%Y-%m-%dT00:00:00+00:00")
        else:
            return "", ""
        return date_from, ""

    def _on_export_clicked(self, _btn: Gtk.Button) -> None:
        """Export filtered audit entries as JSONL to clipboard."""
        if not self._audit_entries_raw:
            return

        import json as _json

        lines = []
        for entry in self._audit_entries_raw:
            # Strip internal viewer fields from export
            clean = {k: v for k, v in entry.items() if k not in ("flagged", "flag_reason")}
            lines.append(_json.dumps(clean, separators=(",", ":")))
        export_text = "\n".join(lines)

        display = self.get_display()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set(export_text)

        window = self.get_root()
        if (
            isinstance(window, Adw.ApplicationWindow)
            and getattr(window, "toast_overlay", None) is not None
        ):
            count = len(self._audit_entries_raw)
            toast = Adw.Toast(
                title=_("Audit log copied to clipboard ({count} entries)").format(count=count),
            )
            toast.set_timeout(3)
            window.toast_overlay.add_toast(toast)

    # ── Data loading ──────────────────────────────────────────────────

    def _load_diagnostics(self) -> None:
        if self._dbus_client is None:
            return
        self._dbus_client.call_method_async("GetGPUInfo", None, self._on_gpu_info_reply)
        self._dbus_client.call_method_async("GetCurrentDriver", None, self._on_driver_reply)
        self._dbus_client.call_method_async("GetDegradedState", None, self._on_state_reply)
        self._load_audit_log()

    def _on_gpu_info_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            GLib.idle_add(self._populate_gpu_info, data)
        except GLib.Error as exc:
            log.warning("GetGPUInfo failed: %s", exc.message)
            GLib.idle_add(self._gpu_info_error)

    def _on_driver_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            GLib.idle_add(self._populate_driver_info, data)
        except GLib.Error as exc:
            log.warning("GetCurrentDriver failed: %s", exc.message)
            GLib.idle_add(self._driver_info_error)

    def _on_state_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            GLib.idle_add(self._populate_state_info, data)
        except GLib.Error as exc:
            log.warning("GetDegradedState failed: %s", exc.message)
            GLib.idle_add(self._state_info_error)

    # ── UI population ─────────────────────────────────────────────────

    def _populate_gpu_info(self, data: dict) -> bool:
        self._gpu_group.remove(self._gpu_loading_row)
        self._gpu_spinner.set_spinning(False)

        available = data.get("available", False)
        name = data.get("name", "")
        pci_bus = data.get("pci_bus_id", "")
        device_count = data.get("device_count", 0)

        if name:
            self._gpu_group.add(_make_row(_("GPU"), str(name)))
        else:
            self._gpu_group.add(_make_row(_("GPU"), _("Not detected (NVML unavailable)")))

        if pci_bus:
            self._gpu_group.add(_make_row(_("PCI Bus"), str(pci_bus)))

        if device_count:
            self._gpu_group.add(_make_row(_("Device Count"), str(device_count)))

        nvml_status = _("Available") if available else _("Unavailable")
        reason = data.get("reason", "")
        if reason:
            nvml_status += f" ({reason})"
        self._gpu_group.add(_make_status_row(_("NVML"), nvml_status, available))

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _gpu_info_error(self) -> bool:
        self._gpu_group.remove(self._gpu_loading_row)
        self._gpu_spinner.set_spinning(False)
        self._gpu_group.add(_make_row(_("GPU"), _("Failed to load GPU info")))
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _populate_driver_info(self, data: dict) -> bool:
        self._driver_group.remove(self._driver_loading_row)
        self._driver_spinner.set_spinning(False)

        version = data.get("version", "")
        loaded = data.get("loaded", False)
        driver_type = data.get("driver_type", "none")
        package_name = data.get("package_name", "")
        module_type = data.get("module_type", "")
        variant = data.get("variant", "")

        if version:
            self._driver_group.add(_make_row(_("Driver Version"), str(version)))
        else:
            self._driver_group.add(_make_row(_("Driver"), _("Not installed")))
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        if package_name:
            self._driver_group.add(_make_row(_("Package"), str(package_name)))

        self._driver_group.add(
            _make_status_row(
                _("Kernel Module"),
                _("Loaded") if loaded else _("Not loaded"),
                loaded,
            )
        )

        if driver_type and driver_type != "none":
            self._driver_group.add(_make_row(_("Driver Type"), str(driver_type)))

        if module_type:
            self._driver_group.add(_make_row(_("Module Type"), str(module_type)))

        if variant:
            self._driver_group.add(_make_row(_("Variant"), str(variant).capitalize()))

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _driver_info_error(self) -> bool:
        self._driver_group.remove(self._driver_loading_row)
        self._driver_spinner.set_spinning(False)
        self._driver_group.add(_make_row(_("Driver"), _("Failed to load driver info")))
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _populate_state_info(self, data: dict) -> bool:
        self._state_group.remove(self._state_loading_row)

        state = data.get("state", "unknown")
        message = data.get("message", "")
        is_ok = state in ("normal", "healthy")
        label = str(state).replace("_", " ").title()
        self._state_group.add(_make_status_row(_("State"), label, is_ok))

        if message:
            self._state_group.add(_make_row(_("Detail"), str(message)))

        # Extra info from enriched response (DRIVER_NOT_LOADED)
        drv_ver = data.get("driver_version", "")
        pkg_name = data.get("package_name", "")
        if drv_ver:
            self._state_group.add(_make_row(_("Installed Version"), str(drv_ver)))
        if pkg_name:
            self._state_group.add(_make_row(_("Installed Package"), str(pkg_name)))

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _state_info_error(self) -> bool:
        self._state_group.remove(self._state_loading_row)
        self._state_group.add(_make_row(_("State"), _("Failed to load health status")))
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]


def _read_os_release() -> str:
    """Read PRETTY_NAME from /etc/os-release."""
    try:
        for line in open("/etc/os-release"):  # noqa: SIM115
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""
