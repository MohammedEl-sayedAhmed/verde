"""Unit tests for OperationProgressPanel widget (Story 2.4)."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.widgets.progress_overlay import OperationProgressPanel  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def panel():
    return OperationProgressPanel()


# ===================================================================
# set_stage
# ===================================================================


class TestSetStage:
    def test_updates_label_and_fraction(self, panel):
        panel.set_stage("Installing packages...", 0.5)
        assert panel._stage_label.get_label() == "Installing packages..."
        assert abs(panel._progress_bar.get_fraction() - 0.5) < 0.01

    def test_clamps_fraction_to_bounds(self, panel):
        panel.set_stage("Overflow", 1.5)
        assert panel._progress_bar.get_fraction() == 1.0

        panel.set_stage("Underflow", -0.1)
        assert panel._progress_bar.get_fraction() == 0.0

    def test_updates_progress_text(self, panel):
        panel.set_stage("Working...", 0.75)
        assert panel._progress_bar.get_text() == "75%"

    def test_hides_result_widgets(self, panel):
        panel.set_success("Done")
        panel.set_stage("Restarting...", 0.0)
        assert panel._result_icon.get_visible() is False
        assert panel._result_label.get_visible() is False


class TestSetStageCount:
    def test_updates_stage_count_label(self, panel):
        panel.set_stage_count(2, 4)
        assert panel._stage_count_label.get_label() == "Step 2 of 4"


# ===================================================================
# set_indeterminate
# ===================================================================


class TestSetIndeterminate:
    def test_sets_pulsing_state(self, panel):
        panel.set_indeterminate()
        assert panel._is_pulsing is True

    def test_stops_pulsing_on_set_stage(self, panel):
        panel.set_indeterminate()
        panel.set_stage("Stage", 0.5)
        assert panel._is_pulsing is False

    def test_idempotent(self, panel):
        panel.set_indeterminate()
        source1 = panel._pulse_source_id
        panel.set_indeterminate()
        # Should not create a second source
        assert panel._pulse_source_id == source1


# ===================================================================
# set_success
# ===================================================================


class TestSetSuccess:
    def test_shows_success_state(self, panel):
        panel.set_success("Driver verified and loaded")
        assert panel._stage_label.get_label() == "Installation Complete"
        assert panel._progress_bar.get_fraction() == 1.0
        assert panel._result_icon.get_visible() is True
        assert panel._result_label.get_visible() is True
        assert panel._result_label.get_label() == "Driver verified and loaded"

    def test_stops_pulsing(self, panel):
        panel.set_indeterminate()
        panel.set_success("Done")
        assert panel._is_pulsing is False


# ===================================================================
# set_error
# ===================================================================


class TestSetError:
    def test_shows_error_state(self, panel):
        panel.set_error("Package dependency failed")
        assert panel._stage_label.get_label() == "Installation Failed"
        assert panel._result_icon.get_visible() is True
        assert panel._result_label.get_visible() is True
        assert panel._result_label.get_label() == "Package dependency failed"
        assert panel._progress_bar.get_visible() is False

    def test_stops_pulsing(self, panel):
        panel.set_indeterminate()
        panel.set_error("Failed")
        assert panel._is_pulsing is False


# ===================================================================
# CSS class and visibility fixes (review patches)
# ===================================================================


class TestReviewPatches:
    def test_set_stage_restores_progress_bar_visibility(self, panel):
        """P-12: set_stage restores progress bar after set_error hides it."""
        panel.set_error("Failed")
        assert panel._progress_bar.get_visible() is False
        panel.set_stage("Retrying...", 0.1)
        assert panel._progress_bar.get_visible() is True

    def test_set_success_removes_crit_class(self, panel):
        """P-11: set_success removes verde-status-crit before adding verde-status-good."""
        panel.set_error("Failed")
        assert panel._result_icon.has_css_class("verde-status-crit")
        panel.set_success("Done")
        assert panel._result_icon.has_css_class("verde-status-good")
        assert not panel._result_icon.has_css_class("verde-status-crit")
