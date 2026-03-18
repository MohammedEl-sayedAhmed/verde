"""Integration tests for Story 2.3: InstallDriver D-Bus method.

These tests exercise the full dispatch path through VerdeService._handle_method_call
with mocked apt subprocess and D-Bus connection, verifying signal sequences and
concurrency behaviour end-to-end.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_connection():
    conn = MagicMock()
    conn.register_object.return_value = 42
    return conn


@pytest.fixture
def wired_service(tmp_path, mock_connection):
    """Fully wired VerdeService with mocked D-Bus connection."""
    from audit import AuditLogger
    from service import VerdeService

    svc = VerdeService(
        loop=MagicMock(),
        on_idle_reset=MagicMock(),
        on_idle_hold=MagicMock(),
        on_idle_release=MagicMock(),
        introspection_xml=_XML,
        audit_logger=AuditLogger(log_dir=tmp_path),
    )
    svc._on_bus_acquired(mock_connection, "com.verde.Manager")
    return svc


def _make_params(version: str) -> MagicMock:
    params = MagicMock()
    child = MagicMock()
    child.get_string.return_value = version
    params.get_child_value.return_value = child
    return params


def _call_method(service, conn, method, params, sender=":1.42"):
    inv = MagicMock()
    service._handle_method_call(
        conn,
        sender,
        "/com/verde/Manager",
        "com.verde.Manager",
        method,
        params,
        inv,
    )
    return inv


# ===================================================================
# End-to-end install with mocked apt
# ===================================================================


class TestInstallDriverE2E:
    @patch("service.check_authorization", return_value=True)
    def test_install_returns_op_id_and_emits_signals(self, _auth, wired_service, mock_connection):
        """Full InstallDriver call: returns op_id, emits progress + complete + reboot."""
        import os

        # Prepare a pipe with APT status data
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"pmstatus:nvidia-driver-565:50.0:Unpacking\n")
        os.write(write_fd, b"pmstatus:nvidia-driver-565:100.0:Installed\n")
        os.close(write_fd)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = MagicMock()

        fake_write_fd = 999  # won't be used since we patch os.close

        def run_idle(fn):
            fn()
            return 0

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, fake_write_fd)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=run_idle),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
        ):
            # Make the call — dispatch_install_driver will be called, then
            # _do_install in a thread. We patch Thread to run synchronously.
            import threading

            def sync_start(self_thread):
                self_thread.run()

            with patch.object(threading.Thread, "start", sync_start):
                inv = _call_method(
                    wired_service,
                    mock_connection,
                    "InstallDriver",
                    _make_params("565"),
                )

        # Should have returned an op_id
        inv.return_value.assert_called_once()
        inv.return_dbus_error.assert_not_called()

        # Verify signal sequence
        signal_names = [c[0][3] for c in mock_connection.emit_signal.call_args_list]
        assert "OperationProgress" in signal_names
        assert "OperationComplete" in signal_names
        assert "RebootRequired" in signal_names

        # OperationComplete should have success=True
        complete_calls = [
            c for c in mock_connection.emit_signal.call_args_list if c[0][3] == "OperationComplete"
        ]
        assert len(complete_calls) == 1

    @patch("service.check_authorization", return_value=True)
    def test_concurrency_rejection(self, _auth, wired_service, mock_connection):
        """Second InstallDriver during active operation returns error."""
        # Start first operation (don't actually run the thread)
        with patch.object(wired_service, "_do_install"):
            inv1 = _call_method(
                wired_service,
                mock_connection,
                "InstallDriver",
                _make_params("565"),
            )

        # First should succeed
        inv1.return_value.assert_called_once()
        inv1.return_dbus_error.assert_not_called()

        # Second should fail
        inv2 = _call_method(
            wired_service,
            mock_connection,
            "InstallDriver",
            _make_params("560"),
        )

        inv2.return_dbus_error.assert_called_once()
        error_name = inv2.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.OperationInProgress"

    @patch("service.check_authorization", return_value=True)
    def test_invalid_version_returns_error(self, _auth, wired_service, mock_connection):
        """Invalid version returns InvalidArgument, not OperationInProgress."""
        inv = _call_method(
            wired_service,
            mock_connection,
            "InstallDriver",
            _make_params("abc"),
        )

        inv.return_dbus_error.assert_called_once()
        error_name = inv.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.InvalidArgument"

    @patch("service.check_authorization", return_value=False)
    def test_unauthorized_returns_not_authorized(self, _auth, wired_service, mock_connection):
        """Unauthorized InstallDriver returns NotAuthorized error."""
        inv = _call_method(
            wired_service,
            mock_connection,
            "InstallDriver",
            _make_params("565"),
        )

        inv.return_dbus_error.assert_called_once()
        error_name = inv.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.NotAuthorized"

    @patch("service.check_authorization", return_value=True)
    def test_failed_install_emits_complete_false(self, _auth, wired_service, mock_connection):
        """Failed apt install emits OperationComplete(False)."""
        import os
        import threading

        read_fd, write_fd = os.pipe()
        os.close(write_fd)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = 1
        mock_proc.stderr.read.return_value = b"E: Unable to locate package"

        def run_idle(fn):
            fn()
            return 0

        def sync_start(self_thread):
            self_thread.run()

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, 999)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=run_idle),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch.object(threading.Thread, "start", sync_start),
        ):
            _call_method(
                wired_service,
                mock_connection,
                "InstallDriver",
                _make_params("565"),
            )

        signal_names = [c[0][3] for c in mock_connection.emit_signal.call_args_list]
        assert "OperationComplete" in signal_names
        # No RebootRequired on failure
        assert "RebootRequired" not in signal_names

        # Guard should be released
        assert wired_service._operation_in_progress is False
