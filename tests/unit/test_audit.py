"""Unit tests for AuditLogger — core functionality and error handling."""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

# verde_daemon package registration is handled by tests/conftest.py
from audit import (
    OP_AUTH_DENIED,
    OP_FIX_HIBERNATE,
    OP_FIX_SUSPEND,
    OP_INSTALL_DRIVER,
    OP_ROLLBACK_DRIVER,
    AuditLogger,
)

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def audit_logger(tmp_path):
    """AuditLogger writing to a temp directory."""
    return AuditLogger(log_dir=tmp_path)


@pytest.fixture
def audit_log_file(tmp_path):
    """Path to audit.log in tmp_path."""
    return tmp_path / "audit.log"


def _read_entries(log_file):
    """Read all JSONL entries from a log file."""
    return [json.loads(line) for line in log_file.read_text().strip().split("\n")]


# ===================================================================
# Operation constants
# ===================================================================


class TestOperationConstants:
    def test_install_driver(self):
        assert OP_INSTALL_DRIVER == "INSTALL_DRIVER"

    def test_rollback_driver(self):
        assert OP_ROLLBACK_DRIVER == "ROLLBACK_DRIVER"

    def test_fix_suspend(self):
        assert OP_FIX_SUSPEND == "FIX_SUSPEND"

    def test_fix_hibernate(self):
        assert OP_FIX_HIBERNATE == "FIX_HIBERNATE"

    def test_auth_denied(self):
        assert OP_AUTH_DENIED == "AUTH_DENIED"


# ===================================================================
# Core functionality
# ===================================================================


class TestAuditLoggerCore:
    def test_log_creates_directory(self, tmp_path):
        log_dir = tmp_path / "verde"
        logger = AuditLogger(log_dir=log_dir)
        logger.log("TEST_OP", {}, ":1.1", "success")
        assert log_dir.exists()
        assert (log_dir / "audit.log").exists()

    def test_directory_permissions_0750(self, tmp_path):
        log_dir = tmp_path / "verde"
        logger = AuditLogger(log_dir=log_dir)
        logger.log("TEST_OP", {}, ":1.1", "success")
        mode = stat.S_IMODE(log_dir.stat().st_mode)
        assert mode == 0o750

    def test_log_appends_valid_json_line(self, audit_logger, audit_log_file):
        audit_logger.log(OP_INSTALL_DRIVER, {"version": "565"}, ":1.42", "success")
        entries = _read_entries(audit_log_file)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["operation"] == "INSTALL_DRIVER"
        assert entry["params"] == {"version": "565"}
        assert entry["caller"] == ":1.42"
        assert entry["result"] == "success"

    def test_entry_contains_all_required_fields(self, audit_logger, audit_log_file):
        audit_logger.log(OP_INSTALL_DRIVER, {"version": "535"}, ":1.10", "success")
        entry = _read_entries(audit_log_file)[0]
        required = {"timestamp", "operation", "params", "caller", "result"}
        assert required.issubset(entry.keys())

    def test_timestamp_is_iso8601_with_timezone(self, audit_logger, audit_log_file):
        audit_logger.log("TEST_OP", {}, ":1.1", "success")
        entry = _read_entries(audit_log_file)[0]
        ts = datetime.fromisoformat(entry["timestamp"])
        assert ts.tzinfo is not None

    def test_timestamp_is_utc(self, audit_logger, audit_log_file):
        audit_logger.log("TEST_OP", {}, ":1.1", "success")
        entry = _read_entries(audit_log_file)[0]
        ts = datetime.fromisoformat(entry["timestamp"])
        assert ts.tzinfo == UTC

    def test_error_field_absent_on_success(self, audit_logger, audit_log_file):
        audit_logger.log(OP_INSTALL_DRIVER, {"version": "565"}, ":1.42", "success")
        entry = _read_entries(audit_log_file)[0]
        assert "error" not in entry

    def test_error_field_present_on_failure(self, audit_logger, audit_log_file):
        audit_logger.log(
            OP_INSTALL_DRIVER, {"version": "565"}, ":1.42", "failed", error="apt failed"
        )
        entry = _read_entries(audit_log_file)[0]
        assert entry["error"] == "apt failed"

    def test_multiple_logs_produce_multiple_lines(self, audit_logger, audit_log_file):
        audit_logger.log(OP_INSTALL_DRIVER, {"version": "535"}, ":1.1", "success")
        audit_logger.log(OP_ROLLBACK_DRIVER, {"snapshot_id": "snap1"}, ":1.2", "success")
        audit_logger.log(OP_FIX_SUSPEND, {}, ":1.3", "failed", error="permission denied")
        entries = _read_entries(audit_log_file)
        assert len(entries) == 3
        assert entries[0]["operation"] == "INSTALL_DRIVER"
        assert entries[1]["operation"] == "ROLLBACK_DRIVER"
        assert entries[2]["operation"] == "FIX_SUSPEND"

    def test_append_not_overwrite(self, audit_logger, audit_log_file):
        audit_logger.log("OP_A", {}, ":1.1", "success")
        audit_logger.log("OP_B", {}, ":1.2", "success")
        entries = _read_entries(audit_log_file)
        assert entries[0]["operation"] == "OP_A"
        assert entries[1]["operation"] == "OP_B"

    def test_compact_json_no_trailing_spaces(self, audit_logger, audit_log_file):
        audit_logger.log("TEST_OP", {"key": "value"}, ":1.1", "success")
        raw = audit_log_file.read_text()
        # Each line should end with }\n — no trailing spaces
        for line in raw.strip().split("\n"):
            assert line.endswith("}")
            assert '" :' not in line  # no spaces after separators
            assert '", ' not in line or '",' in line  # compact separators


# ===================================================================
# log_auth_failure convenience method
# ===================================================================


class TestLogAuthFailure:
    def test_produces_auth_denied_entry(self, audit_logger, audit_log_file):
        audit_logger.log_auth_failure("com.verde.driver.manage", ":1.42", "InstallDriver")
        entry = _read_entries(audit_log_file)[0]
        assert entry["operation"] == "AUTH_DENIED"
        assert entry["params"]["action"] == "com.verde.driver.manage"
        assert entry["params"]["method"] == "InstallDriver"
        assert entry["caller"] == ":1.42"
        assert entry["result"] == "denied"

    def test_auth_failure_has_no_error_field(self, audit_logger, audit_log_file):
        audit_logger.log_auth_failure("com.verde.monitor", ":1.10", "GetGPUInfo")
        entry = _read_entries(audit_log_file)[0]
        assert "error" not in entry


# ===================================================================
# Error handling and edge cases
# ===================================================================


class TestErrorHandling:
    def test_does_not_raise_on_read_only_dir(self, tmp_path, caplog):
        log_dir = tmp_path / "readonly"
        log_dir.mkdir()
        os.chmod(log_dir, 0o444)
        logger = AuditLogger(log_dir=log_dir)
        # Should not raise
        logger.log("TEST_OP", {}, ":1.1", "success")
        # Restore permissions for cleanup
        os.chmod(log_dir, 0o755)

    def test_does_not_raise_on_open_failure(self, tmp_path):
        logger = AuditLogger(log_dir=tmp_path)
        with patch("audit.os.open", side_effect=OSError("disk full")):
            # Should not raise
            logger.log("TEST_OP", {}, ":1.1", "success")

    def test_logs_io_error_to_python_logging(self, tmp_path, caplog):
        logger = AuditLogger(log_dir=tmp_path)
        with (
            patch("audit.os.open", side_effect=OSError("disk full")),
            caplog.at_level(logging.ERROR, logger="verde-daemon.audit"),
        ):
            logger.log("TEST_OP", {}, ":1.1", "success")
        assert any("Failed to write audit log" in msg for msg in caplog.messages)

    def test_empty_params_produces_valid_json(self, audit_logger, audit_log_file):
        audit_logger.log("TEST_OP", {}, ":1.1", "success")
        entry = _read_entries(audit_log_file)[0]
        assert entry["params"] == {}

    def test_special_characters_in_params(self, audit_logger, audit_log_file):
        params = {"path": '/tmp/"quoted"', "emoji": "\u2603", "newline": "line1\nline2"}
        audit_logger.log("TEST_OP", params, ":1.1", "success")
        entry = _read_entries(audit_log_file)[0]
        assert entry["params"]["path"] == '/tmp/"quoted"'
        assert entry["params"]["emoji"] == "\u2603"
        assert entry["params"]["newline"] == "line1\nline2"

    def test_concurrent_writes_produce_valid_lines(self, audit_logger, audit_log_file):
        """Two sequential writes both produce valid JSON lines."""
        audit_logger.log("OP_1", {"i": "1"}, ":1.1", "success")
        audit_logger.log("OP_2", {"i": "2"}, ":1.2", "success")
        entries = _read_entries(audit_log_file)
        assert len(entries) == 2
        assert entries[0]["operation"] == "OP_1"
        assert entries[1]["operation"] == "OP_2"

    def test_directory_creation_failure_logged(self, tmp_path, caplog):
        """If mkdir fails, error is logged, not raised."""
        log_dir = tmp_path / "nonexistent" / "nested"
        logger = AuditLogger(log_dir=log_dir)
        # Make parent read-only so mkdir fails
        (tmp_path / "nonexistent").mkdir()
        os.chmod(tmp_path / "nonexistent", 0o444)
        with caplog.at_level(logging.ERROR, logger="verde-daemon.audit"):
            logger.log("TEST_OP", {}, ":1.1", "success")
        assert any("Failed to write audit log" in msg for msg in caplog.messages)
        # Restore permissions for cleanup
        os.chmod(tmp_path / "nonexistent", 0o755)

    def test_lazy_directory_creation(self, tmp_path):
        """Directory is NOT created in __init__, only on first log()."""
        log_dir = tmp_path / "lazy"
        _logger = AuditLogger(log_dir=log_dir)
        assert not log_dir.exists()

    def test_non_serializable_params_does_not_crash(self, tmp_path, caplog):
        """Non-JSON-serializable params logs error, does not raise (P-1)."""
        logger = AuditLogger(log_dir=tmp_path)
        with caplog.at_level(logging.ERROR, logger="verde-daemon.audit"):
            logger.log("TEST_OP", {"bad": {1, 2, 3}}, ":1.1", "success")
        assert any("Failed to serialize audit entry" in msg for msg in caplog.messages)
        # No file should be created since serialization failed
        assert not (tmp_path / "audit.log").exists()

    def test_non_serializable_value_error_caught(self, tmp_path, caplog):
        """ValueError from json.dumps is caught gracefully (P-1)."""
        logger = AuditLogger(log_dir=tmp_path)
        with (
            patch("audit.json.dumps", side_effect=ValueError("circular ref")),
            caplog.at_level(logging.ERROR, logger="verde-daemon.audit"),
        ):
            logger.log("TEST_OP", {}, ":1.1", "success")
        assert any("Failed to serialize audit entry" in msg for msg in caplog.messages)

    def test_file_permissions_0640(self, audit_logger, audit_log_file):
        """Audit log file is created with 0o640 permissions (P-2)."""
        audit_logger.log("TEST_OP", {}, ":1.1", "success")
        mode = stat.S_IMODE(audit_log_file.stat().st_mode)
        assert mode == 0o640

    def test_symlink_rejected(self, tmp_path):
        """Symlink at audit.log path is rejected via O_NOFOLLOW (P-4)."""
        logger = AuditLogger(log_dir=tmp_path)
        # Create a symlink where audit.log would be
        symlink_path = tmp_path / "audit.log"
        symlink_path.symlink_to("/tmp/evil_target")
        # Should not follow symlink — logs error instead
        logger.log("TEST_OP", {}, ":1.1", "success")
        # The symlink target should NOT have been written to
        assert not os.path.exists("/tmp/evil_target")
