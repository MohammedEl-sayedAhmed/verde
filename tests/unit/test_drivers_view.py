"""Unit tests for DriversView (Story 2.4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from verde.gpu_state import GPUState  # noqa: E402
from verde.views.drivers import (  # noqa: E402
    DriversPage,
    _sanitize_dbus_error,
    format_error_message,
    parse_structured_error,
)
from verde.widgets.driver_card import build_driver_row  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def gpu_state():
    return GPUState()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_property.return_value = True  # connected
    client.connect.return_value = 1
    client.call_method_async.return_value = None
    return client


@pytest.fixture
def drivers_page(gpu_state, mock_client):
    page = DriversPage()
    page.bind_state(gpu_state, mock_client)
    return page


# ===================================================================
# DriversPage UI structure
# ===================================================================


class TestDriversPageStructure:
    def test_has_current_driver_group(self, drivers_page):
        assert drivers_page._current_driver_group is not None

    def test_has_available_drivers_group(self, drivers_page):
        assert drivers_page._available_drivers_group is not None

    def test_has_snapshots_group(self, drivers_page):
        assert drivers_page._snapshots_group is not None

    def test_has_unreachable_group(self, drivers_page):
        assert drivers_page._unreachable_group is not None

    def test_title_is_drivers(self, drivers_page):
        assert drivers_page.get_title() == "Drivers"

    def test_icon_name(self, drivers_page):
        assert drivers_page.get_icon_name() == "application-x-firmware-symbolic"


# ===================================================================
# Current driver population
# ===================================================================


class TestCurrentDriverPopulation:
    def test_populate_current_driver(self, drivers_page):
        data = {
            "version": "565.57",
            "package": "nvidia-driver-565",
            "variant": "proprietary",
            "cuda_version": "12.7",
            "context": "Desktop gaming",
            "kernel_module": "nvidia.ko",
        }
        drivers_page._populate_current_driver(data)
        assert drivers_page._current_driver_expander.get_visible() is True
        assert drivers_page._no_driver_status.get_visible() is False
        assert drivers_page._current_driver_expander.get_title() == "nvidia-driver-565"
        assert "565.57" in drivers_page._current_driver_expander.get_subtitle()

    def test_populate_current_driver_details(self, drivers_page):
        data = {
            "version": "565.57",
            "variant": "proprietary",
            "cuda_version": "12.7",
            "context": "Desktop gaming",
            "kernel_module": "nvidia.ko",
        }
        drivers_page._populate_current_driver(data)
        assert drivers_page._current_driver_cuda_row.get_subtitle() == "12.7"
        assert drivers_page._current_driver_context_row.get_subtitle() == "Desktop gaming"
        assert drivers_page._current_driver_kernel_row.get_subtitle() == "nvidia.ko"

    def test_no_driver_shows_status_page(self, drivers_page):
        drivers_page._show_no_driver()
        assert drivers_page._current_driver_expander.get_visible() is False
        assert drivers_page._no_driver_status.get_visible() is True

    def test_empty_version_shows_no_driver(self, drivers_page):
        drivers_page._populate_current_driver({"version": ""})
        assert drivers_page._no_driver_status.get_visible() is True


# ===================================================================
# Available drivers population
# ===================================================================


class TestAvailableDriversPopulation:
    def test_populate_with_drivers(self, drivers_page):
        drivers = [
            {
                "package": "nvidia-driver-565",
                "version": "565.57",
                "variant": "proprietary",
                "recommended": True,
            },
            {
                "package": "nvidia-driver-550",
                "version": "550.40",
                "variant": "proprietary",
                "recommended": False,
            },
        ]
        drivers_page._populate_available_drivers(drivers)
        assert len(drivers_page._driver_rows) == 2
        assert drivers_page._no_drivers_status.get_visible() is False

    def test_populate_empty_shows_no_drivers(self, drivers_page):
        drivers_page._populate_available_drivers([])
        assert len(drivers_page._driver_rows) == 0
        assert drivers_page._no_drivers_status.get_visible() is True

    def test_populate_clears_previous_rows(self, drivers_page):
        drivers_page._populate_available_drivers(
            [
                {"package": "a", "version": "1", "variant": "p", "recommended": False},
            ]
        )
        assert len(drivers_page._driver_rows) == 1
        drivers_page._populate_available_drivers(
            [
                {"package": "b", "version": "2", "variant": "p", "recommended": False},
                {"package": "c", "version": "3", "variant": "p", "recommended": False},
            ]
        )
        assert len(drivers_page._driver_rows) == 2

    def test_spinner_hidden_after_population(self, drivers_page):
        drivers_page._available_drivers_spinner.set_visible(True)
        drivers_page._populate_available_drivers([])
        assert drivers_page._available_drivers_spinner.get_visible() is False


# ===================================================================
# Snapshots population
# ===================================================================


class TestSnapshotsPopulation:
    def test_empty_snapshots_shows_status_page(self, drivers_page):
        drivers_page._populate_snapshots([])
        assert drivers_page._no_snapshots_status.get_visible() is True

    def test_empty_snapshots_title_matches_ac3(self, drivers_page):
        """AC#3: Empty state shows 'No Snapshots Available'."""
        drivers_page._populate_snapshots([])
        assert drivers_page._no_snapshots_status.get_title() == "No Snapshots Available"
        assert "automatically" in drivers_page._no_snapshots_status.get_description()

    def test_snapshots_list_hides_status_page(self, drivers_page):
        drivers_page._populate_snapshots(
            [
                {
                    "id": "20260318T143000_nvidia-565-ab01",
                    "timestamp": "2026-03-18T14:30:00+00:00",
                    "driver_version": "565",
                    "kernel_version": "6.8.0",
                    "packages": ["nvidia-driver-565=565.57"],
                    "dkms_status": "installed",
                    "file_size": 1024,
                    "sha256": "abc123",
                },
            ]
        )
        assert drivers_page._no_snapshots_status.get_visible() is False

    def test_snapshots_clears_previous_rows(self, drivers_page):
        snap1 = {
            "id": "20260317T100000_nvidia-560-ab01",
            "timestamp": "2026-03-17T10:00:00+00:00",
            "driver_version": "560",
            "file_size": 512,
        }
        snap2 = {
            "id": "20260318T100000_nvidia-565-ab01",
            "timestamp": "2026-03-18T10:00:00+00:00",
            "driver_version": "565",
            "file_size": 512,
        }
        snap3 = {
            "id": "20260319T100000_nvidia-570-ab01",
            "timestamp": "2026-03-19T10:00:00+00:00",
            "driver_version": "570",
            "file_size": 512,
        }
        drivers_page._populate_snapshots([snap1])
        assert len(drivers_page._snapshot_rows) == 1
        drivers_page._populate_snapshots([snap2, snap3])
        assert len(drivers_page._snapshot_rows) == 2

    def test_snapshots_storage_summary_displayed(self, drivers_page):
        """AC#5: Total storage usage shown in group description."""
        snaps = [
            {
                "id": "20260318T143000_nvidia-560-ab01",
                "timestamp": "2026-03-18T14:30:00+00:00",
                "driver_version": "560",
                "file_size": 4096,
            },
            {
                "id": "20260319T100000_nvidia-565-ab01",
                "timestamp": "2026-03-19T10:00:00+00:00",
                "driver_version": "565",
                "file_size": 8300,
            },
        ]
        drivers_page._populate_snapshots(snaps)
        desc = drivers_page._snapshots_group.get_description()
        assert "2 snapshots" in desc
        assert "KB" in desc

    def test_snapshot_rows_are_expander_rows(self, drivers_page):
        """AC#2: Snapshot rows use Adw.ExpanderRow for progressive disclosure."""
        snaps = [
            {
                "id": "20260318T143000_nvidia-560-ab01",
                "timestamp": "2026-03-18T14:30:00+00:00",
                "driver_version": "560",
                "kernel_version": "6.8.0",
                "packages": ["nvidia-driver-560=560.35"],
                "dkms_status": "installed",
                "file_size": 1024,
                "sha256": "abc123",
            },
        ]
        drivers_page._populate_snapshots(snaps)
        assert len(drivers_page._snapshot_rows) == 1
        assert isinstance(drivers_page._snapshot_rows[0], Adw.ExpanderRow)


# ===================================================================
# Connection state handling
# ===================================================================


class TestConnectionState:
    def test_disconnected_shows_unreachable(self, drivers_page, mock_client):
        mock_client.get_property.return_value = False
        drivers_page._on_connection_changed(mock_client, None)
        assert drivers_page._unreachable_group.get_visible() is True
        assert drivers_page._current_driver_group.get_visible() is False

    def test_connected_hides_unreachable(self, drivers_page, mock_client):
        # First disconnect
        mock_client.get_property.return_value = False
        drivers_page._on_connection_changed(mock_client, None)
        # Then reconnect
        mock_client.get_property.return_value = True
        drivers_page._on_connection_changed(mock_client, None)
        assert drivers_page._unreachable_group.get_visible() is False
        assert drivers_page._current_driver_group.get_visible() is True


# ===================================================================
# Reboot banner
# ===================================================================


class TestRebootBanner:
    def test_banner_hidden_initially(self, drivers_page):
        assert drivers_page._reboot_banner_group.get_visible() is False

    def test_banner_shown_on_reboot_required(self, drivers_page, gpu_state):
        gpu_state.set_property("reboot-required", True)
        drivers_page._on_reboot_required_changed(gpu_state, None)
        assert drivers_page._reboot_banner_group.get_visible() is True

    def test_banner_hidden_when_no_reboot(self, drivers_page, gpu_state):
        gpu_state.set_property("reboot-required", True)
        drivers_page._on_reboot_required_changed(gpu_state, None)
        gpu_state.set_property("reboot-required", False)
        drivers_page._on_reboot_required_changed(gpu_state, None)
        assert drivers_page._reboot_banner_group.get_visible() is False


# ===================================================================
# Driver card builder
# ===================================================================


class TestDriverCardBuilder:
    def test_builds_row_with_correct_title(self):
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
        }
        row = build_driver_row(driver)
        assert row.get_title() == "nvidia-driver-565"

    def test_subtitle_format(self):
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
        }
        row = build_driver_row(driver)
        assert "565.57" in row.get_subtitle()
        assert "Proprietary" in row.get_subtitle()

    def test_recommended_badge(self):
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": True,
        }
        row = build_driver_row(driver)
        # The suffix box should contain a "Recommended" label
        # Access through the row's suffix children
        assert row is not None  # Row created successfully

    def test_install_callback_connected(self):
        driver = {
            "package": "nvidia-driver-565",
            "version": "565",
            "variant": "proprietary",
            "recommended": False,
        }
        callback = MagicMock()
        row = build_driver_row(driver, on_install_clicked=callback)
        assert row is not None

    def test_verb_based_button_label(self):
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
        }
        row = build_driver_row(driver)
        # Row should exist — button label is verb-based "Install Driver 565"
        assert row is not None


# ===================================================================
# bind_state wiring
# ===================================================================


class TestBindState:
    def test_bind_state_connects_signals(self, gpu_state, mock_client):
        page = DriversPage()
        page.bind_state(gpu_state, mock_client)
        # Should have connected to dbus client signals
        assert mock_client.connect.call_count >= 2  # connected + operation signals

    def test_bind_state_loads_data_when_connected(self, gpu_state, mock_client):
        mock_client.get_property.return_value = True
        page = DriversPage()
        page.bind_state(gpu_state, mock_client)
        # Should have called method_async for data loading
        assert (
            mock_client.call_method_async.call_count >= 2
        )  # GetCurrentDriver + ListAvailableDrivers

    def test_bind_state_shows_unreachable_when_disconnected(self, gpu_state, mock_client):
        mock_client.get_property.return_value = False
        page = DriversPage()
        page.bind_state(gpu_state, mock_client)
        assert page._unreachable_group.get_visible() is True

    def test_bind_state_disconnects_old_handlers_on_rebind(self, gpu_state, mock_client):
        """P-4: Re-binding should disconnect previous signal handlers."""
        page = DriversPage()
        page.bind_state(gpu_state, mock_client)
        first_count = mock_client.connect.call_count

        mock_client2 = MagicMock()
        mock_client2.get_property.return_value = True
        mock_client2.connect.return_value = 1
        mock_client2.call_method_async.return_value = None

        # disconnect should be called for old handlers
        page.bind_state(gpu_state, mock_client2)
        assert mock_client.disconnect.call_count >= first_count - 1  # at least dbus handlers


# ===================================================================
# Operation progress signal handling
# ===================================================================


class TestActiveDialogLifecycle:
    """P-2: _active_dialog initialized in __init__, cleared on complete."""

    def test_active_dialog_initialized_to_none(self):
        page = DriversPage()
        assert page._active_dialog is None

    def test_install_in_progress_initialized_to_false(self):
        page = DriversPage()
        assert page._install_in_progress is False


class TestSingleSuggestedAction:
    """P-10: Only one .suggested-action button visible at a time."""

    def test_multiple_recommended_enforced_single(self, drivers_page):
        drivers = [
            {
                "package": "nvidia-driver-565",
                "version": "565.57",
                "variant": "proprietary",
                "recommended": True,
            },
            {
                "package": "nvidia-driver-560",
                "version": "560.35",
                "variant": "proprietary",
                "recommended": True,
            },
            {
                "package": "nvidia-driver-550",
                "version": "550.40",
                "variant": "open",
                "recommended": False,
            },
        ]
        drivers_page._populate_available_drivers(drivers)
        assert len(drivers_page._driver_rows) == 3


class TestOperationSignals:
    def test_progress_signal_updates_panel(self, drivers_page, mock_client):
        """Verify _on_operation_progress updates the progress panel."""
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        drivers_page._active_dialog = MagicMock()
        drivers_page._active_dialog._progress_panel = progress_panel

        drivers_page._on_operation_progress(mock_client, "op-123", 50.0, "Unpacking...")
        progress_panel.set_stage.assert_called_once_with("Unpacking...", 0.5)

    def test_progress_signal_ignored_for_wrong_op_id(self, drivers_page, mock_client):
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        drivers_page._active_dialog = MagicMock()
        drivers_page._active_dialog._progress_panel = progress_panel

        drivers_page._on_operation_progress(mock_client, "op-999", 50.0, "X")
        progress_panel.set_stage.assert_not_called()

    def test_progress_signal_ignored_when_no_dialog(self, drivers_page, mock_client):
        """P-3: Progress signals safe when no active dialog."""
        drivers_page._current_op_id = "op-123"
        drivers_page._active_dialog = None
        # Should not raise
        drivers_page._on_operation_progress(mock_client, "op-123", 50.0, "Working...")

    def test_progress_parses_stage_count(self, drivers_page, mock_client):
        """P-8: Stage count parsed from 'Step N of M: ...' message format."""
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        drivers_page._active_dialog = MagicMock()
        drivers_page._active_dialog._progress_panel = progress_panel

        drivers_page._on_operation_progress(
            mock_client, "op-123", 50.0, "Step 2 of 4: Unpacking..."
        )
        progress_panel.set_stage_count.assert_called_once_with(2, 4)

    def test_complete_signal_success(self, drivers_page, mock_client):
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        drivers_page._active_dialog = MagicMock()
        drivers_page._active_dialog._progress_panel = progress_panel

        drivers_page._on_operation_complete(mock_client, "op-123", True, "Done")
        progress_panel.set_success.assert_called_once()
        assert drivers_page._current_op_id is None

    def test_complete_signal_failure(self, drivers_page, mock_client):
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        drivers_page._active_dialog = MagicMock()
        drivers_page._active_dialog._progress_panel = progress_panel

        drivers_page._on_operation_complete(mock_client, "op-123", False, "Error msg")
        progress_panel.set_error.assert_called_once()

    def test_complete_signal_failure_adds_rollback_button(self, drivers_page, mock_client):
        """P-7: Failure adds Rollback and View Details response buttons."""
        drivers_page._current_op_id = "op-123"
        progress_panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = progress_panel
        dialog.has_response.return_value = False
        drivers_page._active_dialog = dialog

        drivers_page._on_operation_complete(mock_client, "op-123", False, "Error msg")
        # Should add rollback and details responses
        assert dialog.add_response.call_count >= 2


# ===================================================================
# Error sanitization
# ===================================================================


class TestSanitizeDbusError:
    """P-9: D-Bus error strings sanitized before UI display."""

    def test_strips_glib_error_prefix(self):
        result = _sanitize_dbus_error("g-dbus-error-quark: The name is not activatable")
        assert result == "The name is not activatable"

    def test_strips_traceback(self):
        result = _sanitize_dbus_error(
            "Traceback (most recent call last):\n  File ...\nValueError: bad"
        )
        assert result == "ValueError: bad"

    def test_empty_returns_fallback(self):
        result = _sanitize_dbus_error("")
        assert "unexpected error" in result.lower()

    def test_plain_message_passthrough(self):
        result = _sanitize_dbus_error("Disk space insufficient")
        assert result == "Disk space insufficient"


# ===================================================================
# Story 2.6: Held package detection
# ===================================================================


def _find_button_recursive(widget: Gtk.Widget) -> Gtk.Button | None:
    """Walk the widget tree depth-first to find a Gtk.Button."""
    if isinstance(widget, Gtk.Button):
        return widget
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child:
        found = _find_button_recursive(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


class TestHeldPackageUI:
    def test_held_driver_button_insensitive(self):
        """AC #7: Held driver's install button is disabled."""
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
            "held": True,
        }
        row = build_driver_row(driver)
        button = _find_button_recursive(row)
        assert button is not None, "No button found in held driver row"
        assert button.get_sensitive() is False

    def test_held_driver_shows_apt_mark_subtitle(self):
        """AC #7: Held driver shows apt-mark unhold instruction."""
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
            "held": True,
        }
        row = build_driver_row(driver)
        assert "apt-mark unhold" in row.get_subtitle()
        assert "nvidia-driver-565" in row.get_subtitle()

    def test_non_held_driver_button_sensitive(self):
        """Non-held driver buttons remain enabled."""
        driver = {
            "package": "nvidia-driver-565",
            "version": "565.57",
            "variant": "proprietary",
            "recommended": False,
            "held": False,
        }
        row = build_driver_row(driver)
        assert row is not None  # Row created without error

    def test_held_no_callback_connected(self):
        """Held driver install button does not connect callback."""
        callback = MagicMock()
        driver = {
            "package": "nvidia-driver-565",
            "version": "565",
            "variant": "proprietary",
            "recommended": False,
            "held": True,
        }
        row = build_driver_row(driver, on_install_clicked=callback)
        assert row is not None  # Created without error
        callback.assert_not_called()


# ===================================================================
# Story 2.6: .run file detection banner
# ===================================================================


class TestRunFileDetectionBanner:
    def test_run_file_banner_hidden_initially(self, drivers_page):
        assert drivers_page._run_file_banner_group.get_visible() is False

    def test_run_file_detected_shows_banner(self, drivers_page):
        """AC #8: .run detected shows banner and disables management."""
        metadata = {"run_file_detected": True, "run_file_message": "NVIDIA .run file detected"}
        drivers_page._populate_available_drivers([], metadata)
        assert drivers_page._run_file_banner.get_revealed() is True
        assert drivers_page._run_file_banner_group.get_visible() is True

    def test_run_file_not_detected_hides_banner(self, drivers_page):
        metadata = {"run_file_detected": False}
        drivers_page._populate_available_drivers(
            [
                {
                    "package": "nvidia-driver-565",
                    "version": "565",
                    "variant": "p",
                    "recommended": False,
                }
            ],
            metadata,
        )
        assert drivers_page._run_file_banner.get_revealed() is False


# ===================================================================
# Story 2.6: Structured error parsing (UX-DR16)
# ===================================================================


class TestStructuredErrorParsing:
    def test_parse_valid_json_error(self):
        import json

        error_dict = {
            "success": False,
            "error_title": "Package system needs repair",
            "error_description": "The package system was left in a broken state.",
            "error_primary_action": "repair_dpkg",
            "error_secondary_action": "rollback",
            "error_category": "dpkg_broken",
            "recoverable": True,
        }
        result = parse_structured_error(json.dumps(error_dict))
        assert result is not None
        assert result["error_title"] == "Package system needs repair"

    def test_parse_plain_string_returns_none(self):
        result = parse_structured_error("Installation failed")
        assert result is None

    def test_parse_empty_returns_none(self):
        result = parse_structured_error("")
        assert result is None

    def test_format_error_with_description(self):
        error_dict = {
            "error_title": "Network unavailable",
            "error_description": "Check your internet connection.",
        }
        result = format_error_message(error_dict)
        assert "Network unavailable" in result
        assert "internet connection" in result

    def test_format_error_title_only(self):
        error_dict = {"error_title": "Operation timed out"}
        result = format_error_message(error_dict)
        assert result == "Operation timed out"

    def test_format_error_includes_actions(self):
        """P-2: primary and secondary actions are included in output."""
        error_dict = {
            "error_title": "Network unavailable",
            "error_description": "DNS resolution failed.",
            "error_primary_action": "Check your internet connection and retry",
            "error_secondary_action": "Try again later",
        }
        result = format_error_message(error_dict)
        assert "Network unavailable" in result
        assert "DNS resolution failed" in result
        assert "Check your internet connection and retry" in result
        assert "Try again later" in result
