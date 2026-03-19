"""Unit tests for Story 3.3: Snapshot Rollback daemon logic."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from audit import OP_ROLLBACK_DRIVER
from gi.repository import GLib
from snapshot_manager import SnapshotManager, _compute_sha256

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_loop():
    return MagicMock()


@pytest.fixture
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path / "audit")


@pytest.fixture
def snapshot_dir(tmp_path):
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


@pytest.fixture
def snapshot_manager(snapshot_dir, mock_audit):
    return SnapshotManager(
        snapshot_dir=snapshot_dir,
        audit_logger=mock_audit,
    )


@pytest.fixture
def service(mock_loop, mock_audit, snapshot_manager):
    from service import VerdeService

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=MagicMock(),
        on_idle_hold=MagicMock(),
        on_idle_release=MagicMock(),
        introspection_xml=_XML,
        audit_logger=mock_audit,
        snapshot_manager=snapshot_manager,
    )


@pytest.fixture
def wired_service(service):
    conn = MagicMock()
    conn.register_object.return_value = 42
    service._on_bus_acquired(conn, "com.verde.Manager")
    return service


@pytest.fixture
def mock_invocation():
    inv = MagicMock()
    inv.return_value = MagicMock()
    inv.return_dbus_error = MagicMock()
    return inv


def _create_valid_snapshot(snap_dir, sid="20260318T143000_nvidia-560-ab01"):
    """Create a snapshot file with valid SHA-256."""
    data = {
        "schema_version": 1,
        "snapshot_id": sid,
        "timestamp": "2026-03-18T14:30:00+00:00",
        "driver_packages": [
            {"name": "nvidia-driver-560", "version": "560.35.03", "architecture": "amd64"},
        ],
        "kernel_version": "6.8.0-45-generic",
        "dkms_modules": [],
        "config_files": {},
        "operation": {"type": "driver_install", "target_driver": "560", "user": "u"},
        "sha256": None,
    }
    data["sha256"] = _compute_sha256(data)
    (snap_dir / f"{sid}.json").write_text(json.dumps(data))
    return sid


def _make_rollback_params(snapshot_id: str) -> GLib.Variant:
    return GLib.Variant("(s)", (snapshot_id,))


# ===================================================================
# RollbackDriver dispatch
# ===================================================================


class TestRollbackDriverDispatch:
    @patch("service.check_authorization", return_value=True)
    @patch("service.detect_dpkg_lock", return_value=None)
    def test_returns_op_id_on_valid_request(
        self, _lock, _auth, wired_service, mock_invocation, snapshot_dir
    ):
        """RollbackDriver returns op_id immediately."""
        sid = _create_valid_snapshot(snapshot_dir)
        params = _make_rollback_params(sid)

        with patch("service.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            wired_service._dispatch_rollback_driver(params, mock_invocation, ":1.42")

        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        op_id = call_args.unpack()[0]
        assert isinstance(op_id, str)
        assert len(op_id) == 12

    @patch("service.check_authorization", return_value=True)
    @patch("service.detect_dpkg_lock", return_value=None)
    def test_rejects_when_operation_in_progress(
        self, _lock, _auth, wired_service, mock_invocation, snapshot_dir
    ):
        """RollbackDriver returns OperationInProgress when another op is running."""
        sid = _create_valid_snapshot(snapshot_dir)
        wired_service._operation_in_progress = True
        wired_service._current_op_type = "InstallDriver"

        params = _make_rollback_params(sid)
        wired_service._dispatch_rollback_driver(params, mock_invocation, ":1.42")

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.OperationInProgress"


# ===================================================================
# Rollback worker
# ===================================================================


class TestRollbackWorker:
    def test_successful_rollback_emits_signals(self, wired_service, snapshot_dir, mock_audit):
        """_do_rollback emits OperationComplete(success) and RebootRequired on success."""
        sid = _create_valid_snapshot(snapshot_dir)
        signals = []

        def track_signal(name, vtype, args):
            signals.append((name, args))

        with (
            patch.object(wired_service, "_emit_signal", side_effect=track_signal),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch(
                "service.GLib.idle_add",
                side_effect=lambda fn, *a: fn(*a) if callable(fn) else None,
            ),
            patch(
                "snapshot_manager._query_nvidia_packages",
                return_value=[
                    {"name": "nvidia-driver-565", "version": "565.57", "architecture": "amd64"},
                ],
            ),
            patch.object(SnapshotManager, "_run_apt", return_value=(True, "")),
            patch("subprocess.run"),
        ):
            wired_service._do_rollback("op-123", sid, ":1.42")

        # Check OperationProgress was emitted
        progress_signals = [s for s in signals if s[0] == "OperationProgress"]
        assert len(progress_signals) >= 1

        # Check OperationComplete(success=True)
        complete_signals = [s for s in signals if s[0] == "OperationComplete"]
        assert len(complete_signals) == 1
        assert complete_signals[0][1][1] is True  # success

        # Check RebootRequired
        reboot_signals = [s for s in signals if s[0] == "RebootRequired"]
        assert len(reboot_signals) == 1
        assert reboot_signals[0][1][0] is True

    def test_failed_rollback_emits_failure_signal(self, wired_service, snapshot_dir):
        """_do_rollback emits OperationComplete(success=False) when restore fails."""
        sid = _create_valid_snapshot(snapshot_dir)
        signals = []

        def track_signal(name, vtype, args):
            signals.append((name, args))

        with (
            patch.object(wired_service, "_emit_signal", side_effect=track_signal),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch(
                "service.GLib.idle_add",
                side_effect=lambda fn, *a: fn(*a) if callable(fn) else None,
            ),
            patch(
                "snapshot_manager._query_nvidia_packages",
                return_value=[
                    {"name": "nvidia-driver-565", "version": "565.57", "architecture": "amd64"},
                ],
            ),
            patch.object(SnapshotManager, "_run_apt", return_value=(False, "dpkg locked")),
        ):
            wired_service._do_rollback("op-123", sid, ":1.42")

        complete_signals = [s for s in signals if s[0] == "OperationComplete"]
        assert len(complete_signals) == 1
        assert complete_signals[0][1][1] is False  # failure

    def test_rollback_audit_logged(self, wired_service, snapshot_dir, mock_audit):
        """_do_rollback writes to audit log on success."""
        sid = _create_valid_snapshot(snapshot_dir)

        with (
            patch.object(wired_service, "_emit_signal"),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch(
                "service.GLib.idle_add",
                side_effect=lambda fn, *a: fn(*a) if callable(fn) else None,
            ),
            patch(
                "snapshot_manager._query_nvidia_packages",
                return_value=[
                    {"name": "nvidia-driver-565", "version": "565.57", "architecture": "amd64"},
                ],
            ),
            patch.object(SnapshotManager, "_run_apt", return_value=(True, "")),
            patch("subprocess.run"),
        ):
            wired_service._do_rollback("op-123", sid, ":1.42")

        # Check audit log
        log_file = mock_audit._log_file
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        rollback_ops = [e for e in ops if e["operation"] == OP_ROLLBACK_DRIVER]
        assert len(rollback_ops) >= 2  # started + success
        results = [e["result"] for e in rollback_ops]
        assert "started" in results
        assert "success" in results

    def test_rollback_releases_guard(self, wired_service, snapshot_dir):
        """_do_rollback releases the concurrency guard in the finally block."""
        sid = _create_valid_snapshot(snapshot_dir)
        wired_service._operation_in_progress = True

        released = []

        def mock_idle_add(fn, *args):
            if callable(fn):
                result = fn(*args) if args else fn()
                if result is not None:
                    released.append(True)

        with (
            patch.object(wired_service, "_emit_signal"),
            patch.object(wired_service, "_acquire_inhibitor_lock", return_value=None),
            patch.object(wired_service, "_release_inhibitor_lock"),
            patch("service.GLib.idle_add", side_effect=mock_idle_add),
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
        ):
            wired_service._do_rollback("op-123", sid, ":1.42")

        # The guard release callback should have run
        assert wired_service._operation_in_progress is False


# ===================================================================
# Input validation
# ===================================================================


class TestRollbackInputValidation:
    @patch("service.check_authorization", return_value=True)
    def test_rejects_invalid_snapshot_id(self, _auth, wired_service, mock_invocation):
        """RollbackDriver rejects path traversal in snapshot_id."""
        params = GLib.Variant("(s)", ("../../../etc/passwd",))
        wired_service._handle_method_call(
            None, ":1.42", None, None, "RollbackDriver", params, mock_invocation
        )

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.InvalidArgument"
