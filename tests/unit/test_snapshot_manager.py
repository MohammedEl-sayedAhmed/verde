"""Unit tests for SnapshotManager."""

from __future__ import annotations

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from snapshot_manager import (
    MAX_SNAPSHOTS,
    InsufficientSpaceError,
    InvalidSnapshotId,
    SnapshotManager,
    _compute_sha256,
    _validate_snapshot_id,
    verify_snapshot_integrity,
)

# ===================================================================
# Task 1: Snapshot data model / JSON schema
# ===================================================================


class TestSnapshotSchema:
    """Snapshot JSON contains all required fields (schema validation)."""

    @patch("snapshot_manager._capture_config_files", return_value={})
    @patch("snapshot_manager._query_dkms_modules", return_value=[])
    @patch(
        "snapshot_manager._query_nvidia_packages",
        return_value=[
            {"name": "nvidia-driver-565", "version": "565.57.01", "architecture": "amd64"},
        ],
    )
    def test_snapshot_contains_all_required_fields(self, _pkgs, _dkms, _conf, tmp_path):
        mgr = SnapshotManager(snapshot_dir=tmp_path / "snapshots")
        sid = mgr.create_snapshot("driver_install", "565", "testuser")

        data = mgr.get_snapshot(sid)
        required = {
            "schema_version",
            "snapshot_id",
            "timestamp",
            "driver_packages",
            "kernel_version",
            "dkms_modules",
            "config_files",
            "operation",
            "sha256",
        }
        assert required.issubset(data.keys())
        assert data["schema_version"] == 1
        assert data["snapshot_id"] == sid
        assert data["operation"]["type"] == "driver_install"
        assert data["operation"]["target_driver"] == "565"
        assert data["operation"]["user"] == "testuser"

    def test_filename_convention_matches_pattern(self, tmp_path):
        """Snapshot filenames match {timestamp}_{driver}.json."""
        pattern = re.compile(r"^[0-9]{8}T[0-9]{6}_[a-zA-Z0-9._-]+-[0-9a-f]{4}\.json$")
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            mgr = SnapshotManager(snapshot_dir=tmp_path / "snapshots")
            mgr.create_snapshot("driver_install", "nvidia-565", "user1")

        files = list((tmp_path / "snapshots").glob("*.json"))
        assert len(files) == 1
        assert pattern.match(files[0].name)


# ===================================================================
# Task 3: SHA-256 integrity hash
# ===================================================================


class TestSha256Integrity:
    def test_sha256_correctly_computed(self, tmp_path):
        """SHA-256 verification passes for valid snapshots."""
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            mgr = SnapshotManager(snapshot_dir=tmp_path / "snapshots")
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        assert mgr.verify_integrity(sid) is True

    def test_sha256_verification_fails_for_tampered_snapshot(self, tmp_path):
        """SHA-256 verification fails for tampered snapshots."""
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            mgr = SnapshotManager(snapshot_dir=tmp_path / "snapshots")
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        # Tamper with the file
        snap_path = tmp_path / "snapshots" / f"{sid}.json"
        data = json.loads(snap_path.read_text())
        data["kernel_version"] = "TAMPERED"
        snap_path.write_text(json.dumps(data))

        assert mgr.verify_integrity(sid) is False

    def test_sha256_excludes_sha256_field(self):
        """SHA-256 is computed with sha256 field set to null."""
        data = {"a": 1, "sha256": "should_be_ignored"}
        h1 = _compute_sha256(data)

        data2 = {"a": 1, "sha256": "different_value"}
        h2 = _compute_sha256(data2)

        assert h1 == h2  # sha256 field value doesn't affect hash


# ===================================================================
# Task 4: Storage space validation
# ===================================================================


class TestStorageSpaceValidation:
    def test_blocks_creation_when_space_is_low(self, tmp_path):
        """Storage space check blocks creation when space is low."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        mock_stat = MagicMock()
        mock_stat.f_bavail = 1  # Very few blocks
        mock_stat.f_frsize = 4096  # 4KB block = 4KB total (well below 10MB)

        with patch("os.statvfs", return_value=mock_stat), pytest.raises(InsufficientSpaceError):
            mgr.create_snapshot("driver_install", "565", "user1")

    def test_allows_creation_when_space_is_sufficient(self, tmp_path):
        """Storage space check allows creation when space is sufficient."""
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        mock_stat = MagicMock()
        mock_stat.f_bavail = 100000  # Plenty of blocks
        mock_stat.f_frsize = 4096  # 400MB total

        with (
            patch("os.statvfs", return_value=mock_stat),
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        assert sid is not None


# ===================================================================
# Task 5: Atomic file write
# ===================================================================


class TestAtomicWrite:
    def test_uses_temp_file_then_rename(self, tmp_path):
        """Atomic write uses temp file then os.replace."""
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        replace_calls = []
        original_replace = os.replace

        def tracking_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
            patch("os.replace", side_effect=tracking_replace),
        ):
            mgr.create_snapshot("driver_install", "565", "user1")

        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert ".tmp_" in src
        assert dst.endswith(".json")

    def test_snapshot_file_permissions(self, tmp_path):
        """Snapshot files are written with 0o644 permissions."""
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        snap_path = snap_dir / f"{sid}.json"
        mode = oct(os.stat(snap_path).st_mode & 0o777)
        assert mode == oct(0o644)


# ===================================================================
# Task 6: Retention policy
# ===================================================================


class TestRetentionPolicy:
    def test_prunes_oldest_when_exceeding_max(self, tmp_path):
        """Retention policy prunes oldest when exceeding MAX_SNAPSHOTS."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True)

        # Pre-create MAX_SNAPSHOTS files
        for i in range(MAX_SNAPSHOTS):
            ts = f"20260301T{i:06d}"
            name = f"{ts}_nvidia-560-aa{i:02x}.json"
            (snap_dir / name).write_text(json.dumps({"snapshot_id": name[:-5]}))

        assert len(list(snap_dir.glob("*.json"))) == MAX_SNAPSHOTS

        # Create one more
        mgr = SnapshotManager(snapshot_dir=snap_dir)
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            mgr.create_snapshot("driver_install", "565", "user1")

        remaining = list(snap_dir.glob("*.json"))
        # Should not exceed MAX_SNAPSHOTS
        assert len(remaining) <= MAX_SNAPSHOTS
        # The oldest (20260301T000000) should be pruned
        names = {f.name for f in remaining}
        assert "20260301T000000_nvidia-560-aa00.json" not in names


# ===================================================================
# Task 7: Recovery instructions
# ===================================================================


class TestRecoveryInstructions:
    def test_recovery_file_written_with_correct_content(self, tmp_path):
        """Recovery instructions file is written with correct content."""
        snap_dir = tmp_path / "snapshots"
        recovery = tmp_path / "recovery-instructions.txt"
        mgr = SnapshotManager(snapshot_dir=snap_dir, recovery_path=recovery)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        mgr.write_recovery_instructions(sid, "Installing", "565")

        content = recovery.read_text()
        assert "Verde Recovery Instructions" in content
        assert sid in content
        assert "verde --repair" in content
        assert "nvidia-driver-565" in content
        assert "Ctrl+Alt+F2" in content

    def test_recovery_write_is_atomic(self, tmp_path):
        """Recovery instructions written atomically."""
        snap_dir = tmp_path / "snapshots"
        recovery = tmp_path / "recovery-instructions.txt"
        mgr = SnapshotManager(snapshot_dir=snap_dir, recovery_path=recovery)

        replace_calls = []
        original_replace = os.replace

        def tracking_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        with patch("os.replace", side_effect=tracking_replace):
            mgr.write_recovery_instructions(sid, "Installing", "565")

        assert any(str(recovery) in dst for _, dst in replace_calls)


# ===================================================================
# Task 8: Audit log integration
# ===================================================================


class TestAuditLogIntegration:
    def test_audit_logger_called_on_snapshot_create(self, tmp_path):
        """Audit logger is called with correct params on snapshot creation."""
        snap_dir = tmp_path / "snapshots"
        audit = MagicMock()
        mgr = SnapshotManager(snapshot_dir=snap_dir, audit_logger=audit)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "testuser")

        audit.log.assert_called_once()
        call_kwargs = audit.log.call_args[1]
        assert call_kwargs["operation"] == "SNAPSHOT_CREATE"
        assert call_kwargs["params"]["snapshot"] == sid
        assert call_kwargs["params"]["driver"] == "565"
        assert call_kwargs["result"] == "success"

    def test_fallback_to_python_logger_when_no_audit(self, tmp_path):
        """Falls back to Python logger when no audit logger provided."""
        import logging as _logging

        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir, audit_logger=None)

        mock_audit_logger = MagicMock()
        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
            patch.object(_logging, "getLogger", wraps=_logging.getLogger) as mock_get,
        ):
            mock_get.return_value = mock_audit_logger
            mgr.create_snapshot("driver_install", "565", "user1")

        # Verify the audit fallback specifically requested "verde.audit"
        audit_calls = [c for c in mock_get.call_args_list if c[0] == ("verde.audit",)]
        assert len(audit_calls) >= 1


# ===================================================================
# Task 9: SnapshotManager public API
# ===================================================================


class TestSnapshotManagerAPI:
    def test_list_snapshots_returns_sorted_newest_first(self, tmp_path):
        """list_snapshots returns snapshots sorted by timestamp (newest first)."""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True)

        # Create snapshots with different timestamps
        for ts in ["20260301T100000", "20260303T100000", "20260302T100000"]:
            data = {
                "schema_version": 1,
                "snapshot_id": f"{ts}_nvidia-565-ab01",
                "timestamp": f"2026-03-0{ts[7]}T10:00:00+00:00",
                "operation": {"type": "install", "target_driver": "565", "user": "u"},
                "driver_packages": [],
                "kernel_version": "6.8.0",
                "dkms_modules": [],
                "config_files": {},
                "sha256": "abc",
            }
            (snap_dir / f"{ts}_nvidia-565-ab01.json").write_text(json.dumps(data))

        mgr = SnapshotManager(snapshot_dir=snap_dir)
        result = mgr.list_snapshots()
        assert len(result) == 3
        # Newest first (files sorted reverse)
        assert result[0]["snapshot_id"] == "20260303T100000_nvidia-565-ab01"
        assert result[2]["snapshot_id"] == "20260301T100000_nvidia-565-ab01"

    def test_list_snapshots_empty_directory(self, tmp_path):
        mgr = SnapshotManager(snapshot_dir=tmp_path / "nonexistent")
        assert mgr.list_snapshots() == []

    def test_delete_snapshot_removes_file(self, tmp_path):
        """delete_snapshot removes the file."""
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        assert (snap_dir / f"{sid}.json").exists()
        mgr.delete_snapshot(sid)
        assert not (snap_dir / f"{sid}.json").exists()

    def test_get_snapshot_returns_full_data(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        with (
            patch(
                "snapshot_manager._query_nvidia_packages",
                return_value=[
                    {"name": "nvidia-driver-565", "version": "565.57", "architecture": "amd64"},
                ],
            ),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        data = mgr.get_snapshot(sid)
        assert data["snapshot_id"] == sid
        assert len(data["driver_packages"]) == 1
        assert data["driver_packages"][0]["name"] == "nvidia-driver-565"

    def test_get_snapshot_raises_for_nonexistent(self, tmp_path):
        mgr = SnapshotManager(snapshot_dir=tmp_path / "snapshots")
        with pytest.raises(FileNotFoundError):
            mgr.get_snapshot("20260301T100000_nvidia-565-abcd")


# ===================================================================
# Snapshot ID validation
# ===================================================================


class TestSnapshotIdValidation:
    def test_valid_id_accepted(self):
        _validate_snapshot_id("20260318T143000_nvidia-560-a1b2")

    def test_valid_id_with_dots(self):
        _validate_snapshot_id("20260318T143000_nvidia-565.57-f0e1")

    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "no-timestamp",
            "20260318_missing-time",
            "../../../etc/passwd",
            "20260318T143000_with spaces",
            "20260318T143000_with/slash",
        ],
    )
    def test_rejects_malformed_ids(self, bad_id):
        with pytest.raises(InvalidSnapshotId):
            _validate_snapshot_id(bad_id)


# ===================================================================
# verify_snapshot_integrity (standalone function)
# ===================================================================


class TestVerifySnapshotIntegrity:
    def test_returns_false_for_nonexistent_file(self, tmp_path):
        assert verify_snapshot_integrity(tmp_path / "nope.json") is False

    def test_returns_false_for_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        assert verify_snapshot_integrity(bad) is False

    def test_returns_false_for_missing_sha256_field(self, tmp_path):
        p = tmp_path / "no_hash.json"
        p.write_text(json.dumps({"a": 1}))
        assert verify_snapshot_integrity(p) is False


# ===================================================================
# IG-1: target_driver validation in write_recovery_instructions
# ===================================================================


class TestTargetDriverValidation:
    def test_rejects_invalid_target_driver(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        mgr = SnapshotManager(snapshot_dir=snap_dir)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        with pytest.raises(ValueError, match="Invalid target_driver"):
            mgr.write_recovery_instructions(sid, "Installing", "565; rm -rf /")

    def test_accepts_valid_target_driver(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        recovery = tmp_path / "recovery.txt"
        mgr = SnapshotManager(snapshot_dir=snap_dir, recovery_path=recovery)

        with (
            patch("snapshot_manager._query_nvidia_packages", return_value=[]),
            patch("snapshot_manager._query_dkms_modules", return_value=[]),
            patch("snapshot_manager._capture_config_files", return_value={}),
        ):
            sid = mgr.create_snapshot("driver_install", "565", "user1")

        mgr.write_recovery_instructions(sid, "Installing", "565")
        assert recovery.exists()


# ===================================================================
# P-2: Audit log on failure paths
# ===================================================================


class TestAuditOnFailure:
    def test_audit_logged_on_snapshot_failure(self, tmp_path):
        """Audit log records failure when snapshot creation crashes."""
        snap_dir = tmp_path / "snapshots"
        audit = MagicMock()
        mgr = SnapshotManager(snapshot_dir=snap_dir, audit_logger=audit)

        with (
            patch("snapshot_manager._query_nvidia_packages", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError),
        ):
            mgr.create_snapshot("driver_install", "565", "user1")

        audit.log.assert_called_once()
        call_kwargs = audit.log.call_args[1]
        assert call_kwargs["result"] == "failed"
        assert "boom" in call_kwargs["error"]
