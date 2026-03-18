"""Unit tests for Story 2.3: Driver Installation with Progress & Safety."""

from __future__ import annotations

import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# verde_daemon package registration is handled by tests/conftest.py
from audit import OP_INSTALL_DRIVER
from gi.repository import GLib

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def idle_reset():
    return MagicMock()


@pytest.fixture
def idle_hold():
    return MagicMock()


@pytest.fixture
def idle_release():
    return MagicMock()


@pytest.fixture
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path)


@pytest.fixture
def service(mock_loop, idle_reset, idle_hold, idle_release, mock_audit, tmp_path):
    from service import VerdeService
    from snapshot_manager import SnapshotManager

    snap_mgr = SnapshotManager(
        snapshot_dir=tmp_path / "snapshots",
        audit_logger=mock_audit,
    )

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=idle_reset,
        on_idle_hold=idle_hold,
        on_idle_release=idle_release,
        introspection_xml=_XML,
        audit_logger=mock_audit,
        snapshot_manager=snap_mgr,
    )


@pytest.fixture
def wired_service(service, mock_connection):
    """Service with D-Bus connection already wired up."""
    service._on_bus_acquired(mock_connection, "com.verde.Manager")
    return service


@pytest.fixture
def mock_invocation():
    return MagicMock()


@pytest.fixture
def mock_connection():
    conn = MagicMock()
    conn.register_object.return_value = 42
    return conn


def _make_params(version: str) -> MagicMock:
    """Create a mock GLib.Variant for InstallDriver(s version) parameters."""
    params = MagicMock()
    child = MagicMock()
    child.get_string.return_value = version
    params.get_child_value.return_value = child
    return params


def _run_do_install(service, op_id, version, sender):
    """Run _do_install with inhibitor lock fully mocked."""
    with (
        patch.object(service, "_acquire_inhibitor_lock", return_value=None),
        patch.object(service, "_release_inhibitor_lock"),
        patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
    ):
        service._do_install(op_id, version, sender)


# ===================================================================
# Concurrency guard
# ===================================================================


class TestConcurrencyGuard:
    @patch("service.check_authorization", return_value=True)
    def test_first_install_returns_op_id(
        self, _auth, wired_service, mock_invocation, mock_connection
    ):
        """First InstallDriver returns an op_id string."""
        params = _make_params("565")

        with patch.object(wired_service, "_do_install"):
            wired_service._handle_method_call(
                mock_connection,
                ":1.42",
                "/com/verde/Manager",
                "com.verde.Manager",
                "InstallDriver",
                params,
                mock_invocation,
            )

        mock_invocation.return_value.assert_called_once()
        returned = mock_invocation.return_value.call_args[0][0]
        assert isinstance(returned, GLib.Variant)

    @patch("service.check_authorization", return_value=True)
    def test_second_install_rejected_when_in_progress(
        self, _auth, wired_service, mock_invocation, mock_connection
    ):
        """Second InstallDriver returns OperationInProgress error."""
        wired_service._operation_in_progress = True
        params = _make_params("565")

        wired_service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.OperationInProgress"

    def test_guard_resets_on_success(self, wired_service):
        """Concurrency guard resets after successful install."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        assert wired_service._operation_in_progress is False
        assert wired_service._current_op_id is None

    def test_guard_resets_on_failure(self, wired_service):
        """Concurrency guard resets after failed install."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(
            wired_service, "_run_apt_install", return_value=(False, "err", 1, False)
        ):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        assert wired_service._operation_in_progress is False

    def test_guard_resets_on_exception(self, wired_service):
        """Concurrency guard resets even on unhandled exception."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(wired_service, "_run_apt_install", side_effect=RuntimeError("boom")):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        assert wired_service._operation_in_progress is False

    @patch("service.check_authorization", return_value=True)
    def test_op_id_is_12_hex_chars(self, _auth, wired_service, mock_invocation, mock_connection):
        """Generated op_id is 12 hex characters."""
        params = _make_params("565")

        with patch.object(wired_service, "_do_install"):
            wired_service._dispatch_install_driver(params, mock_invocation, ":1.42")

        op_id = wired_service._current_op_id
        assert len(op_id) == 12
        # Should be valid hex
        int(op_id, 16)


# ===================================================================
# Idle timer hold/release
# ===================================================================


class TestIdleTimerHoldRelease:
    def test_idle_hold_called_during_operation(self, wired_service, idle_hold, idle_release):
        """Idle timer is held during install and released after."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        idle_hold.assert_called()
        idle_release.assert_called()


# ===================================================================
# Systemd inhibitor lock
# ===================================================================


class TestInhibitorLock:
    def test_acquire_calls_dbus(self, wired_service, mock_connection):
        """Inhibitor lock calls logind Inhibit via D-Bus."""
        mock_fd_list = MagicMock()
        mock_fd_list.get.return_value = 7
        mock_result = MagicMock()
        mock_result.get_child_value.return_value.get_handle.return_value = 0
        mock_connection.call_with_unix_fd_list_sync.return_value = (mock_result, mock_fd_list)

        fd = wired_service._acquire_inhibitor_lock("test reason")

        assert fd == 7
        mock_connection.call_with_unix_fd_list_sync.assert_called_once()
        call_args = mock_connection.call_with_unix_fd_list_sync.call_args[0]
        assert call_args[0] == "org.freedesktop.login1"
        assert call_args[3] == "Inhibit"

    def test_acquire_returns_none_on_failure(self, wired_service, mock_connection):
        """Inhibitor lock failure returns None, does not raise."""
        mock_connection.call_with_unix_fd_list_sync.side_effect = Exception("nope")

        fd = wired_service._acquire_inhibitor_lock("test")
        assert fd is None

    def test_acquire_returns_none_without_connection(self, service):
        """Inhibitor lock returns None when no D-Bus connection."""
        fd = service._acquire_inhibitor_lock("test")
        assert fd is None

    def test_release_closes_fd(self):
        """Release closes the file descriptor."""
        from service import VerdeService

        with patch("service.os.close") as mock_close:
            VerdeService._release_inhibitor_lock(7)
            mock_close.assert_called_once_with(7)

    def test_release_none_is_noop(self):
        """Release with None fd does nothing."""
        from service import VerdeService

        with patch("service.os.close") as mock_close:
            VerdeService._release_inhibitor_lock(None)
            mock_close.assert_not_called()

    def test_inhibitor_failure_does_not_abort_install(self, wired_service, mock_connection):
        """Install proceeds even if inhibitor lock acquisition fails."""
        mock_connection.call_with_unix_fd_list_sync.side_effect = Exception("denied")
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with (
            patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._do_install("testop", "565", ":1.42")

        assert wired_service._operation_in_progress is False


# ===================================================================
# APT subprocess with Status-Fd progress parsing
# ===================================================================


class TestAptInstall:
    def test_apt_success_returns_true(self, wired_service):
        """Successful apt-get returns (True, message)."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = MagicMock()

        read_fd, write_fd = os.pipe()
        os.close(write_fd)  # EOF immediately

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            success, msg, *_ = wired_service._run_apt_install("test-op", "565")

        assert success is True
        assert "565" in msg

    def test_apt_failure_returns_false_with_stderr(self, wired_service):
        """Failed apt-get returns (False, stderr)."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = 1
        mock_proc.stderr.read.return_value = b"E: Package not found"

        read_fd, write_fd = os.pipe()
        os.close(write_fd)

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            success, msg, *_ = wired_service._run_apt_install("test-op", "565")

        assert success is False
        assert "Package not found" in msg

    def test_apt_not_found_returns_error(self, wired_service):
        """Missing apt-get returns error on FileNotFoundError."""
        read_fd, write_fd = os.pipe()

        with (
            patch("service.subprocess.Popen", side_effect=FileNotFoundError),
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
        ):
            success, msg, *_ = wired_service._run_apt_install("test-op", "565")

        assert success is False
        assert "not found" in msg or "not executable" in msg

    def test_apt_permission_error_returns_error(self, wired_service):
        """PermissionError from Popen is handled (P-2: broad OSError catch)."""
        read_fd, write_fd = os.pipe()

        with (
            patch("service.subprocess.Popen", side_effect=PermissionError("denied")),
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
        ):
            success, msg, *_ = wired_service._run_apt_install("test-op", "565")

        assert success is False
        assert "not found" in msg or "not executable" in msg

    def test_apt_timeout_terminates_process(self, wired_service):
        """APT timeout sends SIGTERM when select deadline expires."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.stderr = MagicMock()

        read_fd, write_fd = os.pipe()
        os.close(write_fd)

        # Simulate deadline already expired so select loop exits immediately
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            # First call sets deadline; subsequent calls return past deadline
            if call_count <= 1:
                return 0.0
            return 601.0

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
            patch("service.time.monotonic", side_effect=fake_monotonic),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            success, msg, *_ = wired_service._run_apt_install("test-op", "565")

        assert success is False
        assert "timed out" in msg
        mock_proc.terminate.assert_called_once()

    def test_apt_command_uses_list_form(self, wired_service):
        """APT command is list form, never shell=True (NFR-SEC-3)."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = MagicMock()

        read_fd, write_fd = os.pipe()
        os.close(write_fd)

        with (
            patch("service.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("service.os.pipe", return_value=(read_fd, write_fd)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=lambda fn: fn()),
        ):
            wired_service._run_apt_install("test-op", "565")

        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert isinstance(cmd, list)
        assert "nvidia-driver-565" in cmd
        # Ensure shell=True is NOT passed
        assert call_args[1].get("shell") is not True

    def test_progress_signals_emitted(self, wired_service, mock_connection):
        """APT status lines trigger OperationProgress signals."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = MagicMock()

        # Create a pipe with status data
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"pmstatus:nvidia-driver-565:25.0:Unpacking\n")
        os.write(write_fd, b"pmstatus:nvidia-driver-565:75.0:Configuring\n")
        os.close(write_fd)

        def capture_idle(fn):
            fn()
            return 0

        # We need to intercept os.pipe to return our pre-loaded pipe,
        # but also handle the os.close(write_fd) call in the method.
        # Use a fake write_fd that's already closed.
        fake_write_fd = os.dup(read_fd)  # just need a valid fd number
        os.close(fake_write_fd)

        with (
            patch("service.subprocess.Popen", return_value=mock_proc),
            patch("service.os.pipe", return_value=(read_fd, fake_write_fd)),
            patch("service.os.close"),
            patch("service.GLib.idle_add", side_effect=capture_idle),
        ):
            wired_service._run_apt_install("test-op", "565")

        # Check that OperationProgress signals were emitted
        progress_calls = [
            c for c in mock_connection.emit_signal.call_args_list if c[0][3] == "OperationProgress"
        ]
        assert len(progress_calls) == 2


# ===================================================================
# D-Bus signal emission
# ===================================================================


class TestSignalEmission:
    def test_emit_signal_uses_idle_add(self, wired_service):
        """Signals are dispatched via GLib.idle_add for thread safety."""
        with patch("service.GLib.idle_add") as mock_idle:
            wired_service._emit_signal("OperationProgress", "(sds)", ("op1", 50.0, "msg"))
            mock_idle.assert_called_once()

    def test_emit_signal_emits_on_connection(self, wired_service, mock_connection):
        """Signal is emitted on the D-Bus connection when callback runs."""
        with patch("service.GLib.idle_add", side_effect=lambda fn: fn()):
            wired_service._emit_signal("OperationProgress", "(sds)", ("op1", 50.0, "msg"))

        mock_connection.emit_signal.assert_called_once()
        call_args = mock_connection.emit_signal.call_args[0]
        assert call_args[3] == "OperationProgress"

    def test_success_path_emits_complete_and_reboot(self, wired_service, mock_connection):
        """Success emits OperationComplete(True) and RebootRequired(True)."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        signal_names = [c[0][3] for c in mock_connection.emit_signal.call_args_list]
        assert "OperationComplete" in signal_names
        assert "RebootRequired" in signal_names

    def test_failure_path_emits_complete_false_no_reboot(self, wired_service, mock_connection):
        """Failure emits OperationComplete(False) without RebootRequired."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(
            wired_service, "_run_apt_install", return_value=(False, "err", 1, False)
        ):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        signal_names = [c[0][3] for c in mock_connection.emit_signal.call_args_list]
        assert "OperationComplete" in signal_names
        assert "RebootRequired" not in signal_names

    def test_emit_signal_safe_without_connection(self, service):
        """_emit_signal is safe when connection is None."""
        # Should not raise
        with patch("service.GLib.idle_add", side_effect=lambda fn: fn()):
            service._emit_signal("OperationProgress", "(sds)", ("op1", 50.0, "msg"))


# ===================================================================
# Audit log integration
# ===================================================================


class TestAuditLogging:
    def test_audit_logs_start_and_success(self, wired_service, mock_audit):
        """Audit logger records start and success entries."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        import json

        log_file = mock_audit._log_file
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        install_ops = [e for e in ops if e["operation"] == OP_INSTALL_DRIVER]
        results = [e["result"] for e in install_ops]
        assert "started" in results
        assert "success" in results

    def test_audit_logs_start_and_failure(self, wired_service, mock_audit):
        """Audit logger records start and failure entries with error."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(
            wired_service, "_run_apt_install", return_value=(False, "E: broken", 1, False)
        ):
            _run_do_install(wired_service, "testop", "565", ":1.42")

        import json

        log_file = mock_audit._log_file
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        results = [e["result"] for e in ops]
        assert "started" in results
        assert "failed" in results
        # Failed entry should have error field
        failed = next(e for e in ops if e["result"] == "failed")
        assert "error" in failed

    def test_audit_records_sender(self, wired_service, mock_audit):
        """Audit entries include the D-Bus sender."""
        wired_service._operation_in_progress = True
        wired_service._current_op_id = "testop"

        with patch.object(wired_service, "_run_apt_install", return_value=(True, "ok", 0, False)):
            _run_do_install(wired_service, "testop", "565", ":1.99")

        import json

        log_file = mock_audit._log_file
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        install_ops = [e for e in ops if e["operation"] == OP_INSTALL_DRIVER]
        assert all(e["caller"] == ":1.99" for e in install_ops)


# ===================================================================
# OperationInProgress property
# ===================================================================


class TestOperationInProgressProperty:
    def test_property_false_by_default(self, wired_service, mock_connection):
        """OperationInProgress property is False when no operation running."""
        result = wired_service._handle_get_property(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "OperationInProgress",
        )
        assert result.get_boolean() is False

    def test_property_true_during_operation(self, wired_service, mock_connection):
        """OperationInProgress property is True when operation running."""
        wired_service._operation_in_progress = True
        result = wired_service._handle_get_property(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "OperationInProgress",
        )
        assert result.get_boolean() is True


# ===================================================================
# Snapshot stub
# ===================================================================


class TestSnapshotStub:
    def test_snapshot_stub_does_not_raise(self, service):
        """Snapshot stub logs but does not raise."""
        service._create_pre_install_snapshot("565")


# ===================================================================
# Input validation integration
# ===================================================================


class TestInputValidationIntegration:
    @patch("service.check_authorization", return_value=True)
    def test_invalid_version_rejected_before_auth(
        self, _auth, wired_service, mock_invocation, mock_connection
    ):
        """Invalid driver version rejected with InvalidArgument error."""
        params = _make_params("../etc/passwd")

        wired_service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.InvalidArgument"
        # Polkit should NOT have been called — validation is before auth
        _auth.assert_not_called()

    @patch("service.check_authorization", return_value=True)
    def test_empty_version_rejected(self, _auth, wired_service, mock_invocation, mock_connection):
        """Empty driver version rejected."""
        params = _make_params("")

        wired_service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )

        mock_invocation.return_dbus_error.assert_called_once()

    @patch("service.check_authorization", return_value=True)
    def test_injection_attempt_rejected(
        self, _auth, wired_service, mock_invocation, mock_connection
    ):
        """Command injection attempt in version is rejected."""
        params = _make_params("560; rm -rf /")

        wired_service._handle_method_call(
            mock_connection,
            ":1.42",
            "/com/verde/Manager",
            "com.verde.Manager",
            "InstallDriver",
            params,
            mock_invocation,
        )

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.InvalidArgument"

    @patch("service.check_authorization", return_value=True)
    def test_valid_versions_accepted(self, _auth, wired_service, mock_invocation, mock_connection):
        """Valid versions pass validation and reach dispatch."""
        for version in ("535", "565", "560-server", "570-open"):
            inv = MagicMock()
            params = _make_params(version)

            with patch.object(wired_service, "_do_install"):
                wired_service._operation_in_progress = False
                wired_service._dispatch_install_driver(params, inv, ":1.42")

            # Should have returned an op_id, not an error
            inv.return_dbus_error.assert_not_called()
            inv.return_value.assert_called_once()
