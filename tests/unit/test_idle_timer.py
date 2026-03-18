"""Unit tests for _IdleTimer hold/release (Story 2.3, IG-1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ===================================================================
# _IdleTimer fixture — import from main.py
# ===================================================================


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def timer(mock_loop):
    """Create an _IdleTimer with mocked GLib."""
    from main import _IdleTimer

    return _IdleTimer(timeout=60, loop=mock_loop)


# ===================================================================
# hold() / release() tests
# ===================================================================


class TestIdleTimerHold:
    @patch("main.GLib.source_remove")
    @patch("main.GLib.timeout_add_seconds", return_value=42)
    def test_hold_cancels_pending_timeout(self, mock_add, mock_remove, timer):
        """hold() cancels the pending timeout source."""
        timer.start()
        assert timer._source_id == 42

        timer.hold()

        mock_remove.assert_called_with(42)
        assert timer._source_id is None

    @patch("main.GLib.source_remove")
    @patch("main.GLib.timeout_add_seconds", return_value=42)
    def test_hold_is_safe_when_no_timer_active(self, mock_add, mock_remove, timer):
        """hold() is a no-op when no timeout is scheduled."""
        timer.hold()
        mock_remove.assert_not_called()

    @patch("main.GLib.source_remove")
    @patch("main.GLib.timeout_add_seconds", return_value=99)
    def test_release_restarts_timeout(self, mock_add, mock_remove, timer):
        """release() restarts the idle countdown."""
        timer.start()
        mock_add.reset_mock()

        timer.hold()
        timer.release()

        # release should have called start() which calls timeout_add_seconds
        mock_add.assert_called_once_with(60, timer._on_timeout)
        assert timer._source_id == 99

    @patch("main.GLib.source_remove")
    @patch("main.GLib.timeout_add_seconds", return_value=42)
    def test_daemon_does_not_exit_during_hold(self, mock_add, mock_remove, timer, mock_loop):
        """The main loop is NOT quit while the timer is held."""
        timer.start()
        timer.hold()

        # Verify source_id is None — no timeout can fire
        assert timer._source_id is None

    @patch("main.GLib.source_remove")
    @patch("main.GLib.timeout_add_seconds", return_value=42)
    def test_hold_release_lifecycle(self, mock_add, mock_remove, timer):
        """Full hold-release cycle: start -> hold -> release restores timer."""
        timer.start()
        assert timer._source_id is not None

        timer.hold()
        assert timer._source_id is None

        timer.release()
        assert timer._source_id is not None
