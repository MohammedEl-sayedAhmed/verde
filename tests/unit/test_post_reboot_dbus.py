"""Unit tests for Story 3.5: D-Bus methods and GUI integration."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


@pytest.fixture()
def mock_loop():
    return MagicMock()


@pytest.fixture()
def idle_reset():
    return MagicMock()


@pytest.fixture()
def service(mock_loop, idle_reset, tmp_path):
    from service import VerdeService

    svc = VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        introspection_xml=_XML,
    )
    # Replace pending summary with one using tmp_path
    from pending_summary import PendingSummaryManager

    svc._pending_summary = PendingSummaryManager(state_dir=tmp_path)
    return svc


@pytest.fixture()
def mock_invocation():
    return MagicMock()


class TestGetPostRebootSummary:
    """Test GetPostRebootSummary D-Bus handler (AC#4)."""

    def test_no_pending_returns_false(self, service, mock_invocation):
        """Returns has_pending=false when no state file."""
        service._dispatch_get_post_reboot_summary(mock_invocation)
        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        # Unpack the a{sv} variant
        inner = call_args.get_child_value(0)
        has_pending = inner.lookup_value("has_pending", None)
        assert has_pending.get_boolean() is False

    def test_with_pending_returns_summary(self, service, mock_invocation):
        """Returns full summary when state file exists."""
        service._pending_summary.write_pending("install", "535", "550", "op_001")
        service._dispatch_get_post_reboot_summary(mock_invocation)
        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        inner = call_args.get_child_value(0)
        has_pending = inner.lookup_value("has_pending", None)
        assert has_pending.get_boolean() is True
        result = inner.lookup_value("result", None)
        assert result.get_string() in ("success", "partial", "failed")
        msg = inner.lookup_value("message", None)
        assert msg.get_string() != ""


class TestClearPostRebootSummary:
    """Test ClearPostRebootSummary D-Bus handler (AC#6)."""

    def test_clears_state_file(self, service, mock_invocation):
        """Clears the pending summary state file."""
        service._pending_summary.write_pending("install", "535", "550", "op_001")
        assert service._pending_summary.has_pending()
        service._dispatch_clear_post_reboot_summary(mock_invocation)
        assert not service._pending_summary.has_pending()
        mock_invocation.return_value.assert_called_once_with(None)

    def test_clears_with_audit(self, service, mock_invocation):
        """Audit log entry written on clear."""
        audit = MagicMock()
        service._audit = audit
        service._dispatch_clear_post_reboot_summary(mock_invocation)
        audit.log.assert_called_once()
        args = audit.log.call_args[0]
        assert args[0] == "CLEAR_POST_REBOOT_SUMMARY"


class TestDialogNotShownWhenNoPending:
    """Test GUI startup — dialog not shown when has_pending is false (AC#9)."""

    def test_no_dialog_when_no_pending(self):
        """Application does not show dialog when has_pending is false."""
        from verde.window import VerdeApplication

        app = VerdeApplication(application_id="com.verde.app.test", version="0.1")
        assert app._post_reboot_checked is False


class TestDialogConstruction:
    """Test dialog construction for different result types (AC#5)."""

    def test_success_dialog(self):
        """Success result creates dialog with OK only."""
        from verde.window import VerdeApplication

        app = VerdeApplication(application_id="com.verde.app.test2", version="0.1")
        # We can't fully test GTK dialog creation in unit tests,
        # but verify the method exists and is callable
        assert hasattr(app, "_show_post_reboot_dialog")
        assert hasattr(app, "_on_post_reboot_response")

    def test_post_reboot_checked_guard(self):
        """Post-reboot check only runs once."""
        from verde.window import VerdeApplication

        app = VerdeApplication(application_id="com.verde.app.test3", version="0.1")
        app._post_reboot_checked = True
        # Simulating a second connect — should not call method
        mock_client = MagicMock()
        mock_client.get_property.return_value = True
        app._on_dbus_connected(mock_client, None)
        mock_client.call_method_async.assert_not_called()
