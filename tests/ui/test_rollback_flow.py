"""GUI smoke tests for rollback flow (Story 3.3)."""

from __future__ import annotations

from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from verde.gpu_state import GPUState  # noqa: E402
from verde.views.drivers import DriversPage  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def gpu_state():
    return GPUState()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_property.return_value = True
    client.connect.return_value = 1
    client.call_method_async.return_value = None
    client.rollback_driver.return_value = None
    return client


@pytest.fixture
def page(gpu_state, mock_client):
    page = DriversPage()
    page.bind_state(gpu_state, mock_client)
    return page


SAMPLE_SNAPSHOT = {
    "id": "20260318T143000_nvidia-560-ab01",
    "timestamp": "2026-03-18T14:30:00+00:00",
    "driver_version": "560",
    "kernel_version": "6.8.0-45-generic",
    "packages": ["nvidia-driver-560=560.35.03"],
    "dkms_status": "installed",
    "file_size": 4096,
    "sha256": "a1b2c3d4",
}


# ===================================================================
# Rollback confirmation dialog (AC#2)
# ===================================================================


class TestRollbackConfirmationDialog:
    def test_rollback_click_calls_preflight(self, page, mock_client):
        """Clicking Rollback triggers GetPreflightCheck('rollback:snapshot_id')."""
        mock_client.call_method_async.reset_mock()
        btn = Gtk.Button()
        page._on_rollback_clicked(btn, SAMPLE_SNAPSHOT)

        # Should call GetPreflightCheck with rollback:snapshot_id
        mock_client.call_method_async.assert_called_once()
        call_args = mock_client.call_method_async.call_args
        assert call_args[0][0] == "GetPreflightCheck"
        params = call_args[0][1]
        assert "rollback:" in params.unpack()[0]

    def test_rollback_click_sets_in_progress(self, page):
        """Clicking Rollback sets _install_in_progress to prevent concurrent ops."""
        btn = Gtk.Button()
        page._on_rollback_clicked(btn, SAMPLE_SNAPSHOT)
        assert page._install_in_progress is True

    def test_rollback_ignored_when_in_progress(self, page, mock_client):
        """Clicking Rollback when already in progress is ignored."""
        page._install_in_progress = True
        mock_client.call_method_async.reset_mock()
        btn = Gtk.Button()
        page._on_rollback_clicked(btn, SAMPLE_SNAPSHOT)

        mock_client.call_method_async.assert_not_called()


# ===================================================================
# Progress panel updates (AC#4)
# ===================================================================


class TestRollbackProgressUpdates:
    def test_progress_signal_updates_panel(self, page, mock_client):
        """OperationProgress signal updates the active dialog progress panel."""
        page._current_op_id = "op-123"
        panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = panel
        page._active_dialog = dialog

        page._on_operation_progress(mock_client, "op-123", 50.0, "Installing packages…")
        panel.set_stage.assert_called_once_with("Installing packages…", 0.5)

    def test_progress_signal_ignored_for_different_op(self, page, mock_client):
        """OperationProgress for a different op_id is ignored."""
        page._current_op_id = "op-123"
        panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = panel
        page._active_dialog = dialog

        page._on_operation_progress(mock_client, "op-999", 50.0, "message")
        panel.set_stage.assert_not_called()


# ===================================================================
# Rollback result handling (AC#5, #6)
# ===================================================================


class TestRollbackResultHandling:
    def test_success_shows_rollback_complete(self, page, mock_client):
        """OperationComplete with success shows 'Rollback Complete'."""
        page._current_op_id = "op-123"
        panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = panel
        dialog._is_rollback = True
        dialog.has_response.return_value = False
        page._active_dialog = dialog

        page._on_operation_complete(mock_client, "op-123", True, "Driver restored")
        panel.set_success.assert_called_once()
        dialog.set_heading.assert_called_with("Rollback Complete")

    def test_failure_shows_rollback_failed(self, page, mock_client):
        """OperationComplete with failure shows 'Rollback Failed'."""
        page._current_op_id = "op-123"
        panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = panel
        dialog._is_rollback = True
        dialog.has_response.return_value = False
        page._active_dialog = dialog

        page._on_operation_complete(mock_client, "op-123", False, "Failed to install")
        panel.set_error.assert_called_once()
        dialog.set_heading.assert_called_with("Rollback Failed")

    def test_failure_offers_diagnostic_report(self, page, mock_client):
        """Rollback failure adds diagnostic report button (FR55)."""
        page._current_op_id = "op-123"
        panel = MagicMock()
        dialog = MagicMock()
        dialog._progress_panel = panel
        dialog._is_rollback = True
        dialog.has_response.return_value = False
        page._active_dialog = dialog

        page._on_operation_complete(mock_client, "op-123", False, "Failed")
        dialog.add_response.assert_called()
        response_names = [c[0][0] for c in dialog.add_response.call_args_list]
        assert "diagnostic" in response_names


# ===================================================================
# Concurrency guard (AC#8)
# ===================================================================


class TestRollbackConcurrencyGuard:
    def test_rollback_blocked_during_install(self, page, mock_client):
        """Rollback button click is blocked when install is in progress."""
        page._install_in_progress = True
        mock_client.call_method_async.reset_mock()
        mock_client.rollback_driver.reset_mock()
        btn = Gtk.Button()
        page._on_rollback_clicked(btn, SAMPLE_SNAPSHOT)

        mock_client.call_method_async.assert_not_called()
        mock_client.rollback_driver.assert_not_called()
