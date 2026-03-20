"""Drivers view — driver installation and management."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, Gtk

from verde.widgets.driver_card import build_driver_row
from verde.widgets.preflight_banner import PreflightPanel
from verde.widgets.progress_overlay import OperationProgressPanel
from verde.widgets.snapshot_row import build_snapshot_row

if TYPE_CHECKING:
    from verde.dbus_client import VerdeDBusClient
    from verde.gpu_state import GPUState

log = logging.getLogger("verde.views.drivers")


# gettext stub
try:
    _("test")  # type: ignore[used-before-def]
except NameError:
    import builtins

    if not hasattr(builtins, "_"):

        def _(s: str) -> str:
            return s

        builtins._ = _  # type: ignore[attr-defined]


def _has_resource(path: str) -> bool:
    try:
        Gio.resources_lookup_data(path, Gio.ResourceLookupFlags.NONE)
        return True
    except Exception:
        return False


class DriversPage(Adw.PreferencesPage):
    """Drivers view — browse drivers, pre-flight checks, install with progress."""

    __gtype_name__ = "DriversPage"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title(_("Drivers"))
        self.set_icon_name("application-x-firmware-symbolic")

        self._dbus_client: VerdeDBusClient | None = None
        self._gpu_state: GPUState | None = None
        self._driver_rows: list[Adw.ActionRow] = []
        self._snapshot_rows: list[Adw.ActionRow] = []
        self._rollback_buttons: list[Gtk.Button] = []
        self._current_op_id: str | None = None
        self._signal_handler_ids: list[tuple] = []
        self._active_dialog: Adw.MessageDialog | None = None
        self._install_in_progress: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        """Build the view programmatically (fallback when Blueprint unavailable)."""
        # ── Reboot Banner ──
        self._reboot_banner_group = Adw.PreferencesGroup()
        self._reboot_banner_group.set_visible(False)
        self.add(self._reboot_banner_group)

        self._reboot_banner = Adw.Banner()
        self._reboot_banner.set_title(_("Restart required to complete driver installation"))
        self._reboot_banner.set_button_label(_("Restart\u2026"))
        self._reboot_banner.set_revealed(True)
        self._reboot_banner.connect("button-clicked", self._on_reboot_banner_clicked)
        self._reboot_banner_group.add(self._reboot_banner)

        # ── .run File Detection Banner (FR77) ──
        self._run_file_banner_group = Adw.PreferencesGroup()
        self._run_file_banner_group.set_visible(False)
        self.add(self._run_file_banner_group)

        self._run_file_banner = Adw.Banner()
        self._run_file_banner.set_title(
            _(
                "NVIDIA driver installed via .run file — Verde cannot manage this installation. "
                "Consider uninstalling the .run driver and using Ubuntu repository drivers instead."
            )
        )
        self._run_file_banner.set_revealed(False)
        self._run_file_banner_group.add(self._run_file_banner)

        # ── Current Driver ──
        self._current_driver_group = Adw.PreferencesGroup(title=_("Current Driver"))
        self.add(self._current_driver_group)

        self._current_driver_expander = Adw.ExpanderRow()
        self._current_driver_expander.set_title(_("No Driver"))
        self._current_driver_expander.set_subtitle(_("Loading\u2026"))
        self._current_driver_expander.set_show_enable_switch(False)
        self._current_driver_expander.set_visible(False)
        self._current_driver_group.add(self._current_driver_expander)

        self._current_driver_cuda_row = Adw.ActionRow(title=_("CUDA Compatibility"))
        self._current_driver_expander.add_row(self._current_driver_cuda_row)

        self._current_driver_context_row = Adw.ActionRow(title=_("Driver Context"))
        self._current_driver_expander.add_row(self._current_driver_context_row)

        self._current_driver_kernel_row = Adw.ActionRow(title=_("Kernel Module"))
        self._current_driver_expander.add_row(self._current_driver_kernel_row)

        self._no_driver_status = Adw.StatusPage()
        self._no_driver_status.set_icon_name("system-software-install-symbolic")
        self._no_driver_status.set_title(_("No NVIDIA Driver Installed"))
        self._no_driver_status.set_description(
            _(
                "Install a driver from the Available Drivers section below to enable GPU acceleration."
            )
        )
        self._no_driver_status.set_visible(False)
        self._current_driver_group.add(self._no_driver_status)

        # ── Available Drivers ──
        self._available_drivers_group = Adw.PreferencesGroup(title=_("Available Drivers"))
        self.add(self._available_drivers_group)

        self._available_drivers_spinner = Gtk.Spinner()
        self._available_drivers_spinner.set_spinning(True)
        self._available_drivers_spinner.set_visible(False)
        self._available_drivers_spinner.set_halign(Gtk.Align.CENTER)
        self._available_drivers_spinner.set_valign(Gtk.Align.CENTER)
        self._available_drivers_spinner.set_size_request(32, 32)
        self._available_drivers_group.add(self._available_drivers_spinner)

        self._no_drivers_status = Adw.StatusPage()
        self._no_drivers_status.set_icon_name("dialog-information-symbolic")
        self._no_drivers_status.set_title(_("No Drivers Found"))
        self._no_drivers_status.set_description(
            _("No compatible NVIDIA drivers were found for this system.")
        )
        self._no_drivers_status.set_visible(False)
        self._available_drivers_group.add(self._no_drivers_status)

        # ── Snapshots ──
        self._snapshots_group = Adw.PreferencesGroup(title=_("Snapshots"))
        self.add(self._snapshots_group)

        self._no_snapshots_status = Adw.StatusPage()
        self._no_snapshots_status.set_icon_name("drive-harddisk-symbolic")
        self._no_snapshots_status.set_title(_("No Snapshots Available"))
        self._no_snapshots_status.set_description(
            _("Snapshots are created automatically before driver changes.")
        )
        self._snapshots_group.add(self._no_snapshots_status)

        # ── Daemon Unreachable ──
        self._unreachable_group = Adw.PreferencesGroup()
        self._unreachable_group.set_visible(False)
        self.add(self._unreachable_group)

        self._unreachable_status = Adw.StatusPage()
        self._unreachable_status.set_icon_name("network-error-symbolic")
        self._unreachable_status.set_title(_("Service Unavailable"))
        self._unreachable_status.set_description(
            _(
                "The Verde system service is not responding. Check that the service is running and try again."
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

    # ── Public API ──────────────────────────────────────────────────

    def _disconnect_signals(self) -> None:
        """Disconnect all tracked signal handlers."""
        for obj, handler_id in self._signal_handler_ids:
            with contextlib.suppress(Exception):
                obj.disconnect(handler_id)
        self._signal_handler_ids.clear()

    def bind_state(self, gpu_state: GPUState, dbus_client: VerdeDBusClient) -> None:
        """Bind to GPUState and VerdeDBusClient for live updates."""
        # Disconnect previous handlers if re-binding
        self._disconnect_signals()

        self._gpu_state = gpu_state
        self._dbus_client = dbus_client

        # Connect to D-Bus client signals — store (object, handler_id) tuples
        self._signal_handler_ids.extend(
            [
                (
                    dbus_client,
                    dbus_client.connect("notify::connected", self._on_connection_changed),
                ),
                (
                    dbus_client,
                    dbus_client.connect("operation-progress", self._on_operation_progress),
                ),
                (
                    dbus_client,
                    dbus_client.connect("operation-complete", self._on_operation_complete),
                ),
            ]
        )

        # Connect to GPUState reboot signal
        self._signal_handler_ids.append(
            (
                gpu_state,
                gpu_state.connect("notify::reboot-required", self._on_reboot_required_changed),
            )
        )

        # Initial data load if connected
        if dbus_client.get_property("connected"):
            self._load_driver_data()
        else:
            self._show_unreachable()

        # Check initial reboot state
        if gpu_state.get_property("reboot-required"):
            self._reboot_banner_group.set_visible(True)

    # ── Connection state ────────────────────────────────────────────

    def _on_connection_changed(self, client: VerdeDBusClient, _pspec) -> None:
        if client.get_property("connected"):
            self._hide_unreachable()
            self._load_driver_data()
        else:
            self._show_unreachable()

    def _show_unreachable(self) -> None:
        self._current_driver_group.set_visible(False)
        self._available_drivers_group.set_visible(False)
        self._snapshots_group.set_visible(False)
        self._unreachable_group.set_visible(True)

    def _hide_unreachable(self) -> None:
        self._unreachable_group.set_visible(False)
        self._current_driver_group.set_visible(True)
        self._available_drivers_group.set_visible(True)
        self._snapshots_group.set_visible(True)

    def _on_retry_clicked(self, _btn: Gtk.Button) -> None:
        if self._dbus_client is not None:
            self._dbus_client.connect_async()

    # ── Data loading (async) ────────────────────────────────────────

    def _load_driver_data(self) -> None:
        """Load current driver and available drivers from daemon."""
        if self._dbus_client is None:
            return

        self._available_drivers_spinner.set_visible(True)
        self._available_drivers_spinner.set_spinning(True)

        self._dbus_client.call_method_async(
            "GetCurrentDriver", None, self._on_current_driver_reply
        )
        self._dbus_client.call_method_async(
            "ListAvailableDrivers", None, self._on_available_drivers_reply
        )
        self._dbus_client.call_method_async("ListSnapshots", None, self._on_snapshots_reply)

    def _on_current_driver_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            data = reply.unpack()[0]
            GLib.idle_add(self._populate_current_driver, data)
        except GLib.Error as exc:
            log.warning("GetCurrentDriver failed: %s", exc.message)
            GLib.idle_add(self._show_no_driver)

    def _on_available_drivers_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            unpacked = reply.unpack()
            drivers = unpacked[0]
            metadata = unpacked[1] if len(unpacked) > 1 else {}
            GLib.idle_add(self._populate_available_drivers, drivers, metadata)
        except GLib.Error as exc:
            log.warning("ListAvailableDrivers failed: %s", exc.message)
            GLib.idle_add(self._show_no_drivers_available)

    def _on_snapshots_reply(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
        try:
            reply = proxy.call_finish(result)
            snapshots = reply.unpack()[0]
            GLib.idle_add(self._populate_snapshots, snapshots)
        except GLib.Error as exc:
            log.warning("ListSnapshots failed: %s", exc.message)

    # ── UI population ───────────────────────────────────────────────

    def _populate_current_driver(self, data: dict) -> bool:
        """Populate Current Driver group from D-Bus response."""
        version = data.get("version", "")
        if not version:
            self._show_no_driver()
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        loaded = data.get("loaded", True)
        package = data.get("package_name", "") or data.get("package", f"nvidia-driver-{version}")
        variant = data.get("variant", "proprietary")
        cuda_version = data.get("cuda_version", "")
        context = data.get("context", "")
        kernel_module = data.get("kernel_module", "")

        self._no_driver_status.set_visible(False)
        self._current_driver_expander.set_visible(True)
        self._current_driver_expander.set_title(package)
        if loaded:
            self._current_driver_expander.set_subtitle(
                _("Version {} - {} - Installed").format(version, variant.capitalize())
            )
        else:
            self._current_driver_expander.set_subtitle(
                _("Version {} - {} - Installed (module not loaded)").format(
                    version, variant.capitalize()
                )
            )
            self._current_driver_expander.add_css_class("warning")

        cuda_val = cuda_version if cuda_version else _("Not available")
        context_val = context if context else _("Standard")
        kernel_val = kernel_module if kernel_module else _("Not available")

        self._current_driver_cuda_row.set_subtitle(cuda_val)
        self._current_driver_cuda_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("CUDA Compatibility: {}").format(cuda_val)],
        )
        self._current_driver_context_row.set_subtitle(context_val)
        self._current_driver_context_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Driver Context: {}").format(context_val)],
        )
        self._current_driver_kernel_row.set_subtitle(kernel_val)
        self._current_driver_kernel_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Kernel Module: {}").format(kernel_val)],
        )

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _show_no_driver(self) -> bool:
        self._current_driver_expander.set_visible(False)
        self._no_driver_status.set_visible(True)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _populate_available_drivers(self, drivers: list, metadata: dict | None = None) -> bool:
        """Populate Available Drivers group from D-Bus response."""
        self._available_drivers_spinner.set_visible(False)
        self._available_drivers_spinner.set_spinning(False)

        # Clear old rows
        for row in self._driver_rows:
            self._available_drivers_group.remove(row)
        self._driver_rows.clear()

        # Check for .run file installation (FR77)
        metadata = metadata or {}
        run_detected = metadata.get("run_file_detected", False)
        run_message = metadata.get("run_file_message", "")

        if run_detected:
            self._show_run_file_banner(run_message)
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        self._run_file_banner.set_revealed(False)

        if not drivers:
            self._show_no_drivers_available()
            return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

        self._no_drivers_status.set_visible(False)

        # Enforce single recommended driver — only first gets .suggested-action
        seen_recommended = False
        for driver in drivers:
            if driver.get("recommended", False) and seen_recommended:
                driver = {**driver, "recommended": False}
            elif driver.get("recommended", False):
                seen_recommended = True
            row = build_driver_row(driver, on_install_clicked=self._on_install_clicked)
            self._available_drivers_group.add(row)
            self._driver_rows.append(row)

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _show_run_file_banner(self, message: str = "") -> None:
        """Show .run file detection banner and disable driver management."""
        self._run_file_banner_group.set_visible(True)
        if message:
            self._run_file_banner.set_title(message)
        self._run_file_banner.set_revealed(True)
        self._no_drivers_status.set_visible(False)

    def _disable_driver_rows(self) -> None:
        """Disable all driver install rows (e.g. when .run file detected)."""
        for row in self._driver_rows:
            row.set_sensitive(False)

    def _show_no_drivers_available(self) -> bool:
        self._available_drivers_spinner.set_visible(False)
        self._available_drivers_spinner.set_spinning(False)
        self._no_drivers_status.set_visible(True)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _populate_snapshots(self, snapshots: list) -> bool:
        """Populate Snapshots group from D-Bus response."""
        # Clear old snapshot rows
        for row in self._snapshot_rows:
            self._snapshots_group.remove(row)
        self._snapshot_rows.clear()
        self._rollback_buttons.clear()

        if not snapshots:
            self._no_snapshots_status.set_visible(True)
            self._snapshots_group.set_description("")
        else:
            self._no_snapshots_status.set_visible(False)
            for snap in snapshots:
                row, rollback_btn = build_snapshot_row(
                    snap,
                    on_rollback_clicked=self._on_rollback_clicked,
                    on_delete_clicked=self._on_delete_clicked,
                )
                self._snapshots_group.add(row)
                self._snapshot_rows.append(row)
                if rollback_btn is not None:
                    self._rollback_buttons.append(rollback_btn)
            self._update_snapshot_storage_summary(snapshots)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _update_snapshot_storage_summary(self, snapshots: list) -> None:
        """Update Snapshots group description with storage summary."""
        from verde.widgets.snapshot_row import _format_file_size

        count = len(snapshots)
        total_bytes = sum(s.get("file_size", 0) for s in snapshots)
        size_str = _format_file_size(total_bytes)
        self._snapshots_group.set_description(
            _("{} snapshots \u2014 {} total").format(count, size_str)
        )

    def _on_rollback_clicked(self, _btn: Gtk.Button, snapshot: dict) -> None:
        """Handle rollback button click — show pre-flight confirmation dialog (AC#2)."""
        if self._install_in_progress:
            return

        snapshot_id = snapshot.get("id", "")
        if not snapshot_id:
            return

        log.info("Rollback requested for snapshot %s", snapshot_id)

        self._install_in_progress = True
        self._set_install_buttons_sensitive(False)
        self._set_rollback_buttons_sensitive(False)

        window = self.get_root()
        dialog = Adw.MessageDialog.new(
            window,
            _("Rollback to Snapshot"),
        )
        dialog.set_body_use_markup(False)

        # Pre-flight panel as extra child
        preflight = PreflightPanel()
        preflight.set_loading()
        dialog.set_extra_child(preflight)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rollback", _("Rollback"))
        dialog.set_response_appearance("rollback", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled("rollback", False)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        dialog._preflight = preflight
        dialog._snapshot = snapshot
        dialog._snapshot_id = snapshot_id
        dialog._is_rollback = True

        dialog.connect("response", self._on_rollback_dialog_response)
        dialog.connect("close-request", self._on_dialog_closed)

        # Load rollback pre-flight checks
        self._load_rollback_preflight(dialog, snapshot_id)

        self._active_dialog = dialog
        dialog.present()

    def _load_rollback_preflight(self, dialog: Adw.MessageDialog, snapshot_id: str) -> None:
        """Load rollback-specific pre-flight checks in background."""
        if self._dbus_client is None:
            return

        def _on_reply(proxy, result):
            try:
                reply = proxy.call_finish(result)
                data = reply.unpack()[0]
                GLib.idle_add(self._on_rollback_preflight_result, dialog, data)
            except GLib.Error as exc:
                log.warning("GetPreflightCheck (rollback) failed: %s", exc.message)
                GLib.idle_add(self._on_preflight_error, dialog, str(exc.message))

        self._dbus_client.call_method_async(
            "GetPreflightCheck",
            GLib.Variant("(s)", (f"rollback:{snapshot_id}",)),
            _on_reply,
        )

    def _on_rollback_preflight_result(self, dialog: Adw.MessageDialog, data: dict) -> bool:
        """Handle rollback pre-flight results on main thread."""
        preflight: PreflightPanel = dialog._preflight

        checks = data.get("checks", [])
        current_driver = data.get("current_driver", "")
        snapshot_driver = data.get("snapshot_driver", "")

        # Build changes list showing current vs. snapshot state
        changes = []
        if current_driver or snapshot_driver:
            changes.append(
                {
                    "name": _("Current driver: {}").format(current_driver or _("unknown")),
                    "status": "info",
                    "description": _("Snapshot driver: {}").format(
                        snapshot_driver or _("unknown")
                    ),
                }
            )

        rollback_plan = _(
            "Driver packages will be restored to snapshot state. A restart will be required."
        )

        preflight.set_checks(checks)
        preflight.set_changes(changes)
        preflight.set_rollback_plan(rollback_plan)

        dialog.set_response_enabled("rollback", preflight.all_passed)

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_rollback_dialog_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle rollback dialog response — start rollback or diagnostic."""
        if response == "diagnostic":
            # Navigate to Diagnostics tab (FR55)
            window = self.get_root()
            if window is not None and hasattr(window, "navigate_to"):
                window.navigate_to("diagnostics")
            return

        if response != "rollback":
            return

        snapshot_id = dialog._snapshot_id

        # Switch dialog to progress mode (AC#4)
        progress_panel = OperationProgressPanel()
        progress_panel.set_stage(_("Starting rollback\u2026"), 0.0)
        dialog.set_extra_child(progress_panel)
        dialog.set_heading(_("Rolling Back Driver"))
        dialog.set_body("")

        # No cancel during rollback (AC#4 — partial rollbacks are dangerous)
        dialog.set_close_response("")
        dialog.set_response_enabled("cancel", False)
        dialog.set_response_enabled("rollback", False)

        dialog._progress_panel = progress_panel

        # Call RollbackDriver via D-Bus
        self._start_rollback(snapshot_id)

    def _start_rollback(self, snapshot_id: str) -> None:
        """Initiate driver rollback via D-Bus."""
        if self._dbus_client is None:
            if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
                panel = self._active_dialog._progress_panel
                panel.set_error(_("Service unavailable — cannot start rollback"))
                self._active_dialog.set_close_response("cancel")
                self._active_dialog.set_response_enabled("cancel", True)
            return

        def _on_reply(proxy, result):
            try:
                reply = proxy.call_finish(result)
                op_id = reply.unpack()[0]
                GLib.idle_add(self._on_rollback_started, op_id)
            except GLib.Error as exc:
                log.warning("RollbackDriver failed: %s", exc.message)
                GLib.idle_add(self._on_rollback_start_error, str(exc.message))

        self._dbus_client.rollback_driver(snapshot_id, _on_reply)

    def _on_rollback_started(self, op_id: str) -> bool:
        """Handle successful RollbackDriver call — store op_id."""
        self._current_op_id = op_id
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_rollback_start_error(self, error_text: str) -> bool:
        """Handle RollbackDriver call failure."""
        if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
            panel = self._active_dialog._progress_panel
            panel.set_error(_sanitize_dbus_error(error_text))
            self._active_dialog.set_close_response("cancel")
            self._active_dialog.set_response_enabled("cancel", True)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _set_rollback_buttons_sensitive(self, sensitive: bool) -> None:
        """Enable/disable all Rollback buttons in snapshot rows."""
        tooltip = "" if sensitive else _("Another operation is in progress")
        for btn in self._rollback_buttons:
            btn.set_sensitive(sensitive)
            btn.set_tooltip_text(tooltip)

    def _on_delete_clicked(self, _btn: Gtk.Button, snapshot: dict) -> None:
        """Handle delete button click — show confirmation dialog."""
        self._show_delete_confirmation(snapshot)

    def _show_delete_confirmation(self, snapshot: dict) -> None:
        """Show Adw.MessageDialog to confirm snapshot deletion (AC#4)."""
        from verde.widgets.snapshot_row import _format_timestamp

        snapshot_id = snapshot.get("id", "")
        timestamp = snapshot.get("timestamp", "")
        driver_version = snapshot.get("driver_version", "")
        human_date = _format_timestamp(timestamp)

        window = self.get_root()
        dialog = Adw.MessageDialog.new(window, _("Delete Snapshot?"))
        dialog.set_body(
            _(
                "This snapshot from {} (driver {}) will be permanently removed. "
                "You will not be able to rollback to this state."
            ).format(human_date, driver_version)
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        dialog._snapshot_id = snapshot_id
        dialog.connect("response", self._on_delete_dialog_response)
        dialog.present()

    def _on_delete_dialog_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle delete confirmation dialog response."""
        if response != "delete":
            return

        snapshot_id = dialog._snapshot_id
        if self._dbus_client is None:
            return

        def _on_reply(proxy, result):
            try:
                proxy.call_finish(result)
                GLib.idle_add(self._load_driver_data)
            except GLib.Error as exc:
                log.warning("DeleteSnapshot failed: %s", exc.message)
                GLib.idle_add(self._show_delete_error, str(exc.message))

        self._dbus_client.call_method_async(
            "DeleteSnapshot",
            GLib.Variant("(s)", (snapshot_id,)),
            _on_reply,
        )

    def _show_delete_error(self, error_text: str) -> bool:
        """Show error dialog after failed snapshot deletion."""
        window = self.get_root()
        dialog = Adw.MessageDialog.new(window, _("Deletion Failed"))
        dialog.set_body(_sanitize_dbus_error(error_text))
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present()
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    # ── Install flow (Task 5) ───────────────────────────────────────

    def _set_install_buttons_sensitive(self, sensitive: bool) -> None:
        """Enable/disable all Install buttons to prevent concurrent flows."""
        for row in self._driver_rows:
            # The install button is inside a suffix box
            suffix = row.get_last_child()
            if suffix is not None:
                child = suffix.get_last_child()
                if isinstance(child, Gtk.Button):
                    child.set_sensitive(sensitive)

    def _on_install_clicked(self, _btn: Gtk.Button, driver: dict) -> None:
        """Open pre-flight dialog for driver installation."""
        if self._install_in_progress:
            return

        version = driver.get("version", "")
        package = driver.get("package", f"nvidia-driver-{version}")
        version_short = version.split(".")[0] if version else version

        self._install_in_progress = True
        self._set_install_buttons_sensitive(False)

        # Find the top-level window for dialog parenting
        window = self.get_root()

        dialog = Adw.MessageDialog.new(
            window,
            _("Install {}").format(package),
        )
        dialog.set_body_use_markup(False)

        # Pre-flight panel as extra child
        preflight = PreflightPanel()
        preflight.set_loading()
        dialog.set_extra_child(preflight)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install Driver {}").format(version_short))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("install", False)  # Disabled until checks pass
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        # Store references for callbacks
        dialog._preflight = preflight
        dialog._driver = driver
        dialog._version = version

        dialog.connect("response", self._on_install_dialog_response)
        dialog.connect("close-request", self._on_dialog_closed)

        # Load pre-flight checks asynchronously
        self._load_preflight(dialog)

        self._active_dialog = dialog
        dialog.present()

    def _on_dialog_closed(self, dialog: Adw.MessageDialog) -> bool:
        """Clean up when install/rollback dialog is closed."""
        self._active_dialog = None
        self._install_in_progress = False
        self._set_install_buttons_sensitive(True)
        self._set_rollback_buttons_sensitive(True)
        return False  # Allow default close behavior

    def _load_preflight(self, dialog: Adw.MessageDialog) -> None:
        """Load pre-flight check results in background thread."""
        if self._dbus_client is None:
            return

        def _on_reply(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
                data = reply.unpack()[0]
                GLib.idle_add(self._on_preflight_result, dialog, data)
            except GLib.Error as exc:
                log.warning("GetPreflightCheck failed: %s", exc.message)
                GLib.idle_add(self._on_preflight_error, dialog, str(exc.message))

        self._dbus_client.call_method_async(
            "GetPreflightCheck",
            GLib.Variant("(s)", ("install",)),
            _on_reply,
        )

    def _on_preflight_result(self, dialog: Adw.MessageDialog, data: dict) -> bool:
        """Handle pre-flight check results on main thread."""
        preflight: PreflightPanel = dialog._preflight

        checks = data.get("checks", [])
        changes = data.get("changes", [])
        rollback_plan = data.get(
            "rollback_plan", _("System snapshot will be created before installation")
        )

        preflight.set_checks(checks)
        preflight.set_changes(changes)
        preflight.set_rollback_plan(rollback_plan)

        # Enable Install button only if all checks passed
        dialog.set_response_enabled("install", preflight.all_passed)

        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_preflight_error(self, dialog: Adw.MessageDialog, error_text: str) -> bool:
        """Handle pre-flight check error on main thread."""
        dialog._preflight.set_error(_sanitize_dbus_error(error_text))
        if getattr(dialog, "_is_rollback", False) is True:
            dialog.set_response_enabled("rollback", False)
        else:
            dialog.set_response_enabled("install", False)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_install_dialog_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle install dialog response."""
        if response != "install":
            return

        version = dialog._version

        # Switch dialog to progress mode
        progress_panel = OperationProgressPanel()
        progress_panel.set_stage(_("Starting installation\u2026"), 0.0)
        dialog.set_extra_child(progress_panel)
        dialog.set_heading(_("Installing Driver"))
        dialog.set_body("")

        # Prevent dialog close during install — no cancel (partial installs are dangerous)
        dialog.set_close_response("")  # Escape key does nothing
        dialog.set_response_enabled("cancel", False)
        dialog.set_response_enabled("install", False)

        dialog._progress_panel = progress_panel

        # Call InstallDriver via D-Bus
        self._start_install(version)

    def _start_install(self, version: str) -> None:
        """Initiate driver install via D-Bus."""
        if self._dbus_client is None:
            return

        def _on_reply(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
                op_id = reply.unpack()[0]
                GLib.idle_add(self._on_install_started, op_id)
            except GLib.Error as exc:
                log.warning("InstallDriver failed: %s", exc.message)
                GLib.idle_add(self._on_install_start_error, str(exc.message))

        self._dbus_client.call_method_async(
            "InstallDriver",
            GLib.Variant("(s)", (version,)),
            _on_reply,
        )

    def _on_install_started(self, op_id: str) -> bool:
        """Handle successful InstallDriver call — store op_id."""
        self._current_op_id = op_id
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    def _on_install_start_error(self, error_text: str) -> bool:
        """Handle InstallDriver call failure."""
        if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
            panel = self._active_dialog._progress_panel
            panel.set_error(_sanitize_dbus_error(error_text))
            self._active_dialog.set_close_response("cancel")
            self._active_dialog.set_response_enabled("cancel", True)
        return GLib.SOURCE_REMOVE  # type: ignore[no-any-return]

    # ── D-Bus signal handlers ───────────────────────────────────────

    def _on_operation_progress(
        self, client: VerdeDBusClient, op_id: str, percent: float, message: str
    ) -> None:
        """Handle OperationProgress signal."""
        if op_id != self._current_op_id:
            return
        if self._active_dialog is not None and hasattr(self._active_dialog, "_progress_panel"):
            panel = self._active_dialog._progress_panel
            panel.set_stage(message, percent / 100.0)
            # Parse stage count from message if available (e.g., "Step 2 of 4: Unpacking...")
            if ":" in message:
                prefix = message.split(":")[0].strip()
                if prefix.startswith("Step ") and " of " in prefix:
                    try:
                        parts = prefix.replace("Step ", "").split(" of ")
                        panel.set_stage_count(int(parts[0]), int(parts[1]))
                    except (ValueError, IndexError):
                        pass

    def _on_operation_complete(
        self, client: VerdeDBusClient, op_id: str, success: bool, message: str
    ) -> None:
        """Handle OperationComplete signal."""
        if op_id != self._current_op_id:
            return
        self._current_op_id = None

        if self._active_dialog is None or not hasattr(self._active_dialog, "_progress_panel"):
            return

        dialog = self._active_dialog
        panel = dialog._progress_panel
        is_rollback = getattr(dialog, "_is_rollback", False) is True

        if success:
            if is_rollback:
                panel.set_success(message or _("Driver rollback completed successfully"))
                dialog.set_heading(_("Rollback Complete"))
            else:
                panel.set_success(message or _("Driver installed successfully"))
                dialog.set_heading(_("Installation Complete"))
        else:
            # Try to parse structured error (BS-2: JSON in message field)
            error_data = parse_structured_error(message)
            if error_data:
                safe_msg = format_error_message(error_data)
            else:
                default_msg = _("Rollback failed") if is_rollback else _("Installation failed")
                safe_msg = _sanitize_dbus_error(message) if message else default_msg
            panel.set_error(safe_msg)

            if is_rollback:
                dialog.set_heading(_("Rollback Failed"))
                # AC#6: Offer alternative recovery paths (FR55)
                if not dialog.has_response("diagnostic"):
                    dialog.add_response("diagnostic", _("Generate Diagnostic Report"))
                    dialog.set_response_appearance("diagnostic", Adw.ResponseAppearance.DEFAULT)
            else:
                dialog.set_heading(_("Installation Failed"))
                # Add error action buttons per AC7
                if not dialog.has_response("rollback"):
                    dialog.add_response("rollback", _("Rollback to Previous Driver"))
                    dialog.set_response_appearance("rollback", Adw.ResponseAppearance.SUGGESTED)
                if not dialog.has_response("details"):
                    dialog.add_response("details", _("View Details"))
                    dialog.set_response_appearance("details", Adw.ResponseAppearance.DEFAULT)

        dialog.set_close_response("cancel")  # Re-enable Escape to close
        dialog.set_response_enabled("cancel", True)
        # Refresh driver data
        self._load_driver_data()

    # ── Reboot banner ───────────────────────────────────────────────

    def _on_reboot_required_changed(self, gpu_state: GPUState, _pspec) -> None:
        required = gpu_state.get_property("reboot-required")
        self._reboot_banner_group.set_visible(required)

    def _on_reboot_banner_clicked(self, _banner: Adw.Banner) -> None:
        """Handle reboot banner button click — open restart confirmation."""
        window = self.get_root()
        dialog = Adw.MessageDialog.new(
            window,
            _("Restart System"),
        )
        dialog.set_body(
            _(
                "A restart is required to finish the driver installation. "
                "Save your work before restarting."
            )
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restart", _("Restart Now"))
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reboot_dialog_response)
        dialog.present()

    def _on_reboot_dialog_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle reboot confirmation dialog response."""
        if response != "restart":
            return
        import subprocess

        try:
            subprocess.Popen(["systemctl", "reboot"])
        except FileNotFoundError:
            log.warning("systemctl not found, cannot initiate reboot")


def _sanitize_dbus_error(error_text: str) -> str:
    """Convert raw D-Bus/GLib error strings into user-friendly messages."""
    if not error_text:
        return _("An unexpected error occurred")

    # Strip GLib error domain prefixes (e.g., "g-dbus-error-quark: ")
    if ": " in error_text and error_text.startswith("g-"):
        error_text = error_text.split(": ", 1)[1]

    # Strip Python traceback indicators
    if "Traceback" in error_text or "Error:" in error_text:
        lines = error_text.strip().splitlines()
        error_text = lines[-1] if lines else error_text

    return error_text


def parse_structured_error(message: str) -> dict | None:
    """Parse a JSON-encoded structured error dict from OperationComplete message.

    Returns a dict with keys: error_title, error_description, error_primary_action,
    error_secondary_action, error_category, recoverable.
    Returns None if message is not valid JSON or not a structured error.
    """
    try:
        data = json.loads(message)
        if isinstance(data, dict) and "error_title" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def format_error_message(error_dict: dict) -> str:
    """Format a structured error dict into UX-DR16 compliant display text.

    Pattern: title + description + primary action + secondary action.
    No raw exceptions, no error codes.
    """
    title = error_dict.get("error_title", _("An error occurred"))
    description = error_dict.get("error_description", "")
    primary = error_dict.get("error_primary_action", "")
    secondary = error_dict.get("error_secondary_action", "")

    parts = [title]
    if description:
        parts.append(description)
    if primary:
        parts.append(_("Suggested action: {}").format(primary))
    if secondary:
        parts.append(_("Alternative: {}").format(secondary))
    return "\n\n".join(parts)
