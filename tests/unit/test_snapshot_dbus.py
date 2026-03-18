"""Unit tests for Story 3.2: Snapshot Management D-Bus dispatch."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
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
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path / "audit")


@pytest.fixture
def snapshot_manager(tmp_path, mock_audit):
    from snapshot_manager import SnapshotManager

    return SnapshotManager(
        snapshot_dir=tmp_path / "snapshots",
        audit_logger=mock_audit,
    )


@pytest.fixture
def service(mock_loop, mock_audit, snapshot_manager):
    from service import VerdeService

    return VerdeService(
        loop=mock_loop,
        on_idle_reset=MagicMock(),
        introspection_xml=_XML,
        audit_logger=mock_audit,
        snapshot_manager=snapshot_manager,
    )


@pytest.fixture
def mock_invocation():
    inv = MagicMock()
    inv.return_value = MagicMock()
    inv.return_dbus_error = MagicMock()
    return inv


# ===================================================================
# ListSnapshots dispatch
# ===================================================================


class TestListSnapshotsDispatch:
    def test_returns_empty_list_when_no_snapshots(self, service, mock_invocation):
        """ListSnapshots returns empty aa{sv} when no snapshots exist."""
        service._dispatch_list_snapshots(mock_invocation)

        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        # Unpack the GLib.Variant tuple
        result = call_args.unpack()
        assert result == ([],)

    def test_returns_snapshot_data(self, service, snapshot_manager, mock_invocation):
        """ListSnapshots returns snapshot metadata in aa{sv} format."""
        with (
            patch(
                "snapshot_manager._query_nvidia_packages",
                return_value=[
                    {"name": "nvidia-driver-560", "version": "560.35", "architecture": "amd64"},
                ],
            ),
            patch(
                "snapshot_manager._query_dkms_modules",
                return_value=[
                    {
                        "module": "nvidia",
                        "version": "560.35",
                        "kernel": "6.8.0",
                        "status": "installed",
                    },
                ],
            ),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = snapshot_manager.create_snapshot("driver_install", "560", "testuser")

        service._dispatch_list_snapshots(mock_invocation)

        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        result = call_args.unpack()
        snapshots = result[0]
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap["id"] == sid
        assert snap["driver_version"] == "560"
        assert snap["kernel_version"] != ""
        assert isinstance(snap["file_size"], int)
        assert snap["file_size"] > 0
        assert snap["dkms_status"] == "installed"
        assert "packages" in snap


# ===================================================================
# DeleteSnapshot dispatch
# ===================================================================


class TestDeleteSnapshotDispatch:
    def test_deletes_existing_snapshot(
        self, service, snapshot_manager, mock_invocation, mock_audit
    ):
        """DeleteSnapshot successfully deletes an existing snapshot."""
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = snapshot_manager.create_snapshot("driver_install", "560", "testuser")

        params = GLib.Variant("(s)", (sid,))
        service._dispatch_delete_snapshot(params, mock_invocation, ":1.42")

        mock_invocation.return_value.assert_called_once()
        call_args = mock_invocation.return_value.call_args[0][0]
        assert call_args.unpack() == (True,)

        # Verify audit log recorded the deletion
        log_file = mock_audit._log_file
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        ops = [json.loads(line) for line in lines]
        delete_ops = [e for e in ops if e["operation"] == "DELETE_SNAPSHOT"]
        assert len(delete_ops) == 1
        assert delete_ops[0]["result"] == "success"
        assert delete_ops[0]["params"]["snapshot_id"] == sid

    def test_returns_error_for_nonexistent_snapshot(self, service, mock_invocation):
        """DeleteSnapshot returns SnapshotNotFound for missing snapshots."""
        params = GLib.Variant("(s)", ("20260318T143000_nvidia-560-ab01",))
        service._dispatch_delete_snapshot(params, mock_invocation, ":1.42")

        mock_invocation.return_dbus_error.assert_called_once()
        error_name = mock_invocation.return_dbus_error.call_args[0][0]
        assert error_name == "com.verde.Error.SnapshotNotFound"
