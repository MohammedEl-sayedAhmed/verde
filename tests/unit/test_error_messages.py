"""Tests for the error message catalog (error_messages.py).

Verifies every error code maps to a complete message structure
with title, description, and suggestion.
"""

from __future__ import annotations

import pytest

from verde.error_messages import ERROR_MESSAGES, get_error_message

# Every error code that MUST be mapped
REQUIRED_ERROR_KEYS = [
    "com.verde.Error.PreflightFailed",
    "com.verde.Error.OperationInProgress",
    "com.verde.Error.InvalidArgument",
    "com.verde.Error.SnapshotNotFound",
    "daemon_unreachable",
    "nvml_unavailable",
    "apt_lock",
    "network_unavailable",
    "kernel_headers_missing",
    "dkms_failure",
    "secure_boot_unsigned",
]

REQUIRED_FIELDS = ["title", "description", "suggestion"]


class TestErrorMessagesCatalog:
    """Verify ERROR_MESSAGES catalog completeness and structure."""

    @pytest.mark.parametrize("key", REQUIRED_ERROR_KEYS)
    def test_error_key_exists(self, key: str) -> None:
        assert key in ERROR_MESSAGES, f"Missing error message for: {key}"

    @pytest.mark.parametrize("key", REQUIRED_ERROR_KEYS)
    def test_error_has_required_fields(self, key: str) -> None:
        entry = ERROR_MESSAGES[key]
        for field in REQUIRED_FIELDS:
            assert field in entry, f"Error '{key}' missing field: {field}"
            assert isinstance(entry[field], str)
            assert len(entry[field].strip()) > 0, f"Error '{key}' has empty {field}"

    @pytest.mark.parametrize("key", REQUIRED_ERROR_KEYS)
    def test_error_has_doc_link_key(self, key: str) -> None:
        """doc_link must exist (can be None)."""
        entry = ERROR_MESSAGES[key]
        assert "doc_link" in entry, f"Error '{key}' missing doc_link key"

    def test_no_raw_dbus_names_in_titles(self) -> None:
        """Titles should not contain raw D-Bus error names."""
        for key, entry in ERROR_MESSAGES.items():
            title = entry["title"]
            assert "com.verde" not in title, f"Error '{key}' title contains raw D-Bus name"
            assert "GDBus" not in title, f"Error '{key}' title contains raw GDBus reference"

    def test_no_raw_subprocess_in_descriptions(self) -> None:
        """Descriptions should not contain raw subprocess output."""
        forbidden = ["stderr", "subprocess", "Traceback", "Exception"]
        for key, entry in ERROR_MESSAGES.items():
            desc = entry["description"]
            for term in forbidden:
                assert term not in desc, f"Error '{key}' description contains raw output: {term}"


class TestGetErrorMessage:
    """Test the get_error_message() helper function."""

    def test_known_error_returns_catalog_entry(self) -> None:
        result = get_error_message("daemon_unreachable")
        assert result["title"] != ""
        assert "suggestion" in result

    def test_unknown_error_returns_fallback(self) -> None:
        result = get_error_message("some.unknown.error.code")
        assert result["title"] != ""
        assert "suggestion" in result

    def test_dbus_error_prefix_stripped(self) -> None:
        """GDBus.Error: prefix should be stripped before lookup."""
        result = get_error_message("GDBus.Error:com.verde.Error.OperationInProgress: busy")
        assert (
            "another operation" in result["title"].lower() or "running" in result["title"].lower()
        )
