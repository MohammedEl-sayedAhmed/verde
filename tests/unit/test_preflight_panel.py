"""Unit tests for PreflightPanel widget (Story 2.4)."""

from __future__ import annotations

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw  # noqa: E402

from verde.widgets.preflight_banner import PreflightPanel  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _gtk_init():
    Adw.init()


@pytest.fixture
def panel():
    return PreflightPanel()


# ===================================================================
# States
# ===================================================================


class TestPreflightPanelLoading:
    def test_initial_all_passed_is_false(self, panel):
        assert panel.all_passed is False

    def test_set_loading_shows_spinner(self, panel):
        panel.set_loading()
        assert panel._spinner.get_visible() is True
        assert panel._spinner.get_spinning() is True
        assert panel.all_passed is False


class TestPreflightPanelChecks:
    def test_all_checks_passed(self, panel):
        checks = [
            {"name": "Disk Space", "status": "passed", "explanation": ""},
            {"name": "Kernel Headers", "status": "passed", "explanation": ""},
        ]
        panel.set_checks(checks)
        assert panel.all_passed is True
        assert len(panel._check_rows) == 2

    def test_some_checks_failed(self, panel):
        checks = [
            {"name": "Disk Space", "status": "passed", "explanation": ""},
            {
                "name": "Kernel Headers",
                "status": "failed",
                "explanation": "Missing linux-headers-6.8.0",
            },
        ]
        panel.set_checks(checks)
        assert panel.all_passed is False
        assert len(panel._check_rows) == 2

    def test_failed_check_shows_explanation_in_subtitle(self, panel):
        checks = [
            {"name": "Secure Boot", "status": "failed", "explanation": "Secure Boot is enabled"},
        ]
        panel.set_checks(checks)
        assert panel._check_rows[0].get_subtitle() == "Secure Boot is enabled"

    def test_passed_check_has_no_subtitle(self, panel):
        checks = [
            {"name": "Disk Space", "status": "passed", "explanation": ""},
        ]
        panel.set_checks(checks)
        # Passed checks don't set subtitle
        assert (
            panel._check_rows[0].get_subtitle() == ""
            or panel._check_rows[0].get_subtitle() is None
        )

    def test_set_checks_clears_previous(self, panel):
        panel.set_checks([{"name": "A", "status": "passed", "explanation": ""}])
        assert len(panel._check_rows) == 1
        panel.set_checks(
            [
                {"name": "B", "status": "passed", "explanation": ""},
                {"name": "C", "status": "passed", "explanation": ""},
            ]
        )
        assert len(panel._check_rows) == 2

    def test_set_checks_hides_spinner(self, panel):
        panel.set_loading()
        panel.set_checks([{"name": "A", "status": "passed", "explanation": ""}])
        assert panel._spinner.get_visible() is False


class TestPreflightPanelChanges:
    def test_set_changes(self, panel):
        changes = [
            {"action": "Install", "package": "nvidia-driver-565", "version": "565.57"},
            {"action": "Remove", "package": "nvidia-driver-550", "version": "550.40"},
        ]
        panel.set_changes(changes)
        assert len(panel._change_rows) == 2
        assert "Install nvidia-driver-565" in panel._change_rows[0].get_title()

    def test_set_changes_clears_previous(self, panel):
        panel.set_changes([{"action": "Install", "package": "a", "version": "1"}])
        assert len(panel._change_rows) == 1
        panel.set_changes([])
        assert len(panel._change_rows) == 0


class TestPreflightPanelRollback:
    def test_set_rollback_plan(self, panel):
        panel.set_rollback_plan("Snapshot will be created before installation")
        assert panel._rollback_row is not None
        assert panel._rollback_row.get_subtitle() == "Snapshot will be created before installation"


class TestPreflightPanelError:
    def test_set_error(self, panel):
        panel.set_error("D-Bus timeout")
        assert panel.all_passed is False
        assert panel._error_row is not None
        assert panel._error_row.get_subtitle() == "D-Bus timeout"

    def test_set_error_hides_spinner(self, panel):
        panel.set_loading()
        panel.set_error("Timeout")
        assert panel._spinner.get_visible() is False
