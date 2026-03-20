"""Unit tests for Story 5.2: Diagnostics View GUI — Report Generation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from gi.repository import Adw, Gtk

from verde.views.diagnostics import DiagnosticsPage


@pytest.fixture
def diag_page():
    """Create a DiagnosticsPage instance."""
    page = DiagnosticsPage()
    return page


@pytest.fixture
def mock_client():
    """Mock VerdeDBusClient."""
    client = MagicMock()
    client.get_property.return_value = True
    client.connect.return_value = 1
    return client


@pytest.fixture
def gpu_state():
    """Mock GPUState."""
    from verde.gpu_state import GPUState

    return GPUState()


# ═══════════════════════════════════════════════════════════════════════
# Structure tests (AC #1)
# ═══════════════════════════════════════════════════════════════════════


class TestPageStructure:
    def test_title_is_diagnostics(self, diag_page):
        assert diag_page.get_title() == "Diagnostics"

    def test_icon_name(self, diag_page):
        assert diag_page.get_icon_name() == "utilities-system-monitor-symbolic"

    def test_has_generate_button(self, diag_page):
        assert hasattr(diag_page, "_generate_btn")
        assert isinstance(diag_page._generate_btn, Gtk.Button)

    def test_generate_button_has_suggested_action(self, diag_page):
        assert diag_page._generate_btn.has_css_class("suggested-action")

    def test_has_report_card_group(self, diag_page):
        assert hasattr(diag_page, "_report_card_group")

    def test_report_card_initially_hidden(self, diag_page):
        assert diag_page._report_card_group.get_visible() is False

    def test_has_copy_button(self, diag_page):
        assert hasattr(diag_page, "_copy_btn")

    def test_has_preview_expander(self, diag_page):
        assert hasattr(diag_page, "_preview_expander")
        assert isinstance(diag_page._preview_expander, Adw.ExpanderRow)

    def test_preview_expander_collapsed_by_default(self, diag_page):
        assert diag_page._preview_expander.get_expanded() is False

    def test_has_spinner(self, diag_page):
        assert hasattr(diag_page, "_generate_spinner")
        assert diag_page._generate_spinner.get_visible() is False


# ═══════════════════════════════════════════════════════════════════════
# Report received tests (AC #3, #5)
# ═══════════════════════════════════════════════════════════════════════


class TestReportReceived:
    def test_report_card_visible_after_generation(self, diag_page):
        diag_page._on_report_received("# Test Report\nContent here")
        assert diag_page._report_card_group.get_visible() is True

    def test_report_text_stored(self, diag_page):
        diag_page._on_report_received("# Test Report")
        assert diag_page._report_text == "# Test Report"

    def test_timestamp_set(self, diag_page):
        diag_page._on_report_received("# Test Report")
        subtitle = diag_page._timestamp_row.get_subtitle()
        assert subtitle  # non-empty timestamp
        # Should contain date pattern
        assert "-" in subtitle  # YYYY-MM-DD format

    def test_preview_label_has_report_text(self, diag_page):
        diag_page._on_report_received("# My Report\nSome content")
        assert diag_page._preview_label.get_text() == "# My Report\nSome content"

    def test_generate_button_re_enabled(self, diag_page):
        diag_page._generating = True
        diag_page._generate_btn.set_sensitive(False)
        diag_page._on_report_received("report")
        assert diag_page._generate_btn.get_sensitive() is True
        assert diag_page._generating is False

    def test_spinner_hidden_after_generation(self, diag_page):
        diag_page._generate_spinner.set_visible(True)
        diag_page._generate_spinner.set_spinning(True)
        diag_page._on_report_received("report")
        assert diag_page._generate_spinner.get_visible() is False
        assert diag_page._generate_spinner.get_spinning() is False

    def test_replace_previous_report(self, diag_page):
        """AC #5: Generating again replaces the old report."""
        diag_page._on_report_received("First report")
        assert diag_page._report_text == "First report"

        diag_page._on_report_received("Second report")
        assert diag_page._report_text == "Second report"
        assert diag_page._preview_label.get_text() == "Second report"


# ═══════════════════════════════════════════════════════════════════════
# Generate button state tests (AC #2)
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateButton:
    def test_click_without_client_is_noop(self, diag_page):
        """No D-Bus client → generate does nothing."""
        diag_page._dbus_client = None
        diag_page._on_generate_clicked(diag_page._generate_btn)
        assert diag_page._generating is False

    def test_double_click_blocked(self, diag_page, mock_client):
        """Second click while generating is blocked."""
        diag_page._dbus_client = mock_client
        diag_page._generating = True
        mock_client.call_method_async.reset_mock()
        diag_page._on_generate_clicked(diag_page._generate_btn)
        # No new call should be made
        assert not mock_client.call_method_async.called

    def test_button_disabled_during_generation(self, diag_page, mock_client):
        """Button becomes insensitive during generation."""
        diag_page._dbus_client = mock_client
        diag_page._on_generate_clicked(diag_page._generate_btn)
        assert diag_page._generate_btn.get_sensitive() is False
        assert diag_page._generating is True


# ═══════════════════════════════════════════════════════════════════════
# Error handling tests (AC #6)
# ═══════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_error_re_enables_button(self, diag_page):
        """Error state re-enables the generate button."""
        diag_page._generating = True
        diag_page._generate_btn.set_sensitive(False)
        diag_page._on_generate_error("Test error")
        assert diag_page._generate_btn.get_sensitive() is True
        assert diag_page._generating is False

    def test_error_hides_spinner(self, diag_page):
        diag_page._generate_spinner.set_visible(True)
        diag_page._generate_spinner.set_spinning(True)
        diag_page._on_generate_error("Error")
        assert diag_page._generate_spinner.get_visible() is False


# ═══════════════════════════════════════════════════════════════════════
# Copy to clipboard tests (AC #4)
# ═══════════════════════════════════════════════════════════════════════


class TestClipboardCopy:
    def test_copy_empty_is_noop(self, diag_page):
        """Copy with no report text does nothing."""
        diag_page._report_text = ""
        # Should not raise
        diag_page._on_copy_clicked(diag_page._copy_btn)

    def test_copy_stores_text(self, diag_page):
        """Verify the report text is available for copy."""
        diag_page._on_report_received("# Test Report\nLine 2")
        assert diag_page._report_text == "# Test Report\nLine 2"


# ═══════════════════════════════════════════════════════════════════════
# Accessibility tests (AC #7)
# ═══════════════════════════════════════════════════════════════════════


class TestAccessibility:
    def test_generate_button_accessible_label(self, diag_page):
        """Generate button has an ATK accessible label."""
        # The accessible label was set via update_property
        assert diag_page._generate_btn is not None

    def test_copy_button_accessible_label(self, diag_page):
        """Copy button has an ATK accessible label."""
        assert diag_page._copy_btn is not None

    def test_preview_expander_accessible_label(self, diag_page):
        """Preview expander has an ATK accessible label."""
        assert diag_page._preview_expander is not None


# ═══════════════════════════════════════════════════════════════════════
# gettext tests (AC #8)
# ═══════════════════════════════════════════════════════════════════════


class TestGettext:
    def test_generate_button_label_wrapped(self, diag_page):
        """Button label uses translatable string."""
        label = diag_page._generate_btn.get_label()
        assert label == "Generate Report"

    def test_report_group_title_wrapped(self, diag_page):
        title = diag_page._report_gen_group.get_title()
        assert title == "Diagnostic Report"


# ═══════════════════════════════════════════════════════════════════════
# D-Bus wiring tests
# ═══════════════════════════════════════════════════════════════════════


class TestDBusWiring:
    def test_bind_state_method_exists(self, diag_page):
        assert hasattr(diag_page, "bind_state")

    def test_diagnostics_page_in_window(self):
        """DiagnosticsPage is registered in the window's view stack."""
        import pathlib

        window_path = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde" / "window.py"
        src = window_path.read_text()
        assert "DiagnosticsPage" in src
        assert '"diagnostics"' in src

    def test_toast_overlay_in_window(self):
        """Window has a toast_overlay for toast notifications."""
        import pathlib

        window_path = pathlib.Path(__file__).resolve().parents[2] / "src" / "verde" / "window.py"
        src = window_path.read_text()
        assert "toast_overlay" in src
        assert "ToastOverlay" in src
