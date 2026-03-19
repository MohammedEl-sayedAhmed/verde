"""Unit tests for Story 3.3: Rollback pre-flight checks."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from gi.repository import GLib
from snapshot_manager import SnapshotManager, _compute_sha256

_XML_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "com.verde.Manager.xml"
_XML = _XML_PATH.read_text()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def snapshot_dir(tmp_path):
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


@pytest.fixture
def mock_audit(tmp_path):
    from audit import AuditLogger

    return AuditLogger(log_dir=tmp_path / "audit")


@pytest.fixture
def snapshot_manager(snapshot_dir, mock_audit):
    return SnapshotManager(
        snapshot_dir=snapshot_dir,
        audit_logger=mock_audit,
    )


@pytest.fixture
def service(snapshot_manager, mock_audit):
    from service import VerdeService

    return VerdeService(
        loop=MagicMock(),
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


def _create_snapshot(snap_dir, sid="20260318T143000_nvidia-560-ab01", packages=None):
    """Create a valid snapshot file."""
    data = {
        "schema_version": 1,
        "snapshot_id": sid,
        "timestamp": "2026-03-18T14:30:00+00:00",
        "driver_packages": packages
        or [
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


# ===================================================================
# Rollback pre-flight checks
# ===================================================================


class TestRollbackPreflight:
    def test_returns_all_4_checks(self, service, snapshot_dir, mock_invocation):
        """Rollback pre-flight returns 4 checks: integrity, disk, packages, dpkg."""
        sid = _create_snapshot(snapshot_dir)
        params = GLib.Variant("(s)", (f"rollback:{sid}",))

        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                service._driver_manager, "get_current_driver", return_value={"version": "565"}
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            service._dispatch_preflight_check(params, mock_invocation)

        mock_invocation.return_value.assert_called_once()
        result = mock_invocation.return_value.call_args[0][0].unpack()[0]
        checks = result["checks"]
        assert len(checks) == 4

        check_names = [c["name"] for c in checks]
        assert "snapshot_integrity" in check_names
        assert "disk_space" in check_names
        assert "package_availability" in check_names
        assert "dpkg_state" in check_names

    def test_integrity_check_fails_for_corrupted_snapshot(
        self, service, snapshot_dir, mock_invocation
    ):
        """Rollback pre-flight fails integrity for corrupted snapshot."""
        sid = _create_snapshot(snapshot_dir)

        # Tamper with snapshot
        path = snapshot_dir / f"{sid}.json"
        data = json.loads(path.read_text())
        data["kernel_version"] = "TAMPERED"
        path.write_text(json.dumps(data))

        params = GLib.Variant("(s)", (f"rollback:{sid}",))

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch.object(
                service._driver_manager, "get_current_driver", return_value={"version": "565"}
            ),
        ):
            service._dispatch_preflight_check(params, mock_invocation)

        result = mock_invocation.return_value.call_args[0][0].unpack()[0]
        assert result["overall_pass"] is False

        integrity = next(c for c in result["checks"] if c["name"] == "snapshot_integrity")
        assert integrity["status"] == "fail"

    def test_missing_snapshot_fails_integrity(self, service, mock_invocation):
        """Rollback pre-flight fails integrity for missing snapshot."""
        params = GLib.Variant("(s)", ("rollback:20260318T143000_nvidia-560-ab01",))

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch.object(
                service._driver_manager, "get_current_driver", return_value={"version": ""}
            ),
        ):
            service._dispatch_preflight_check(params, mock_invocation)

        result = mock_invocation.return_value.call_args[0][0].unpack()[0]
        assert result["overall_pass"] is False

    def test_includes_current_and_snapshot_driver_versions(
        self, service, snapshot_dir, mock_invocation
    ):
        """Rollback pre-flight includes current_driver and snapshot_driver."""
        sid = _create_snapshot(snapshot_dir)
        params = GLib.Variant("(s)", (f"rollback:{sid}",))

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
            patch.object(
                service._driver_manager, "get_current_driver", return_value={"version": "565"}
            ),
        ):
            service._dispatch_preflight_check(params, mock_invocation)

        result = mock_invocation.return_value.call_args[0][0].unpack()[0]
        assert result["current_driver"] == "565"
        assert result["snapshot_driver"] == "560"

    def test_package_availability_fails_for_missing_packages(
        self, service, snapshot_dir, mock_invocation
    ):
        """Rollback pre-flight fails when required packages aren't available."""
        sid = _create_snapshot(snapshot_dir)
        params = GLib.Variant("(s)", (f"rollback:{sid}",))

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "apt-cache" in cmd:
                result.returncode = 100  # not found
                result.stdout = ""
                result.stderr = "No packages found"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch.object(
                service._driver_manager, "get_current_driver", return_value={"version": "565"}
            ),
        ):
            service._dispatch_preflight_check(params, mock_invocation)

        result = mock_invocation.return_value.call_args[0][0].unpack()[0]
        pkg_check = next(c for c in result["checks"] if c["name"] == "package_availability")
        assert pkg_check["status"] == "fail"
        assert "unavailable" in pkg_check["description"].lower()
