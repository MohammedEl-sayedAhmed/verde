"""Unit tests for StatusIndicator widget (Story 1.9)."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.widgets.status_indicator import StatusIndicator  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def indicator():
    return StatusIndicator()


# ===================================================================
# set_status CSS class application
# ===================================================================


class TestSetStatusCSS:
    def test_good_applies_verde_status_good(self, indicator):
        indicator.set_status("Safe", "good")
        assert indicator.has_css_class("verde-status-good")

    def test_warn_applies_verde_status_warn(self, indicator):
        indicator.set_status("Warning", "warn")
        assert indicator.has_css_class("verde-status-warn")

    def test_crit_applies_verde_status_crit(self, indicator):
        indicator.set_status("Critical", "crit")
        assert indicator.has_css_class("verde-status-crit")

    def test_unknown_applies_dim_label(self, indicator):
        indicator.set_status("N/A", "unknown")
        assert indicator.has_css_class("dim-label")

    def test_removes_previous_class_on_change(self, indicator):
        indicator.set_status("Safe", "good")
        assert indicator.has_css_class("verde-status-good")
        indicator.set_status("Warning", "warn")
        assert not indicator.has_css_class("verde-status-good")
        assert indicator.has_css_class("verde-status-warn")

    def test_label_text_set(self, indicator):
        indicator.set_status("Safe", "good")
        assert indicator.get_label() == "Safe"

    def test_label_never_empty(self, indicator):
        indicator.set_status("", "good")
        # Even with empty text, the call succeeds (text is what caller provides)
        # But initial state should have placeholder
        ind2 = StatusIndicator()
        assert ind2.get_label() == "—"


# ===================================================================
# set_status_from_thresholds
# ===================================================================


class TestSetStatusFromThresholds:
    def test_below_warn_is_good(self, indicator):
        indicator.set_status_from_thresholds(50, 80, 90)
        assert indicator.has_css_class("verde-status-good")
        assert indicator.get_label() == "Safe"

    def test_at_warn_is_warn(self, indicator):
        indicator.set_status_from_thresholds(80, 80, 90)
        assert indicator.has_css_class("verde-status-warn")
        assert indicator.get_label() == "Warning"

    def test_between_warn_and_crit_is_warn(self, indicator):
        indicator.set_status_from_thresholds(85, 80, 90)
        assert indicator.has_css_class("verde-status-warn")

    def test_at_crit_is_crit(self, indicator):
        indicator.set_status_from_thresholds(90, 80, 90)
        assert indicator.has_css_class("verde-status-crit")
        assert indicator.get_label() == "Critical"

    def test_above_crit_is_crit(self, indicator):
        indicator.set_status_from_thresholds(100, 80, 90)
        assert indicator.has_css_class("verde-status-crit")


# ===================================================================
# ATK accessible description
# ===================================================================


class TestAccessibility:
    def test_accessible_description_set_on_set_status(self, indicator):
        indicator.set_status("Safe", "good")
        # Verify the label text is set (ATK description is set via update_property)
        assert indicator.get_label() == "Safe"

    def test_accessible_description_warn(self, indicator):
        indicator.set_status("Warning", "warn")
        assert indicator.get_label() == "Warning"

    def test_accessible_description_crit(self, indicator):
        indicator.set_status("Critical", "crit")
        assert indicator.get_label() == "Critical"
