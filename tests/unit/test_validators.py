"""Unit tests for D-Bus input validators."""

from __future__ import annotations

import pytest

# verde_daemon package registration is handled by tests/conftest.py
from validators import (
    validate_driver_version,
    validate_operation_name,
    validate_snapshot_id,
)

# ===================================================================
# Driver version validation
# ===================================================================


class TestValidateDriverVersion:
    @pytest.mark.parametrize(
        "version",
        ["535", "565", "5650", "535-server", "560-open", "5650-server", "5650-open"],
    )
    def test_valid_versions(self, version):
        assert validate_driver_version(version) == version

    @pytest.mark.parametrize(
        "version",
        [
            "",
            "abc",
            "53",  # too short
            "53500",  # too long
            "535; rm -rf /",  # injection
            "../etc/passwd",  # path traversal
            "535-beta",  # invalid suffix
            "535 ",  # trailing space
            " 535",  # leading space
            "535-server-open",  # double suffix
        ],
    )
    def test_invalid_versions(self, version):
        with pytest.raises(ValueError, match="Invalid driver version"):
            validate_driver_version(version)


# ===================================================================
# Snapshot ID validation
# ===================================================================


class TestValidateSnapshotId:
    @pytest.mark.parametrize(
        "snapshot_id",
        [
            "2026-03-18T14:30:00_nvidia-565",
            "2025-01-01T00:00:00_nvidia-535",
            "2026-12-31T23:59:59_nvidia-5650",
        ],
    )
    def test_valid_snapshot_ids(self, snapshot_id):
        assert validate_snapshot_id(snapshot_id) == snapshot_id

    @pytest.mark.parametrize(
        "snapshot_id",
        [
            "",
            "not-a-snapshot",
            "2026-03-18T14:30:00_nvidia-565; whoami",  # injection
            "2026-03-18T14:30:00_amd-565",  # wrong vendor
            "2026-3-18T14:30:00_nvidia-565",  # single-digit month
            "2026-03-18 14:30:00_nvidia-565",  # space instead of T
            "2026-03-18T14:30:00_nvidia-56",  # version too short
        ],
    )
    def test_invalid_snapshot_ids(self, snapshot_id):
        with pytest.raises(ValueError, match="Invalid snapshot ID"):
            validate_snapshot_id(snapshot_id)


# ===================================================================
# Operation name validation
# ===================================================================


class TestValidateOperationName:
    @pytest.mark.parametrize(
        "operation",
        ["driver_install", "driver_rollback", "fix_suspend", "fix_hibernate"],
    )
    def test_valid_operations(self, operation):
        assert validate_operation_name(operation) == operation

    @pytest.mark.parametrize(
        "operation",
        [
            "",
            "drop_table",
            "driver_install && rm -rf /",  # injection
            "DRIVER_INSTALL",  # wrong case
            "driver_update",  # not in allowed set
            "fix_suspend; echo pwned",  # injection
        ],
    )
    def test_invalid_operations(self, operation):
        with pytest.raises(ValueError, match="Invalid operation name"):
            validate_operation_name(operation)


# ===================================================================
# Length guard (P-4: prevents DoS via oversized inputs)
# ===================================================================


class TestLengthGuard:
    def test_oversized_driver_version_rejected(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_driver_version("5" * 257)

    def test_oversized_snapshot_id_rejected(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_snapshot_id("x" * 257)

    def test_oversized_operation_name_rejected(self):
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_operation_name("a" * 257)

    def test_max_length_input_still_validated(self):
        """256-char input passes length check but fails regex."""
        with pytest.raises(ValueError, match="Invalid driver version"):
            validate_driver_version("5" * 256)
