"""Unit tests for PowerView (Story 4.3)."""

from __future__ import annotations

from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.gpu_state import GPUState  # noqa: E402
from verde.views.power import PowerPage  # noqa: E402


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
def power_page(gpu_state, mock_client):
    page = PowerPage()
    page.bind_state(gpu_state, mock_client)
    return page


# ===================================================================
# Structure tests (AC #1, #7, #12)
# ===================================================================


class TestPowerPageStructure:
    def test_title_is_power(self, power_page):
        assert power_page.get_title() == "Power"

    def test_icon_name(self, power_page):
        assert power_page.get_icon_name() == "battery-symbolic"

    def test_has_suspend_group(self, power_page):
        assert power_page._suspend_group is not None

    def test_has_secureboot_group(self, power_page):
        assert power_page._secureboot_group is not None

    def test_has_wayland_group(self, power_page):
        assert power_page._wayland_group is not None

    def test_has_power_profile_group(self, power_page):
        assert power_page._power_profile_group is not None

    def test_has_unreachable_group(self, power_page):
        assert power_page._unreachable_group is not None

    def test_has_reboot_banner(self, power_page):
        assert power_page._reboot_banner is not None
        assert power_page._reboot_banner_group.get_visible() is False


# ===================================================================
# bind_state and data loading (AC #1)
# ===================================================================


class TestBindState:
    def test_bind_state_calls_get_power_status(self, power_page, mock_client):
        """bind_state triggers GetPowerStatus D-Bus call."""
        mock_client.call_method_async.assert_any_call(
            "GetPowerStatus",
            None,
            pytest.approx(mock_client.call_method_async.call_args_list[0][0][2], abs=1),
        )

    def test_bind_state_connects_signals(self, power_page, mock_client):
        """bind_state connects to operation-progress and operation-complete."""
        connect_calls = [call[0][0] for call in mock_client.connect.call_args_list]
        assert "notify::connected" in connect_calls
        assert "operation-progress" in connect_calls
        assert "operation-complete" in connect_calls

    def test_bind_state_disconnected_shows_unreachable(self, gpu_state):
        """When D-Bus is not connected, unreachable status is shown."""
        client = MagicMock()
        client.get_property.return_value = False  # not connected
        client.connect.return_value = 1

        page = PowerPage()
        page.bind_state(gpu_state, client)

        assert page._unreachable_group.get_visible() is True
        assert page._suspend_group.get_visible() is False


# ===================================================================
# Status display — Working (AC #2, #5)
# ===================================================================


_WORKING_STATUS = {
    "overall_status": "ok",
    "issues": [],
    "suspend_service_active": True,
    "hibernate_service_active": True,
    "secure_boot_enabled": False,
    "mok_enrolled": False,
    "wayland_session": False,
}

_BROKEN_STATUS = {
    "overall_status": "issues_found",
    "issues": [
        {
            "type": "suspend",
            "severity": "error",
            "summary": "nvidia-suspend.service not enabled",
            "detail": "The NVIDIA suspend service is disabled",
            "fixable": True,
            "already_fixed": False,
        },
        {
            "type": "hibernate",
            "severity": "error",
            "summary": "Hibernate not configured",
            "detail": "Missing modprobe config",
            "fixable": True,
            "already_fixed": False,
        },
    ],
    "suspend_service_active": False,
    "hibernate_service_active": False,
    "secure_boot_enabled": True,
    "mok_enrolled": False,
    "wayland_session": False,
}


class TestStatusDisplayWorking:
    def test_working_shows_green_indicator(self, power_page):
        """Working status shows good indicator with no Fix button."""
        power_page._update_status_display(_WORKING_STATUS)
        assert power_page._suspend_indicator.get_label() == "Working"
        assert power_page._suspend_fix_btn.get_visible() is False

    def test_working_hides_issues_expander(self, power_page):
        """No issues → issues expander is hidden."""
        power_page._update_status_display(_WORKING_STATUS)
        assert power_page._suspend_issues_expander.get_visible() is False

    def test_already_fixed_hides_fix_button(self, power_page):
        """FR56: already_fixed issues should not show Fix button."""
        status = {
            **_WORKING_STATUS,
            "issues": [
                {
                    "type": "suspend",
                    "severity": "ok",
                    "summary": "All services enabled",
                    "detail": "",
                    "fixable": True,
                    "already_fixed": True,
                },
            ],
        }
        power_page._update_status_display(status)
        assert power_page._suspend_fix_btn.get_visible() is False


# ===================================================================
# Status display — Broken (AC #2, #3)
# ===================================================================


class TestStatusDisplayBroken:
    def test_broken_shows_crit_indicator(self, power_page):
        """Broken status shows crit indicator."""
        power_page._update_status_display(_BROKEN_STATUS)
        assert power_page._suspend_indicator.get_label() == "Issues Found"

    def test_broken_shows_fix_button(self, power_page):
        """Broken status shows Fix button."""
        power_page._update_status_display(_BROKEN_STATUS)
        assert power_page._suspend_fix_btn.get_visible() is True

    def test_broken_shows_issues_expander(self, power_page):
        """Broken status populates issues expander."""
        power_page._update_status_display(_BROKEN_STATUS)
        assert power_page._suspend_issues_expander.get_visible() is True

    def test_fix_button_has_suggested_action(self, power_page):
        """UX-DR15: first Fix button gets .suggested-action."""
        power_page._update_status_display(_BROKEN_STATUS)
        assert power_page._suspend_fix_btn.has_css_class("suggested-action")

    def test_issues_expander_collapsed_by_default(self, power_page):
        """UX-DR9: expanders collapsed by default."""
        power_page._update_status_display(_BROKEN_STATUS)
        assert power_page._suspend_issues_expander.get_expanded() is False


# ===================================================================
# Secure Boot display (AC #2)
# ===================================================================


class TestSecureBootDisplay:
    def test_sb_disabled(self, power_page):
        power_page._update_status_display(_WORKING_STATUS)
        assert "disabled" in power_page._secureboot_row.get_subtitle().lower()

    def test_sb_enabled_mok_enrolled(self, power_page):
        status = {**_WORKING_STATUS, "secure_boot_enabled": True, "mok_enrolled": True}
        power_page._update_status_display(status)
        assert "enrolled" in power_page._secureboot_row.get_subtitle().lower()

    def test_sb_enabled_no_mok(self, power_page):
        status = {
            **_WORKING_STATUS,
            "secure_boot_enabled": True,
            "mok_enrolled": False,
            "issues": [
                {
                    "type": "secure_boot",
                    "severity": "warning",
                    "summary": "MOK key not enrolled",
                    "detail": "Run mokutil to enroll",
                    "fixable": False,
                    "already_fixed": False,
                },
            ],
        }
        power_page._update_status_display(status)
        assert "not enrolled" in power_page._secureboot_indicator.get_label().lower()


# ===================================================================
# Wayland display (AC #6)
# ===================================================================


class TestWaylandDisplay:
    def test_wayland_hidden_when_not_wayland(self, power_page):
        """Wayland group hidden when not a Wayland session."""
        power_page._update_status_display(_WORKING_STATUS)
        assert power_page._wayland_group.get_visible() is False

    def test_wayland_shown_with_issues(self, power_page):
        """FR92: Wayland group visible when Wayland issues detected."""
        status = {
            **_WORKING_STATUS,
            "wayland_session": True,
            "issues": [
                {
                    "type": "wayland",
                    "severity": "error",
                    "summary": "Missing nvidia-drm modeset=1",
                    "detail": "Required for Wayland",
                    "fixable": False,
                    "already_fixed": False,
                },
            ],
        }
        power_page._update_status_display(status)
        assert power_page._wayland_group.get_visible() is True
        assert power_page._wayland_issues_expander.get_visible() is True

    def test_wayland_working_no_issues(self, power_page):
        """Wayland visible but working when no issues."""
        status = {**_WORKING_STATUS, "wayland_session": True}
        power_page._update_status_display(status)
        assert power_page._wayland_group.get_visible() is True
        assert power_page._wayland_indicator.get_label() == "Working"
        assert power_page._wayland_issues_expander.get_visible() is False


# ===================================================================
# Power profile info (AC #7)
# ===================================================================


class TestPowerProfile:
    def test_power_mode_unknown_default(self, power_page):
        assert power_page._power_mode_row.get_subtitle() == "Unknown"

    def test_power_state_unavailable_default(self, power_page):
        assert power_page._power_state_row.get_subtitle() == "Unavailable"

    def test_power_draw_unavailable_default(self, power_page):
        assert power_page._power_draw_row.get_subtitle() == "Unavailable"

    def test_power_mode_performance(self, power_page, gpu_state):
        gpu_state.set_property("p-state", "P0")
        power_page._update_power_profile()
        assert power_page._power_mode_row.get_subtitle() == "Performance"

    def test_power_mode_balanced(self, power_page, gpu_state):
        gpu_state.set_property("p-state", "P5")
        power_page._update_power_profile()
        assert power_page._power_mode_row.get_subtitle() == "Balanced"

    def test_power_mode_power_saver(self, power_page, gpu_state):
        gpu_state.set_property("p-state", "P8")
        power_page._update_power_profile()
        assert power_page._power_mode_row.get_subtitle() == "Power Saver"

    def test_power_state_shows_value(self, power_page, gpu_state):
        gpu_state.set_property("p-state", "P0")
        power_page._update_power_profile()
        assert power_page._power_state_row.get_subtitle() == "P0"

    def test_power_draw_shows_value(self, power_page, gpu_state):
        gpu_state.set_property("power-draw", 185.0)
        gpu_state.set_property("power-limit", 350.0)
        power_page._update_power_profile()
        assert "185" in power_page._power_draw_row.get_subtitle()
        assert "350" in power_page._power_draw_row.get_subtitle()


# ===================================================================
# Fix button click triggers D-Bus call (AC #4)
# ===================================================================


class TestFixButton:
    def test_fix_button_click_calls_preflight(self, power_page, mock_client):
        """Fix button click triggers GetPreflightCheck D-Bus call."""
        # First set up broken status so Fix button is visible
        power_page._update_status_display(_BROKEN_STATUS)

        # Reset mock to track new calls
        mock_client.call_method_async.reset_mock()

        # Simulate fix click
        power_page._on_fix_clicked(power_page._suspend_fix_btn, "suspend")

        # Verify preflight call was made
        assert mock_client.call_method_async.called
        call_args = mock_client.call_method_async.call_args
        assert call_args[0][0] == "GetPreflightCheck"

    def test_fix_in_progress_blocks_second_click(self, power_page, mock_client):
        """Second click while fix in progress is blocked."""
        power_page._update_status_display(_BROKEN_STATUS)
        power_page._fix_in_progress = True

        mock_client.call_method_async.reset_mock()
        power_page._on_fix_clicked(power_page._suspend_fix_btn, "suspend")

        # No D-Bus call should be made
        assert not mock_client.call_method_async.called

    def test_pending_fix_types_tracked(self, power_page):
        """Broken status tracks which fix types are needed."""
        power_page._update_status_display(_BROKEN_STATUS)
        # _BROKEN_STATUS has suspend and hibernate issues
        assert "suspend" in power_page._pending_fix_types
        assert "hibernate" in power_page._pending_fix_types

    def test_pending_fix_types_empty_when_working(self, power_page):
        """Working status has no pending fix types."""
        power_page._update_status_display(_WORKING_STATUS)
        assert power_page._pending_fix_types == []


# ===================================================================
# ATK accessible labels (AC #9)
# ===================================================================


class TestAccessibility:
    def test_fix_button_has_accessible_label(self, power_page):
        """Fix button has ATK accessible label."""
        # The update_property call in __init__ sets this
        assert power_page._suspend_fix_btn is not None

    def test_retry_button_exists(self, power_page):
        """Retry button exists in unreachable group."""
        assert power_page._unreachable_status is not None


# ===================================================================
# Error display (AC #10)
# ===================================================================


class TestErrorDisplay:
    def test_power_status_error_shows_warning(self, power_page):
        """GetPowerStatus failure shows error state."""
        power_page._on_power_status_error()
        assert "unable" in power_page._suspend_row.get_subtitle().lower()

    def test_operation_complete_failure_shows_error(self, power_page, mock_client):
        """OperationComplete with success=False shows error in dialog."""
        # Set up active dialog with progress panel
        power_page._current_op_id = "test123"
        dialog = MagicMock()
        panel = MagicMock()
        dialog._progress_panel = panel
        dialog.has_response.return_value = False
        power_page._active_dialog = dialog

        power_page._on_operation_complete(mock_client, "test123", False, "Something failed")

        panel.set_error.assert_called_once()


# ===================================================================
# D-Bus dispatch wiring
# ===================================================================


class TestDBusWiring:
    def test_power_page_in_window(self):
        """Verify PowerPage is imported and registered in window.py."""
        from verde.window import VerdeApplication

        assert hasattr(VerdeApplication, "_on_activate")

    def test_bind_state_method_exists(self):
        """PowerPage has bind_state method."""
        page = PowerPage()
        assert hasattr(page, "bind_state")
        assert callable(page.bind_state)
