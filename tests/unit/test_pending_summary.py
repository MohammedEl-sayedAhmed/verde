"""Unit tests for Story 3.5: Pending summary manager."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture()
def manager(state_dir):
    from pending_summary import PendingSummaryManager

    return PendingSummaryManager(state_dir=state_dir)


class TestWritePending:
    """Test write_pending() — AC#1."""

    def test_creates_valid_json(self, manager, state_dir):
        """write_pending creates a valid JSON file at the expected path."""
        manager.write_pending("install", "535", "550", "op_001")
        data = json.loads(manager.state_file.read_text())
        assert data["operation_type"] == "install"
        assert data["previous_version"] == "535"
        assert data["expected_version"] == "550"
        assert data["operation_id"] == "op_001"
        assert "timestamp" in data
        assert "kernel_version" in data

    def test_atomic_write(self, manager, state_dir):
        """write_pending uses atomic write (tmp + os.replace)."""
        with patch("os.replace") as mock_replace:
            # Let it actually write the tmp file
            mock_replace.side_effect = lambda src, dst: __import__("shutil").move(src, dst)
            manager.write_pending("install", "535", "550", "op_001")
            mock_replace.assert_called_once()
            args = mock_replace.call_args[0]
            assert args[0].endswith(".json.tmp")

    def test_rollback_operation_type(self, manager):
        """write_pending accepts rollback operation type."""
        manager.write_pending("rollback", "550", "535", "op_002")
        data = json.loads(manager.state_file.read_text())
        assert data["operation_type"] == "rollback"

    def test_invalid_operation_type(self, manager):
        """write_pending rejects invalid operation types."""
        with pytest.raises(ValueError, match="Invalid operation_type"):
            manager.write_pending("upgrade", "535", "550", "op_001")

    def test_overwrites_previous(self, manager):
        """write_pending overwrites any existing state file."""
        manager.write_pending("install", "535", "550", "op_001")
        manager.write_pending("rollback", "550", "535", "op_002")
        data = json.loads(manager.state_file.read_text())
        assert data["operation_type"] == "rollback"
        assert data["operation_id"] == "op_002"


class TestHasPending:
    """Test has_pending() — AC#2."""

    def test_true_when_exists(self, manager):
        """has_pending returns True when state file exists."""
        manager.write_pending("install", "535", "550", "op_001")
        assert manager.has_pending() is True

    def test_false_when_missing(self, manager):
        """has_pending returns False when no state file."""
        assert manager.has_pending() is False


class TestReadPending:
    """Test read_pending()."""

    def test_returns_dict(self, manager):
        """read_pending returns parsed dict for valid file."""
        manager.write_pending("install", "535", "550", "op_001")
        data = manager.read_pending()
        assert isinstance(data, dict)
        assert data["operation_type"] == "install"

    def test_returns_none_when_missing(self, manager):
        """read_pending returns None when file doesn't exist."""
        assert manager.read_pending() is None

    def test_returns_none_for_corrupt_json(self, manager):
        """read_pending returns None for corrupt JSON (preserves file)."""
        manager.state_file.write_text("not valid json {{{")
        result = manager.read_pending()
        assert result is None
        assert manager.state_file.exists()  # File preserved for debugging

    def test_returns_none_for_non_dict_json(self, manager):
        """read_pending returns None when JSON is not an object."""
        manager.state_file.write_text(json.dumps([1, 2, 3]))
        assert manager.read_pending() is None


class TestClearPending:
    """Test clear_pending()."""

    def test_removes_file(self, manager):
        """clear_pending removes the state file."""
        manager.write_pending("install", "535", "550", "op_001")
        assert manager.has_pending() is True
        manager.clear_pending()
        assert manager.has_pending() is False

    def test_silent_when_missing(self, manager):
        """clear_pending succeeds silently when file doesn't exist."""
        manager.clear_pending()  # Should not raise
